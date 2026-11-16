"""Address search over the instance BAN (D-105, D-106, D-107).

The search reuses :func:`app.ban.queries.geocoder` — never a second
geocoding engine, never a remote service — and projects its rows without
coordinates. A territory out of coverage is recognized in the national
referential ``geo_communes`` (all homonyms), never in ``adresses`` nor in
the lookup derived from it: those only know the hosted departements.
"""

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.schemas import (
    CorrespondanceNumeroRecherche,
    DepartementRef,
    HebergementBanRecherche,
    RechercheAdresses,
    ResultatRechercheAdresse,
    TerritoireRecherche,
    TypeResultat,
)
from app.ban.queries import (
    _LIAISONS,
    _RE_POSTAL,
    _VOIES,
    RechercheTropLargeError,
    _extract_numero,
    erreur_de_lecture,
    geocoder,
    normalize_address_text,
)
from app.resolve import telemetry
from app.scope.coverage import REASON_ORDER, hosting_for
from app.scope.dependencies import RouteHosting, commune_departement, real_departement
from app.scope.model import InstanceScope
from app.zone.queries import postal_code_departement

logger = logging.getLogger(__name__)

ADRESSES_D_ABORD: list[TypeResultat] = ["adresse", "commune"]
COMMUNES_D_ABORD: list[TypeResultat] = ["commune", "adresse"]

# A house number of one to four digits followed by a space and a word, a
# comma, or an index letter ("12bis"). A bare number ("12") or a postal code
# ("44000") does not start an address.
_RE_NUMERO_EN_TETE = re.compile(r"^\d{1,4}(?:\s*,|\s+\S|[a-z])")

_MOTS_COMMUNE_MAX = 8

# Rows fetched to group a street without number before keeping `limite`.
LIGNES_PAR_VOIE = 50


def _sans_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold().replace("œ", "oe"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _contient_type_de_voie(mots: Sequence[str]) -> bool:
    for longueur in {len(voie) for voie in _VOIES}:
        for debut in range(len(mots) - longueur + 1):
            if tuple(mots[debut : debut + longueur]) in _VOIES:
                return True
    return False


def ordre_des_types(q: str) -> list[TypeResultat]:
    """Served order of the result types for the input *q* (D-106).

    Addresses first when the input starts with a house number or contains a
    street type; communes first otherwise. Lower case, accents removed.
    """
    saisie = _sans_accents(q.strip())
    if _RE_NUMERO_EN_TETE.match(saisie):
        return ADRESSES_D_ABORD
    if _contient_type_de_voie(normalize_address_text(saisie).split()):
        return ADRESSES_D_ABORD
    return COMMUNES_D_ABORD


async def _lire(session: AsyncSession, statement, params: dict) -> list:
    """Rows of *statement* in a savepoint.

    An availability failure is the typed geocoding outage; any other error
    is a defect, raised as the bounded error of the geocoder. Neither keeps
    the input nor the database error (C58 WR-03 and CR-01, C59 B1).
    """
    try:
        async with session.begin_nested():
            result = await session.execute(statement, params)
            return list(result)
    except Exception as exc:  # noqa: BLE001 - re-raised bounded, below
        # Bounded code only (PRIV-01): params carry the input.
        telemetry.emit_resolve_event(
            logger,
            "address_search_read_failed",
            level=logging.WARNING,
            error_code=telemetry.error_code_for(exc),
        )
        erreur = erreur_de_lecture(exc)
    raise erreur from None


_DEPARTEMENT_NAMES = text(
    "SELECT code, nom FROM geo_departements WHERE code = ANY(CAST(:codes AS text[]))"
)


async def noms_departements(
    session: AsyncSession, codes: Iterable[str | None]
) -> dict[str, str]:
    """Names of *codes* in the national referential ``geo_departements``."""
    wanted = sorted({code for code in codes if code})
    if not wanted:
        return {}
    rows = await _lire(session, _DEPARTEMENT_NAMES, {"codes": wanted})
    return {row.code: row.nom for row in rows}


def _departement(
    code_commune: str | None, noms: dict[str, str]
) -> DepartementRef | None:
    code = commune_departement(code_commune)
    return DepartementRef(code=code, nom=noms.get(code)) if code else None


def _adresse(row: dict, noms: dict[str, str]) -> ResultatRechercheAdresse:
    return ResultatRechercheAdresse(
        type_resultat="adresse",
        ban_id=row["ban_id"],
        numero=row["numero"],
        rep=row["rep"],
        nom_voie=row["nom_voie"],
        code_postal=row["code_postal"],
        code_commune=row["code_commune"],
        nom_commune=row["nom_commune"],
        departement=_departement(row["code_commune"], noms),
    )


def regrouper_par_voie(
    rows: Iterable[dict], noms: dict[str, str]
) -> list[ResultatRechercheAdresse]:
    """One ``voie`` result per (street, postal code, commune), first seen first.

    A street has neither number nor key: an address page needs a number
    (D-107).
    """
    voies: dict[tuple, ResultatRechercheAdresse] = {}
    for row in rows:
        cle = (row["nom_voie"], row["code_postal"], row["code_commune"])
        if cle not in voies:
            voies[cle] = ResultatRechercheAdresse(
                type_resultat="voie",
                nom_voie=row["nom_voie"],
                code_postal=row["code_postal"],
                code_commune=row["code_commune"],
                nom_commune=row["nom_commune"],
                departement=_departement(row["code_commune"], noms),
            )
    return list(voies.values())


def _numero_demande(q: str) -> tuple[int, str | None] | None:
    """Explicit leading number and suffix parsed by the existing BAN rules."""
    _, numero, suffixe = _extract_numero(normalize_address_text(_RE_POSTAL.sub(" ", q)))
    return (numero, suffixe) if numero is not None else None


def _a_un_numero(q: str) -> bool:
    return _numero_demande(q) is not None


def qualifier_correspondance_numero(
    q: str, resultats: Sequence[ResultatRechercheAdresse]
) -> CorrespondanceNumeroRecherche | None:
    """Qualify number/suffix equality within *resultats*, never beyond them."""
    demande = _numero_demande(q)
    if demande is None:
        return None
    numero_demande, suffixe_demande = demande
    exacte = False
    for resultat in resultats:
        if resultat.type_resultat != "adresse" or resultat.numero is None:
            continue
        numero = resultat.numero.strip()
        if not numero.isascii() or not numero.isdigit():
            continue
        suffixe = (resultat.rep or "").strip().casefold() or None
        if (int(numero), suffixe) == demande:
            exacte = True
            break
    etat: Literal["exacte", "proches", "aucune_dans_resultats"]
    if exacte:
        etat = "exacte"
    elif resultats:
        etat = "proches"
    else:
        etat = "aucune_dans_resultats"
    return CorrespondanceNumeroRecherche(
        etat=etat,
        numero_demande=numero_demande,
        suffixe_demande=suffixe_demande,
    )


async def rechercher_adresses(
    session: AsyncSession, q: str, departements: Sequence[str], limite: int
) -> list[ResultatRechercheAdresse]:
    """BAN results of *q* in *departements*, without coordinates.

    With a number: the numbered addresses (a row without number is grouped
    as its street). Without: the streets, grouped. The departement filter is
    applied in SQL before ranking and LIMIT; an availability failure raises
    :class:`GeocodageIndisponibleError`, any other error
    :class:`GeocodageDefautError`.
    """
    numerote = _a_un_numero(q)
    rows = await geocoder(
        session,
        q,
        limite if numerote else LIGNES_PAR_VOIE,
        departements_heberges=departements,
        erreurs="lever",
    )
    noms = await noms_departements(
        session, (commune_departement(row["code_commune"]) for row in rows)
    )
    avec_numero = [row for row in rows if numerote and (row["numero"] or "").strip()]
    sans_numero = [
        row for row in rows if not (numerote and (row["numero"] or "").strip())
    ]
    resultats = [_adresse(row, noms) for row in avec_numero]
    resultats += regrouper_par_voie(sans_numero, noms)
    return resultats[:limite]


# Same normalization as the commune search and its index (migration 071).
_NOM_NORMALISE = r"regexp_replace(immutable_unaccent(lower({})), '[-\s]+', ' ', 'g')"
_COMMUNES_PAR_NOM = text(f"""
    SELECT gc.code, gc.nom, gc.departement_code,
           {_NOM_NORMALISE.format("gc.nom")} AS nom_norm
    FROM geo_communes gc
    WHERE {_NOM_NORMALISE.format("gc.nom")} = ANY(ARRAY(
        SELECT {_NOM_NORMALISE.format("c")}
        FROM unnest(CAST(:candidats AS text[])) AS c
    ))
    ORDER BY gc.code
""")


def candidats_commune(q: str) -> list[str]:
    """Trailing word sequences of *q* that may name the commune typed.

    A sequence that follows a linking word or a street type belongs to the
    street name ("12 rue de Rennes"); a sequence with a digit is no name.
    """
    mots = [mot.strip(",;.") for mot in re.split(r"[\s\-]+", q.strip())]
    mots = [mot for mot in mots if mot]
    candidats = []
    for taille in range(1, min(len(mots), _MOTS_COMMUNE_MAX) + 1):
        debut = len(mots) - taille
        if debut > 0:
            precedent = _sans_accents(mots[debut - 1])
            if precedent in _LIAISONS or (precedent,) in _VOIES:
                continue
        candidat = " ".join(mots[debut:])
        if not any(c.isdigit() for c in candidat):
            candidats.append(candidat)
    return candidats


def _hors_couverture(
    hostings, territoires: list[TerritoireRecherche]
) -> HebergementBanRecherche:
    motifs = {hosting.motif for hosting in hostings}
    motif = next(reason for reason in REASON_ORDER if reason in motifs)
    seul = territoires[0] if len(territoires) == 1 else None
    return HebergementBanRecherche(
        etat="hors_couverture",
        motif=motif,
        territoire=seul,
        homonymes=[] if seul else territoires,
    )


async def detecter_territoire(
    session: AsyncSession, q: str, scope: InstanceScope
) -> HebergementBanRecherche | None:
    """BAN out-of-coverage state of the territory named by *q*, or None.

    A five-digit postal code names its departement; otherwise the commune
    name at the end of the input is looked up in ``geo_communes`` with every
    homonym. Out of coverage only when every departement found is out for
    ``ban``; a territory naming no real departement is no proof (02-07).
    """
    postal = _RE_POSTAL.search(q)
    if postal:
        departement = real_departement(postal_code_departement(postal.group(1)))
        if departement is not None:
            hosting = hosting_for("ban", scope, departement)
            if hosting.etat == "heberge":
                return None
            noms = await noms_departements(session, [departement])
            ref = DepartementRef(code=departement, nom=noms.get(departement))
            return _hors_couverture([hosting], [TerritoireRecherche(departement=ref)])

    candidats = candidats_commune(q)
    if not candidats:
        return None
    rows = await _lire(session, _COMMUNES_PAR_NOM, {"candidats": candidats})
    if not rows:
        return None
    # The longest name matched wins ("Saint-Philbert-de-Grand-Lieu" over "Lieu").
    taille = max(len(row.nom_norm.split()) for row in rows)
    communes = [row for row in rows if len(row.nom_norm.split()) == taille]
    hostings = [
        hosting_for("ban", scope, real_departement(row.departement_code))
        for row in communes
    ]
    if any(hosting.etat == "heberge" for hosting in hostings):
        return None
    noms = await noms_departements(session, (row.departement_code for row in communes))
    territoires = [
        TerritoireRecherche(
            code_insee=row.code,
            nom=row.nom,
            departement=DepartementRef(
                code=row.departement_code, nom=noms.get(row.departement_code)
            ),
        )
        for row in communes
    ]
    return _hors_couverture(hostings, territoires)


def _hebergement(hosting: RouteHosting, **extra) -> dict:
    return {
        "ban": HebergementBanRecherche(**hosting.hostings["ban"].model_dump(), **extra)
    }


async def servir_recherche(
    session: AsyncSession,
    q: str,
    *,
    departement: str | None,
    limite: int,
    hosting: RouteHosting,
    scope: InstanceScope,
) -> RechercheAdresses:
    """Typed search response: served order, results and BAN hosting.

    With a ``departement`` filter, its hosting is the route's (computed for
    that departement); without, the territory typed is looked up first. No
    BAN row is read for a territory out of coverage.
    """
    ordre = ordre_des_types(q)
    ban = hosting.hostings["ban"]
    if departement is not None:
        if ban.etat != "heberge":
            noms = await noms_departements(session, [departement])
            territoire = TerritoireRecherche(
                departement=DepartementRef(code=departement, nom=noms.get(departement))
            )
            return RechercheAdresses(
                ordre_types=ordre,
                resultats=[],
                hebergement=_hebergement(hosting, territoire=territoire),
            )
        departements = [departement]
    else:
        hors_couverture = await detecter_territoire(session, q, scope)
        if hors_couverture is not None:
            return RechercheAdresses(
                ordre_types=ordre, resultats=[], hebergement={"ban": hors_couverture}
            )
        if ban.etat != "heberge":
            return RechercheAdresses(
                ordre_types=ordre, resultats=[], hebergement=_hebergement(hosting)
            )
        departements = ban.departements
    try:
        resultats = await rechercher_adresses(session, q, departements, limite)
    except RechercheTropLargeError:
        # Too broad for the budget of the national trigram read (R-08): no
        # result and no number match, since nothing was read to the end.
        return RechercheAdresses(
            ordre_types=ordre,
            resultats=[],
            hebergement=_hebergement(hosting),
            etat="trop_large",
        )
    return RechercheAdresses(
        ordre_types=ordre,
        resultats=resultats,
        hebergement=_hebergement(hosting),
        correspondance_numero=qualifier_correspondance_numero(q, resultats),
    )

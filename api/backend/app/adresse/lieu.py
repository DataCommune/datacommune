"""Themes of the place on the address page (04-06, D-104, D-115, LIEU-01/02).

Under the sales, the page folds the place into themes, in the fixed order of
D-115: « Bâtiment et parcelle », « Risques », « Urbanisme », « Réseaux et
transports »; the commune of the address (reference) is read by the page
from ``/v1/commune`` and is never served here (D-103, CMP-01).

Each theme is composed from the readers that already exist -- the parcel
relation of the identity (:func:`app.adresse.relations.resoudre_parcelle`),
:func:`app.adresse.relations.batiments_sur_parcelle`, the dataset query
modules -- each read in its own guarded block (:func:`lire_bloc`): an outage
of one source turns its value or its theme into ``temporary_error`` and
never another theme (N-01, N-14). A source the instance does not host is
``out_of_coverage`` without any read (N-07).

Building and parcel (LIEU-02, R06-09/2-3): the count of the buildings of the
parcel that contains the BAN point (every building, no LIMIT; the total and
the count without declared annexes served apart) is distinct from the
building that contains the point, whose characteristics are served only
when exactly one building geometry covers the point. A point outside every
parcel or on several parcels attributes no building and no condominium. No
dwelling and no lot are ever derived.

Periods (C14, SEM-02): a value read from an imported table carries the
millésime of that import, else the day of its last success
(:func:`app.adresse.blocs.periode_de_source`); nothing known, no period.

Risks (CMP-01, N-03): what is measured at the point and what is read for the
commune are two groups that never merge. Planning and communal risks are
upstream sources, read by :func:`lire_amont_lieu` outside any transaction
(API CR-01), with a deadline.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse import risques
from app.adresse.blocs import (
    MOTIF_HORS_PERIMETRE,
    faits_de_provenance,
    lire_bloc,
    periode_de_source,
    rayon_deborde,
    source_valeur,
)
from app.adresse.relations import PointBan, batiments_sur_parcelle
from app.adresse.schemas import (
    ElementLieu,
    GroupeTheme,
    IdentiteAdresse,
    RelationParcelle,
    ThemeLieu,
    ValeurLieu,
    ValeurTheme,
)
from app.batiments.queries import batiments_par_point
from app.cadastre.queries import parcelle_par_reference
from app.common.exceptions import UpstreamUnavailableError
from app.connectivite.queries import eligibilite_adresse_exacte
from app.logements.queries import copropriete_par_parcelle
from app.resolve import telemetry
from app.scope.dependencies import RouteHosting
from app.transport.queries import transports_a_proximite
from app.urbanisme.client import query_urbanisme

logger = logging.getLogger(__name__)

TITRES = {
    "batiment_parcelle": "Bâtiment et parcelle",
    "risques": "Risques",
    "urbanisme": "Urbanisme",
    "reseaux_transports": "Réseaux et transports",
}

FIELD_BATIMENTS = "adresse.batiments.nombre_parcelle"
FIELD_BATIMENTS_HORS_ANNEXE = "adresse.batiments.nombre_parcelle_hors_annexe"
FIELD_HAUTEUR = "adresse.batiment.hauteur"
FIELD_ETAGES = "adresse.batiment.etages"
FIELD_RNB = "adresse.batiment.rnb_id"
FIELD_PARCELLE = "adresse.parcelle.identifiant"
FIELD_CONTENANCE = "adresse.parcelle.contenance"
FIELD_COPROPRIETE = "adresse.copropriete.immatriculation"
FIELD_ZONE_PLU = "adresse.urbanisme.zone_plu"
FIELD_PRESCRIPTIONS = "adresse.urbanisme.prescriptions"
FIELD_FIBRE = "adresse.connectivite.eligibilite_fibre"
FIELD_DEBIT = "adresse.connectivite.debit"
FIELD_ARRETS = "adresse.transports.arret_proche"

# Titles of the two groups of « Risques » (UI-SPEC point 6): never merged.
TITRE_AU_POINT = "Au point de l'adresse"
TITRE_COMMUNE = "Sur la commune (référence)"

# The nearest stops and stations: at most three, within a kilometre of the
# BAN point, at the geodesic distance (never a travel time). One more is read:
# a fourth one says the list of three is partial (final review API IN-01).
RAYON_ARRETS_M = 1000
ARRETS_MAX = 3

# Reasons of a height without value (D-15 API CR-01): no building geometry
# covers the BAN point, or more than one does (none is chosen).
MOTIF_AUCUN_BATIMENT = "aucun_batiment_au_point"
MOTIF_BATIMENT_NON_UNIQUE = "batiment_du_point_non_unique"

# Reason of an RNB identifier without value (C52, B3): the RNB links more than
# one building to the BAN key, and none is chosen.
MOTIF_RNB_NON_UNIQUE = "batiment_rnb_non_unique"

# A registration bound of ``copropriete_par_parcelle``: one more is read, and
# only a list longer than the bound is partial (final review API IN-04).
COPROPRIETES_MAX = 5

# Theme-wide gaps of the parcel relation (N-02): never a zero, never the
# nearest parcel.
_MOTIFS_RELATION = {
    "point_hors_parcelle": "point_hors_parcelle",
    "ambigue": "parcelles_multiples",
    "indeterminee": "parcelle_indeterminee",
}

# RNB buildings that the RNB itself links to this BAN key (its departmental
# export, ``rnb_liens``): an identifier with its source, never a proof of
# the building that contains the point. Two rows are enough to know the link
# is not one; neither of them is ever served then (C52, B3).
_RNB_DE_LA_CLE = text("""
    SELECT DISTINCT id_rnb
    FROM rnb_liens
    WHERE cle_ban = :ban_id
    ORDER BY id_rnb
    LIMIT 2
""")


@dataclass(frozen=True)
class Contexte:
    """What every theme reader needs: the served identity and its point."""

    identite: IdentiteAdresse
    point: PointBan | None
    hosting: RouteHosting


def _theme(cle: str, **etat) -> ThemeLieu:
    return ThemeLieu(cle=cle, titre=TITRES[cle], **etat)


def _champ(field_id: str, source: str, rattachement: str, **etat) -> ValeurTheme:
    return ValeurTheme(
        field_id=field_id,
        source=source_valeur(source),
        rattachement=rattachement,
        **etat,
    )


async def _periode(session: AsyncSession, dataset: str) -> str | None:
    """Period of the values read from the table of *dataset* (C14, SEM-02).

    Read after a successful read only: an outage or a source not hosted
    carries no period. Unknown facts give no period, never an invented one.
    """
    return periode_de_source(await faits_de_provenance(session, dataset))


def _absence_du_theme(
    cle: str, relation: RelationParcelle | None, motif_sans_point: str
) -> ThemeLieu | None:
    """The theme state of a parcel relation that is not ``unique``, if any."""
    if relation is None:
        return _theme(cle, etat="not_available", motif=motif_sans_point)
    if relation.etat == "hors_couverture":
        return _theme(cle, etat="out_of_coverage", motif=relation.motif)
    if relation.etat == "indisponible":
        return _theme(cle, etat="temporary_error", motif="lecture_indisponible")
    if relation.etat != "unique" or relation.id_parcelle is None:
        motif = _MOTIFS_RELATION.get(relation.etat, relation.motif)
        return _theme(cle, etat="not_available", motif=motif)
    return None


async def _champs_batiments(
    session: AsyncSession, contexte: Contexte, id_parcelle: str
) -> list[ValeurTheme]:
    """Buildings of the parcel (count) then the building of the point."""
    commun = {"source": "batiments", "rattachement": "parcelle_du_point"}
    hebergement = contexte.hosting.hostings["batiments"]
    if hebergement.etat != "heberge":
        return [
            _champ(
                FIELD_BATIMENTS,
                **commun,
                etat="out_of_coverage",
                motif=hebergement.motif,
                unite="count",
            ),
        ]
    compte = await lire_bloc(
        session, ["batiments"], lambda: batiments_sur_parcelle(session, id_parcelle)
    )
    if compte.en_panne:
        return [
            _champ(
                FIELD_BATIMENTS,
                **commun,
                etat="temporary_error",
                motif=compte.panne,
                unite="count",
            ),
        ]
    periode = await _periode(session, "batiments")
    if compte.valeur is None:
        # The parcel is not one row of the cadastre: unknown, never zero.
        return [
            _champ(
                FIELD_BATIMENTS,
                **commun,
                etat="not_reported",
                unite="count",
                periode=periode,
            ),
        ]
    champs = [
        _champ(
            FIELD_BATIMENTS,
            **commun,
            etat="value",
            valeur=compte.valeur.total,
            unite="count",
            periode=periode,
        ),
        _champ(
            FIELD_BATIMENTS_HORS_ANNEXE,
            **commun,
            etat="value",
            valeur=compte.valeur.hors_annexe,
            unite="count",
            periode=periode,
        ),
    ]
    return champs + await _champs_batiment_du_point(session, contexte, periode)


async def _champs_batiment_du_point(
    session: AsyncSession, contexte: Contexte, periode: str | None
) -> list[ValeurTheme]:
    """Height and floors of the building containing the BAN point, if unique.

    ``batiments_par_point`` within 0 m returns the buildings whose geometry
    touches the point; two rows are enough to know it is not one. Distinct
    from the count of the parcel: a parcel of six buildings may have one
    building at the point, a parcel of one building may have none.
    """
    point = contexte.point
    if point is None:
        return []
    commun = {"source": "batiments", "rattachement": "batiment_du_point"}
    lu = await lire_bloc(
        session,
        ["batiments"],
        lambda: batiments_par_point(session, point.lat, point.lon, 0.0, limit=2),
    )
    if lu.en_panne:
        return [
            _champ(
                FIELD_HAUTEUR,
                **commun,
                etat="temporary_error",
                motif=lu.panne,
                unite="metre",
            ),
        ]
    if len(lu.valeur) != 1:
        # The height stays typed by its cause (D-15 API CR-01): no building at
        # the point is not « several buildings », and neither is a value.
        motif = MOTIF_BATIMENT_NON_UNIQUE if lu.valeur else MOTIF_AUCUN_BATIMENT
        return [
            _champ(
                FIELD_HAUTEUR,
                **commun,
                etat="not_available",
                motif=motif,
                unite="metre",
                periode=periode,
            )
        ]
    batiment = lu.valeur[0]
    hauteur = batiment.get("hauteur")
    etages = batiment.get("nombre_d_etages")
    return [
        _champ(
            FIELD_HAUTEUR,
            **commun,
            unite="metre",
            **(
                {"etat": "value", "valeur": float(hauteur)}
                if hauteur is not None
                else {"etat": "not_reported"}
            ),
            periode=periode,
        ),
        _champ(
            FIELD_ETAGES,
            **commun,
            unite="count",
            **(
                {"etat": "value", "valeur": int(etages)}
                if etages is not None
                else {"etat": "not_reported"}
            ),
            periode=periode,
        ),
    ]


async def _champs_rnb(session: AsyncSession, contexte: Contexte) -> list[ValeurTheme]:
    """The RNB field of the address, always served with its state (C52, B3),
    whatever the parcel relation of the point (C55, fix 1).

    The identifier is a value only when the RNB links exactly one building to
    this BAN key. No link is « not reported » (never a zero), several links
    are « not available » with their reason and none is chosen, a source not
    hosted is out of coverage without any read, an outage stays visible.
    """
    commun = {"source": "rnb", "rattachement": "batiment_rnb_de_l_adresse"}
    hebergement = contexte.hosting.hostings["rnb"]
    if hebergement.etat != "heberge":
        return [
            _champ(
                FIELD_RNB,
                **commun,
                etat="out_of_coverage",
                motif=hebergement.motif,
                unite="none",
            )
        ]
    ban_id = contexte.identite.ban_id

    async def lecteur():
        result = await session.execute(_RNB_DE_LA_CLE, {"ban_id": ban_id})
        return [row[0] for row in result]

    lu = await lire_bloc(session, ["rnb"], lecteur)
    if lu.en_panne:
        return [
            _champ(
                FIELD_RNB,
                **commun,
                etat="temporary_error",
                motif=lu.panne,
                unite="none",
            )
        ]
    # The links are read in the RNB links export: its millésime.
    periode = await _periode(session, "rnb_liens")
    if not lu.valeur:
        return [
            _champ(
                FIELD_RNB, **commun, etat="not_reported", unite="none", periode=periode
            )
        ]
    if len(lu.valeur) > 1:
        return [
            _champ(
                FIELD_RNB,
                **commun,
                etat="not_available",
                motif=MOTIF_RNB_NON_UNIQUE,
                unite="none",
                periode=periode,
            )
        ]
    return [
        _champ(
            FIELD_RNB,
            **commun,
            etat="value",
            valeur=lu.valeur[0],
            unite="none",
            periode=periode,
        )
    ]


async def _champs_parcelle(
    session: AsyncSession, id_parcelle: str
) -> list[ValeurTheme]:
    """Identifier and area of the parcel that contains the point."""
    # The identifier comes from the parcel relation of the identity, read in
    # the same cadastre: both values carry its period.
    periode = await _periode(session, "cadastre")
    commun = {"source": "cadastre", "rattachement": "parcelle_du_point"}
    identifiant = _champ(
        FIELD_PARCELLE,
        **commun,
        etat="value",
        valeur=id_parcelle,
        unite="none",
        periode=periode,
    )
    lu = await lire_bloc(
        session, ["cadastre"], lambda: parcelle_par_reference(session, id_parcelle)
    )
    if lu.en_panne:
        contenance = _champ(
            FIELD_CONTENANCE,
            **commun,
            etat="temporary_error",
            motif=lu.panne,
            unite="m2",
        )
    else:
        # One row of the cadastre, or the area is unknown (never zero).
        surface = lu.valeur[0]["contenance"] if len(lu.valeur) == 1 else None
        contenance = _champ(
            FIELD_CONTENANCE,
            **commun,
            unite="m2",
            **(
                {"etat": "value", "valeur": int(surface)}
                if surface is not None
                else {"etat": "not_reported"}
            ),
            periode=periode,
        )
    return [identifiant, contenance]


async def _champs_copropriete(
    session: AsyncSession, contexte: Contexte, id_parcelle: str
) -> list[ValeurTheme]:
    """Registered condominiums on the parcel of the point (exact parcel join)."""
    commun = {
        "source": "copropriete",
        "rattachement": "parcelle_du_point",
        "unite": "count",
    }
    hebergement = contexte.hosting.hostings["copropriete"]
    if hebergement.etat != "heberge":
        return [
            _champ(
                FIELD_COPROPRIETE,
                **commun,
                etat="out_of_coverage",
                motif=hebergement.motif,
            )
        ]
    lu = await lire_bloc(
        session,
        ["copropriete"],
        lambda: copropriete_par_parcelle(session, id_parcelle, COPROPRIETES_MAX + 1),
    )
    if lu.en_panne:
        return [
            _champ(FIELD_COPROPRIETE, **commun, etat="temporary_error", motif=lu.panne)
        ]
    return [
        _champ(
            FIELD_COPROPRIETE,
            **commun,
            etat="value",
            valeur=min(len(lu.valeur), COPROPRIETES_MAX),
            liste_incomplete=len(lu.valeur) > COPROPRIETES_MAX,
            periode=await _periode(session, "copropriete"),
        )
    ]


async def theme_batiment_parcelle(
    session: AsyncSession, contexte: Contexte
) -> ThemeLieu:
    """« Bâtiment et parcelle »: the parcel of the point, its buildings, the
    building of the point when unique, the RNB identifier, the condominium.

    Without a ``unique`` parcel the theme carries the reason of the relation
    and no building, parcel nor condominium value (N-02). The RNB field is
    still served with its state: the RNB links it to the BAN key, never to
    the parcel (C55, fix 1).
    """
    cle = "batiment_parcelle"
    absence = _absence_du_theme(
        cle, contexte.identite.parcelle, motif_sans_point="parcelle_indeterminee"
    )
    if absence is not None:
        champs = await _champs_rnb(session, contexte)
        return absence.model_copy(
            update={"groupes": [GroupeTheme(cle="lieu", champs=champs)]}
        )
    id_parcelle = contexte.identite.parcelle.id_parcelle
    champs = [
        *await _champs_batiments(session, contexte, id_parcelle),
        *await _champs_rnb(session, contexte),
        *await _champs_parcelle(session, id_parcelle),
        *await _champs_copropriete(session, contexte, id_parcelle),
    ]
    return _theme(
        cle,
        etat="value",
        groupes=[GroupeTheme(cle="lieu", champs=champs)],
        liste_incomplete=any(champ.liste_incomplete for champ in champs),
    )


# --- Upstream reads of the themes (API CR-01): outside any transaction ---------

# Deadline of an upstream read of the page, the one of landmark ③: a slow
# source only turns its own theme into an outage, well under the SSR timeout.
AMONT_DEADLINE_S = 5.0


@dataclass(frozen=True)
class PanneAmont:
    """An upstream source that did not answer (bounded cause, no message)."""

    motif: str = "amont_indisponible"


@dataclass(frozen=True)
class AmontLieu:
    """Upstream answers of the themes: a value, an outage, or ``None`` when
    not read (source not hosted, no single point)."""

    risques_commune: risques.RisquesCommune | PanneAmont | None = None
    urbanisme: dict | PanneAmont | None = None


def _panne_amont(dataset: str, exc: BaseException) -> PanneAmont:
    # Bounded code only (PRIV-01): never the point nor the INSEE code.
    telemetry.emit_resolve_event(
        logger,
        "address_block_unavailable",
        level=logging.WARNING,
        dataset=dataset,
        outcome="amont_indisponible",
        error_code=telemetry.error_code_for(exc),
    )
    return PanneAmont()


async def _risques_commune(code_insee: str, hosting: RouteHosting):
    if hosting.hostings["georisques"].etat != "heberge":
        return None
    try:
        return await risques.lire_risques_commune(code_insee)
    except (UpstreamUnavailableError, TimeoutError, httpx.HTTPError) as exc:
        return _panne_amont("georisques", exc)


async def _urbanisme(point: PointBan | None, hosting: RouteHosting):
    if hosting.hostings["urbanisme"].etat != "heberge" or point is None:
        return None
    try:
        async with asyncio.timeout(AMONT_DEADLINE_S):
            return await query_urbanisme(point.lat, point.lon)
    except (UpstreamUnavailableError, TimeoutError, httpx.HTTPError) as exc:
        return _panne_amont("urbanisme", exc)


async def lire_amont_lieu(
    point: PointBan | None, code_insee: str, hosting: RouteHosting
) -> AmontLieu:
    """The communal risks and the planning at the point, read in parallel.

    Called by the page between two transactions (API CR-01), concurrently
    with landmark ③: no publication lock and no pool connection is held
    while an upstream answers. Nothing is called for a source the instance
    does not host, nor at a point that is not established.
    """
    commune, urbanisme = await asyncio.gather(
        _risques_commune(code_insee, hosting), _urbanisme(point, hosting)
    )
    return AmontLieu(risques_commune=commune, urbanisme=urbanisme)


# --- « Risques » (D-103, CMP-01, N-03) ----------------------------------------


def _champ_commune(cle: str, lu: risques.RisquesCommune, as_of: date) -> ValeurTheme:
    """One communal phenomenon, attached to the commune (never to the point).

    GASPAR: the risks listed for the commune; none listed is not served as a
    zero (« 0 risque » is never written). Radon: the potential class as
    served. ICPE: the installations recorded in the commune, counted on the
    total announced by the source.
    """
    phen = risques.PHENOMENES[cle]
    commun = {
        "source": "georisques",
        "rattachement": "commune_reference",
        "periode": as_of.isoformat(),
    }
    lignes = lu.lignes[cle]
    couverture = lu.couverture[cle]
    if lignes is None:
        return _champ(
            phen.field_id, **commun, etat="temporary_error", motif="amont_indisponible"
        )
    incomplete = couverture.complet is not True
    if cle == "gaspar":
        libelles = sorted(
            {
                detail["libelle_risque_long"].strip()
                for ligne in lignes
                for detail in (ligne.get("risques_detail") or [])
                if isinstance(detail, dict)
                and isinstance(detail.get("libelle_risque_long"), str)
                and detail["libelle_risque_long"].strip()
            }
        )
        if not libelles:
            return _champ(
                phen.field_id,
                **commun,
                etat="not_reported",
                unite="count",
                liste_incomplete=incomplete,
            )
        return _champ(
            phen.field_id,
            **commun,
            etat="value",
            valeur=len(libelles),
            unite="count",
            elements=[ElementLieu(libelle=libelle) for libelle in libelles],
            liste_incomplete=incomplete,
        )
    if cle == "radon":
        classes = {
            str(ligne["classe_potentiel"]).strip()
            for ligne in lignes
            if ligne.get("classe_potentiel") not in (None, "")
        }
        if len(classes) != 1:
            return _champ(phen.field_id, **commun, etat="not_reported", unite="none")
        return _champ(
            phen.field_id, **commun, etat="value", valeur=classes.pop(), unite="none"
        )
    total = lu.total[cle]
    if incomplete and total is None:
        return _champ(
            phen.field_id,
            **commun,
            etat="not_reported",
            unite="count",
            liste_incomplete=True,
        )
    return _champ(
        phen.field_id,
        **commun,
        etat="value",
        valeur=total if total is not None else len(lignes),
        unite="count",
    )


def theme_risques(
    contexte: Contexte, argiles: ValeurLieu, amont: AmontLieu, as_of: date
) -> ThemeLieu:
    """« Risques »: what is measured at the point, then the commune (reference).

    Two groups that never mix (CMP-01, decision of Ianis of 2026-10-02): « Au
    point de l'adresse » carries the phenomena measured at the point (clay,
    the very value of landmark ③, never read twice); « Sur la commune
    (référence) » the phenomena read by INSEE code. No merged count, no « 0
    risque »; a sub-source down is said in its group.
    """
    cle = "risques"
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return _theme(cle, etat="out_of_coverage", motif=hebergement.motif)
    au_point = [
        ValeurTheme(**argiles.model_dump())
        for phen in risques.phenomenes_au_point()
        if risques.PHENOMENES[phen].field_id == argiles.field_id
    ]
    lu = amont.risques_commune
    if isinstance(lu, risques.RisquesCommune):
        commune = [
            _champ_commune(phen, lu, as_of) for phen in risques.phenomenes_commune()
        ]
    else:
        commune = [
            _champ(
                risques.PHENOMENES[phen].field_id,
                "georisques",
                "commune_reference",
                etat="temporary_error",
                motif="amont_indisponible",
            )
            for phen in risques.phenomenes_commune()
        ]
    tous = au_point + commune
    if all(champ.etat == "temporary_error" for champ in tous):
        return _theme(cle, etat="temporary_error", motif="amont_indisponible")
    return _theme(
        cle,
        etat="value",
        groupes=[
            GroupeTheme(cle="au_point", titre=TITRE_AU_POINT, champs=au_point),
            GroupeTheme(cle="commune", titre=TITRE_COMMUNE, champs=commune),
        ],
        liste_incomplete=any(champ.liste_incomplete for champ in tous),
    )


# --- « Urbanisme » --------------------------------------------------------------


def _elements_gpu(features: list, *cles: str) -> list[ElementLieu]:
    elements = []
    for feature in features:
        proprietes = (
            feature.get("properties") if isinstance(feature, dict) else None
        ) or {}
        morceaux = [
            proprietes[cle].strip()
            for cle in cles
            if isinstance(proprietes.get(cle), str) and proprietes[cle].strip()
        ]
        if morceaux:
            elements.append(ElementLieu(libelle=" · ".join(dict.fromkeys(morceaux))))
    return elements


def _lecture_gpu(lu: dict, nom: str) -> tuple[list | None, bool]:
    """Rows of one GPU endpoint (None when down) and whether the read is partial."""
    couverture = (lu.get("sources") or {}).get(nom) or {}
    rows = lu.get(nom)
    if couverture.get("statut") == "indisponible" or not isinstance(rows, list):
        return None, False
    return rows, couverture.get("complet") is not True


def theme_urbanisme(contexte: Contexte, amont: AmontLieu, as_of: date) -> ThemeLieu:
    """« Urbanisme »: the zone of the planning document at the point and the
    prescriptions there (API Carto GPU).

    No zone published at the point (a commune without a published document)
    is a typed gap, never a zero; a partial read of the GPU is served as
    « Liste incomplète »; an outage of one endpoint stays in its value.
    """
    cle = "urbanisme"
    hebergement = contexte.hosting.hostings["urbanisme"]
    if hebergement.etat != "heberge":
        return _theme(cle, etat="out_of_coverage", motif=hebergement.motif)
    if contexte.point is None:
        return _theme(cle, etat="not_available", motif="position_indeterminee")
    lu = amont.urbanisme
    if not isinstance(lu, dict):
        return _theme(cle, etat="temporary_error", motif="amont_indisponible")
    commun = {
        "source": "urbanisme",
        "rattachement": "au_point",
        "periode": as_of.isoformat(),
        "unite": "count",
    }
    zones, zones_partielles = _lecture_gpu(lu, "zones_plu")
    prescriptions, prescriptions_partielles = _lecture_gpu(lu, "prescriptions")
    # Nothing published at the point: a gap of the source, never « 0 zone ».
    sans_document = zones is not None and not zones and not zones_partielles
    champs: list[ValeurTheme] = []
    for field_id, rows, partielle, cles in (
        (FIELD_ZONE_PLU, zones, zones_partielles, ("libelle", "libelong")),
        (FIELD_PRESCRIPTIONS, prescriptions, prescriptions_partielles, ("libelle",)),
    ):
        if rows is None:
            champs.append(
                _champ(
                    field_id,
                    **commun,
                    etat="temporary_error",
                    motif="amont_indisponible",
                )
            )
        elif sans_document:
            champs.append(
                _champ(
                    field_id,
                    **commun,
                    etat="not_available",
                    motif="aucun_document_publie",
                )
            )
        else:
            elements = _elements_gpu(rows, *cles)
            champs.append(
                _champ(
                    field_id,
                    **commun,
                    etat="value",
                    valeur=len(elements),
                    elements=elements,
                    liste_incomplete=partielle,
                )
            )
    if all(champ.etat == "temporary_error" for champ in champs):
        return _theme(cle, etat="temporary_error", motif="amont_indisponible")
    return _theme(
        cle,
        etat="value",
        groupes=[GroupeTheme(cle="au_point", champs=champs)],
        liste_incomplete=any(champ.liste_incomplete for champ in champs),
    )


# --- « Réseaux et transports » -----------------------------------------------------


async def _champs_connectivite(
    session: AsyncSession, contexte: Contexte
) -> list[ValeurTheme]:
    """Fibre eligibility published for exactly this address (R06-09/4)."""
    commun = {
        "source": "connectivite",
        "rattachement": "eligibilite_adresse",
        "unite": "none",
    }
    hebergement = contexte.hosting.hostings["connectivite"]
    if hebergement.etat != "heberge":
        return [
            _champ(
                FIELD_FIBRE, **commun, etat="out_of_coverage", motif=hebergement.motif
            )
        ]
    identite = contexte.identite
    if identite.etat != "etablie" or not identite.numero or not identite.nom_voie:
        return [
            _champ(
                FIELD_FIBRE,
                **commun,
                etat="not_available",
                motif="adresse_non_verifiable",
            )
        ]

    async def lecteur():
        eligibilite = await eligibilite_adresse_exacte(
            session,
            identite.commune.code_insee,
            numero=identite.numero,
            rep=identite.rep,
            nom_voie=identite.nom_voie,
        )
        return eligibilite, await faits_de_provenance(session, "connectivite")

    lu = await lire_bloc(session, ["connectivite"], lecteur)
    if lu.en_panne:
        return [_champ(FIELD_FIBRE, **commun, etat="temporary_error", motif=lu.panne)]
    eligibilite, faits = lu.valeur
    periode = periode_de_source(faits)
    if eligibilite.etat == "aucune":
        return [_champ(FIELD_FIBRE, **commun, etat="not_reported", periode=periode)]
    if eligibilite.etat == "indeterminee":
        return [
            _champ(
                FIELD_FIBRE,
                **commun,
                etat="not_available",
                motif="adresse_non_verifiable",
                periode=periode,
            )
        ]
    fibre = [ligne for ligne in eligibilite.lignes if ligne["technologie"] == "FO"]
    champs = [
        _champ(FIELD_FIBRE, **commun, etat="value", valeur=bool(fibre), periode=periode)
    ]
    debits = {str(ligne["debit_max"]) for ligne in fibre if ligne["debit_max"]}
    if len(debits) == 1:
        champs.append(
            _champ(
                FIELD_DEBIT,
                **commun,
                etat="value",
                valeur=debits.pop(),
                periode=periode,
            )
        )
    return champs


async def _champs_transports(
    session: AsyncSession, contexte: Contexte
) -> list[ValeurTheme]:
    """The nearest stops and stations, at a geodesic distance from the point."""
    commun: dict[str, Any] = {
        "source": "transports",
        "rattachement": "rayon_du_point",
        "unite": "count",
        "rayon_m": RAYON_ARRETS_M,
    }
    sources = [
        source
        for source in ("transports", "gares")
        if contexte.hosting.hostings[source].etat == "heberge"
    ]
    if not sources:
        motif = contexte.hosting.hostings["transports"].motif
        return [_champ(FIELD_ARRETS, **commun, etat="out_of_coverage", motif=motif)]
    point = contexte.point
    if point is None:
        return [
            _champ(
                FIELD_ARRETS,
                **commun,
                etat="not_available",
                motif="position_indeterminee",
            )
        ]
    # A departement is covered when every source read is hosted there (C16).
    heberges = sorted(
        set.intersection(
            *(set(contexte.hosting.hostings[source].departements) for source in sources)
        )
    )

    async def lire() -> tuple[list[dict], bool]:
        lignes = await transports_a_proximite(
            session,
            point.lat,
            point.lon,
            RAYON_ARRETS_M,
            ARRETS_MAX + 1,
            sources=sources,
        )
        deborde = await rayon_deborde(
            session, point.lon, point.lat, RAYON_ARRETS_M, heberges
        )
        return lignes, deborde

    lu = await lire_bloc(session, sources, lire)
    if lu.en_panne or lu.valeur is None:
        return [_champ(FIELD_ARRETS, **commun, etat="temporary_error", motif=lu.panne)]
    lignes, deborde = lu.valeur
    elements = [
        ElementLieu(
            libelle=ligne["stop_name"].strip(),
            distance_m=round(ligne["distance_m"]),
            nature=ligne["type_transport"],
        )
        for ligne in lignes[:ARRETS_MAX]
        if isinstance(ligne.get("stop_name"), str) and ligne["stop_name"].strip()
    ]
    if deborde and not elements:
        # No stop on the hosted side: never « aucun arrêt » (C16).
        return [
            _champ(
                FIELD_ARRETS,
                **commun,
                etat="out_of_coverage",
                motif=MOTIF_HORS_PERIMETRE,
                liste_incomplete=True,
            )
        ]
    # One import loads the stops and the SNCF stations (``etl_transport``).
    return [
        _champ(
            FIELD_ARRETS,
            **commun,
            etat="value",
            valeur=len(elements),
            elements=elements,
            liste_incomplete=deborde or len(lignes) > ARRETS_MAX,
            # A stop beyond the hosted departements may be nearer (C16).
            motif=MOTIF_HORS_PERIMETRE if deborde else None,
            periode=await _periode(session, "transports"),
        )
    ]


async def theme_reseaux_transports(
    session: AsyncSession, contexte: Contexte
) -> ThemeLieu:
    """« Réseaux et transports »: eligibility published for this address, then
    the nearest stops « à {d} m » (as the crow flies, never a travel time).

    The broadband generation is a journaled source: its block takes its
    publication guard, and a refused publication turns this theme only into
    an outage (N-01, T-04-06-05).
    """
    champs = [
        *await _champs_connectivite(session, contexte),
        *await _champs_transports(session, contexte),
    ]
    cle = "reseaux_transports"
    if all(champ.etat == "temporary_error" for champ in champs):
        return _theme(cle, etat="temporary_error", motif=champs[0].motif)
    return _theme(
        cle,
        etat="value",
        groupes=[GroupeTheme(cle="lieu", champs=champs)],
        liste_incomplete=any(champ.liste_incomplete for champ in champs),
    )


async def lire_themes_lieu(
    session: AsyncSession,
    identite: IdentiteAdresse,
    point: PointBan | None,
    hosting: RouteHosting,
    argiles: ValeurLieu,
    amont: AmontLieu,
    as_of: date,
) -> list[ThemeLieu]:
    """The themes of the place in the fixed order of D-115.

    Called inside the database part of the page, after the upstream reads
    (:func:`lire_amont_lieu`, API CR-01); each database reader takes its own
    savepoint.
    """
    contexte = Contexte(identite=identite, point=point, hosting=hosting)
    return [
        await theme_batiment_parcelle(session, contexte),
        theme_risques(contexte, argiles, amont, as_of),
        theme_urbanisme(contexte, amont, as_of),
        await theme_reseaux_transports(session, contexte),
    ]

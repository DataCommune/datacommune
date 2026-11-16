"""DVF sales of an address page (04-05, D-114, D-115, LIEU-02).

Two readers, both attached to no dwelling:

- :func:`lire_ventes_parcelle`: the distinct mutations of the parcel that
  contains the BAN point (:func:`app.adresse.relations.resoudre_parcelle`,
  never the nearest parcel), over the same population as landmark ② (the
  fragment ``_MUTATIONS_DE_LA_PARCELLE_SQL`` of 04-03), 10 per page, latest
  first then by mutation. Its page is read by :func:`lire_page_parcelle`,
  which the map of the parcels (``app.carte.ventes``, 04.10-05) awaits too
  with its own page size: one reader, one price rule;
- :func:`lire_ventes_ressemblantes`: the similar sales of D-114, around the BAN
  point (250 m, 500 m, 1 km) or in the commune of the address, of one type
  (a single local of that kind), within a surface tolerance and a number of
  calendar years counted back from the last imported DVF date; filtered,
  ordered (distance, then date descending, then mutation) and counted here,
  never in the browser. The criteria are served as applied.

Each reads DVF in its own guarded block (:func:`app.adresse.blocs.lire_bloc`,
N-01): a refused publication or a failed read is an outage of the section,
never an empty list. Every page carries the generation of the two DVF
relations it read (``vg``); a stale token is refused (409).

Minimisation (DVF reuse restrictions, R*112 A-3 LPF): the price of a recent
isolated sale never leaves the API. Recent = within 24 months (inclusive) of
the last imported DVF date, never of the system clock; isolated = one of its
parcels carries this mutation only among the positioned mutations of the
imported population, computed for each candidate by a targeted query, without
any departmental LIMIT. When the isolation cannot be established, no exact
price is published either.

A mutation none of whose lines is geocoded (``position`` NULL, phase 04.4,
D-03) is in none of these lists: it serves the commune statistics only.
"""

import hashlib
import math
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import (
    MOTIF_HORS_PERIMETRE,
    faits_de_provenance,
    lire_bloc,
    periode_de_source,
    rayon_deborde,
    source_valeur,
)
from app.adresse.documents import (
    GenerationChangeeError,
    PageHorsLimitesError,
    departement_de_cle,
    point_unique,
)
from app.adresse.relations import resoudre_parcelle
from app.adresse.schemas import (
    CarteVente,
    CompositionVente,
    CriteresAppliques,
    MinimisationVente,
    PeriodeAppliquee,
    TypeComparable,
    ValeurLieu,
    VentesAdresse,
    VentesParcelle,
    VentesRessemblantes,
)
from app.datasets.provenance import ProvenanceFacts
from app.dvf.queries import (
    _MUTATIONS_DE_LA_PARCELLE_SQL,
    _PRIX_M2_SQL,
    POSITION_CONNUE_SQL,
    dvf_compte_par_id_parcelle,
)
from app.etl_dvf.config import MARQUEUR_ADRESSE_SUFFIXE
from app.scope.dependencies import RouteHosting

PAR_PAGE = 10
# Recent sale: within 24 months of the last imported DVF date (inclusive).
FENETRE_RECENCE_MOIS = 24


class VentesIndisponiblesError(Exception):
    """Refused DVF publication or failed read of the sales."""


def nombre_pages(total: int, par_page: int = PAR_PAGE) -> int:
    """Pages of *par_page* (ten here); a list without sale has one page."""
    return max(1, math.ceil(total / par_page))


def jeton_generation_dvf(mutations: int | None, liens: int | None) -> str:
    """Opaque token of the two DVF relations read by a page."""
    raw = f"dvf:{mutations}:{liens}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def seuil_recence(fin_dvf: date) -> date:
    """First day of the recency window: 24 months before *fin_dvf*.

    Anchored on the DVF period only (never ``today``); a 29 February goes
    back to the 28th, as PostgreSQL's ``interval '24 months'``.
    """
    annee = fin_dvf.year - FENETRE_RECENCE_MOIS // 12
    try:
        return fin_dvf.replace(year=annee)
    except ValueError:
        return fin_dvf.replace(year=annee, day=28)


def minimiser(
    jour: date, isolee: bool | None, seuil: date | None
) -> MinimisationVente | None:
    """Reason to withhold the price of a sale of *jour*, or None.

    *isolee* is None when the isolation could not be established; *seuil*
    is None when the last DVF date is unknown (recency undetermined).
    """
    if seuil is not None and jour < seuil:
        return None
    if isolee is False:
        return None
    if isolee is True and seuil is not None:
        return MinimisationVente(motif="vente_isolee_recente")
    return MinimisationVente(motif="isolement_indetermine")


def _entier(value) -> int | None:
    return None if value is None else int(value)


def carte_vente(
    ligne: Mapping, minimisation: MinimisationVente | None, distance_m=None
) -> CarteVente:
    """One served card; a withheld price leaves no amount behind."""
    affiche = minimisation is None
    valeur = ligne["valeur_fonciere"]
    return CarteVente(
        date=ligne["date_mutation"],
        nature=ligne["nature_mutation"],
        composition=CompositionVente(
            maisons=ligne["nb_locaux_maison"] or 0,
            appartements=ligne["nb_locaux_appartement"] or 0,
            dependances=ligne["nb_locaux_dependance"] or 0,
            locaux_activite=ligne["nb_locaux_commercial"] or 0,
        ),
        surface_batie=_entier(ligne["surface_bati_totale"]),
        prix=float(valeur) if affiche and valeur is not None else None,
        prix_m2=_entier(ligne["prix_m2"]) if affiche else None,
        prix_affiche=affiche,
        minimisation=minimisation,
        distance_m=None if distance_m is None else round(distance_m),
        hors_stats_vefa=bool(ligne.get("hors_stats_vefa", False)),
    )


# Columns of a card. One row per mutation: ``DISTINCT ON`` folds a
# duplicated DVF row of the same mutation. ``hors_stats_vefa`` marks a VEFA
# (plan 04.1-15, D-12): a sale listed, never counted in a statistic.
_COLONNES_CARTE = f"""
        mg.id_mutation, mg.date_mutation, mg.nature_mutation,
        mg.valeur_fonciere, mg.nb_locaux_maison, mg.nb_locaux_appartement,
        mg.nb_locaux_dependance, mg.nb_locaux_commercial,
        mg.surface_bati_totale, {_PRIX_M2_SQL} AS prix_m2, mg.hors_stats_vefa
"""

_PAGE_PARCELLE_SQL = text(f"""
    WITH mutations AS (
        SELECT DISTINCT ON (mg.id_mutation) {_COLONNES_CARTE}
        {_MUTATIONS_DE_LA_PARCELLE_SQL}
        ORDER BY mg.id_mutation, mg.id
    )
    SELECT * FROM mutations
    ORDER BY date_mutation DESC, id_mutation
    LIMIT :limit OFFSET :offset
""")

# Identities of both DVF relations, read after the publication guard.
_GENERATION_SQL = text("""
    SELECT to_regclass('public.mutations_grain')::oid::bigint AS mutations,
           to_regclass('public.mutation_parcelles')::oid::bigint AS liens
""")

# Last imported DVF date: the anchor of the recency window.
_FIN_DVF_SQL = text("SELECT max(date_mutation) AS fin FROM mutations_grain")

# Isolation of each candidate (R02-05/3): isolated when at least one of its
# parcels carries this mutation only, counting distinct mutations of the
# whole imported population (no departmental LIMIT, no line count). A
# mutation without any parcel link is absent from the result: undetermined.
# Only the positioned mutations are counted (D-03): every served sale is
# positioned, so a mutation of unknown position can only leave a parcel with
# fewer mutations, i.e. make a sale isolated, never « not isolated » -- the
# minimisation can only get stricter, and no price withheld before phase 04.4
# is published because of them.
_ISOLEMENT_SQL = text(f"""
    SELECT c.id_mutation, bool_or(n.nombre = 1) AS isolee
    FROM mutation_parcelles c
    CROSS JOIN LATERAL (
        SELECT count(DISTINCT o.id_mutation) AS nombre
        FROM mutation_parcelles o
        WHERE o.id_parcelle = c.id_parcelle
          AND EXISTS (
              SELECT 1 FROM mutations_grain mg
              WHERE mg.id_mutation = o.id_mutation
                AND mg.code_departement = o.code_departement
                AND {POSITION_CONNUE_SQL}
          )
    ) AS n
    WHERE c.id_mutation = ANY(CAST(:ids AS text[]))
    GROUP BY c.id_mutation
""")

_POINTS_DE_LA_CLE = text("""
    SELECT ST_X(position) AS lon, ST_Y(position) AS lat
    FROM public.adresses
    WHERE ban_id = :ban_id AND code_departement = :department
""")


async def _generation(session: AsyncSession) -> str:
    row = (await session.execute(_GENERATION_SQL)).mappings().one()
    return jeton_generation_dvf(row["mutations"], row["liens"])


async def _isolements(
    session: AsyncSession, ids: Sequence[str]
) -> dict[str, bool | None]:
    """Isolation of each mutation; None for all of them when it cannot be read.

    Read in its own savepoint: an outage of the predicate withholds the
    recent prices (no exact price), it never breaks the list.
    """
    if not ids:
        return {}
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(_ISOLEMENT_SQL, {"ids": list(ids)})
            ).mappings()
            lus = {row["id_mutation"]: bool(row["isolee"]) for row in rows}
    except SQLAlchemyError:
        return dict.fromkeys(ids)
    return {id_mutation: lus.get(id_mutation) for id_mutation in ids}


@dataclass(frozen=True)
class _Fenetre:
    fin: date | None

    @property
    def seuil(self) -> date | None:
        return seuil_recence(self.fin) if self.fin is not None else None


async def _fenetre(session: AsyncSession) -> _Fenetre:
    return _Fenetre(fin=(await session.execute(_FIN_DVF_SQL)).scalar_one())


async def _cartes(
    session: AsyncSession, lignes: Sequence[Mapping], fenetre: _Fenetre
) -> list[CarteVente]:
    isolements = await _isolements(session, [ligne["id_mutation"] for ligne in lignes])
    return [
        carte_vente(
            ligne,
            minimiser(
                ligne["date_mutation"],
                isolements.get(ligne["id_mutation"]),
                fenetre.seuil,
            ),
            ligne.get("distance"),
        )
        for ligne in lignes
    ]


async def _point(session: AsyncSession, ban_id: str):
    """(rows found, single BAN point or None); a failed read is an outage.

    The outage is raised detached from the database error (C59 B1).
    """
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    _POINTS_DE_LA_CLE,
                    {"ban_id": ban_id, "department": departement_de_cle(ban_id)},
                )
            ).all()
    except SQLAlchemyError:
        rows = None
    if rows is None:
        raise VentesIndisponiblesError from None
    return bool(rows), point_unique(rows)


@dataclass(frozen=True)
class PageVentes:
    """One page of the sales of one parcel, as read under the DVF guard.

    ``lignes`` are the rows of the page with their mutation, for an internal
    caller that reads what else these mutations hold (the parcels sold
    together, on the map): they are never served.
    """

    generation: str
    total: int
    pages: int
    cartes: list[CarteVente]
    faits: ProvenanceFacts | None
    lignes: Sequence[Mapping]


async def lire_page_parcelle(
    session: AsyncSession,
    id_parcelle: str,
    page: int,
    generation: str | None,
    par_page: int = PAR_PAGE,
) -> PageVentes:
    """Page *page* of the sales of *id_parcelle*, *par_page* per page.

    The one reader of the sales of a parcel, shared by the address page (ten
    per page) and by the map (``app.carte.ventes``, its own page size): same
    population, same order, same cards, same withheld prices. It reads DVF
    without guarding it: await it inside the DVF block of the caller
    (:func:`app.adresse.blocs.lire_bloc`). Raises
    :class:`GenerationChangeeError` or :class:`PageHorsLimitesError`.
    """
    jeton = await _generation(session)
    if generation is not None and generation != jeton:
        raise GenerationChangeeError
    compte = await dvf_compte_par_id_parcelle(session, id_parcelle)
    pages = nombre_pages(compte["nombre"], par_page)
    if page > pages:
        raise PageHorsLimitesError
    lignes = (
        (
            await session.execute(
                _PAGE_PARCELLE_SQL,
                {
                    "id_parcelle": id_parcelle,
                    "limit": par_page,
                    "offset": (page - 1) * par_page,
                },
            )
        )
        .mappings()
        .all()
    )
    cartes = await _cartes(session, lignes, await _fenetre(session))
    faits = await faits_de_provenance(session, "dvf")
    return PageVentes(
        generation=jeton,
        total=compte["nombre"],
        pages=pages,
        cartes=cartes,
        faits=faits,
        lignes=lignes,
    )


async def lire_ventes_parcelle(  # noqa: C901
    session: AsyncSession,
    ban_id: str,
    page: int,
    generation: str | None,
    hosting: RouteHosting,
) -> VentesParcelle | None:
    """Page *page* of the sales of the parcel of the point of *ban_id*.

    Returns None for a key absent from the BAN. Raises
    :class:`GenerationChangeeError`, :class:`PageHorsLimitesError` or
    :class:`VentesIndisponiblesError`. Nothing is read when the BAN or DVF
    is out of coverage; DVF is not read without one parcel under the point.
    """
    commun = {"source": source_valeur("dvf"), "page": page, "par_page": PAR_PAGE}
    for cle in ("ban", "dvf"):
        hebergement = hosting.hostings[cle]
        if hebergement.etat != "heberge":
            return VentesParcelle(
                etat="out_of_coverage", motif=hebergement.motif, **commun
            )
    trouvee, point = await _point(session, ban_id)
    if not trouvee:
        return None
    if point is None:
        return VentesParcelle(
            etat="not_available", motif="parcelle_indeterminee", **commun
        )
    relation = await resoudre_parcelle(session, point, hosting)
    if relation.etat == "hors_couverture":
        return VentesParcelle(etat="out_of_coverage", motif=relation.motif, **commun)
    if relation.etat == "indisponible":
        return VentesParcelle(
            etat="temporary_error", motif="lecture_indisponible", **commun
        )
    if relation.etat == "ambigue":
        return VentesParcelle(
            etat="not_available",
            motif="parcelles_multiples",
            nombre_parcelles=relation.nombre_candidats,
            **commun,
        )
    if relation.etat != "unique" or relation.id_parcelle is None:
        return VentesParcelle(
            etat="not_available", motif="point_hors_parcelle", **commun
        )
    id_parcelle = relation.id_parcelle

    async def lecteur() -> PageVentes:
        return await lire_page_parcelle(
            session, id_parcelle, page, generation, PAR_PAGE
        )

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.en_panne or bloc.valeur is None:
        raise VentesIndisponiblesError
    lue = bloc.valeur
    return VentesParcelle(
        etat="value",
        periode=periode_de_source(lue.faits),
        total=lue.total,
        pages=lue.pages,
        generation=lue.generation,
        ventes=lue.cartes,
        **commun,
    )


# --- Sales declared at the address (C37) ----------------------------------------

# Street name as compared on both sides: upper case without accents, the
# apostrophes and hyphens as spaces, spaces collapsed. One fragment, applied
# by PostgreSQL to the BAN street and to the DVF street, so that both go
# through the same ``unaccent``.
_VOIE_NORMALISEE_SQL = (
    "btrim(regexp_replace(regexp_replace(upper(unaccent({})), "
    "'[''\u2019-]', ' ', 'g'), '\\s+', ' ', 'g'))"
)

# Street types as abbreviated in DVF (FANTOIR codes observed in the imported
# file). A BAN street is compared under its full and its abbreviated form,
# so a DVF street written in full still matches.
ABREVIATIONS_VOIE: dict[str, str] = {
    "AVENUE": "AV",
    "IMPASSE": "IMP",
    "BOULEVARD": "BD",
    "ROUTE": "RTE",
    "ALLEE": "ALL",
    "CHEMIN": "CHE",
    "PLACE": "PL",
    "RESIDENCE": "RES",
    "SQUARE": "SQ",
    "ESPLANADE": "ESP",
    "LOTISSEMENT": "LOT",
    "PROMENADE": "PROM",
    "PASSAGE": "PAS",
    "COURS": "CRS",
    "RUELLE": "RLE",
    "VENELLE": "VEN",
    "HAMEAU": "HAM",
    "DOMAINE": "DOM",
    "FAUBOURG": "FG",
    "SENTIER": "SEN",
    "QUARTIER": "QUA",
    "CHEMINEMENT": "CHEM",
}


def formes_de_voie(voie_normalisee: str | None) -> list[str]:
    """Forms of a normalised BAN street compared with DVF: full, abbreviated.

    *voie_normalisee* is the output of ``_VOIE_NORMALISEE_SQL``. Only the
    leading street type is abbreviated; a street without a known type keeps
    its one form. An empty street has no form.
    """
    if not voie_normalisee:
        return []
    type_voie, _, reste = voie_normalisee.partition(" ")
    abrege = ABREVIATIONS_VOIE.get(type_voie)
    if abrege is None or not reste:
        return [voie_normalisee]
    return [voie_normalisee, f"{abrege} {reste}"]


def numero_dvf(numero: str | None) -> str | None:
    """The BAN number as DVF writes it (digits, no leading zero), or None.

    A number that is not made of digits only cannot be compared with DVF, nor
    can 0: the query strips the leading zeros of the DVF number, so « 0 » never
    matches (IN-02 of 261010-wpt: 89 BAN rows of 44 carry « 0 »).
    """
    if numero is None:
        return None
    numero = numero.strip()
    if not numero.isascii() or not numero.isdigit() or int(numero) == 0:
        return None
    return str(int(numero))


# Rule of the declared sales. ``numero`` (C37, « Au 2 seulement »): the
# default, what the current pages read. ``numero_et_suffixe`` (C44, plan
# 04.1-14): served on request until the pages word it (04.1-REPORTS-UI.md).
RegleVentesAdresse = Literal["numero", "numero_et_suffixe"]

# VEFA of the declared sales (plan 04.1-15, D-128). ``exclues``: the sales
# (« Vente ») only, the default, what the current pages read and word.
# ``incluses``: the sales and the VEFA, each card marked ``hors_stats_vefa``,
# the predicate of ``/v1/dvf/adresse`` and of the similar sales (C56, D-12);
# served on request until the pages word it (04.1-REPORTS-UI.md, Plan 15).
VefaVentesAdresse = Literal["exclues", "incluses"]
_NATURES_DECLAREES: dict[str, str] = {
    "exclues": "mg.est_vente",
    "incluses": "(mg.est_vente OR mg.hors_stats_vefa)",
}

# BAN ``rep`` (lower case, trimmed) -> DVF ``adresse_suffixe``, as measured
# on 2026-10-19 on the served DVF generation (run 34) and the BAN of 44/85
# (mesures/dvf-suffixe/correspondance.json): DVF writes one capital letter,
# BAN writes bis/ter/qua/quater or a letter, in any case. Nothing else has
# an established correspondence (quinquies, bisa, logement1, digits...).
CORRESPONDANCE_SUFFIXE_BAN_DVF: dict[str, str] = {
    "bis": "B",
    "ter": "T",
    "quater": "Q",
    "qua": "Q",
    **{lettre: lettre.upper() for lettre in string.ascii_lowercase},
}
MOTIF_SUFFIXE_SANS_CORRESPONDANCE = "suffixe_sans_correspondance"
MOTIF_SUFFIXE_NON_IMPORTE = "suffixe_non_importe"


def suffixe_dvf(rep: str | None) -> str | None:
    """DVF suffix of a BAN ``rep``: "" without suffix, None without correspondence."""
    rep = (rep or "").strip().lower()
    if not rep:
        return ""
    return CORRESPONDANCE_SUFFIXE_BAN_DVF.get(rep)


@dataclass(frozen=True)
class CleAdresseDvf:
    """What the DVF declared address is compared with, or why it is not.

    ``suffixe`` is None under the number-only rule (not compared), "" for an
    address without suffix (sales declared without one), else the DVF code.
    """

    motif: str | None
    commune: str | None = None
    numero: str | None = None
    voies: tuple[str, ...] = ()
    suffixe: str | None = None


def cle_adresse_dvf(
    rows: Sequence[Mapping], regle: RegleVentesAdresse = "numero"
) -> CleAdresseDvf:
    """Key of the DVF declared address of the BAN rows of one key.

    Rows that disagree on the number, the suffix, the street or the commune
    leave the address undetermined, never resolved by picking one.

    ``numero`` (C37, « Au 2 seulement »): the suffix is not compared, so an
    address with a suffix shows none of the sales declared at its number and
    a sale declared at « 2 B » shows on « 2 ». ``numero_et_suffixe`` (C44):
    « 2 » takes the sales declared without suffix, « 2 bis » those declared
    « 2 B »; a ``rep`` without measured correspondence is a typed state,
    never the sales of the bare number.
    """
    champs = ("numero", "rep", "voie_normalisee", "code_commune")
    if any(len({row[champ] for row in rows}) != 1 for champ in champs):
        return CleAdresseDvf(motif="adresse_indeterminee")
    row = rows[0]
    if regle == "numero" and (row["rep"] or "").strip():
        return CleAdresseDvf(motif="suffixe_non_distingue")
    numero = numero_dvf(row["numero"])
    if numero is None:
        return CleAdresseDvf(motif="sans_numero")
    suffixe = None
    if regle == "numero_et_suffixe":
        suffixe = suffixe_dvf(row["rep"])
        if suffixe is None:
            return CleAdresseDvf(motif=MOTIF_SUFFIXE_SANS_CORRESPONDANCE)
    voies = formes_de_voie(row["voie_normalisee"])
    if not voies or not row["code_commune"]:
        return CleAdresseDvf(motif="adresse_indeterminee")
    return CleAdresseDvf(
        motif=None,
        commune=row["code_commune"],
        numero=numero,
        voies=tuple(voies),
        suffixe=suffixe,
    )


def sans_ambiguite(cle: CleAdresseDvf, voisines: Sequence[Mapping]) -> CleAdresseDvf:
    """*cle*, unless another BAN address reads the same DVF sales.

    *voisines* are BAN rows (the columns of :func:`cle_adresse_dvf`). Each one
    gets its key by the attachment's own normalisation: « 06 bis » and « 6 b »,
    or « 6 bis Avenue des Lilas » and « 6 b AV DES LILAS », both read DVF
    « 6 B » of « AV DES LILAS ». The sale is attached to neither (typed
    state), never to the one picked first.
    """
    for row in voisines:
        autre = cle_adresse_dvf([row], "numero_et_suffixe")
        if (
            autre.motif is None
            and (autre.commune, autre.numero, autre.suffixe)
            == (cle.commune, cle.numero, cle.suffixe)
            and set(autre.voies) & set(cle.voies)
        ):
            return CleAdresseDvf(motif=MOTIF_SUFFIXE_SANS_CORRESPONDANCE)
    return cle


_ADRESSE_DE_LA_CLE = text(f"""
    SELECT numero, rep, code_commune,
           {_VOIE_NORMALISEE_SQL.format("nom_voie")} AS voie_normalisee
    FROM public.adresses
    WHERE ban_id = :ban_id AND code_departement = :department
    ORDER BY id
""")

# The other BAN addresses with a suffix that may read the same DVF number:
# same commune, same number once its leading zeros are dropped (« 06 » reads
# DVF « 6 »), whatever the way their street is written. Their street forms
# and suffix are compared by :func:`sans_ambiguite`, with the key's own
# normalisation (Codex review I1 of 04.5-08).
_ADRESSES_VOISINES = text(f"""
    WITH voisines AS MATERIALIZED (
        SELECT numero, rep, nom_voie, code_commune FROM public.adresses
        WHERE code_departement = :department AND code_commune = :commune
          AND ltrim(btrim(numero), '0') = :numero
          AND ban_id <> :ban_id AND rep IS NOT NULL
    )
    SELECT DISTINCT numero, rep, code_commune,
           {_VOIE_NORMALISEE_SQL.format("nom_voie")} AS voie_normalisee
    FROM voisines
""")

# The comment the DVF reconstruction leaves on the column (C44): absent on a
# base migrated but not reimported, or before migration 105.
_MARQUEUR_SUFFIXE_SQL = text("""
    SELECT col_description(a.attrelid, a.attnum)
    FROM pg_attribute a
    WHERE a.attrelid = to_regclass('public.mutations_grain')
      AND a.attname = 'adresse_suffixe' AND NOT a.attisdropped
""")

# The sales of the commune declared at the number, kept apart first
# (materialised: the street normalisation runs on these rows only), then
# those whose normalised street is one of the forms of the BAN street.
# One row per mutation, as the parcel list.
# Under the number and suffix rule (C44), the candidates also carry the
# suffix of the address: "" for none. The number-only rule never names the
# column (it reads a base migrated or not).
_FILTRE_SUFFIXE = """
          AND coalesce(upper(btrim(mg.adresse_suffixe)), '') = :suffixe"""


def _declarees_cte(filtre_suffixe: str, natures: str) -> str:
    # A mutation of unknown position is never attached to an address (D-03).
    return f"""
    WITH candidates AS MATERIALIZED (
        SELECT mg.* FROM mutations_grain mg
        WHERE mg.code_departement = :departement
          AND mg.code_commune = :commune
          AND {natures}
          AND {POSITION_CONNUE_SQL}
          AND ltrim(mg.adresse_numero, '0') = :numero{filtre_suffixe}
    ), mutations AS (
        SELECT DISTINCT ON (mg.id_mutation) {_COLONNES_CARTE}
        FROM candidates mg
        WHERE {_VOIE_NORMALISEE_SQL.format("mg.adresse_nom_voie")}
              = ANY(CAST(:voies AS text[]))
        ORDER BY mg.id_mutation, mg.id
    )
"""


def _requetes_declarees(filtre_suffixe: str, natures: str):
    """(count, page) statements of the declared sales.

    The count's first column is the number of listed mutations, the second
    the VEFA among them (0 when the VEFA are not listed).
    """
    cte = _declarees_cte(filtre_suffixe, natures)
    return (
        text(f"""{cte}
    SELECT count(*) AS total, count(*) FILTER (WHERE hors_stats_vefa) AS vefa
    FROM mutations
"""),
        text(f"""{cte}
    SELECT * FROM mutations
    ORDER BY date_mutation DESC, id_mutation
    LIMIT :limit OFFSET :offset
"""),
    )


# (suffix compared, VEFA) -> (count, page) statements. « exclues »: never a
# VEFA, the sales only (D-12: a VEFA is out of every statistic). The
# comparator (C40) counts with the « exclues » statement of the rule it
# passes (04.5-08).
_REQUETES_DECLAREES = {
    (suffixe, vefa): _requetes_declarees(
        _FILTRE_SUFFIXE if suffixe else "", _NATURES_DECLAREES[vefa]
    )
    for suffixe in (False, True)
    for vefa in ("exclues", "incluses")
}


async def _cle_de_l_adresse(
    session: AsyncSession, ban_id: str, regle: RegleVentesAdresse = "numero"
):
    """(rows found, key of the DVF address); a failed read is an outage.

    Under the number and suffix rule, the other BAN addresses that may read
    the same DVF number and street are read too (:func:`sans_ambiguite`). The
    outage is raised detached from the database error (C59 B1).
    """
    departement = departement_de_cle(ban_id)
    cle = None
    try:
        async with session.begin_nested():
            rows = (
                (
                    await session.execute(
                        _ADRESSE_DE_LA_CLE,
                        {"ban_id": ban_id, "department": departement},
                    )
                )
                .mappings()
                .all()
            )
            cle = cle_adresse_dvf(rows, regle) if rows else None
            if cle is not None and cle.suffixe:
                voisines = (
                    await session.execute(
                        _ADRESSES_VOISINES,
                        {
                            "department": departement,
                            "commune": cle.commune,
                            "numero": cle.numero,
                            "ban_id": ban_id,
                        },
                    )
                ).mappings()
                cle = sans_ambiguite(cle, voisines.all())
    except SQLAlchemyError:
        rows = None
    if rows is None:
        raise VentesIndisponiblesError from None
    return bool(rows), cle


def _parametres_declarees(ban_id: str, adresse: CleAdresseDvf) -> dict[str, Any]:
    parametres: dict[str, Any] = {
        "departement": departement_de_cle(ban_id),
        "commune": adresse.commune,
        "numero": adresse.numero,
        "voies": list(adresse.voies),
    }
    if adresse.suffixe is not None:
        parametres["suffixe"] = adresse.suffixe
    return parametres


async def _suffixe_importe(session: AsyncSession) -> bool:
    """True when the served `mutations_grain` was rebuilt with the suffix (C44)."""
    marqueur = (await session.execute(_MARQUEUR_SUFFIXE_SQL)).scalar_one_or_none()
    return marqueur == MARQUEUR_ADRESSE_SUFFIXE


async def lire_ventes_adresse(  # noqa: C901
    session: AsyncSession,
    ban_id: str,
    page: int,
    generation: str | None,
    hosting: RouteHosting,
    regle: RegleVentesAdresse = "numero",
    vefa: VefaVentesAdresse = "exclues",
) -> VentesAdresse | None:
    """Page *page* of the sales declared at the address of *ban_id* (C37, C44).

    Returns None for a key absent from the BAN. Raises
    :class:`GenerationChangeeError`, :class:`PageHorsLimitesError` or
    :class:`VentesIndisponiblesError`. Nothing is read when the BAN or DVF
    is out of coverage; DVF is not read for an address that is not compared
    (suffix under the number-only rule, suffix without correspondence,
    number not usable). Under the number and suffix rule, a generation
    imported without the suffix is a typed absence (``suffixe_non_importe``),
    never every sale taken as declared without suffix. Under ``vefa``
    ``incluses`` (plan 04.1-15), the VEFA are listed and marked, and
    ``nombre_vefa`` says how many of ``total`` they are; the count of the
    sales (:func:`compte_ventes_adresse`) never takes them. The parcel is
    never read.
    """
    source = source_valeur("dvf")

    def servies(**champs: Any) -> VentesAdresse:
        return VentesAdresse(source=source, page=page, par_page=PAR_PAGE, **champs)

    for cle in ("ban", "dvf"):
        hebergement = hosting.hostings[cle]
        if hebergement.etat != "heberge":
            return servies(etat="out_of_coverage", motif=hebergement.motif)
    trouvee, adresse = await _cle_de_l_adresse(session, ban_id, regle)
    if not trouvee or adresse is None:
        return None
    if adresse.motif is not None:
        return servies(etat="not_available", motif=adresse.motif)
    params = _parametres_declarees(ban_id, adresse)
    compte_sql, page_sql = _REQUETES_DECLAREES[(adresse.suffixe is not None, vefa)]

    async def lecteur():
        jeton = await _generation(session)
        if generation is not None and generation != jeton:
            raise GenerationChangeeError
        if adresse.suffixe is not None and not await _suffixe_importe(session):
            return None
        compte = (await session.execute(compte_sql, params)).mappings().one()
        total = compte["total"]
        pages = nombre_pages(total)
        if page > pages:
            raise PageHorsLimitesError
        lignes = (
            (
                await session.execute(
                    page_sql,
                    {**params, "limit": PAR_PAGE, "offset": (page - 1) * PAR_PAGE},
                )
            )
            .mappings()
            .all()
        )
        cartes = await _cartes(session, lignes, await _fenetre(session))
        faits = await faits_de_provenance(session, "dvf")
        nombre_vefa = compte["vefa"] if vefa == "incluses" else None
        return jeton, total, nombre_vefa, pages, cartes, faits

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.en_panne:
        raise VentesIndisponiblesError
    if bloc.valeur is None:
        return servies(etat="not_available", motif=MOTIF_SUFFIXE_NON_IMPORTE)
    jeton, total, nombre_vefa, pages, cartes, faits = bloc.valeur
    return servies(
        etat="value",
        periode=periode_de_source(faits),
        total=total,
        nombre_vefa=nombre_vefa,
        pages=pages,
        generation=jeton,
        ventes=cartes,
    )


FIELD_VENTES_ADRESSE_NOMBRE = "adresse.dvf.ventes_adresse.nombre"
# C44 (CR-02): the comparator row of an address to which no sale is attached.
MOTIF_AUCUNE_VENTE_RATTACHEE = "aucune_vente_rattachee"


# One branch per typed state of the row, as lire_ventes_adresse (C901 there too).
async def compte_ventes_adresse(  # noqa: C901
    session: AsyncSession,
    ban_id: str,
    hosting: RouteHosting,
    regle: RegleVentesAdresse = "numero",
) -> ValeurLieu:
    """Number of sales declared at the address of *ban_id*, for the comparator (C40).

    The key, the marker guard and the count query of
    :func:`lire_ventes_adresse` under *regle* (C37, C44), no copy; no card is
    read. The comparator passes the rule the page reads (plan 04.5-08, R8).
    The sales only: a VEFA is never counted, even when the list serves them
    on request (plan 04.1-15, D-12). Every outcome is a typed value of the
    row: out of coverage (BAN or DVF not hosted), the served reason of an
    address that is not searched (suffix, number, undetermined), a generation
    imported without the suffix under the suffix rule
    (``suffixe_non_importe``, never every sale of the number), an outage
    (failed read, refused publication), or the count with the period of DVF.
    A count of 0 is served as ``not_available`` / ``aucune_vente_rattachee``
    with that period (C44, CR-02): no sale was attached to the address, which
    is not a zero of the source (the first line of a sale only, sales without
    a BAN address). Never a zero for a count that was not read.
    """
    commun: dict[str, Any] = {
        "field_id": FIELD_VENTES_ADRESSE_NOMBRE,
        "unite": "count",
        "source": source_valeur("dvf"),
        "rattachement": "adresse_declaree_vente",
    }
    for cle in ("ban", "dvf"):
        hebergement = hosting.hostings[cle]
        if hebergement.etat != "heberge":
            return ValeurLieu(**commun, etat="out_of_coverage", motif=hebergement.motif)
    try:
        trouvee, adresse = await _cle_de_l_adresse(session, ban_id, regle)
    except VentesIndisponiblesError:
        return ValeurLieu(
            **commun, etat="temporary_error", motif="lecture_indisponible"
        )
    if not trouvee or adresse is None:
        # The identity of the column was read: rows gone since are undetermined.
        return ValeurLieu(**commun, etat="not_available", motif="adresse_indeterminee")
    if adresse.motif is not None:
        return ValeurLieu(**commun, etat="not_available", motif=adresse.motif)
    params = _parametres_declarees(ban_id, adresse)
    # The sales only (« exclues »), filtered by the suffix under the suffix rule.
    compte_sql = _REQUETES_DECLAREES[(adresse.suffixe is not None, "exclues")][0]

    async def lecteur():
        if adresse.suffixe is not None and not await _suffixe_importe(session):
            return None
        total = (await session.execute(compte_sql, params)).scalar_one()
        return total, await faits_de_provenance(session, "dvf")

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.en_panne:
        return ValeurLieu(**commun, etat="temporary_error", motif=bloc.panne)
    if bloc.valeur is None:
        return ValeurLieu(
            **commun, etat="not_available", motif=MOTIF_SUFFIXE_NON_IMPORTE
        )
    total, faits = bloc.valeur
    periode = periode_de_source(faits)
    if total == 0:
        return ValeurLieu(
            **commun,
            etat="not_available",
            motif=MOTIF_AUCUNE_VENTE_RATTACHEE,
            periode=periode,
        )
    return ValeurLieu(**commun, etat="value", valeur=total, periode=periode)


# --- Similar sales (D-114) ------------------------------------------------------

# One residential local of the chosen kind: the closed classes of the DVF
# qualification (``app.etl_dvf.qualification``), bound as parameters.
CLASSE_PAR_TYPE: dict[str, str] = {
    "maison": "residentiel_maison",
    "appartement": "residentiel_appartement",
}


class CriteresComparables(BaseModel):
    """Criteria of the similar sales, closed enumerations (T-04-05-02).

    ``rayon`` None means the whole commune of the address. Defaults are the
    UI-SPEC ones (± 20 %, 500 m, 5 years); no type is ever assumed (Q6).
    """

    type: TypeComparable
    surface: int | None = Field(default=None, ge=9, le=1000)
    tolerance: Literal[10, 20, 30] = 20
    rayon: Literal[250, 500, 1000] | None = 500
    periode: Literal[1, 3, 5] = 5
    page: int = Field(default=1, ge=1, le=100_000)

    def bornes_surface(self) -> tuple[int | None, int | None]:
        """Bounds of the built surface, rounded half up to the square metre."""
        if self.surface is None:
            return None, None
        bas = (self.surface * (100 - self.tolerance) + 50) // 100
        haut = (self.surface * (100 + self.tolerance) + 50) // 100
        return bas, haut


def criteres_appliques(
    criteres: CriteresComparables, fin_dvf: date | None
) -> CriteresAppliques:
    """The criteria as applied: computed bounds and calendar years."""
    bas, haut = criteres.bornes_surface()
    fin = fin_dvf.year if fin_dvf is not None else None
    return CriteresAppliques(
        type=criteres.type,
        surface=criteres.surface,
        tolerance=criteres.tolerance,
        surface_min=bas,
        surface_max=haut,
        perimetre="commune" if criteres.rayon is None else "rayon",
        rayon_m=criteres.rayon,
        periode=PeriodeAppliquee(
            annees=criteres.periode,
            debut=fin - criteres.periode + 1 if fin is not None else None,
            fin=fin,
        ),
    )


_POINT_SQL = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"
# Fixed fragments only: the criteria are bound parameters, never interpolated.
# A mutation of unknown position is in neither perimeter (D-03): the radius
# already excludes it, the commune would list it without a distance.
_FILTRE_COMMUN = f"""
        FROM mutations_grain mg
        WHERE mg.classe_mutation = :classe
          AND (mg.est_vente OR mg.hors_stats_vefa)
          AND mg.date_mutation >= :debut AND mg.date_mutation <= :fin
          AND {POSITION_CONNUE_SQL}
"""
_FILTRE_SURFACE = """
          AND mg.surface_bati_totale >= :surface_min
          AND mg.surface_bati_totale <= :surface_max
"""
_FILTRE_RAYON = f"""
          AND ST_DWithin(mg.position::geography, {_POINT_SQL}, :rayon)
"""
_FILTRE_COMMUNE = """
          AND mg.code_departement = :departement
          AND mg.code_commune = :commune
"""


def _requetes(avec_surface: bool, commune: bool):
    """(count, page) statements of one combination of the filters."""
    filtre = (
        _FILTRE_COMMUN
        + (_FILTRE_SURFACE if avec_surface else "")
        + (_FILTRE_COMMUNE if commune else _FILTRE_RAYON)
    )
    compte = text(f"""
        WITH mutations AS (
            SELECT DISTINCT ON (mg.id_mutation) mg.id_mutation
            {filtre}
            ORDER BY mg.id_mutation, mg.id
        )
        SELECT count(*) FROM mutations
    """)
    page = text(f"""
        WITH mutations AS (
            SELECT DISTINCT ON (mg.id_mutation) {_COLONNES_CARTE},
                   ST_Distance(mg.position::geography, {_POINT_SQL}) AS distance
            {filtre}
            ORDER BY mg.id_mutation, mg.id
        )
        SELECT * FROM mutations
        ORDER BY distance ASC NULLS LAST, date_mutation DESC, id_mutation
        LIMIT :limit OFFSET :offset
    """)
    return compte, page


_REQUETES = {
    (surface, commune): _requetes(surface, commune)
    for surface in (False, True)
    for commune in (False, True)
}


def _servies(
    lu: tuple | None, criteres: CriteresComparables, commun: dict[str, Any]
) -> VentesRessemblantes:
    """The similar sales served after a successful read (API IN-05, C16)."""
    if lu is None:
        return VentesRessemblantes(
            etat="not_available",
            criteres_appliques=criteres_appliques(criteres, None),
            **commun,
        )
    jeton, appliques, effectif, pages, cartes, deborde = lu
    if deborde and effectif == 0:
        # No sale on the hosted side of the disc: never a zero (C16).
        return VentesRessemblantes(
            etat="out_of_coverage",
            motif=MOTIF_HORS_PERIMETRE,
            liste_incomplete=True,
            criteres_appliques=appliques,
            **commun,
        )
    return VentesRessemblantes(
        etat="value",
        motif=MOTIF_HORS_PERIMETRE if deborde else None,
        liste_incomplete=deborde,
        criteres_appliques=appliques,
        effectif=effectif,
        pages=pages,
        generation=jeton,
        ventes=cartes,
        **commun,
    )


async def lire_ventes_ressemblantes(
    session: AsyncSession,
    ban_id: str,
    criteres: CriteresComparables,
    generation: str | None,
    hosting: RouteHosting,
) -> VentesRessemblantes | None:
    """Page ``criteres.page`` of the similar sales of *ban_id* (D-114).

    Returns None for a key absent from the BAN. Raises
    :class:`GenerationChangeeError`, :class:`PageHorsLimitesError` or
    :class:`VentesIndisponiblesError`. The parcel is not needed: the radius
    or the commune is taken around the BAN point.
    """
    commun: dict[str, Any] = {
        "source": source_valeur("dvf"),
        "rattachement": (
            "commune_de_l_adresse" if criteres.rayon is None else "rayon_du_point"
        ),
        "page": criteres.page,
        "par_page": PAR_PAGE,
    }
    for cle in ("ban", "dvf"):
        hebergement = hosting.hostings[cle]
        if hebergement.etat != "heberge":
            return VentesRessemblantes(
                etat="out_of_coverage",
                motif=hebergement.motif,
                criteres_appliques=criteres_appliques(criteres, None),
                **commun,
            )
    trouvee, point = await _point(session, ban_id)
    if not trouvee:
        return None
    if point is None:
        return VentesRessemblantes(
            etat="not_available",
            motif="position_indeterminee",
            criteres_appliques=criteres_appliques(criteres, None),
            **commun,
        )
    bas, haut = criteres.bornes_surface()
    compte_sql, page_sql = _REQUETES[(bas is not None, criteres.rayon is None)]

    async def lecteur():
        jeton = await _generation(session)
        if generation is not None and generation != jeton:
            raise GenerationChangeeError
        fenetre = await _fenetre(session)
        appliques = criteres_appliques(criteres, fenetre.fin)
        if fenetre.fin is None:
            # No DVF date: nothing was measured, never « 0 vente » (API IN-05).
            return None
        params = {
            "classe": CLASSE_PAR_TYPE[criteres.type],
            "debut": date(appliques.periode.debut, 1, 1),
            "fin": fenetre.fin,
            "surface_min": bas,
            "surface_max": haut,
            "rayon": float(criteres.rayon) if criteres.rayon else None,
            "departement": departement_de_cle(ban_id),
            "commune": ban_id.split("_", 1)[0],
            "lon": point.lon,
            "lat": point.lat,
        }
        effectif = (await session.execute(compte_sql, params)).scalar_one()
        pages = nombre_pages(effectif)
        if criteres.page > pages:
            raise PageHorsLimitesError
        lignes = (
            (
                await session.execute(
                    page_sql,
                    {
                        **params,
                        "limit": PAR_PAGE,
                        "offset": (criteres.page - 1) * PAR_PAGE,
                    },
                )
            )
            .mappings()
            .all()
        )
        cartes = await _cartes(session, lignes, fenetre)
        # The commune is within its departement; a radius may leave it (C16).
        deborde = criteres.rayon is not None and await rayon_deborde(
            session,
            point.lon,
            point.lat,
            criteres.rayon,
            hosting.hostings["dvf"].departements,
        )
        return jeton, appliques, effectif, pages, cartes, deborde

    bloc = await lire_bloc(session, ["dvf"], lecteur)
    if bloc.en_panne:
        raise VentesIndisponiblesError
    return _servies(bloc.valeur, criteres, commun)

"""DPE documents declared at an address (04-03, D-109, D-111, DPE-01).

« Declared » means: the address typed by the diagnostician matches this BAN
key (``diagnostics_dpe.identifiant_ban``). No document is tied to a dwelling.

Three distinct axes describe a document and are never merged: its method
(before or after July 2021, D-111), its calendar validity
(:func:`app.dpe.lifecycle.validity_at`, the only validity computation) and
its replacement state (lifecycle facts of the ETL). The most-recent selection
is computed on the whole population of the snapshot, never on a page.
"""

import hashlib
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import (
    faits_de_provenance,
    jour_d_import,
    lire_bloc,
    source_valeur,
)
from app.adresse.relations import (
    BatimentsParcelle,
    PointBan,
    batiments_sur_parcelle,
    resoudre_parcelle,
)
from app.adresse.schemas import (
    AdresseDeclaree,
    BadgeDocument,
    ConstatD110,
    CritereD110,
    DocumentDpeCarte,
    DocumentDpeCarteAdresseBrute,
    DocumentsDpeAdresse,
    DocumentSelectionne,
    MotifSelection,
    RelationParcelle,
    RepereDpe,
    SelectionPlusRecent,
    SignalPlusieursLogements,
)
from app.common.publication_readiness import (
    describe_read_failure,
    guard_snapshot_publication,
)
from app.dpe import queries
from app.dpe.concordance import (
    FAITS_DOCUMENTS_SQL,
    Concordance,
    FaitsConcordance,
    concordance_adresse,
)
from app.dpe.lifecycle import DATE_ISSUES, GRAPH_ISSUES, Validity, validity_at
from app.dpe.public_projection import (
    COLONNES_COMPLEMENT,
    AdresseBruteDocument,
    IdentiteBan,
    projeter_adresse_declaree,
)
from app.dpe.queries import DpeDocumentAdresse, DpeRelationGenerations
from app.dpe.schemas import DpeInventoryDistribution
from app.logements.queries import copropriete_par_parcelle
from app.scope.dependencies import RouteHosting

logger = logging.getLogger(__name__)

PAR_PAGE = 10
# Plan 04.1-13: cards of the raw-address group served with the list, the most
# recent first, with their total (block H: about 1.5 documents per address).
ADRESSE_BRUTE_MAX = 10
# Bound of the population read for the selection: the traversal budget of the
# lifecycle graph. Beyond it the selection is not computed (never guessed).
CANDIDATS_MAX = 1000
# D-111: a document established before July 2021 follows the former method.
DEBUT_METHODE_ACTUELLE = date(2021, 7, 1)
FIELD_ID_NOMBRE = "adresse.dpe.nombre_declares"
RATTACHEMENT = "adresse_saisie_diagnostiqueur"

_CLASSES = frozenset("ABCDEFG")
_REPLACEMENT_STATES = frozenset(
    {"not_checked", "no_successor_known", "replaced", "ambiguous"}
)

Methode = Literal["ancienne", "actuelle", "inconnue"]


class PageHorsLimitesError(Exception):
    """The requested page is beyond the last one."""


class GenerationChangeeError(Exception):
    """The page link carries a generation that is no longer the current one."""


class DocumentsIndisponiblesError(Exception):
    """Refused DPE publication or failed read of the list."""


def nombre_pages(total: int) -> int:
    """Number of pages of ten; an address without document has one page."""
    return max(1, math.ceil(total / PAR_PAGE))


def decalage(page: int) -> int:
    """SQL offset of *page* (from 1)."""
    return (page - 1) * PAR_PAGE


def methode_document(etablissement: date | None) -> Methode:
    """Method of a document by its establishment date (D-111)."""
    if etablissement is None:
        return "inconnue"
    return "ancienne" if etablissement < DEBUT_METHODE_ACTUELLE else "actuelle"


@dataclass(frozen=True)
class StatutDocument:
    """The three axes of a document, as read; nothing is inferred."""

    etablissement: date | None
    methode: Methode
    validite: Validity
    remplacement: str
    anomalies_graphe: tuple[str, ...]

    @property
    def eligible_valide(self) -> bool:
        """Current method, not expired by date, no anomaly, not replaced.

        An uncertain replacement (not checked, ambiguous, graph anomaly) is
        never turned into an established validity.
        """
        return (
            self.methode == "actuelle"
            and self.validite == "not_expired_by_date"
            and self.remplacement == "no_successor_known"
            and not self.anomalies_graphe
        )


def _faits(doc: DpeDocumentAdresse) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Replacement state, graph and date issues; malformed facts are uncertain."""
    facts = doc.lifecycle_facts if isinstance(doc.lifecycle_facts, dict) else {}
    state = facts.get("replacement_state")
    graph = facts.get("graph_issues")
    dates = facts.get("date_issues")
    if state not in _REPLACEMENT_STATES or not isinstance(graph, list):
        return "not_checked", (), ()
    graph_issues = tuple(issue for issue in GRAPH_ISSUES if issue in graph)
    date_issues = (
        tuple(issue for issue in DATE_ISSUES if issue in dates)
        if isinstance(dates, list)
        else ()
    )
    return state, graph_issues, date_issues


def statut_document(doc: DpeDocumentAdresse, as_of: date) -> StatutDocument:
    """Method, calendar validity (``validity_at``) and replacement of *doc*."""
    remplacement, graphe, dates = _faits(doc)
    return StatutDocument(
        etablissement=doc.date_etablissement_dpe,
        methode=methode_document(doc.date_etablissement_dpe),
        validite=validity_at(
            doc.date_etablissement_dpe,
            doc.date_fin_validite_dpe,
            as_of,
            date_issues=dates,
        ),
        remplacement=remplacement,
        anomalies_graphe=graphe,
    )


def _classe(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in _CLASSES else None


def _nombre(value: Decimal | float | int | None) -> float | None:
    return None if value is None else float(value)


def _selectionne(doc: DpeDocumentAdresse) -> DocumentSelectionne:
    return DocumentSelectionne(
        numero=doc.numero_dpe,
        etiquette_dpe=_classe(doc.etiquette_dpe),
        etiquette_ges=_classe(doc.etiquette_ges),
        date_etablissement=doc.date_etablissement_dpe,
    )


def remplacement_verifie(statut: StatutDocument) -> bool:
    """Replaced according to the ADEME, without any anomaly of the graph.

    The one rule of « Remplacé selon l'ADEME » for the card, the selection
    and the document page (API WR-01, decision n° 24): any other replacement
    state, or a replacement with a graph anomaly, is « not verified ».
    """
    return statut.remplacement == "replaced" and not statut.anomalies_graphe


def remplacement_incertain(statut: StatutDocument) -> bool:
    """A replacement state that is neither « no successor » nor verified."""
    return not remplacement_verifie(statut) and (
        statut.remplacement != "no_successor_known" or bool(statut.anomalies_graphe)
    )


def motif_non_valide(statut: StatutDocument) -> MotifSelection:
    """Why a selected document is not valid, first reason first (D-111)."""
    if statut.methode == "ancienne":
        return "ancienne_methode"
    if statut.validite == "expired":
        return "expire"
    if remplacement_verifie(statut):
        return "remplace"
    if remplacement_incertain(statut):
        return "remplacement_incertain"
    return "validite_inconnue"


def choisir_plus_recent(
    docs: Sequence[DpeDocumentAdresse], as_of: date, *, complete: bool = True
) -> SelectionPlusRecent:
    """Most recent document of the whole population (D-109, D-111).

    Valid documents first (current method, not expired by date, no anomaly,
    not replaced); without one, the latest dated document that is neither
    future nor inconsistent, with the reason it is not valid. Several
    documents at the latest date: a tie, none chosen, all listed by number.
    The order of *docs* (the page order) never breaks a tie.
    """
    if not complete:
        return SelectionPlusRecent(etat="indeterminee", motif="population_incomplete")
    if not docs:
        return SelectionPlusRecent(etat="indeterminee", motif="aucun_document")
    statuts = [(doc, statut_document(doc, as_of)) for doc in docs]
    valides = [(doc, statut) for doc, statut in statuts if statut.eligible_valide]
    candidats = valides or [
        (doc, statut)
        for doc, statut in statuts
        if statut.etablissement is not None
        and statut.validite not in ("future_establishment", "inconsistent_dates")
    ]
    if not candidats:
        return SelectionPlusRecent(etat="indeterminee", motif="aucun_document_date")
    plus_recente = max(statut.etablissement for _doc, statut in candidats)
    top = sorted(
        (
            (doc, statut)
            for doc, statut in candidats
            if statut.etablissement == plus_recente
        ),
        key=lambda pair: pair[0].numero_dpe,
    )
    if len(top) > 1:
        return SelectionPlusRecent(
            etat="egalite",
            date=plus_recente,
            documents_ex_aequo=[_selectionne(doc) for doc, _statut in top],
            motif="document_valide" if valides else "aucun_document_valide",
        )
    doc, statut = top[0]
    return SelectionPlusRecent(
        etat="unique",
        date=plus_recente,
        document=_selectionne(doc),
        motif="document_valide" if valides else motif_non_valide(statut),
    )


def badges_statut(statut: StatutDocument) -> list[BadgeDocument]:
    """Badges of the three axes of a document (D-111): nothing served is hidden."""
    badges: list[BadgeDocument] = []
    if statut.methode == "ancienne":
        badges.append("ancienne_methode")
    if statut.validite == "expired":
        badges.append("expire")
    if statut.validite == "inconsistent_dates":
        badges.append("dates_incoherentes")
    if statut.validite == "future_establishment":
        badges.append("etablissement_futur")
    if remplacement_verifie(statut):
        badges.append("remplace")
    elif remplacement_incertain(statut):
        badges.append("remplacement_incertain")
    return badges


def badges_de_selection(selection: SelectionPlusRecent) -> dict[str, BadgeDocument]:
    """Badge of each document named by the selection, by source number."""
    if selection.etat == "unique" and selection.document is not None:
        return {selection.document.numero: "plus_recent"}
    return {
        doc.numero: "meme_date_plus_recente" for doc in selection.documents_ex_aequo
    }


def carte_document(
    doc: DpeDocumentAdresse,
    statut: StatutDocument,
    *,
    badges_selection: Iterable[BadgeDocument],
    adresse_declaree: AdresseDeclaree | None = None,
) -> DocumentDpeCarte:
    """Public card of one document: the source number as is (LIEU-03)."""
    return DocumentDpeCarte(
        numero=doc.numero_dpe,
        etiquette_dpe=_classe(doc.etiquette_dpe),
        etiquette_ges=_classe(doc.etiquette_ges),
        type_batiment=doc.type_batiment,
        surface_habitable=_nombre(doc.surface_habitable),
        date_etablissement=doc.date_etablissement_dpe,
        date_fin_validite=doc.date_fin_validite_dpe,
        badges=[*badges_selection, *badges_statut(statut)],
        adresse_declaree=adresse_declaree,
    )


def carte_adresse_brute(
    doc: DpeDocumentAdresse,
    statut: StatutDocument,
    *,
    adresse_declaree: AdresseDeclaree | None,
) -> DocumentDpeCarteAdresseBrute:
    """Card of a document attached by its exact raw address (plan 04.1-13).

    Status badges only: the most recent document and a tie belong to the
    ADEME key group, never to this one.
    """
    carte = carte_document(
        doc, statut, badges_selection=(), adresse_declaree=adresse_declaree
    )
    return DocumentDpeCarteAdresseBrute(**carte.model_dump())


@dataclass(frozen=True)
class FaitsAdresseDocument:
    """Private address facts of one document of the page; never serialized."""

    concordance: Concordance
    brute: AdresseBruteDocument


async def faits_adresse_documents(
    session: AsyncSession, ids: Sequence[int], departement: str
) -> dict[int, FaitsAdresseDocument]:
    """Concordance and raw address columns of the documents *ids*.

    Read in the snapshot of the page by :data:`FAITS_DOCUMENTS_SQL`; the raw
    texts only feed :func:`projeter_adresse_declaree`.
    """
    if not ids:
        return {}
    result = await session.execute(
        text(FAITS_DOCUMENTS_SQL), {"ids": list(ids), "department": departement}
    )
    faits: dict[int, FaitsAdresseDocument] = {}
    for ligne in result.mappings().all():
        concordance: Concordance = (
            concordance_adresse(FaitsConcordance.depuis_ligne(ligne))
            if ligne["cle_jointe"]
            else "non_evaluable"
        )
        faits[int(ligne["id"])] = FaitsAdresseDocument(
            concordance=concordance,
            brute=AdresseBruteDocument(
                adresse_brut=ligne["adresse_brut"],
                adresse_complete_brut=ligne["adresse_complete_brut"],
                complements={col: ligne[col] for col in COLONNES_COMPLEMENT},
            ),
        )
    return faits


def adresse_declaree_de_carte(
    faits: FaitsAdresseDocument | None, identite: IdentiteBan
) -> AdresseDeclaree | None:
    """Line 4 of a card: nothing for a strictly concordant document.

    Facts not read (a document absent from the facts) mask the field: a
    declared address is never published without its verification.
    """
    if faits is None:
        return AdresseDeclaree(etat="masquee", motif="non_verifiable_sans_complement")
    if faits.concordance == "concordant":
        return None
    return projeter_adresse_declaree(faits.brute, identite)


def jeton_generation(generations: DpeRelationGenerations) -> str:
    """Opaque token of the snapshot: the identities of both parent relations."""
    raw = f"{generations.addresses}:{generations.diagnostics}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def departement_de_cle(ban_id: str) -> str:
    """Partition of a BAN key: three characters overseas, else two."""
    return ban_id[:3] if ban_id.startswith("97") else ban_id[:2]


async def _documents_et_selection(
    session: AsyncSession, ban_id: str, departement: str, as_of: date
) -> tuple[queries.DpeInventoryAggregate, SelectionPlusRecent]:
    agregat = await queries.dpe_documents_adresse_agregat(
        session, ban_id=ban_id, department=departement
    )
    candidats, tronque = await queries.dpe_documents_adresse_candidats(
        session, ban_id=ban_id, department=departement, limit=CANDIDATS_MAX
    )
    return agregat, choisir_plus_recent(candidats, as_of, complete=not tronque)


async def repere_documents(
    session: AsyncSession, ban_id: str, hosting: RouteHosting, as_of: date
) -> RepereDpe:
    """Landmark ①: declared DPE count and most recent, in its guarded block."""
    commun = {
        "field_id": FIELD_ID_NOMBRE,
        "source": source_valeur("dpe"),
        "rattachement": RATTACHEMENT,
    }
    dpe = hosting.hostings["dpe"]
    if dpe.etat != "heberge":
        return RepereDpe(etat="out_of_coverage", motif=dpe.motif, **commun)
    departement = departement_de_cle(ban_id)

    async def lecteur():
        agregat, selection = await _documents_et_selection(
            session, ban_id, departement, as_of
        )
        return agregat, selection, await date_import_dpe(session)

    bloc = await lire_bloc(session, ["dpe"], lecteur)
    if bloc.en_panne:
        return RepereDpe(etat="temporary_error", motif=bloc.panne, **commun)
    agregat, selection, date_import = bloc.valeur
    # Dated by the import the list serves as `date_import` (C14, SEM-02).
    return RepereDpe(
        etat="value",
        valeur=agregat.total,
        selection=selection,
        **commun,
        periode=date_import.isoformat() if date_import is not None else None,
    )


async def _demarrer_instantane(session: AsyncSession) -> DpeRelationGenerations:
    """Same snapshot as the DPE inventory (``inventory_service``).

    The isolation level is set before any read of the new transaction, the
    publication is admitted, then both parents are locked and identified.
    """
    await session.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )
    await guard_snapshot_publication(session, ["dpe"])
    return await queries.lock_dpe_inventory_relations(session)


# Every BAN row of the key, bounded to its partition (as the identity reads
# it); the point stays internal and only feeds the parcel relation, the
# identity fields only feed the declared-address projection and the
# coherence check of the D-110 finding.
_POINTS_DE_LA_CLE = text("""
    SELECT ST_X(position) AS lon, ST_Y(position) AS lat,
           numero, rep, nom_voie, code_postal, code_commune, nom_commune
    FROM public.adresses
    WHERE ban_id = :ban_id AND code_departement = :department
""")
# Identity fields of a BAN row, as ``app.adresse.service`` compares them.
_CHAMPS_IDENTITE = (
    "numero",
    "rep",
    "nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
)


def identite_ban(rows: Sequence) -> IdentiteBan:
    """Street, commune and postcode of the key; unknown when its rows disagree."""
    voies = {row.nom_voie for row in rows}
    communes = {row.nom_commune for row in rows}
    codes = {row.code_postal for row in rows}
    return IdentiteBan(
        nom_voie=voies.pop() if len(voies) == 1 else None,
        nom_commune=communes.pop() if len(communes) == 1 else None,
        code_postal=codes.pop() if len(codes) == 1 else None,
    )


def identite_coherente(rows: Sequence) -> bool:
    """True when every BAN row of the key agrees on every identity field.

    Rows that disagree give an undetermined identity on the address page
    (``app.adresse.service``); nothing that compares a document with « the »
    BAN address may then be served (API WR-04, D-15).
    """
    return bool(rows) and all(
        len({getattr(row, champ) for row in rows}) == 1 for champ in _CHAMPS_IDENTITE
    )


def point_unique(rows: Sequence) -> PointBan | None:
    """The single BAN point of a key; None when absent or contradictory."""
    points = {(row.lon, row.lat) for row in rows}
    if len(points) != 1:
        return None
    lon, lat = points.pop()
    return None if lon is None or lat is None else PointBan(lon=lon, lat=lat)


async def signal_plusieurs_logements(
    session: AsyncSession, point: PointBan | None, hosting: RouteHosting
) -> SignalPlusieursLogements | None:
    """Registered condominium on the ``unique`` parcel of the point (LIEU-02).

    ``None`` without a single point, without the registry hosted, when the
    parcel is not ``unique`` or carries no registered condominium: a signal
    is never derived from a nearby or chosen parcel (N-02).
    """
    source = source_valeur("copropriete")
    if point is None or hosting.hostings["copropriete"].etat != "heberge":
        return None
    relation = await resoudre_parcelle(session, point, hosting)
    if relation.etat == "indisponible":
        return SignalPlusieursLogements(etat="temporary_error", source=source)
    if relation.etat != "unique" or relation.id_parcelle is None:
        return None
    id_parcelle = relation.id_parcelle

    async def lecteur():
        return await copropriete_par_parcelle(session, id_parcelle)

    bloc = await lire_bloc(session, ["copropriete"], lecteur)
    if bloc.en_panne:
        return SignalPlusieursLogements(etat="temporary_error", source=source)
    if not bloc.valeur:
        return None
    registre = bloc.valeur
    lots = registre[0]["nb_lots_habitation_strict"] if len(registre) == 1 else None
    return SignalPlusieursLogements(
        etat="value",
        motif="copropriete_immatriculee",
        nombre_lots_habitation=lots,
        source=source,
    )


# --- D-110 finding (DPE-01 option-d, P-D110-NULL, Ianis 2026-10-02) ----------

CRITERES_D110: tuple[CritereD110, ...] = (
    "maison",
    "document_unique_cle",
    "concordance_stricte",
    "batiment_unique_parcelle_du_point",
    "annexes_exclues_usage_inconnu_compte",
)
SOURCES_D110 = ("dpe", "ban", "cadastre", "batiments")


@dataclass(frozen=True)
class FaitsD110:
    """Facts the finding is decided on; ``None`` means not read or unknown."""

    type_batiment: str | None
    documents_presents: int
    documents_de_la_cle: int | None
    concordance: Concordance | None
    relation: RelationParcelle | None
    batiments: BatimentsParcelle | None


def constat_d110(faits: FaitsD110) -> ConstatD110 | None:
    """The finding with its criteria, or None as soon as one is false or unknown.

    (a) a house; (b) the only document of the BAN key, retired documents
    included (N-11) and present; (c) strict concordance of its raw address
    (``app.dpe.concordance``); (d) one parcel containing the BAN point, by
    the point rule only (never ``cad_parcelles``, a nearest parcel, an RNB
    link or a proximity); (e) exactly one building on that parcel that is
    not an explicit annex, an unknown use counting as a building.
    """
    if (faits.type_batiment or "").strip().lower() != "maison":
        return None
    if faits.documents_presents != 1 or faits.documents_de_la_cle != 1:
        return None
    if faits.concordance != "concordant":
        return None
    relation = faits.relation
    if (
        relation is None
        or relation.etat != "unique"
        or relation.rattachement != "parcelle_du_point"
        or relation.id_parcelle is None
    ):
        return None
    if faits.batiments is None or faits.batiments.hors_annexe != 1:
        return None
    return ConstatD110(
        criteres=list(CRITERES_D110),
        sources=[source_valeur(cle) for cle in SOURCES_D110],
    )


# Every document of the key, retired ones included (« seul document de la
# clé », N-11, as ``n_docs`` of the measurement script).
_DOCUMENTS_DE_LA_CLE = text("""
    SELECT count(*) AS total
    FROM public.diagnostics_dpe AS d
    WHERE d.identifiant_ban = :ban_id AND d.code_departement = :department
""")


async def lire_constat_d110(
    session: AsyncSession,
    *,
    ban_id: str,
    departement: str,
    total: int,
    documents: Sequence[DpeDocumentAdresse],
    faits_adresse: dict[int, FaitsAdresseDocument],
    point: PointBan | None,
    hosting: RouteHosting,
    identite_coherente: bool,
) -> ConstatD110 | None:
    """Read the facts of the finding, each in its own guarded block.

    The reads stop at the first criterion that fails: a page that cannot
    carry the finding reads neither the parcel nor the buildings. BAN rows
    that disagree (an undetermined identity, even around one point) carry no
    finding: the concordance would read one row chosen by its id.
    """
    if not identite_coherente:
        return None
    if total != 1 or len(documents) != 1:
        return None
    document = documents[0]
    faits = faits_adresse.get(document.document_id)
    concordance = faits.concordance if faits else None
    # Early exits only; the decision is :func:`constat_d110` below.
    if (document.type_batiment or "").strip().lower() != "maison":
        return None
    if concordance != "concordant" or point is None:
        return None
    if hosting.hostings["batiments"].etat != "heberge":
        return None

    async def compter_documents() -> int:
        result = await session.execute(
            _DOCUMENTS_DE_LA_CLE, {"ban_id": ban_id, "department": departement}
        )
        return int(result.scalar_one())

    compte = await lire_bloc(session, ["dpe"], compter_documents)
    if compte.en_panne or compte.valeur != 1:
        return None
    relation = await resoudre_parcelle(session, point, hosting)
    if relation.etat != "unique" or relation.id_parcelle is None:
        return None
    id_parcelle = relation.id_parcelle

    async def compter_batiments() -> BatimentsParcelle | None:
        return await batiments_sur_parcelle(session, id_parcelle)

    batiments = await lire_bloc(session, ["batiments"], compter_batiments)
    return constat_d110(
        FaitsD110(
            type_batiment=document.type_batiment,
            documents_presents=total,
            documents_de_la_cle=compte.valeur,
            concordance=concordance,
            relation=relation,
            batiments=None if batiments.en_panne else batiments.valeur,
        )
    )


@dataclass(frozen=True)
class GroupeAdresseBrute:
    """The raw-address group of one page (plan 04.1-13), never serialized as is.

    ``calcule`` false: the served generation was imported without the
    attachment; ``total`` is then None (a gap), never 0.
    """

    calcule: bool
    total: int | None = None
    documents: Sequence[DpeDocumentAdresse] = ()
    faits: dict[int, FaitsAdresseDocument] = field(default_factory=dict)


async def lire_groupe_adresse_brute(
    session: AsyncSession, ban_id: str, departement: str
) -> GroupeAdresseBrute:
    """Total and most recent cards attached to *ban_id* by raw address.

    Read in the snapshot of the page, inside its guarded block: a failure
    propagates to the caller, which raises the detached outage (C59 B1).
    """
    if not await queries.dpe_adresse_brute_calculee(session):
        return GroupeAdresseBrute(calcule=False)
    total, documents = await queries.dpe_documents_adresse_brute(
        session, ban_id=ban_id, department=departement, limit=ADRESSE_BRUTE_MAX
    )
    faits = await faits_adresse_documents(
        session, [doc.document_id for doc in documents], departement
    )
    return GroupeAdresseBrute(
        calcule=True, total=total, documents=documents, faits=faits
    )


async def date_import_dpe(session: AsyncSession) -> date | None:
    """Day (Paris) of the last successful DPE import; None if unknown."""
    return jour_d_import(await faits_de_provenance(session, "dpe"))


async def lire_documents_adresse(
    session: AsyncSession,
    ban_id: str,
    page: int,
    generation: str | None,
    hosting: RouteHosting,
    as_of: date,
) -> DocumentsDpeAdresse | None:
    """Page *page* of the documents declared at *ban_id*, under one snapshot.

    Returns ``None`` for a key absent from the BAN. Raises
    :class:`GenerationChangeeError`, :class:`PageHorsLimitesError` or
    :class:`DocumentsIndisponiblesError`. Nothing is read when the BAN or
    the DPE is out of coverage for the departement of the key.
    """
    source = source_valeur("dpe")
    for cle in ("ban", "dpe"):
        hebergement = hosting.hostings[cle]
        if hebergement.etat != "heberge":
            return DocumentsDpeAdresse(
                etat="out_of_coverage",
                motif=hebergement.motif,
                source=source,
                page=page,
                par_page=PAR_PAGE,
            )
    departement = departement_de_cle(ban_id)
    # Leave the scope read of the dependencies: the snapshot must be the
    # first statement of its own transaction.
    await session.rollback()
    # The outage is raised after the try, detached from the database error
    # (C59 B1): never ``from exc``.
    indisponible = False
    try:
        generations = await _demarrer_instantane(session)
        jeton = jeton_generation(generations)
        if generation is not None and generation != jeton:
            raise GenerationChangeeError
        cle = await session.execute(
            _POINTS_DE_LA_CLE, {"ban_id": ban_id, "department": departement}
        )
        rows = cle.all()
        if not rows:
            return None
        agregat, selection = await _documents_et_selection(
            session, ban_id, departement, as_of
        )
        pages = nombre_pages(agregat.total)
        if page > pages:
            raise PageHorsLimitesError
        documents = await queries.dpe_documents_adresse_chronologiques(
            session,
            ban_id=ban_id,
            department=departement,
            limit=PAR_PAGE,
            offset=decalage(page),
        )
        faits_adresse = await faits_adresse_documents(
            session, [doc.document_id for doc in documents], departement
        )
        date_import = await date_import_dpe(session)
        point = point_unique(rows)
        signal = await signal_plusieurs_logements(session, point, hosting)
        constat = await lire_constat_d110(
            session,
            ban_id=ban_id,
            departement=departement,
            total=agregat.total,
            documents=documents,
            faits_adresse=faits_adresse,
            point=point,
            hosting=hosting,
            identite_coherente=identite_coherente(rows),
        )
        brute = await lire_groupe_adresse_brute(session, ban_id, departement)
    except (HTTPException, SQLAlchemyError) as exc:
        if isinstance(exc, HTTPException) and exc.status_code != 503:
            raise
        logger.warning("documents DPE indisponibles (%s)", describe_read_failure(exc))
        indisponible = True
    if indisponible:
        raise DocumentsIndisponiblesError from None
    selection_badges = badges_de_selection(selection)
    identite = identite_ban(rows)
    cartes = []
    for doc in documents:
        badge = selection_badges.get(doc.numero_dpe)
        cartes.append(
            carte_document(
                doc,
                statut_document(doc, as_of),
                badges_selection=(badge,) if badge else (),
                adresse_declaree=adresse_declaree_de_carte(
                    faits_adresse.get(doc.document_id), identite
                ),
            )
        )
    cartes_brute = [
        carte_adresse_brute(
            doc,
            statut_document(doc, as_of),
            adresse_declaree=adresse_declaree_de_carte(
                brute.faits.get(doc.document_id), identite
            ),
        )
        for doc in brute.documents
    ]
    return DocumentsDpeAdresse(
        etat="value",
        source=source,
        total=agregat.total,
        page=page,
        pages=pages,
        par_page=PAR_PAGE,
        generation=jeton,
        distribution=DpeInventoryDistribution.model_validate(agregat.distribution),
        date_import=date_import,
        documents=cartes,
        selection=selection,
        signal_plusieurs_logements=signal,
        constat_d110=constat,
        etat_adresse_brute="value" if brute.calcule else "not_available",
        total_adresse_brute=brute.total,
        documents_adresse_brute=cartes_brute,
    )

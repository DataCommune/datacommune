"""Public DPE document read by its source number (04-04, D-102, LIEU-03).

``GET /v1/dpe/{numero}`` serves what one document declares, where it comes
from, whether it is in force and at which address the source attaches it
(contract V3 of ``docs/audit-recentrage-2026-09-10/CONTRAT-DPE-DOCUMENT.md``).
Consulting a document attributes it to nothing: no dwelling, sale or commune
datum is read or served here.

The number is read on every hosted departement of the instance: 3 544
numbers do not encode the departement of their partition (block E of
``04-MESURE-DPE.md``), so no partition is ever excluded on the digits of the
number alone. A retired document (tombstone) keeps its number and its known
regimes; its descriptive values stay purged (``inventory_service._minimize``).

Method, calendar validity and replacement stay three axes (D-111): they are
read by :func:`app.adresse.documents.statut_document` (``validity_at`` of
``app.dpe.lifecycle``) and only then worded as one status code, through an
exhaustive table without any « in force » fallback (R02-05/6). The declared
address goes through the single public projection of 04-19
(``projeter_adresse_declaree``); the raw columns never leave this module.

The schema lives here rather than in ``app.dpe.schemas``: it reuses the value
model of the address page (``app.adresse.schemas``), which already imports
``app.dpe.schemas``.
"""

import datetime as dt
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import source_valeur
from app.adresse.documents import (
    Methode,
    date_import_dpe,
    faits_adresse_documents,
    methode_document,
    remplacement_incertain,
    remplacement_verifie,
    statut_document,
)
from app.adresse.schemas import AdresseDeclaree, CommuneRef, ValeurLieu
from app.adresse.service import libelle_adresse
from app.common.schemas import OutOfCoverageReason, ProblemDetail
from app.common.semantic_contract import get_field
from app.dpe import queries
from app.dpe.concordance import Concordance
from app.dpe.inventory_service import _lifecycle_source, _minimize
from app.dpe.lifecycle import Validity
from app.dpe.public_projection import (
    COLONNES_COMPLEMENT,
    AdresseBruteDocument,
    IdentiteBan,
    projeter_adresse_declaree,
)
from app.dpe.queries import DpeDocumentAdresse, DpeInventoryRecord
from app.dpe.references import paris_today
from app.scope.dependencies import RouteHosting

# Default ADEME number form (block E): thirteen upper-case alphanumerics.
NUMERO_DPE_PATTERN = r"^[0-9A-Z]{13}$"
NUMERO_DPE_RE = re.compile(NUMERO_DPE_PATTERN)

DatasetDpe = Literal["dpe03existant", "dpe02neuf"]
# Contract V3: catalogue constants of the closed dataset enum; no URL is ever
# read from a free text of the source.
URLS_JEUX: dict[str, str] = {
    "dpe03existant": "https://data.ademe.fr/datasets/dpe03existant",
    "dpe02neuf": "https://data.ademe.fr/datasets/dpe02neuf",
}
_JEU_DU_REGIME: dict[str, DatasetDpe] = {
    "existant": "dpe03existant",
    "neuf": "dpe02neuf",
}
RATTACHEMENT = "adresse_saisie_diagnostiqueur"
_CLASSES = frozenset("ABCDEFG")

CodeStatut = Literal[
    "en_vigueur",
    "expire",
    "remplace",
    "remplacement_non_verifie",
    "ancienne_methode",
    "validite_inconnue",
    "dates_incoherentes",
    "etablissement_futur",
    "retire",
]
EtatAdresseBan = Literal[
    "retenue", "sans_cle", "cle_inconnue", "hors_couverture", "indisponible"
]


# --- public schema -----------------------------------------------------------


class SourceJeu(BaseModel):
    """One ADEME dataset of the document and its catalogue page."""

    model_config = ConfigDict(extra="forbid")

    dataset: DatasetDpe
    url: str

    @model_validator(mode="after")
    def _url_du_catalogue(self) -> "SourceJeu":
        if self.url != URLS_JEUX[self.dataset]:
            raise ValueError("source URL must be the catalogue constant")
        return self


class SourceIdentity(BaseModel):
    """Contract V3: the ADEME number as is and the established datasets."""

    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1)
    sources: list[SourceJeu] = Field(max_length=2)


class DatesDocument(BaseModel):
    """Establishment and end-of-validity dates declared by the document."""

    model_config = ConfigDict(extra="forbid")

    etablissement: dt.date | None = None
    fin_validite: dt.date | None = None


class RepereEtiquette(BaseModel):
    """A label of the document: its class and the figures it is read from.

    ``mesure``: the yearly total of the document (kWhEP/an, kg CO2 eq/an).
    ``mesure_m2``: the per-square-metre value published by the ADEME, the one
    compared with the class (C51, D-127); added next to the total so that the
    current page and both comparators keep reading ``mesure`` unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    classe: ValeurLieu
    mesure: ValeurLieu
    mesure_m2: ValeurLieu | None = None


class RepereDescription(BaseModel):
    """What the document describes: building type and declared surface."""

    model_config = ConfigDict(extra="forbid")

    type_batiment: ValeurLieu
    surface: ValeurLieu


class ReperesDocument(BaseModel):
    """The four landmarks of the document page (UI-SPEC « /dpe/[numero] »)."""

    model_config = ConfigDict(extra="forbid")

    etiquette_energie: RepereEtiquette
    etiquette_climat: RepereEtiquette
    logement_decrit: RepereDescription
    cout_annuel: ValeurLieu


class StatutPublic(BaseModel):
    """One status code of the three axes, without any « in force » fallback.

    ``date``: end of validity, for ``expire`` only. Code ``remplace``: verified
    replacement (the lifecycle says replaced, no graph issue). ``remplace_par``:
    its successor, only when a single successor row is served (absent
    otherwise, decision n° 24: « Remplacé selon l'ADEME »).
    ``remplace``: the number this document declares it replaces, as is.
    """

    model_config = ConfigDict(extra="forbid")

    code: CodeStatut
    # The calendar axis as read (``validity_at``), kept apart from the code so
    # that an expired document stays « Expiré » whatever its replacement.
    validite: Validity | None = None
    date: dt.date | None = None
    remplace_par: str | None = None
    remplace: str | None = None


class AdresseBanDocument(BaseModel):
    """BAN address the source attaches the document to (key equality).

    ``retenue``: the key exists in the hosted BAN (``libelle`` is None when
    its rows disagree). ``sans_cle``: the document carries no key.
    ``cle_inconnue``: the key is absent from the hosted BAN.
    ``hors_couverture``: the BAN is not hosted for the departement.
    ``indisponible``: the BAN read failed; the document is still served.
    """

    model_config = ConfigDict(extra="forbid")

    etat: EtatAdresseBan
    ban_id: str | None = None
    libelle: str | None = None
    numero: str | None = None
    rep: str | None = None
    nom_voie: str | None = None
    commune: CommuneRef | None = None
    motif: OutOfCoverageReason | None = None


class ProvenanceDocument(BaseModel):
    """Producer and day (Paris) of the last successful DPE import."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["ADEME"] = "ADEME"
    date_import: dt.date | None = None


class DocumentDpePublic(BaseModel):
    """One DPE document, read by its source number; attributed to nothing.

    ``disponibilite=retire``: the source no longer publishes the document
    (tombstone); only its number, its datasets, its status and provenance
    remain. ``adresse_divergente`` is ``True`` only when the raw address is
    strictly divergent from its BAN key (``app.dpe.concordance``), ``False``
    only when it is strictly concordant, ``None`` when the concordance cannot
    be read or is not evaluable (a range, a BAN suffix, no street...).
    """

    model_config = ConfigDict(extra="forbid")

    numero: str
    source_identity: SourceIdentity
    disponibilite: Literal["disponible", "retire"]
    methode: Methode
    statut: StatutPublic
    dates: DatesDocument | None = None
    reperes: ReperesDocument | None = None
    caracteristiques: list[ValeurLieu] = Field(default_factory=list)
    adresse_declaree: AdresseDeclaree | None = None
    adresse_ban: AdresseBanDocument | None = None
    adresse_divergente: bool | None = None
    provenance: ProvenanceDocument
    association: Literal["source_ban_equality"] = "source_ban_equality"
    address_check: Literal["unverified"] = "unverified"

    @model_validator(mode="after")
    def _retire_sans_descriptif(self) -> "DocumentDpePublic":
        if self.source_identity.reference != self.numero:
            raise ValueError("the source reference is the number itself")
        retire = self.disponibilite == "retire"
        if retire != (self.statut.code == "retire"):
            raise ValueError("a retired document has the retired status only")
        if retire and (
            self.dates is not None
            or self.reperes is not None
            or self.caracteristiques
            or self.adresse_declaree is not None
            or self.adresse_ban is not None
            or self.adresse_divergente is not None
        ):
            raise ValueError("a retired document carries no description")
        return self


class CouvertureDocument(BaseModel):
    """Departements whose imported DPE base this instance serves."""

    departements: list[str]


class ProblemeDocumentDpe(ProblemDetail):
    """RFC 9457 problem of the document route: constant, never reflects input."""

    code: Literal[
        "dpe_inconnu",
        "numero_mal_forme",
        "document_dpe_indisponible",
        "numeros_invalides",
        "comparaison_dpe_indisponible",
    ]
    couverture: CouvertureDocument | None = None


# --- reading -------------------------------------------------------------------


class DpeInconnuError(Exception):
    """No document carries the number in the served departements."""

    def __init__(self, departements: list[str]) -> None:
        super().__init__("dpe_inconnu")
        self.departements = departements


class DocumentIndisponibleError(Exception):
    """The read failed, or the number is not unique (never chosen)."""


class BanIndisponibleError(Exception):
    """The BAN read of the document key failed."""


# Per-square-metre values published by the ADEME (C51, migration 100): read
# here rather than in ``DpeInventoryRecord``, so that the inventory and the
# purge of retired documents (``inventory_service._minimize``) stay as they are.
_COLONNES_PAR_M2 = ("conso_5_usages_par_m2_ep", "emission_ges_5_usages_par_m2")
# The raw address columns only feed the public projection; never serialized.
# The per-square-metre columns only feed the landmarks of a served document.
_COLONNES_PRIVEES = ", ".join(
    f"d.{colonne}"
    for colonne in (
        "code_departement",
        "identifiant_ban",
        "adresse_brut",
        "adresse_complete_brut",
        *COLONNES_COMPLEMENT,
        *_COLONNES_PAR_M2,
    )
)
_DOCUMENT_PAR_NUMERO = text(f"""
    SELECT {queries._INVENTORY_SELECT_COLUMNS},
           {_COLONNES_PRIVEES}
    FROM public.diagnostics_dpe AS d
    WHERE d.numero_dpe = :numero
      AND d.code_departement = ANY(CAST(:departements AS text[]))
    LIMIT 2
""")
# Successor declared by the ADEME (``numero_dpe_remplace`` of the new one).
_REMPLACANTS = text("""
    SELECT DISTINCT d.numero_dpe
    FROM public.diagnostics_dpe AS d
    WHERE d.numero_dpe_remplace = :numero
      AND d.code_departement = ANY(CAST(:departements AS text[]))
    LIMIT 2
""")
# Every BAN row of the key within its partition; no point is read.
_LIGNES_BAN = text("""
    SELECT numero, rep, nom_voie, code_postal, code_commune, nom_commune
    FROM public.adresses
    WHERE ban_id = :ban_id AND code_departement = :departement
    ORDER BY id
""")
_IDENTITE_BAN = (
    "numero",
    "rep",
    "nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
)


def jeux_sources(record: DpeInventoryRecord) -> list[SourceJeu]:
    """Datasets established by the validated observations (contract V3).

    Same rule as the inventory provenance (``_lifecycle_source``): validated
    observations first, the explicit legacy regime without observation, the
    order existing then new. An incoherent provenance establishes nothing.
    """
    try:
        regimes = _lifecycle_source(record)["regimes"]
    except (ValueError, TypeError, KeyError):
        return []
    return [
        SourceJeu(dataset=_JEU_DU_REGIME[r], url=URLS_JEUX[_JEU_DU_REGIME[r]])
        for r in regimes
    ]


def _retire(record: DpeInventoryRecord) -> bool:
    return bool(record.is_tombstone) or record.source_state in (
        "absent_from_complete_scope",
        "explicitly_deactivated",
    )


def _vide(valeur: Any) -> bool:
    return valeur is None or (isinstance(valeur, str) and not valeur.strip())


def valeur_document(field_id: str, valeur: str | float | None) -> ValeurLieu:
    """A declared value; an empty field of an existing document is not reported."""
    commun = {
        "field_id": field_id,
        "source": source_valeur("dpe"),
        "rattachement": RATTACHEMENT,
    }
    if _vide(valeur):
        return ValeurLieu(etat="not_reported", **commun)
    return ValeurLieu(
        etat="value", valeur=valeur, unite=get_field(field_id).unit, **commun
    )


def _classe(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in _CLASSES else None


def _nombre(value: Decimal | float | int | None) -> float | None:
    return None if value is None else float(value)


def reperes_document(
    record: DpeInventoryRecord, par_m2: Mapping[str, Any] | None = None
) -> ReperesDocument:
    """Landmarks as declared: nothing computed, an empty field stays empty.

    ``par_m2``: the per-square-metre columns of the document row
    (``_COLONNES_PAR_M2``). A missing one is not reported, never zero and
    never the total divided by the surface (C51).
    ④ is ``cout_total_5_usages`` as is: one value, the source serving no
    range (no range is ever made up).
    """
    par_m2 = par_m2 or {}
    return ReperesDocument(
        etiquette_energie=RepereEtiquette(
            classe=valeur_document(
                "dpe.document.etiquette_dpe", _classe(record.etiquette_dpe)
            ),
            mesure=valeur_document(
                "dpe.document.conso_ep", _nombre(record.conso_5_usages_ep)
            ),
            mesure_m2=valeur_document(
                "dpe.document.conso_ep_m2",
                _nombre(par_m2.get("conso_5_usages_par_m2_ep")),
            ),
        ),
        etiquette_climat=RepereEtiquette(
            classe=valeur_document(
                "dpe.document.etiquette_ges", _classe(record.etiquette_ges)
            ),
            mesure=valeur_document(
                "dpe.document.emissions_ges", _nombre(record.emission_ges_5_usages)
            ),
            mesure_m2=valeur_document(
                "dpe.document.emissions_ges_m2",
                _nombre(par_m2.get("emission_ges_5_usages_par_m2")),
            ),
        ),
        logement_decrit=RepereDescription(
            type_batiment=valeur_document(
                "dpe.document.type_batiment", record.type_batiment
            ),
            surface=valeur_document(
                "dpe.document.surface_habitable", _nombre(record.surface_habitable)
            ),
        ),
        cout_annuel=valeur_document(
            "dpe.document.cout_annuel", _nombre(record.cout_total_5_usages)
        ),
    )


def caracteristiques_document(record: DpeInventoryRecord) -> list[ValeurLieu]:
    """The served columns « Ce que déclare le document », in the page order."""
    return [
        valeur_document(field_id, valeur)
        for field_id, valeur in (
            ("dpe.document.type_batiment", record.type_batiment),
            ("dpe.document.surface_habitable", _nombre(record.surface_habitable)),
            ("dpe.document.periode_construction", record.periode_construction),
            (
                "dpe.document.energie_chauffage",
                record.type_energie_principale_chauffage,
            ),
            ("dpe.document.installation_chauffage", record.type_installation_chauffage),
            ("dpe.document.isolation", record.qualite_isolation_enveloppe),
        )
    ]


def _document_adresse(record: DpeInventoryRecord) -> DpeDocumentAdresse:
    return DpeDocumentAdresse(
        document_id=record.document_id,
        numero_dpe=record.dpe_number,
        type_batiment=record.type_batiment,
        surface_habitable=record.surface_habitable,
        etiquette_dpe=record.etiquette_dpe,
        etiquette_ges=record.etiquette_ges,
        date_etablissement_dpe=record.date_etablissement_dpe,
        date_fin_validite_dpe=record.date_fin_validite_dpe,
        lifecycle_facts=record.lifecycle_facts,
    )


def remplacement_a_verifier(record: DpeInventoryRecord, as_of: dt.date) -> bool:
    """Only a document the lifecycle says replaced, without graph issue."""
    return remplacement_verifie(statut_document(_document_adresse(record), as_of))


def statut_validite(
    record: DpeInventoryRecord, as_of: dt.date, remplacant: str | None
) -> StatutPublic:
    """The status code of the three axes (exhaustive table, R02-05/6).

    Inconsistent or future dates first (no validity can be read), then a
    verified replacement (``remplace_par`` when its successor row is served,
    absent otherwise: decision n° 24, the same rule as the card and the
    selection, API WR-01), an expiry by date, an uncertain replacement, the
    former method, an unknown validity; « en_vigueur » only for a document of
    the current method, not expired by date, without known successor nor
    graph issue (``app.adresse.documents.StatutDocument.eligible_valide``).
    """
    statut = statut_document(_document_adresse(record), as_of)
    remplace = record.replaced_dpe_number
    axes = {"remplace": remplace, "validite": statut.validite}
    if statut.validite == "inconsistent_dates":
        return StatutPublic(code="dates_incoherentes", **axes)
    if statut.validite == "future_establishment":
        return StatutPublic(code="etablissement_futur", **axes)
    if remplacement_verifie(statut):
        return StatutPublic(code="remplace", remplace_par=remplacant, **axes)
    if statut.validite == "expired":
        return StatutPublic(code="expire", date=record.date_fin_validite_dpe, **axes)
    if remplacement_incertain(statut):
        return StatutPublic(code="remplacement_non_verifie", **axes)
    if statut.methode == "ancienne":
        return StatutPublic(code="ancienne_methode", **axes)
    if not statut.eligible_valide:
        return StatutPublic(code="validite_inconnue", **axes)
    return StatutPublic(code="en_vigueur", **axes)


async def lire_remplacant(
    session: AsyncSession, numero: str, departements: list[str]
) -> str | None:
    """The single successor row of *numero*; None when absent, several or unread."""
    try:
        async with session.begin_nested():
            result = await session.execute(
                _REMPLACANTS, {"numero": numero, "departements": departements}
            )
            numeros = list(result.scalars().all())
    except SQLAlchemyError:
        return None
    return numeros[0] if len(numeros) == 1 else None


async def lire_lignes_ban(
    session: AsyncSession, ban_id: str, departement: str
) -> list[dict]:
    """BAN rows of the document key, in their own savepoint."""
    # Raised after the try, detached from the database error, whose message
    # can quote the key (C60): never ``from exc``.
    try:
        async with session.begin_nested():
            result = await session.execute(
                _LIGNES_BAN, {"ban_id": ban_id, "departement": departement}
            )
            return [dict(row) for row in result.mappings().all()]
    except SQLAlchemyError:
        pass
    raise BanIndisponibleError


def adresse_ban_de_lignes(ban_id: str, lignes: list[Mapping]) -> AdresseBanDocument:
    """The retained BAN address; a field its rows disagree on stays None."""
    if not lignes:
        return AdresseBanDocument(etat="cle_inconnue")
    commun = {
        champ: lignes[0][champ]
        if len({ligne[champ] for ligne in lignes}) == 1
        else None
        for champ in _IDENTITE_BAN
    }
    coherent = all(
        len({ligne[champ] for ligne in lignes}) == 1 for champ in _IDENTITE_BAN
    )
    return AdresseBanDocument(
        etat="retenue",
        ban_id=ban_id,
        libelle=libelle_adresse(commun) if coherent else None,
        numero=commun["numero"],
        rep=commun["rep"],
        nom_voie=commun["nom_voie"],
        commune=CommuneRef(
            code_insee=commun["code_commune"] or ban_id.split("_", 1)[0],
            nom=commun["nom_commune"],
        ),
    )


async def _concordance(
    session: AsyncSession, document_id: int, departement: str
) -> Concordance | None:
    try:
        async with session.begin_nested():
            faits = await faits_adresse_documents(session, [document_id], departement)
    except SQLAlchemyError:
        return None
    fait = faits.get(document_id)
    return fait.concordance if fait else None


async def rapprochement_ban(
    session: AsyncSession, ligne: Mapping, hosting: RouteHosting
) -> tuple[AdresseBanDocument, IdentiteBan, Concordance | None]:
    """BAN address of the key, the identity the projection checks, the match.

    Nothing is read in the BAN for a departement it does not cover; a failed
    read types the match, never the document (block E).
    """
    inconnue = IdentiteBan(nom_voie=None)
    ban_id = ligne.get("identifiant_ban")
    departement = ligne.get("code_departement")
    if _vide(ban_id) or _vide(departement):
        return AdresseBanDocument(etat="sans_cle"), inconnue, None
    ban = hosting.hostings["ban"]
    if ban.etat != "heberge":
        return (
            AdresseBanDocument(etat="hors_couverture", motif=ban.motif),
            inconnue,
            None,
        )
    if departement not in ban.departements:
        # The departement is served for the DPE, hence in the scope: the BAN
        # source does not cover it.
        motif = "source_sans_couverture"
        return AdresseBanDocument(etat="hors_couverture", motif=motif), inconnue, None
    try:
        lignes = await lire_lignes_ban(session, ban_id, departement)
    except BanIndisponibleError:
        return AdresseBanDocument(etat="indisponible"), inconnue, None
    adresse = adresse_ban_de_lignes(ban_id, lignes)
    if adresse.etat != "retenue":
        return adresse, inconnue, None
    codes = {ligne["code_postal"] for ligne in lignes}
    identite = IdentiteBan(
        nom_voie=adresse.nom_voie,
        nom_commune=adresse.commune.nom if adresse.commune else None,
        code_postal=codes.pop() if len(codes) == 1 else None,
    )
    concordance = await _concordance(session, int(ligne["id"]), departement)
    return adresse, identite, concordance


def divergence(concordance: Concordance | None) -> bool | None:
    """``True`` / ``False`` for a decided concordance only (API WR-05).

    ``non_evaluable`` is not a concordance: served as unknown, never as
    « not divergent ».
    """
    return {"ecart": True, "concordant": False}.get(concordance or "")


async def lire_document_public(
    session: AsyncSession,
    numero: str,
    hosting: RouteHosting,
    as_of: dt.date | None = None,
) -> DocumentDpePublic:
    """The public projection of the document *numero*.

    Raises :class:`DpeInconnuError` (with the served departements, empty
    when the DPE is not hosted) and :class:`DocumentIndisponibleError`.
    """
    as_of = as_of or paris_today()
    dpe = hosting.hostings["dpe"]
    departements = list(dpe.departements) if dpe.etat == "heberge" else []
    if not departements:
        raise DpeInconnuError([])
    # Raised after the try, detached from the database error, whose message
    # can quote the number (C60): never ``from exc``.
    rows: list[dict] | None
    try:
        result = await session.execute(
            _DOCUMENT_PAR_NUMERO, {"numero": numero, "departements": departements}
        )
        rows = [dict(row) for row in result.mappings().all()]
    except SQLAlchemyError:
        rows = None
    if rows is None:
        raise DocumentIndisponibleError
    if not rows:
        raise DpeInconnuError(departements)
    if len(rows) > 1:
        raise DocumentIndisponibleError
    ligne = rows[0]
    brut = queries._record_from_mapping(ligne)
    record = _minimize(brut)
    commun = {
        "numero": record.dpe_number,
        "source_identity": SourceIdentity(
            reference=record.dpe_number, sources=jeux_sources(brut)
        ),
        "methode": methode_document(record.date_etablissement_dpe),
        "provenance": ProvenanceDocument(date_import=await date_import_dpe(session)),
    }
    if _retire(brut):
        return DocumentDpePublic(
            **commun,
            disponibilite="retire",
            statut=StatutPublic(code="retire", remplace=record.replaced_dpe_number),
        )
    remplacant = (
        await lire_remplacant(session, record.dpe_number, departements)
        if remplacement_a_verifier(record, as_of)
        else None
    )
    adresse_ban, identite, concordance = await rapprochement_ban(
        session, ligne, hosting
    )
    adresse_declaree = projeter_adresse_declaree(
        AdresseBruteDocument(
            adresse_brut=ligne.get("adresse_brut"),
            adresse_complete_brut=ligne.get("adresse_complete_brut"),
            complements={col: ligne.get(col) for col in COLONNES_COMPLEMENT},
        ),
        identite,
    )
    return DocumentDpePublic(
        **commun,
        disponibilite="disponible",
        statut=statut_validite(record, as_of, remplacant),
        dates=DatesDocument(
            etablissement=record.date_etablissement_dpe,
            fin_validite=record.date_fin_validite_dpe,
        ),
        reperes=reperes_document(record, ligne),
        caracteristiques=caracteristiques_document(record),
        adresse_declaree=adresse_declaree,
        adresse_ban=adresse_ban,
        adresse_divergente=divergence(concordance),
    )

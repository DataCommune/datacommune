"""One documentary projection for every DPE comparison (04-10, R06-09/7, CMP-02).

A compared document is the public document of 04-04
(:func:`app.dpe.document_public.lire_document_public`), never a raw row: its
dates and status are those of the documentary cycle (``statut_validite``),
its declared address the public projection of 04-19 (P-ADR-01). This module
only lines its served values up in four groups of rows — « Étiquettes »,
« Logement décrit », « Coût et validité », « Adresse » — and gets each verdict
from the single rule :func:`app.commune.comparison.compare_cell` (03-09).

The direct comparison of documents (``app.dpe.comparaison_public``) and the
documents designated on compared addresses (04-11, ``app.adresse.comparaison``)
both read this projection: the same couple of documents gets the same rows,
values, gaps and reasons. A compared address without a designated (or
readable) document is a :class:`ColonneSansDocument`: each of its rows is an
absence of its own state, never a value chosen here.

Method of calculation (R06-09/7): the classes, consumptions and emissions
depend on it. A document of before July 2021 and one of after never get a
gap or a verdict on these rows (``methodes_differentes``); a method that
cannot be established (no establishment date, inconsistent or future dates)
gives no verdict either (``methode_indeterminee``).

Values ① ② (R5, plan 04.5-13): the per-square-metre measures published by the
ADEME come first (``mesure_m2``, under ``dpe.document.conso_ep_m2`` and
``dpe.document.emissions_ges_m2``); the yearly totals keep their own rows and
fields after them. A document without a published per-m² value gets a
``not_reported`` cell (a member the reader did not serve, an outage): nothing
is divided by the surface here, and no total stands in for it.

The declared address is compared only between two published texts; a masked
one gives the reason ``adresse_declaree_non_affichee`` and its text is never
read here (the projection of 04-19 does not serve it).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.adresse.blocs import source_valeur
from app.adresse.schemas import ValeurLieu
from app.common.semantic_contract import load_registry
from app.commune.comparison import (
    METHODE_INCONNUE,
    MotifHorsCouverture,
    ValeurServie,
    Verdict,
    compare_cell,
)
from app.dpe.document_public import RATTACHEMENT, DocumentDpePublic, valeur_document

CleGroupeDocument = Literal["etiquettes", "logement", "cout_validite", "adresse"]

TITRES_GROUPES_DOCUMENT: dict[CleGroupeDocument, str] = {
    "etiquettes": "Étiquettes",
    "logement": "Logement décrit",
    "cout_validite": "Coût et validité",
    "adresse": "Adresse",
}

FIELD_DATE_ETABLISSEMENT = "dpe.document.date_etablissement"
FIELD_DATE_FIN_VALIDITE = "dpe.document.date_fin_validite"
FIELD_METHODE = "dpe.document.methode"
FIELD_ADRESSE_DECLAREE = "dpe.document.adresse_declaree"
FIELD_ADRESSE_BAN = "dpe.document.adresse_ban"

# A document the source no longer publishes keeps its column; its description
# is purged (04-04), so each of its rows says so.
MOTIF_DOCUMENT_RETIRE = "document_retire"
# The BAN key is retained, but its BAN rows disagree: no label is served.
MOTIF_BAN_INDETERMINEE = "adresse_ban_indeterminee"
MOTIF_CLE_INCONNUE = "cle_inconnue"

# Dates that make the method unreadable, as the documentary cycle reads them.
_VALIDITES_ILLISIBLES = frozenset({"inconsistent_dates", "future_establishment"})


@dataclass(frozen=True)
class ValeurDocument:
    """One served cell of a document and what the rule compares.

    ``comparee``: the compared value (None for an absence). ``sensible``: the
    value depends on the method of calculation. ``non_affichee``: the value is
    withheld by its publication rule (masked declared address).
    """

    cellule: ValeurLieu
    comparee: Any = None
    sensible: bool = False
    non_affichee: bool = False


@dataclass(frozen=True)
class ColonneSansDocument:
    """A column that compares no document (04-11): every row is this absence.

    An address without designation (``not_applicable``), a designation whose
    read failed (``temporary_error``, the pair is kept) or an address out of
    coverage (``out_of_coverage``, ``hors_couverture``: the column names the
    address, never a document).
    """

    etat: str
    motif: str | None = None
    hors_couverture: bool = False


@dataclass(frozen=True)
class LigneDocument:
    """A row of the comparison: its key, its field and its reader."""

    cle: str
    field_id: str
    lire: Callable[[DocumentDpePublic], ValeurDocument]


@dataclass(frozen=True)
class CelluleComparee:
    """The served cell of one column and its verdict (None for column 1)."""

    valeur: ValeurDocument
    verdict: Verdict | None


@dataclass(frozen=True)
class LigneComparee:
    cle: str
    field_id: str
    identiques: bool
    cellules: list[CelluleComparee]


@dataclass(frozen=True)
class GroupeCompare:
    cle: CleGroupeDocument
    titre: str
    lignes: list[LigneComparee]


def methode_comparable(document: DocumentDpePublic) -> str:
    """``ancienne`` / ``actuelle``, or ``METHODE_INCONNUE`` when not established.

    Read on the served document only: its method axis (``methode``, by the
    establishment date) and its calendar axis (``statut.validite``): dates the
    cycle reads as inconsistent or future establish no method.
    """
    if document.methode == "inconnue" or document.statut.validite in (
        _VALIDITES_ILLISIBLES
    ):
        return METHODE_INCONNUE
    return document.methode


# --- Readers of one row in one served document ---------------------------------------


def _servie(valeur: ValeurLieu, *, sensible: bool = False) -> ValeurDocument:
    return ValeurDocument(
        cellule=valeur,
        comparee=valeur.valeur if valeur.etat == "value" else None,
        sensible=sensible,
    )


def _repere(
    chemin: Callable[[Any], ValeurLieu], *, sensible: bool = False
) -> Callable[[DocumentDpePublic], ValeurDocument]:
    def lire(document: DocumentDpePublic) -> ValeurDocument:
        return _servie(chemin(document.reperes), sensible=sensible)

    return lire


def _par_m2(
    etiquette: Callable[[Any], Any], field_id: str
) -> Callable[[DocumentDpePublic], ValeurDocument]:
    """The published per-m² measure of a label, as served (zero and absence kept).

    A member the reader did not serve is an outage of the row, as on the
    document page (04.5-12): never « not reported », never the total.
    """

    def lire(document: DocumentDpePublic) -> ValeurDocument:
        mesure = etiquette(document.reperes).mesure_m2
        if mesure is None:
            return ValeurDocument(_absente(field_id, "temporary_error"))
        return _servie(mesure, sensible=True)

    return lire


def _caracteristique(field_id: str) -> Callable[[DocumentDpePublic], ValeurDocument]:
    def lire(document: DocumentDpePublic) -> ValeurDocument:
        for valeur in document.caracteristiques:
            if valeur.field_id == field_id:
                return _servie(valeur)
        return _servie(valeur_document(field_id, None))

    return lire


def _date(membre: str, field_id: str) -> Callable[[DocumentDpePublic], ValeurDocument]:
    def lire(document: DocumentDpePublic) -> ValeurDocument:
        jour = getattr(document.dates, membre) if document.dates else None
        return _servie(valeur_document(field_id, jour.isoformat() if jour else None))

    return lire


def _methode(document: DocumentDpePublic) -> ValeurDocument:
    """The method served by the page; an unknown one is not reported."""
    methode = None if document.methode == "inconnue" else document.methode
    return _servie(valeur_document(FIELD_METHODE, methode))


def _absente(field_id: str, etat: str, motif: str | None = None) -> ValeurLieu:
    return ValeurLieu(
        field_id=field_id,
        etat=etat,
        motif=motif,
        unite=load_registry()[field_id].unit,
        source=source_valeur("dpe"),
        rattachement=RATTACHEMENT,
    )


def _adresse_declaree(document: DocumentDpePublic) -> ValeurDocument:
    """Published text, masked with its reason, or not reported (P-ADR-01)."""
    adresse = document.adresse_declaree
    if adresse is not None and adresse.etat == "publiee" and adresse.texte:
        return _servie(valeur_document(FIELD_ADRESSE_DECLAREE, adresse.texte))
    if adresse is not None and adresse.etat == "masquee":
        cellule = _absente(FIELD_ADRESSE_DECLAREE, "suppressed", adresse.motif)
        return ValeurDocument(cellule=cellule, non_affichee=True)
    return _servie(valeur_document(FIELD_ADRESSE_DECLAREE, None))


def _adresse_ban(document: DocumentDpePublic) -> ValeurDocument:
    """The BAN address the source attaches the document to, compared by key."""
    ban = document.adresse_ban
    if ban is None or ban.etat == "sans_cle":
        return _servie(valeur_document(FIELD_ADRESSE_BAN, None))
    if ban.etat == "retenue" and ban.libelle:
        cellule = valeur_document(FIELD_ADRESSE_BAN, ban.libelle)
        return ValeurDocument(cellule=cellule, comparee=ban.ban_id)
    if ban.etat == "retenue":
        motif = MOTIF_BAN_INDETERMINEE
        return ValeurDocument(_absente(FIELD_ADRESSE_BAN, "not_available", motif))
    if ban.etat == "cle_inconnue":
        motif = MOTIF_CLE_INCONNUE
        return ValeurDocument(_absente(FIELD_ADRESSE_BAN, "not_available", motif))
    if ban.etat == "hors_couverture":
        return ValeurDocument(_absente(FIELD_ADRESSE_BAN, "out_of_coverage", ban.motif))
    return ValeurDocument(_absente(FIELD_ADRESSE_BAN, "temporary_error"))


GROUPES_DOCUMENT: tuple[tuple[CleGroupeDocument, tuple[LigneDocument, ...]], ...] = (
    (
        "etiquettes",
        (
            LigneDocument(
                "classe_energie",
                "dpe.document.etiquette_dpe",
                _repere(lambda r: r.etiquette_energie.classe, sensible=True),
            ),
            LigneDocument(
                "consommation_m2",
                "dpe.document.conso_ep_m2",
                _par_m2(lambda r: r.etiquette_energie, "dpe.document.conso_ep_m2"),
            ),
            LigneDocument(
                "classe_climat",
                "dpe.document.etiquette_ges",
                _repere(lambda r: r.etiquette_climat.classe, sensible=True),
            ),
            LigneDocument(
                "emissions_m2",
                "dpe.document.emissions_ges_m2",
                _par_m2(lambda r: r.etiquette_climat, "dpe.document.emissions_ges_m2"),
            ),
            # The yearly totals, after the per-m² measures (R5 « totals in detail »).
            LigneDocument(
                "consommation",
                "dpe.document.conso_ep",
                _repere(lambda r: r.etiquette_energie.mesure, sensible=True),
            ),
            LigneDocument(
                "emissions",
                "dpe.document.emissions_ges",
                _repere(lambda r: r.etiquette_climat.mesure, sensible=True),
            ),
        ),
    ),
    (
        "logement",
        (
            LigneDocument(
                "type_batiment",
                "dpe.document.type_batiment",
                _repere(lambda r: r.logement_decrit.type_batiment),
            ),
            LigneDocument(
                "surface",
                "dpe.document.surface_habitable",
                _repere(lambda r: r.logement_decrit.surface),
            ),
            LigneDocument(
                "periode_construction",
                "dpe.document.periode_construction",
                _caracteristique("dpe.document.periode_construction"),
            ),
            LigneDocument(
                "energie_chauffage",
                "dpe.document.energie_chauffage",
                _caracteristique("dpe.document.energie_chauffage"),
            ),
        ),
    ),
    (
        "cout_validite",
        (
            LigneDocument(
                "cout_annuel",
                "dpe.document.cout_annuel",
                _repere(lambda r: r.cout_annuel),
            ),
            LigneDocument(
                "etabli_le",
                FIELD_DATE_ETABLISSEMENT,
                _date("etablissement", FIELD_DATE_ETABLISSEMENT),
            ),
            LigneDocument(
                "valable_jusqu_au",
                FIELD_DATE_FIN_VALIDITE,
                _date("fin_validite", FIELD_DATE_FIN_VALIDITE),
            ),
            LigneDocument("methode", FIELD_METHODE, _methode),
        ),
    ),
    (
        "adresse",
        (
            LigneDocument(
                "adresse_declaree", FIELD_ADRESSE_DECLAREE, _adresse_declaree
            ),
            LigneDocument("adresse_ban", FIELD_ADRESSE_BAN, _adresse_ban),
        ),
    ),
)


# --- The projection and its comparison (pure) ----------------------------------------


def projeter_document(
    document: DocumentDpePublic | ColonneSansDocument,
) -> dict[str, ValeurDocument]:
    """Every row of one document, by row key; a retired one says so in each row."""
    lignes = (ligne for _, lignes in GROUPES_DOCUMENT for ligne in lignes)
    if isinstance(document, ColonneSansDocument):
        return {
            ligne.cle: ValeurDocument(
                _absente(ligne.field_id, document.etat, document.motif)
            )
            for ligne in lignes
        }
    if document.disponibilite == "retire" or document.reperes is None:
        return {
            ligne.cle: ValeurDocument(
                _absente(ligne.field_id, "not_reported", MOTIF_DOCUMENT_RETIRE)
            )
            for ligne in lignes
        }
    return {ligne.cle: ligne.lire(document) for ligne in lignes}


def _comparable(
    ligne: LigneDocument,
    valeur: ValeurDocument,
    methode: str | None,
    hors_couverture: bool = False,
) -> ValeurServie:
    cellule = valeur.cellule
    presente = cellule.etat == "value"
    return ValeurServie(
        etat=cellule.etat,
        valeur=valeur.comparee if presente else None,
        periode=None,
        # The served unit, the contract's when none is served (D-15 API IN-11).
        unite=cellule.unite or load_registry()[ligne.field_id].unit,
        definition=(ligne.field_id,),
        motif=None if presente else cellule.motif,
        methode=methode if presente and valeur.sensible else None,
        non_affichee=valeur.non_affichee,
        hors_couverture=hors_couverture,
    )


def _ligne(
    ligne: LigneDocument,
    projections: Sequence[Mapping[str, ValeurDocument]],
    methodes: Sequence[str | None],
    hors_couverture: Sequence[bool],
    motif_hors_couverture: MotifHorsCouverture,
) -> LigneComparee:
    contrat = load_registry()[ligne.field_id]
    valeurs = [projection[ligne.cle] for projection in projections]
    comparables = [
        _comparable(ligne, valeur, methode, hors)
        for valeur, methode, hors in zip(
            valeurs, methodes, hors_couverture, strict=True
        )
    ]
    identiques = True
    cellules = []
    for index, valeur in enumerate(valeurs):
        verdict = (
            compare_cell(
                contrat,
                comparables[0],
                comparables[index],
                hors_couverture=motif_hors_couverture,
                categoriel=True,
            )
            if index
            else None
        )
        identiques = identiques and (verdict is None or verdict.identique)
        cellules.append(CelluleComparee(valeur=valeur, verdict=verdict))
    return LigneComparee(
        cle=ligne.cle,
        field_id=ligne.field_id,
        identiques=identiques,
        cellules=cellules,
    )


def comparer_projections(
    documents: Sequence[DocumentDpePublic | ColonneSansDocument],
    *,
    hors_couverture: MotifHorsCouverture = "commune_hors_couverture",
) -> list[GroupeCompare]:
    """The four groups of rows of *documents* (column 1 is the reference). Pure.

    A :class:`ColonneSansDocument` compares no document: its rows are its
    absence, and *hors_couverture* names the reason of a column out of
    coverage (the address, for the documents designated on addresses).
    """
    if not documents:
        return []
    projections = [projeter_document(document) for document in documents]
    methodes = [
        None
        if isinstance(document, ColonneSansDocument)
        else methode_comparable(document)
        for document in documents
    ]
    hors = [
        isinstance(document, ColonneSansDocument) and document.hors_couverture
        for document in documents
    ]
    return [
        GroupeCompare(
            cle=cle,
            titre=TITRES_GROUPES_DOCUMENT[cle],
            lignes=[
                _ligne(ligne, projections, methodes, hors, hors_couverture)
                for ligne in lignes
            ],
        )
        for cle, lignes in GROUPES_DOCUMENT
    ]

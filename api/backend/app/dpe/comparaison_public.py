"""Two to four DPE documents compared by their source number (04-10, CMP-02, D-116).

``GET /v1/dpe/comparaison?numeros=`` reads each document with the reader of
the document page (:func:`app.dpe.document_public.lire_document_public`):
same number form, same departements, same status, same declared address
projection; nothing is read from a raw row here. The rows and verdicts come
from the single documentary projection (``app.dpe.comparison_projection``),
whose verdicts are those of the 03-09 rule.

A number the instance does not know is named in ``numeros_ecartes`` and gets
no column. A failed or ambiguous read makes the whole comparison unavailable:
no column is served partially. Every document is read in the transaction of
the request, after the publication guard of ``/v1/dpe/*``
(``require_publication``): one generation for every column (N-01).

Consulting documents attributes them to nothing: no dwelling, sale or commune
datum is served, and the masked declared address never leaves the projection.
"""

import datetime as dt
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.schemas import AdresseDeclaree, ValeurLieu
from app.commune.schemas import Ecart
from app.dpe import comparison_projection
from app.dpe.document_public import (
    CodeStatut,
    DocumentDpePublic,
    DpeInconnuError,
    lire_document_public,
)
from app.dpe.lifecycle import Validity
from app.scope.dependencies import RouteHosting

# Reasons not to serve a gap between two documents: the 03-09 rule, with the
# reasons of the method of calculation and of a masked declared address.
MotifNonCalculDocument = Literal[
    "donnee_absente",
    "periode_inconnue",
    "periodes_differentes",
    "unites_differentes",
    "definitions_differentes",
    "non_numerique",
    "methodes_differentes",
    "methode_indeterminee",
    "adresse_declaree_non_affichee",
]


class ColonneDocument(BaseModel):
    """A compared document: its number, method, status axes and declared address.

    ``adresse_declaree`` is the public projection of 04-19: the text only when
    published, else the reason of the mask; never the raw text.
    """

    numero: str
    disponibilite: Literal["disponible", "retire"]
    methode: Literal["ancienne", "actuelle", "inconnue"]
    statut: CodeStatut
    validite: Validity | None = None
    adresse_declaree: AdresseDeclaree | None = None


class CelluleComparaisonDocument(ValeurLieu):
    """One document's value for a row: the value of its page, and its verdict.

    Columns 2 to 4 carry the gap to column 1, the reason why none is served,
    or for a comparable non-numeric value the verdict ``identique`` /
    ``different`` (no figure).
    """

    numero: str
    ecart: Ecart | None = None
    motif_non_calcul: MotifNonCalculDocument | None = None
    verdict: Literal["identique", "different"] | None = None
    identique_a_la_reference: bool | None = None

    @model_validator(mode="after")
    def one_verdict_at_most(self) -> Self:
        served = [self.ecart, self.motif_non_calcul, self.verdict]
        if sum(item is not None for item in served) > 1:
            raise ValueError("a gap, a reason and a verdict are exclusive")
        return self


class LigneComparaisonDocument(BaseModel):
    """One compared value, one cell per column (column 1 is the reference)."""

    cle: str
    field_id: str
    identiques: bool = Field(
        description=(
            "Vrai si valeurs, états, motifs et méthodes sont égaux dans toutes "
            "les colonnes. Deux absences de même état et de même motif sont "
            "identiques (D-116) ; une valeur de méthode non déterminée ou une "
            "adresse déclarée non affichée ne l'est jamais."
        )
    )
    cellules: list[CelluleComparaisonDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def verdict_per_column(self) -> Self:
        reference, *others = self.cellules
        if (
            reference.ecart is not None
            or reference.motif_non_calcul is not None
            or reference.verdict is not None
            or reference.identique_a_la_reference is not None
        ):
            raise ValueError("column 1 is the reference: no verdict")
        for cellule in others:
            if (
                cellule.ecart is None
                and cellule.motif_non_calcul is None
                and cellule.verdict is None
            ):
                raise ValueError("a compared cell needs a gap, a reason or a verdict")
        return self


class GroupeComparaisonDocument(BaseModel):
    cle: comparison_projection.CleGroupeDocument
    titre: str
    lignes: list[LigneComparaisonDocument]


class ComparaisonDocuments(BaseModel):
    """2 to 4 DPE documents compared row by row, in four groups (CMP-02, D-116).

    ``numeros_ecartes``: the requested numbers no document carries in the
    departements served by this instance, named so the link can say it was
    altered. Nothing is attached to a dwelling.
    """

    colonnes: list[ColonneDocument]
    groupes: list[GroupeComparaisonDocument]
    numeros_ecartes: list[str] = Field(default_factory=list)


def _colonne(document: DocumentDpePublic) -> ColonneDocument:
    return ColonneDocument(
        numero=document.numero,
        disponibilite=document.disponibilite,
        methode=document.methode,
        statut=document.statut.code,
        validite=document.statut.validite,
        adresse_declaree=document.adresse_declaree,
    )


def _cellule(
    numero: str, cellule: comparison_projection.CelluleComparee
) -> CelluleComparaisonDocument:
    verdict = cellule.verdict
    return CelluleComparaisonDocument(
        **cellule.valeur.cellule.model_dump(),
        numero=numero,
        ecart=verdict.ecart if verdict else None,
        motif_non_calcul=verdict.motif if verdict else None,
        verdict=verdict.categorie if verdict else None,
        identique_a_la_reference=verdict.identique if verdict else None,
    )


def construire_comparaison(
    documents: Sequence[DocumentDpePublic], numeros_ecartes: Sequence[str] = ()
) -> ComparaisonDocuments:
    """The comparison of the served documents (column 1 first). Pure."""
    numeros = [document.numero for document in documents]
    groupes = [
        GroupeComparaisonDocument(
            cle=groupe.cle,
            titre=groupe.titre,
            lignes=[
                LigneComparaisonDocument(
                    cle=ligne.cle,
                    field_id=ligne.field_id,
                    identiques=ligne.identiques,
                    cellules=[
                        _cellule(numero, cellule)
                        for numero, cellule in zip(numeros, ligne.cellules, strict=True)
                    ],
                )
                for ligne in groupe.lignes
            ],
        )
        for groupe in comparison_projection.comparer_projections(documents)
    ]
    return ComparaisonDocuments(
        colonnes=[_colonne(document) for document in documents],
        groupes=groupes,
        numeros_ecartes=list(numeros_ecartes),
    )


async def comparer_documents(
    session: AsyncSession,
    numeros: Sequence[str],
    hosting: RouteHosting,
    as_of: dt.date,
) -> ComparaisonDocuments:
    """Compare the documents *numeros* (validated, distinct, column 1 first).

    Raises :class:`app.dpe.document_public.DocumentIndisponibleError` when a
    read fails or a number is not unique (the comparison is then unavailable
    as a whole, never partial).
    """
    documents: list[DocumentDpePublic] = []
    ecartes: list[str] = []
    for numero in numeros:
        try:
            documents.append(
                await lire_document_public(session, numero, hosting, as_of)
            )
        except DpeInconnuError:
            ecartes.append(numero)
    return construire_comparaison(documents, ecartes)

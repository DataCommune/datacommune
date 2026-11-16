"""Common response schemas for the API (all fields in French)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErreurApi(BaseModel):
    """Format d'erreur API inspire de RFC 9457."""

    statut: str = "erreur"
    code: int
    message: str
    detail: str | None = None


class ProblemDetail(BaseModel):
    """RFC 9457 Problem Details for HTTP APIs."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class SourceAttribution(BaseModel):
    """Dataset source attribution for license compliance."""

    nom: str
    licence: str
    url: str
    producteur: str


class PublicationCadence(BaseModel):
    """Official publication cadence of an upstream data source (D-05/D-06).

    ``official`` is only allowed with verifiable proof: a non-empty label, an
    HTTPS evidence URL and a positive interval in days. A source without proof
    is ``non_publiee_ou_irreguliere`` and carries no invented interval or
    uniform threshold (never a fabricated 545-day value).
    """

    statut: Literal["official", "non_publiee_ou_irreguliere"]
    label: str | None = None
    evidence_url: str | None = None
    intervalle_jours: int | None = None

    @model_validator(mode="after")
    def _validate_proof(self) -> "PublicationCadence":
        if self.statut == "official":
            if not self.label or not self.label.strip():
                raise ValueError("official cadence requires a non-empty label")
            if not self.evidence_url or not self.evidence_url.startswith("https://"):
                raise ValueError("official cadence requires an https evidence_url")
            if self.intervalle_jours is None or self.intervalle_jours <= 0:
                raise ValueError(
                    "official cadence requires a positive intervalle_jours"
                )
        elif self.intervalle_jours is not None:
            raise ValueError(
                "irregular cadence must not carry an interval (no invented threshold)"
            )
        return self


class SourcePeriod(BaseModel):
    """Structured source period; explains why when no period is asserted.

    An unknown period is expressed explicitly, never fabricated from an
    internal ``last_updated_at`` timestamp.
    """

    statut: Literal["connue", "glissante", "inconnue"] = "inconnue"
    label: str | None = None
    debut: str | None = None
    fin: str | None = None


class DatasetCoverage(BaseModel):
    """Effective coverage of a dataset relative to the demo instance."""

    type: Literal["departements", "nationale", "amont", "inconnue"] = "inconnue"
    departements: list[str] | None = None


class OutOfCoverageReason(StrEnum):
    """Why a dataset is « hors couverture » (D-24).

    A reason qualifies the existing ``out_of_coverage`` state; it is never a
    fifth state. Order of precedence when several apply: the order below.
    """

    DATASET_NON_HEBERGE = "dataset_non_heberge"
    TERRITOIRE_HORS_PERIMETRE = "territoire_hors_perimetre"
    SOURCE_SANS_COUVERTURE = "source_sans_couverture"


class DatasetHosting(BaseModel):
    """Hosting of a dataset by this instance, read from the persisted scope."""

    etat: Literal["heberge", "hors_couverture"]
    motif: OutOfCoverageReason | None = None
    departements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _motif_iff_out_of_coverage(self) -> "DatasetHosting":
        if (self.etat == "hors_couverture") != (self.motif is not None):
            raise ValueError("motif is required for, and only for, hors_couverture")
        return self


class EditorialState(StrEnum):
    """Editorial freshness state, distinct from technical availability (D-07)."""

    ACTUEL = "actuel"
    RETARDE = "retarde"
    ERREUR = "erreur"
    INCONNU = "inconnu"


class DatasetProvenance(BaseModel):
    """Additive public provenance contract (D-02).

    Keeps the four historical attribution fields (``nom``, ``licence``,
    ``url``, ``producteur``) and adds structured static+dynamic facts. Kept
    disjoint from :class:`SourceAttribution` so both remain unambiguous inside
    a union without silent field loss.
    """

    nom: str
    licence: str
    url: str
    producteur: str
    dataset: str
    periode: SourcePeriod
    derniere_importation_reussie: datetime | None = None
    couverture: DatasetCoverage
    cadence: PublicationCadence
    etat: EditorialState = EditorialState.INCONNU


class ResolutionOutcome(BaseModel):
    """Machine-readable outcome of a polymorphic resolution (Phase 45, D-04).

    ``exact``/``ambiguous`` qualify a non-empty result; ``not_reported`` and
    ``out_of_coverage`` type an empty one (Phase 43 absence taxonomy — never a
    best-effort neighbour presented as certainty). ``parcelles_candidates``
    lists the stable-ordered cadastre candidates when the issue is ambiguous.
    """

    issue: Literal["exact", "ambiguous", "not_reported", "out_of_coverage"]
    raison: str | None = None
    parcelles_candidates: list[str] | None = None
    # Additive (D-24): omitted from the payload unless the issue is
    # out_of_coverage, so existing outcomes keep their wire shape.
    motif: OutOfCoverageReason | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _motif_only_out_of_coverage(self) -> "ResolutionOutcome":
        if self.motif is not None and self.issue != "out_of_coverage":
            raise ValueError("motif only qualifies an out_of_coverage issue")
        return self


class MetaReponse(BaseModel):
    """Typed metadata for API responses (source attribution).

    Accepts legacy :class:`SourceAttribution` (single or list) and the
    additive :class:`DatasetProvenance` (single or list) without dropping any
    field. Members are disjoint, so the union stays unambiguous.

    ``resolution`` is additive and only populated by the polymorphic resolve
    endpoint (Phase 45); every other endpoint leaves it ``None``.

    ``hebergement`` (D-24, additive) is set by a route serving catalogue keys
    when one of them is out of coverage: one entry per key the route serves,
    whatever their number. Omitted while every served key is hosted.
    """

    source: (
        SourceAttribution
        | list[SourceAttribution]
        | DatasetProvenance
        | list[DatasetProvenance]
    )
    resolution: ResolutionOutcome | None = None
    hebergement: dict[str, DatasetHosting] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class PaginationMeta(BaseModel):
    """Metadata for cursor-based pagination."""

    curseur_suivant: str | None = None
    taille: int
    limite: int


class ReponseApi[T](BaseModel):
    """Enveloppe standard pour les reponses API."""

    statut: str = "ok"
    donnees: T
    meta: MetaReponse | None = None


class ReponsePaginee[T](BaseModel):
    """Enveloppe paginee pour les reponses volumineuses."""

    statut: str = "ok"
    donnees: list[T]
    pagination: PaginationMeta | None = None
    meta: MetaReponse | None = None
    warning: str | None = None

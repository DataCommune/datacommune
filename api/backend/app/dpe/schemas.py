"""Pydantic schemas for DPE search, attribution and document consultation."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.dpe.lifecycle import validity_at


class ResultatDpe(BaseModel):
    """Historical proximity-search result returned by GET /v1/dpe."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "numero_dpe": "2331E0123456789A",
                    "date_etablissement_dpe": "2023-04-15",
                    "annee_construction": 1985,
                    "periode_construction": "1975-1977",
                    "type_batiment": "maison",
                    "surface_habitable": 95.5,
                    "etiquette_dpe": "D",
                    "etiquette_ges": "E",
                    "type_installation_chauffage": "chaudiere gaz",
                    "type_energie_principale_chauffage": "Gaz naturel",
                    "conso_5_usages_ep": 245.30,
                    "emission_ges_5_usages": 42.10,
                    "cout_total_5_usages": 1180.00,
                    "qualite_isolation_enveloppe": "moyenne",
                    "code_postal": "75012",
                    "code_insee": "75112",
                    "nom_commune": "Paris",
                    "longitude": 2.3880,
                    "latitude": 48.8410,
                }
            ]
        }
    )

    numero_dpe: str
    date_etablissement_dpe: date | None = None
    annee_construction: int | None = None
    periode_construction: str | None = None
    type_batiment: str | None = None
    surface_habitable: float | None = None
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None
    type_installation_chauffage: str | None = None
    type_energie_principale_chauffage: str | None = None
    conso_5_usages_ep: float | None = None
    emission_ges_5_usages: float | None = None
    cout_total_5_usages: float | None = None
    qualite_isolation_enveloppe: str | None = None
    code_postal: str | None = None
    code_insee: str | None = None
    nom_commune: str | None = None
    longitude: float
    latitude: float


# Historical exact/multiple/building variants remain constructible while known
# consumers migrate. The current classifier only emits absent or unassigned.
DpeAttributionStatus = Literal[
    "exact", "multiple", "building", "unassigned", "absent", "error"
]

DPE_ATTRIBUTION_RAISONS: dict[str, str] = {
    "identity_unavailable": "Aucune identité exacte résolue pour cette adresse.",
    "not_reported": "Aucun DPE rattaché exactement à cette adresse.",
    "ambiguous_unresolved": (
        "Plusieurs diagnostics rattachés à cette adresse, aucun attribuable "
        "à ce logement."
    ),
    "truncated": (
        "Trop de diagnostics rattachés à cette adresse pour en attribuer un."
    ),
    "dwelling_identity_unproven": (
        "Des diagnostics sont connus à cette adresse, sans preuve permettant "
        "d'en attribuer un au logement."
    ),
    "temporary_error": "Données DPE momentanément indisponibles.",
}

_RAISONS_PAR_ETAT: dict[str, frozenset[str]] = {
    "exact": frozenset(),
    "multiple": frozenset(),
    "building": frozenset({"not_reported", "ambiguous_unresolved", "truncated"}),
    "unassigned": frozenset({"dwelling_identity_unproven"}),
    "absent": frozenset({"identity_unavailable", "not_reported"}),
    "error": frozenset({"temporary_error"}),
}


class DpeCandidate(BaseModel):
    """Historical candidate shape kept only for coordinated migration."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    numero_dpe: str
    etage: int | None = None
    batiment: str | None = None
    porte: str | None = None
    surface_habitable: float | None = None
    etiquette_dpe: str | None = None
    annee_diagnostic: int | None = None
    perime: bool


class DpePublicDiagnostic(BaseModel):
    """Historical attributed diagnostic shape kept only for migration."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    numero_dpe: str
    etage: int | None = None
    batiment: str | None = None
    porte: str | None = None
    type_batiment: str | None = None
    surface_habitable: float | None = None
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None
    annee_construction: int | None = None
    periode_construction: str | None = None
    type_installation_chauffage: str | None = None
    type_energie_principale_chauffage: str | None = None
    conso_5_usages_ep: float | None = None
    emission_ges_5_usages: float | None = None
    cout_total_5_usages: float | None = None
    qualite_isolation_enveloppe: str | None = None
    date_etablissement_dpe: date | None = None
    date_fin_validite_dpe: date | None = None
    annee_diagnostic: int | None = None
    perime: bool


_DPE_ENERGY_CLASSES: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G")


class DpeBuildingContext(BaseModel):
    """Aggregate for diagnostics attached to other addresses of an RNB context."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    count: int = Field(default=0, ge=0)
    distribution: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict,
        json_schema_extra={"propertyNames": {"enum": list(_DPE_ENERGY_CLASSES)}},
    )
    truncated: bool = False

    @field_validator("distribution", mode="before")
    @classmethod
    def _validate_distribution_keys(cls, value):
        if isinstance(value, dict) and any(
            key not in _DPE_ENERGY_CLASSES for key in value
        ):
            # Keep the invalid key out of the error location and message: it may
            # itself be a private source identifier.
            raise ValueError("class distribution contains an unsupported key")
        return value

    @model_validator(mode="after")
    def _validate_distribution(self) -> "DpeBuildingContext":
        if sum(self.distribution.values()) > self.count:
            raise ValueError("class distribution cannot exceed the diagnostic count")
        return self


class DpeAttribution(BaseModel):
    """Closed V2 attribution decision, independent from document consultation."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: Literal[2] = 2
    status: DpeAttributionStatus
    reason: str | None = None
    diagnostic: DpePublicDiagnostic | None = None
    candidates: list[DpeCandidate] = Field(default_factory=list)
    building_context: DpeBuildingContext | None = None
    graph_state: Literal["not_checked", "incoherent"] = "not_checked"

    @model_validator(mode="after")
    def _validate_reason(self) -> "DpeAttribution":
        allowed = _RAISONS_PAR_ETAT[self.status]
        if self.reason is not None and self.reason not in DPE_ATTRIBUTION_RAISONS:
            raise ValueError("reason is not part of the DPE attribution taxonomy")
        if self.reason is not None and self.reason not in allowed:
            raise ValueError("reason is not admissible for this attribution status")
        if allowed and self.reason is None:
            raise ValueError("this attribution status requires a public reason")
        return self

    @model_validator(mode="after")
    def _validate_payload(self) -> "DpeAttribution":
        if (self.status == "exact") != (self.diagnostic is not None):
            raise ValueError("only exact attribution may carry a diagnostic")
        if self.status == "multiple" and len(self.candidates) < 2:
            raise ValueError("a multiple attribution needs at least two candidates")
        if self.status != "multiple" and self.candidates:
            raise ValueError("only multiple attribution may carry candidates")
        context_statuses = {"building", "unassigned", "absent"}
        if self.status == "building" and self.building_context is None:
            raise ValueError("a building attribution requires its aggregate")
        if self.status not in context_statuses and self.building_context is not None:
            raise ValueError("this attribution status cannot carry building context")
        return self


DpeInventoryState = Literal["ready", "absent", "identity_unavailable", "unavailable"]
DpeInventoryReadState = Literal["complete", "partial"]
DpeInventoryGraphState = Literal["not_checked", "incoherent"]
DpeValidityByDate = Literal["unknown", "expired", "not_expired_by_date"]


def dpe_validity_by_date(
    end_date: date | None,
    *,
    reference_date: date,
) -> DpeValidityByDate:
    """Classify source validity against an explicit calendar reference."""
    if end_date is None:
        return "unknown"
    if end_date < reference_date:
        return "expired"
    return "not_expired_by_date"


class DpeInventoryDistribution(BaseModel):
    """Closed A-G distribution plus diagnostics with no known energy class."""

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    a: int = Field(default=0, alias="A", ge=0)
    b: int = Field(default=0, alias="B", ge=0)
    c: int = Field(default=0, alias="C", ge=0)
    d: int = Field(default=0, alias="D", ge=0)
    e: int = Field(default=0, alias="E", ge=0)
    f: int = Field(default=0, alias="F", ge=0)
    g: int = Field(default=0, alias="G", ge=0)
    unknown: int = Field(default=0, ge=0)

    def total_count(self) -> int:
        """Return the confirmed number of imported diagnostics."""
        return sum((self.a, self.b, self.c, self.d, self.e, self.f, self.g)) + (
            self.unknown
        )


def _strict_date(value: object) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("date must use the ISO calendar format") from None
        if parsed.isoformat() == value:
            return parsed
    raise ValueError("date must use the ISO calendar format")


DpeCalendarDate = Annotated[date, BeforeValidator(_strict_date)]
# Copy of app.scope.model.DEPARTEMENTS_HEBERGEABLES (101 codes, 975 and the
# other overseas collectivities excluded), as in migration 101: these public
# schemas never import the configured application (golden audit, export).
# Checked equal by tests/test_dpe_inventory_lifecycle_contract.py.
SOURCE_SCOPE_DEPARTEMENTS: tuple[str, ...] = (
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "2A",
    "2B", "21", "22", "23", "24", "25", "26", "27", "28", "29",
    "30", "31", "32", "33", "34", "35", "36", "37", "38", "39",
    "40", "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "50", "51", "52", "53", "54", "55", "56", "57", "58", "59",
    "60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
    "70", "71", "72", "73", "74", "75", "76", "77", "78", "79",
    "80", "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "90", "91", "92", "93", "94", "95",
    "971", "972", "973", "974", "976",
)  # fmt: skip
DpeSourceDepartement = Annotated[
    str,
    StringConstraints(
        strict=True, pattern=rf"^(?:{'|'.join(SOURCE_SCOPE_DEPARTEMENTS)})$"
    ),
]
DpeLifecycleValidity = Literal[
    "unknown",
    "expired",
    "not_expired_by_date",
    "inconsistent_dates",
    "future_establishment",
]


class DpeLifecycle(BaseModel):
    """Whole closed public lifecycle; verified evidence is reserved for future use."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    source_state: Literal[
        "present", "absent_from_complete_scope", "explicitly_deactivated", "unknown"
    ]
    source_snapshot_date: DpeCalendarDate | None
    source_scope: Literal["44/85"] | DpeSourceDepartement = Field(
        description=(
            "Département dont le téléchargement complet fonde l'état de source "
            "(code parmi les départements hébergeables). Un cycle de vie fondé "
            "sur 44 ou 85 garde la valeur historique « 44/85 »."
        )
    )
    regimes: list[Literal["existant", "neuf"]] = Field(
        strict=True, max_length=2, json_schema_extra={"uniqueItems": True}
    )
    replacement_state: Literal[
        "not_checked", "no_successor_known", "replaced", "ambiguous"
    ]
    graph_issues: list[
        Literal["cycle", "fork", "missing_target", "truncated", "ambiguous_target"]
    ] = Field(strict=True, max_length=5, json_schema_extra={"uniqueItems": True})
    validity: DpeLifecycleValidity
    as_of_date: DpeCalendarDate
    revision_status: Literal["unverified", "verified_attestation"]
    revision_kind: Literal["2024", "2026"] | None
    revision_date: DpeCalendarDate | None
    detail_availability: Literal["available", "source_reconciled"]

    @model_validator(mode="before")
    @classmethod
    def _validate_keys(cls, value: object) -> object:
        if isinstance(value, dict) and set(value) != set(cls.model_fields):
            # An unknown key can itself be a source identifier: keep it out of
            # the validation error location as well as its rendered message.
            raise ValueError("lifecycle requires exactly its public fields")
        return value

    @model_validator(mode="after")
    def _validate_dimensions(self) -> "DpeLifecycle":
        if len(set(self.regimes)) != len(self.regimes) or len(
            set(self.graph_issues)
        ) != len(self.graph_issues):
            raise ValueError("lifecycle lists must contain unique values")
        if self.source_state != "unknown" and self.source_snapshot_date is None:
            raise ValueError("a proven source state requires its snapshot date")
        if (
            self.source_snapshot_date is not None
            and self.source_snapshot_date > self.as_of_date
        ):
            raise ValueError("source snapshot cannot follow the reference date")
        if self.revision_status == "unverified":
            if self.revision_kind is not None or self.revision_date is not None:
                raise ValueError("unverified revision cannot carry revision metadata")
        elif self.revision_kind is None or self.revision_date is None:
            raise ValueError("verified revision requires kind and date")
        if self.revision_date is not None and self.revision_date > self.as_of_date:
            raise ValueError("revision cannot follow the reference date")
        reconciled = self.source_state in {
            "absent_from_complete_scope",
            "explicitly_deactivated",
        }
        if reconciled != (self.detail_availability == "source_reconciled"):
            raise ValueError("detail availability must match source reconciliation")
        if reconciled and self.validity != "unknown":
            raise ValueError("reconciled source cannot carry documentary validity")
        return self


_DOCUMENTARY_FIELDS = (
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "etiquette_ges",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
    "annee_construction",
    "periode_construction",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "qualite_isolation_enveloppe",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
    "cout_total_5_usages",
)


def _validate_lifecycle_document(model: BaseModel, lifecycle: DpeLifecycle) -> None:
    if lifecycle.detail_availability == "source_reconciled":
        if any(getattr(model, name, None) is not None for name in _DOCUMENTARY_FIELDS):
            raise ValueError("reconciled source cannot carry documentary content")
        return
    established = getattr(model, "date_etablissement_dpe", None)
    end = getattr(model, "date_fin_validite_dpe", None)
    expected = validity_at(established, end, lifecycle.as_of_date)
    # Invalid source dates are intentionally not exposed; their missing parsed
    # value may still carry the private date-issue fact as inconsistent_dates.
    invalid_source_date = lifecycle.validity == "inconsistent_dates" and (
        established is None or end is None
    )
    if lifecycle.validity != expected and not invalid_source_date:
        raise ValueError("lifecycle validity does not match its explicit dates")


class DpeInventoryItem(BaseModel):
    """Public allowlist for one opaque inventory row."""

    # Pydantic keeps raw values in ``ValidationError.errors()`` even when their
    # textual representation is hidden. HTTP boundaries must map validation
    # failures to a constant public error instead of serializing that structure.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    document_ref: str = Field(min_length=1)
    type_batiment: str | None = None
    surface_habitable: float | None = None
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None
    date_etablissement_dpe: DpeCalendarDate | None = None
    date_fin_validite_dpe: DpeCalendarDate | None = None
    lifecycle: DpeLifecycle | None = None

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "DpeInventoryItem":
        if self.lifecycle is not None:
            _validate_lifecycle_document(self, self.lifecycle)
        return self


class DpePublicProvenance(BaseModel):
    """Shared public provenance and reservation vocabulary."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    association: Literal["source_ban_equality"] = "source_ban_equality"
    address_check: Literal["unverified"] = "unverified"
    source: Literal["ADEME"] = "ADEME"
    source_coverage: Literal["unknown"] = "unknown"
    graph_state: DpeInventoryGraphState = "not_checked"
    imported_at: datetime | None = None


class DpeInventoryPage(DpePublicProvenance):
    """One bounded page over the imported address inventory."""

    version: Literal[2] = 2
    as_of_date: DpeCalendarDate | None = None
    state: DpeInventoryState
    context_ref: str | None = None
    items: list[DpeInventoryItem] = Field(default_factory=list)
    total: int | None = Field(default=None, ge=0)
    distribution: DpeInventoryDistribution
    page_size: int = Field(ge=1, le=50)
    has_more: bool
    next_cursor: str | None = None
    read_state: DpeInventoryReadState

    @model_validator(mode="after")
    def _validate_page(self) -> "DpeInventoryPage":  # noqa: C901
        if any(
            item.lifecycle is not None and item.lifecycle.as_of_date != self.as_of_date
            for item in self.items
        ):
            raise ValueError("all lifecycle dates must match the page reference date")
        distribution_total = self.distribution.total_count()
        if len(self.items) > self.page_size:
            raise ValueError("items exceed the declared page size")
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("cursor presence must match has_more")
        if self.items and self.state != "ready":
            raise ValueError("only a ready inventory may carry items")
        if self.state != "ready" and self.has_more:
            raise ValueError("only a ready inventory may carry a next page")
        if self.state == "ready" and self.context_ref is None:
            raise ValueError("a ready inventory requires an opaque context")
        if self.state == "absent" and self.total != 0:
            raise ValueError("an absent inventory requires a confirmed zero total")
        if self.state in {
            "identity_unavailable",
            "unavailable",
        } and (self.total is not None or distribution_total != 0):
            raise ValueError("an unread inventory cannot carry confirmed totals")
        if self.state == "identity_unavailable" and self.context_ref is not None:
            raise ValueError("an unavailable identity cannot carry an opaque context")
        if self.total is not None and len(self.items) > self.total:
            raise ValueError("page items cannot exceed the confirmed total")
        if self.total is not None and distribution_total != self.total:
            raise ValueError("confirmed distribution must sum to total")
        return self


class DpeConsultedDocument(DpePublicProvenance):
    """Public allowlist for a document consulted independently of attribution."""

    version: Literal[2] = 2
    context_ref: str = Field(min_length=1)
    document_ref: str = Field(min_length=1)
    type_batiment: str | None = None
    surface_habitable: float | None = None
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None
    date_etablissement_dpe: DpeCalendarDate | None = None
    date_fin_validite_dpe: DpeCalendarDate | None = None
    annee_construction: int | None = None
    periode_construction: str | None = None
    type_installation_chauffage: str | None = None
    type_energie_principale_chauffage: str | None = None
    qualite_isolation_enveloppe: str | None = None
    conso_5_usages_ep: float | None = None
    emission_ges_5_usages: float | None = None
    cout_total_5_usages: float | None = None
    validity_by_date: DpeValidityByDate
    lifecycle: DpeLifecycle | None = None

    @model_validator(mode="after")
    def _validate_validity(self) -> "DpeConsultedDocument":
        if self.date_fin_validite_dpe is None and self.validity_by_date != "unknown":
            raise ValueError("missing source end date requires unknown validity")
        if self.lifecycle is not None:
            _validate_lifecycle_document(self, self.lifecycle)
            expected = self.lifecycle.validity
            if expected in {"inconsistent_dates", "future_establishment"}:
                expected = "unknown"
            if self.validity_by_date != expected:
                raise ValueError("legacy validity must match the lifecycle projection")
        return self

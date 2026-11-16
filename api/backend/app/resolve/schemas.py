"""Pydantic schemas for the polymorphic resolve endpoint."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.common.proxy_coverage import ProxySource, proxy_sources
from app.common.schemas import OutOfCoverageReason
from app.common.semantic_contract import ALLOWED_STATES
from app.dpe.schemas import DpeAttribution, DpeInventoryPage
from app.dvf.schemas import DvfAttribution

# Reuse the Phase 43 absence taxonomy verbatim (no parallel registry, D-04).
# ``value`` is a presence, not an absence, so it is excluded here. ``source_delayed``
# is the only Phase 44 extension: a not_reported dataset proven late by its source's
# editorial state. No other state is invented.
_ABSENCE_STATES: frozenset[str] = (ALLOWED_STATES - {"value"}) | {"source_delayed"}

# Closed set of short, constant human messages. Threat T-44-03: never embed
# upstream exceptions or SQL — internal details stay in logs only.
# ``ambiguous`` and ``parcel_mismatch`` are Phase 45 identity outcomes, not
# absence states: they never enter the DatasetResolutionState taxonomy.
RESOLUTION_RAISONS: dict[str, str] = {
    "not_reported": "Aucune donnee rapportee pour ce point.",
    "temporary_error": "Source temporairement indisponible.",
    "source_delayed": "Publication de la source en retard.",
    "out_of_coverage": "Point hors du perimetre couvert.",
    "not_applicable": "Non applicable pour ce point.",
    "ambiguous": "Plusieurs parcelles candidates pour ce point.",
    "parcel_mismatch": "Incoherence d'identite parcellaire; donnees ecartees.",
}


def _coerce_decimals(value: Any) -> Any:
    """Recursively convert ``Decimal`` to ``float`` in nested dict/list data.

    Resolve datasets are raw DB rows (``dict[str, Any]``) whose ``numeric``
    columns come back as ``Decimal`` from asyncpg. Pydantic v2 serializes a
    ``Decimal`` held under an ``Any`` field as a JSON *string* ("150000.00"),
    which the frontend numeric readers reject → every numeric field renders as
    "Donnée non disponible". Coercing to float makes them serialize as JSON
    numbers across all datasets, in one place.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _coerce_decimals(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_decimals(v) for v in value]
    return value


class DatasetErreur(BaseModel):
    """Error info for a single dataset that failed during multi-dataset query."""

    dataset: str
    message: str


class Avertissement(BaseModel):
    """Non-blocking warning attached to a resolve response.

    Signals that a dataset returned coarser data than requested because
    the BAN address lacked fine-grain fields (e.g. nom_voie or numero).
    Machine-readable via ``code``; human-readable via ``message``.
    """

    dataset: str = Field(
        ..., description="Dataset concerne (ex: connectivite, scolaire)"
    )
    code: str = Field(..., description="Code machine (ex: ADRESSE_INCOMPLETE)")
    message: str = Field(..., description="Message lisible")


class DatasetResolutionState(BaseModel):
    """Typed presence/absence state for one requested resolve dataset (D-04).

    ``presence`` is the coarse outcome (``value``/``absent``/``error``);
    ``absence_reason`` refines an absence using the Phase 43 taxonomy plus the
    ``source_delayed`` editorial extension. ``raison`` is a short constant
    message — never an upstream/SQL detail (T-44-03).
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    dataset: str
    # Proxy provenance is additive; absent provenance must not change the
    # closed DPE wire shape. Keep the two nullable public reason fields.
    sources: dict[str, ProxySource] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    presence: Literal["value", "absent", "error"]
    absence_reason: str | None = None
    raison: str | None = None
    # Why the dataset is out of coverage (D-24). Omitted when None so every
    # other state, DPE included, keeps its wire shape.
    motif: OutOfCoverageReason | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def _validate_absence_reason(self) -> "DatasetResolutionState":
        if self.dataset == "dpe" and self.sources is not None:
            raise ValueError("DPE state cannot carry proxy sources")
        reason = self.absence_reason
        if reason is not None and reason not in _ABSENCE_STATES:
            raise ValueError(
                "absence_reason is not part of the Phase 43 taxonomy (+ source_delayed)"
            )
        if self.presence == "value" and self.absence_reason is not None:
            raise ValueError("a present value must not carry an absence_reason")
        if self.motif is not None and reason != "out_of_coverage":
            raise ValueError("motif only qualifies an out_of_coverage state")
        return self


def out_of_coverage_state(
    name: str, motif: OutOfCoverageReason
) -> DatasetResolutionState:
    """State of a dataset declared out of coverage before any query (D-24)."""
    return DatasetResolutionState(
        dataset=name,
        presence="absent",
        absence_reason="out_of_coverage",
        raison=RESOLUTION_RAISONS["out_of_coverage"],
        motif=motif,
    )


def resolution_state(
    name: str,
    data: Any,
    *,
    is_error: bool,
) -> DatasetResolutionState:
    """Build the base resolution state for a queried dataset.

    ``value`` when data is non-empty, ``temporary_error`` on a caught error,
    ``not_reported`` for ``None``/empty results. ``not_applicable`` is never
    inferred (D-04): it requires an explicit business rule proven elsewhere.
    The router may later upgrade ``not_reported`` to ``source_delayed`` or
    ``out_of_coverage`` when it has proof.
    """
    if is_error:
        return DatasetResolutionState(
            dataset=name,
            sources=proxy_sources(data) or None,
            presence="error",
            absence_reason="temporary_error",
            raison=RESOLUTION_RAISONS["temporary_error"],
        )
    if data is None or (isinstance(data, list) and not data):
        return DatasetResolutionState(
            dataset=name,
            sources=proxy_sources(data) or None,
            presence="absent",
            absence_reason="not_reported",
            raison=RESOLUTION_RAISONS["not_reported"],
        )
    return DatasetResolutionState(
        dataset=name, presence="value", sources=proxy_sources(data) or None
    )


class ResultatResolution(BaseModel):
    """Resultat unifie de la resolution polymorphe.

    Guarantees a geographic point (latitude, longitude) regardless of
    the input type. Optional fields are populated when the corresponding
    resolver returns data.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type_detecte": "gps",
                    "latitude": 48.8606,
                    "longitude": 2.3411,
                    "adresse": "1 Rue de Rivoli, 75001 Paris",
                    "id_parcelle": "75101000AB0001",
                    "commune": "Paris 1er Arrondissement",
                    "code_postal": "75001",
                    "score": None,
                }
            ]
        }
    )

    type_detecte: str  # "adresse" | "gps" | "cadastre"
    latitude: float
    longitude: float
    # Optional fields populated depending on input type
    adresse: str | None = None  # formatted address (geocoding)
    id_parcelle: str | None = None  # canonical cadastral reference
    commune: str | None = None
    code_postal: str | None = None
    score: float | None = None  # similarity score (geocoding only)
    # Phase 45 canonical identity (additive, D-01/D-04): the three input modes
    # expose the same INSEE code, and the parcel resolution outcome is explicit.
    code_insee: str | None = None
    resolution: Literal["exact", "ambiguous"] = "exact"
    parcelles_candidates: list[str] | None = None  # only when ambiguous
    # BAN interoperability key (INSEE_FANTOIR_NUMERO) of the resolved address.
    # Additive interoperability context for address/GPS modes. It may target
    # the DPE inventory only after strict address-candidate selection (including
    # a genuine unnumbered place with a unique full label and locality). A GPS
    # reverse-geocode is display context and is never promoted to a DPE identity;
    # cadastre mode leaves the field null for the same reason (D-02).
    ban_id: str | None = None


class ResultatMultiResolution(ResultatResolution):
    """Extended resolution result with multi-dataset data and error info.

    Inherits all fields from ResultatResolution and adds datasets
    (per-dataset query results) and erreurs (per-dataset failures).
    """

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra={
            "examples": [
                {
                    "type_detecte": "gps",
                    "latitude": 48.8606,
                    "longitude": 2.3411,
                    "adresse": "1 Rue de Rivoli, 75001 Paris",
                    "id_parcelle": "75101000AB0001",
                    "commune": "Paris 1er Arrondissement",
                    "code_postal": "75001",
                    "score": None,
                    "datasets": {
                        "dvf": [{"id_mutation": "2024-00001", "prix": 450000}],
                    },
                    "erreurs": [],
                }
            ]
        },
    )

    datasets: dict[str, Any] | None = None
    erreurs: list[DatasetErreur] | None = None
    avertissements: list[Avertissement] = Field(
        default_factory=list,
        description=(
            "Avertissements non bloquants (donnees partielles, "
            "enrichissement degrade). Liste vide si tout est nominal."
        ),
    )
    dataset_states: dict[str, DatasetResolutionState] | None = Field(
        default=None,
        description=(
            "Etat typé (presence/absence) par dataset demandé, additif : "
            "n'altère ni datasets, ni erreurs, ni avertissements."
        ),
    )
    dpe_attribution: DpeAttribution | None = Field(
        default=None,
        description=(
            "Décision V2 non attributive pour l'identité résolue. Elle est "
            "présente avec `dpe_inventory`; `datasets.dpe` reste vide."
        ),
    )
    dpe_inventory: DpeInventoryPage | None = Field(
        default=None,
        description=(
            "Première page fermée de l'inventaire exact-BAN. Les documents "
            "restent indépendants de toute attribution à un logement."
        ),
    )
    dvf_attribution: DvfAttribution | None = Field(
        default=None,
        description=(
            "Décision d'attribution DVF pour l'identité résolue (Phase 57, "
            "D-16/D-37), présente seulement quand « dvf » est demandé. C'est "
            "la seule source de vérité de l'état de liaison DVF : l'état se "
            "lit sur `status`, jamais sur le remplissage de `datasets.dvf`, "
            "qui continue de porter l'historique de la parcelle (D-11/D-17). "
            "`compatible_parcelle` désigne une transaction compatible "
            "observée sur cette parcelle — non attribuée au bien."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _require_explicit_dpe_v2(cls, value: Any) -> Any:
        """Revalidate nested models and reject implicit legacy versions."""
        if not isinstance(value, dict):
            return value

        value = dict(value)
        for field in ("dpe_inventory", "dpe_attribution"):
            payload = value.get(field)
            if isinstance(payload, BaseModel):
                payload = payload.model_dump(
                    mode="python", by_alias=True, warnings=False
                )
                value[field] = payload
            if isinstance(payload, dict) and payload.get("version") != 2:
                raise ValueError(f"{field} must explicitly declare version 2")

        dataset_states = value.get("dataset_states")
        if isinstance(dataset_states, dict):
            dataset_states = dict(dataset_states)
            dpe_state = dataset_states.get("dpe")
            if isinstance(dpe_state, BaseModel):
                dataset_states["dpe"] = dpe_state.model_dump(
                    mode="python", by_alias=True, warnings=False
                )
                value["dataset_states"] = dataset_states
        return value

    @model_validator(mode="after")
    def _validate_dpe_contract(self) -> "ResultatMultiResolution":  # noqa: C901
        inventory = self.dpe_inventory
        attribution = self.dpe_attribution
        datasets_has_dpe = self.datasets is not None and "dpe" in self.datasets
        state_has_dpe = self.dataset_states is not None and "dpe" in self.dataset_states
        if (
            datasets_has_dpe
            and self.datasets is not None
            and self.datasets["dpe"] is not None
        ):
            raise ValueError("datasets.dpe must be empty in V2")
        if (inventory is None) != (attribution is None):
            raise ValueError("DPE inventory and attribution must travel together")
        if inventory is None or attribution is None:
            # A DPE declared out of coverage was never read (D-24): its state
            # is the only DPE marker and carries its reason.
            dpe_state = (self.dataset_states or {}).get("dpe")
            declared_out = (
                dpe_state is not None
                and dpe_state.absence_reason == "out_of_coverage"
                and dpe_state.motif is not None
            )
            if datasets_has_dpe or (state_has_dpe and not declared_out):
                raise ValueError("DPE markers require inventory and attribution V2")
            return self
        if inventory.version != 2 or attribution.version != 2:
            raise ValueError("DPE inventory and attribution must use version 2")

        expected = {
            "ready": ("unassigned", "dwelling_identity_unproven", "value"),
            "absent": ("absent", "not_reported", "absent"),
            "identity_unavailable": (
                "absent",
                "identity_unavailable",
                "absent",
            ),
            "unavailable": ("error", "temporary_error", "error"),
        }[inventory.state]
        if inventory.state == "ready" and (
            inventory.total is None or inventory.total <= 0 or not inventory.items
        ):
            raise ValueError("a ready DPE inventory requires a non-empty first page")
        if (attribution.status, attribution.reason) != expected[:2]:
            raise ValueError("DPE attribution does not match the inventory state")
        if (
            inventory.state not in {"ready", "absent"}
            and attribution.building_context is not None
        ):
            raise ValueError("an unread DPE inventory cannot carry building context")
        if attribution.graph_state != inventory.graph_state:
            raise ValueError("DPE graph states must agree")
        if not datasets_has_dpe:
            raise ValueError("datasets.dpe must be present and empty in V2")
        state = (self.dataset_states or {}).get("dpe")
        if state is None or state.dataset != "dpe" or state.presence != expected[2]:
            raise ValueError("DPE coarse state does not match the inventory")
        if inventory.state == "ready":
            if state.absence_reason is not None or state.raison is not None:
                raise ValueError("DPE coarse state carries an invalid public reason")
        elif inventory.state in {"absent", "identity_unavailable"}:
            allowed_reasons = {"not_reported", "source_delayed", "out_of_coverage"}
            if (
                state.absence_reason not in allowed_reasons
                or state.raison != RESOLUTION_RAISONS[state.absence_reason]
            ):
                raise ValueError("DPE coarse state carries an invalid public reason")
        elif (
            state.absence_reason != "temporary_error"
            or state.raison != RESOLUTION_RAISONS["temporary_error"]
        ):
            raise ValueError("DPE coarse state carries an invalid public reason")
        return self

    @field_serializer("datasets")
    def _serialize_datasets(
        self, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Emit numeric DB columns as JSON numbers, not Decimal-as-string."""
        return _coerce_decimals(value) if value is not None else None

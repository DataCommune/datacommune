"""Closed public contracts for factual DPE inventory comparison."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dpe.schemas import DpeAttribution, DpeInventoryPage

_CLOSED = ConfigDict(extra="forbid", hide_input_in_errors=True)

DpePropertyType = Literal["maison", "appartement"]
DpeSurfaceNature = Literal["habitable_dpe", "carrez", "annonce", "dvf", "autre"]
DpeMatchCriterionState = Literal[
    "known",
    "matching",
    "conflicting",
    "unknown",
    "not_applicable",
    "not_comparable",
]
DpeMatchBlocker = Literal[
    "address_unverified",
    "source_coverage_unknown",
    "source_state_unknown",
    "source_absent",
    "source_deactivated",
    "validity_unknown",
    "validity_expired",
    "validity_inconsistent",
    "validity_future",
    "replacement_unchecked",
    "replacement_replaced",
    "replacement_ambiguous",
    "graph_incoherent",
    "inventory_partial",
]
DpeMatchLimit = Literal[
    "dwelling_identity_unproven",
    "address_association_unverified",
    "source_coverage_unknown",
    "graph_incoherent",
    "inventory_partial",
    "candidates_outside_page",
    "candidate_count_unknown",
    "surface_has_no_calibrated_threshold",
]


class DpeMatchSurface(BaseModel):
    """A positive finite surface paired with its documentary nature."""

    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, allow_inf_nan=False
    )

    value: Annotated[float, Field(strict=True, gt=0)]
    nature: DpeSurfaceNature


class DpeMatchTarget(BaseModel):
    """Public optional criteria; private dwelling discriminants are forbidden."""

    model_config = _CLOSED

    type_bien: DpePropertyType | None = None
    surface: DpeMatchSurface | None = None


class DpeTypeComparison(BaseModel):
    """Explanation of the optional property-type criterion."""

    model_config = _CLOSED

    criterion: Literal["type_bien"] = "type_bien"
    state: DpeMatchCriterionState
    target: DpePropertyType | None = None
    source: DpePropertyType | Literal["autre"] | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> "DpeTypeComparison":
        if self.state == "not_applicable":
            valid = self.target is None
        elif self.state == "unknown":
            valid = self.target is not None and self.source is None
        elif self.state == "matching":
            valid = self.target is not None and self.source == self.target
        elif self.state == "conflicting":
            valid = self.target is not None and self.source not in {None, self.target}
        else:
            valid = False
        if not valid:
            raise ValueError("type comparison fields do not match their state")
        return self


class DpeSurfaceComparison(BaseModel):
    """Explanation of surface comparability without an implicit threshold."""

    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, allow_inf_nan=False
    )

    criterion: Literal["surface"] = "surface"
    state: DpeMatchCriterionState
    target: Annotated[float, Field(gt=0)] | None = None
    source: Annotated[float, Field(gt=0)] | None = None
    target_nature: DpeSurfaceNature | None = None
    source_nature: Literal["habitable_dpe"] = "habitable_dpe"
    delta: float | None = None

    @model_validator(mode="after")
    def _validate_delta(self) -> "DpeSurfaceComparison":
        comparable = self.state in {"known", "matching"}
        if comparable:
            valid = (
                self.target is not None
                and self.source is not None
                and self.target_nature == "habitable_dpe"
                and self.delta == self.source - self.target
                and ((self.state == "matching") == (self.delta == 0))
            )
        elif self.state == "not_applicable":
            valid = self.target is None and self.target_nature is None
        elif self.state == "unknown":
            valid = (
                self.target is not None
                and self.target_nature == "habitable_dpe"
                and self.source is None
            )
        elif self.state == "not_comparable":
            valid = self.target is not None and self.target_nature not in {
                None,
                "habitable_dpe",
            }
        else:
            valid = False
        if not valid or (not comparable and self.delta is not None):
            raise ValueError("surface comparison fields do not match their state")
        return self


DpeCriterionComparison = Annotated[
    DpeTypeComparison | DpeSurfaceComparison,
    Field(discriminator="criterion"),
]


class DpeDocumentComparison(BaseModel):
    """Factual comparison for one preserved inventory document."""

    model_config = _CLOSED

    document_ref: str = Field(min_length=1)
    criteria: list[DpeCriterionComparison] = Field(min_length=2, max_length=2)
    blockers: list[DpeMatchBlocker] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_criteria(self) -> "DpeDocumentComparison":
        if [criterion.criterion for criterion in self.criteria] != [
            "type_bien",
            "surface",
        ]:
            raise ValueError("comparison requires ordered type and surface criteria")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("comparison blockers must be unique")
        return self


class DpeMatchResult(BaseModel):
    """Non-attributive comparison result preserving the complete input page."""

    model_config = _CLOSED

    inventory: DpeInventoryPage
    attribution: DpeAttribution
    rule_version: Literal["dpe-factual-comparison-v1"] = "dpe-factual-comparison-v1"
    comparisons: list[DpeDocumentComparison] = Field(default_factory=list)
    limits: list[DpeMatchLimit] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_non_attribution(self) -> "DpeMatchResult":
        if self.attribution.status not in {"unassigned", "absent", "error"}:
            raise ValueError("matching cannot promote a dwelling attribution")
        if self.attribution.diagnostic is not None or self.attribution.candidates:
            raise ValueError("matching cannot carry attributed documents")
        inventory_refs = [item.document_ref for item in self.inventory.items]
        comparison_refs = [item.document_ref for item in self.comparisons]
        if comparison_refs != inventory_refs or len(set(comparison_refs)) != len(
            comparison_refs
        ):
            raise ValueError("comparisons must preserve unique inventory references")
        if len(set(self.limits)) != len(self.limits):
            raise ValueError("matching limits must be unique")
        return self

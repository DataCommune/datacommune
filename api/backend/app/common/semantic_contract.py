"""Backend semantic contract registry (Phase 43, D-03/D-04/D-06).

Single, versioned authority for every Fiche field: canonical field_id,
value type, unit, precision, plausible range, zero semantics, formatter
and allowed absence states. This module reads and validates the packaged
copy of the registry shipped alongside it (`fiche-fields.v1.json`), which
is kept byte-identical to the canonical `contracts/fiche-fields.v1.json`
by `scripts/sync_semantic_contract.py`. Manual edits to the packaged copy
are forbidden — the sync script is the only writer.

D-06: Phase 43 locks value/field semantics only. Provenance, freshness,
cadence and producer metadata belong to Phase 44 and must never appear
in this contract — see `RESERVED_PHASE_44_KEYS`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACT_PATH = Path(__file__).parent / "fiche-fields.v1.json"

# D-04: the canonical value states — closed set, never extended ad hoc. The
# five of Phase 43, plus the states of D-47 (gate of 2026-09-24): the typed
# absences of the flat /v1/commune columns, aligned on IndicateurAbsent.etat.
ALLOWED_STATES = frozenset(
    {
        "value",
        "not_reported",
        "out_of_coverage",
        "temporary_error",
        "not_applicable",
        "insufficient_sample",
        "not_available",
        "zero_denominator",
        "suppressed",
        "not_qualified",
    }
)

# Closed formatter enum — every formatter identifier must map to a pure
# function in the shared frontend primitives (D-05), never an arbitrary
# expression evaluated from the JSON.
ALLOWED_FORMATTERS = frozenset(
    {
        "number",
        "percent_points",
        "currency",
        "area_km2",
        "density",
        "date",
        "year",
        "boolean",
        "text",
    }
)

ALLOWED_VALUE_TYPES = frozenset({"string", "integer", "decimal", "boolean"})

# D-24: the reasons qualifying ``out_of_coverage`` -- exact list, in order of
# precedence; never a fifth state. Mirrored by app.common.schemas.OutOfCoverageReason.
ALLOWED_OUT_OF_COVERAGE_REASONS = (
    "dataset_non_heberge",
    "territoire_hors_perimetre",
    "source_sans_couverture",
)

ALLOWED_ZERO_SEMANTICS = frozenset(
    {"measured_zero", "implausible_zero", "not_applicable"}
)

# SEM-02 (04-03): the nature of the match between a served value and the
# object of the page -- closed list, in this order. A field may carry one
# ``rattachement``; a value outside this list is refused, never guessed.
ALLOWED_RATTACHEMENTS = (
    "adresse_saisie_diagnostiqueur",
    "parcelle_du_point",
    "batiment_du_point",
    "batiment_rnb_de_l_adresse",
    "au_point",
    "rayon_du_point",
    "commune_de_l_adresse",
    "eligibilite_adresse",
    "commune_reference",
    "adresse_declaree_vente",
    "adresse_brute_exacte",
    # 04.7-10: a key figure of a departement page, over its communes.
    "departement",
)

# D-06: Phase 44's contract (provenance/freshness) — must never be duplicated here.
RESERVED_PHASE_44_KEYS = frozenset({"provenance", "freshness", "cadence", "producer"})

_REQUIRED_FIELD_KEYS = frozenset(
    {
        "field_id",
        "value_type",
        "unit",
        "precision",
        "plausible_range",
        "zero_semantics",
        "formatter",
        "allowed_states",
    }
)


class SemanticContractError(ValueError):
    """Raised when the semantic contract registry fails D-03/D-04/D-06 validation."""


@dataclass(frozen=True)
class FieldContract:
    """Typed, validated view of one semantic-contract entry (D-03)."""

    field_id: str
    value_type: str
    unit: str
    fraction_digits: int | None
    range_min: float | None
    range_max: float | None
    zero_semantics: str
    formatter: str
    allowed_states: tuple[str, ...]
    description: str | None = None
    rattachement: str | None = None


def validate_field_entry(entry: dict[str, Any]) -> list[str]:  # noqa: C901
    """Validate a single semantic-contract entry.

    Returns a list of human-readable error messages; empty list = valid.
    """
    errors: list[str] = []

    missing = _REQUIRED_FIELD_KEYS - entry.keys()
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")
        return errors  # cannot validate the rest without the required shape

    reserved = RESERVED_PHASE_44_KEYS & entry.keys()
    if reserved:
        errors.append(f"reserved Phase 44 keys must not appear: {sorted(reserved)}")

    value_type = entry["value_type"]
    if value_type not in ALLOWED_VALUE_TYPES:
        errors.append(f"unknown value_type {value_type!r} — not in closed enum")

    formatter = entry["formatter"]
    if formatter not in ALLOWED_FORMATTERS:
        errors.append(f"unknown formatter {formatter!r} — not in closed enum")

    zero_semantics = entry["zero_semantics"]
    if zero_semantics not in ALLOWED_ZERO_SEMANTICS:
        errors.append(f"unknown zero_semantics {zero_semantics!r} — not in closed enum")

    states = entry["allowed_states"]
    if not states:
        errors.append("allowed_states must not be empty")
    else:
        unknown_states = set(states) - ALLOWED_STATES
        if unknown_states:
            errors.append(f"unknown states {sorted(unknown_states)} — not D-04")

    precision = entry["precision"]
    if not isinstance(precision, dict) or "fraction_digits" not in precision:
        errors.append("precision must be an object with a fraction_digits key")

    plausible_range = entry["plausible_range"]
    if not isinstance(plausible_range, dict) or {"min", "max"} - plausible_range.keys():
        errors.append("plausible_range must be an object with min/max keys")

    if "rattachement" in entry and entry["rattachement"] not in ALLOWED_RATTACHEMENTS:
        errors.append(
            f"unknown rattachement {entry['rattachement']!r} — not in closed list"
        )

    return errors


def validate_registry(entries: list[dict[str, Any]]) -> list[str]:
    """Validate a whole registry: per-entry rules + field_id uniqueness."""
    errors: list[str] = []
    seen_ids: set[str] = set()

    for entry in entries:
        field_id = entry.get("field_id")
        if field_id in seen_ids:
            errors.append(f"duplicate field_id: {field_id!r}")
        elif field_id is not None:
            seen_ids.add(field_id)
        errors.extend(validate_field_entry(entry))

    return errors


def validate_out_of_coverage_reasons(document: dict[str, Any]) -> list[str]:
    """The document lists exactly the D-24 reasons, in order, without duplicate."""
    reasons = document.get("allowed_out_of_coverage_reasons")
    if not isinstance(reasons, list):
        return ["allowed_out_of_coverage_reasons must be a list"]
    if len(reasons) != len(set(reasons)):
        return [f"duplicate out-of-coverage reasons: {reasons}"]
    if tuple(reasons) != ALLOWED_OUT_OF_COVERAGE_REASONS:
        return [
            f"allowed_out_of_coverage_reasons must be exactly "
            f"{list(ALLOWED_OUT_OF_COVERAGE_REASONS)}, got {reasons}"
        ]
    return []


def validate_rattachements(document: dict[str, Any]) -> list[str]:
    """The document lists exactly the SEM-02 rattachements, in order."""
    rattachements = document.get("allowed_rattachements")
    if not isinstance(rattachements, list):
        return ["allowed_rattachements must be a list"]
    if tuple(rattachements) != ALLOWED_RATTACHEMENTS:
        return [
            f"allowed_rattachements must be exactly "
            f"{list(ALLOWED_RATTACHEMENTS)}, got {rattachements}"
        ]
    return []


def _to_field_contract(entry: dict[str, Any]) -> FieldContract:
    return FieldContract(
        field_id=entry["field_id"],
        value_type=entry["value_type"],
        unit=entry["unit"],
        fraction_digits=entry["precision"].get("fraction_digits"),
        range_min=entry["plausible_range"].get("min"),
        range_max=entry["plausible_range"].get("max"),
        zero_semantics=entry["zero_semantics"],
        formatter=entry["formatter"],
        allowed_states=tuple(entry["allowed_states"]),
        description=entry.get("description"),
        rattachement=entry.get("rattachement"),
    )


def _parse_and_validate(raw: bytes, source: Path) -> dict[str, FieldContract]:
    """Parse+validate registry bytes into an indexed dict of FieldContract.

    Shared by `load_registry` (packaged copy) and any caller that needs to
    validate an arbitrary registry document (e.g. the sync script validating
    the canonical `contracts/` source before propagating it).
    """
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SemanticContractError(f"{source} is not valid JSON: {exc}") from exc

    fields = document.get("fields")
    if not isinstance(fields, list) or not fields:
        raise SemanticContractError(f"{source} has no non-empty 'fields' array")

    errors = (
        validate_registry(fields)
        + validate_out_of_coverage_reasons(document)
        + validate_rattachements(document)
    )
    if errors:
        raise SemanticContractError(
            f"{source} failed semantic contract validation: " + "; ".join(errors)
        )

    return {entry["field_id"]: _to_field_contract(entry) for entry in fields}


@lru_cache(maxsize=1)
def load_registry() -> dict[str, FieldContract]:
    """Load, validate and index the packaged semantic contract.

    Raises `SemanticContractError` (fail loud, never silently degrade) if the
    packaged copy is missing, malformed, or fails D-03/D-04/D-06 validation.
    Cached — the contract is immutable for the lifetime of the process.
    """
    if not _CONTRACT_PATH.exists():
        raise SemanticContractError(f"semantic contract not found at {_CONTRACT_PATH}")
    return _parse_and_validate(_CONTRACT_PATH.read_bytes(), _CONTRACT_PATH)


def get_field(field_id: str) -> FieldContract:
    """Typed access to a single field contract. Raises KeyError if unknown."""
    try:
        return load_registry()[field_id]
    except KeyError:
        raise KeyError(f"no semantic contract for field_id {field_id!r}") from None


def list_field_ids() -> frozenset[str]:
    """All field_id known to the registry — the coverage assertion surface."""
    return frozenset(load_registry().keys())


def assert_fields_covered(field_ids: Iterable[str]) -> None:
    """Fail loudly if any of `field_ids` has no semantic contract (D-03).

    Intended for call sites (mappers, schemas) that must prove every field
    they emit is governed by this registry — a missing entry is a build-time
    bug to fix in `contracts/fiche-fields.v1.json`, not a runtime degradation.
    """
    missing = sorted(set(field_ids) - list_field_ids())
    if missing:
        raise SemanticContractError(f"fields missing a semantic contract: {missing}")

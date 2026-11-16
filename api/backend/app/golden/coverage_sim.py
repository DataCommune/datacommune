"""Pure simulation of golden-case category coverage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from scripts.verify_golden_cases import derive_category, observation_is_satisfied


def _manifest_cases(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not all(
        isinstance(entry, Mapping) for entry in cases
    ):
        raise ValueError("manifest cases must be a list of objects")
    return cases


def _case_id(entry: Mapping[str, Any]) -> str:
    case_id = entry.get("case_id")
    if not isinstance(case_id, str):
        raise ValueError("every case and reserve must have a string case_id")  # noqa: TRY004
    return case_id


def _api_states(entry: Mapping[str, Any]) -> dict[str, str]:
    invariants = entry.get("invariants")
    api = invariants.get("api") if isinstance(invariants, Mapping) else None
    if not isinstance(api, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in api.items()
    ):
        raise ValueError(f"{_case_id(entry)} must provide string API states")
    return dict(api)


def _derived_entry(
    entry: Mapping[str, Any], patch: Mapping[str, str] | None = None
) -> dict[str, Any]:
    states = _api_states(entry)
    if patch is not None:
        if any(
            not key.endswith("_state") or not isinstance(value, str)
            for key, value in patch.items()
        ):
            raise ValueError("patches must map _state keys to strings")
        states.update(patch)
    invariants = dict(entry["invariants"])
    invariants["api"] = states
    derived = dict(entry)
    derived["invariants"] = invariants
    derived["category"] = derive_category(states)
    return derived


def _prepare_manifest(
    manifest: Mapping[str, Any], patches: Mapping[str, Mapping[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    cases = _manifest_cases(manifest)
    by_id = {_case_id(entry): entry for entry in cases}
    if len(by_id) != len(cases):
        raise ValueError("manifest contains duplicate case IDs")
    unknown = sorted(set(patches) - set(by_id))
    if unknown:
        raise ValueError(f"patches target unknown cases: {unknown}")

    derived = [
        _derived_entry(entry, patches.get(case_id)) for case_id, entry in by_id.items()
    ]
    changes = {
        _case_id(entry): entry["category"]
        for entry in derived
        if entry["category"] != by_id[_case_id(entry)].get("category")
    }
    return derived, dict(sorted(changes.items()))


def _prepare_reserves(
    reserves: Sequence[Mapping[str, Any]], existing_ids: set[str]
) -> list[dict[str, Any]]:
    derived = [_derived_entry(entry) for entry in reserves]
    ids = [_case_id(entry) for entry in derived]
    if len(set(ids)) != len(ids):
        raise ValueError("reserves contain duplicate case IDs")
    overlap = sorted(existing_ids.intersection(ids))
    if overlap:
        raise ValueError(f"reserve case IDs already exist in manifest: {overlap}")
    return sorted(derived, key=_case_id)


def _missing_categories(
    entries: Sequence[Mapping[str, Any]], required: Sequence[str]
) -> list[str]:
    observed = {entry.get("category") for entry in entries}
    return sorted(set(required) - observed)


def _missing_observations(
    entries: Sequence[Mapping[str, Any]], requirements: Sequence[Mapping[str, Any]]
) -> list[int]:
    return [
        index
        for index, requirement in enumerate(requirements)
        if not any(observation_is_satisfied(entry, requirement) for entry in entries)
    ]


def _cardinality(
    entries: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    details: dict[str, dict[str, int | bool]] = {}
    violations: list[str] = []
    for case_type, bounds in schema["cardinality"].items():
        count = sum(entry.get("type") == case_type for entry in entries)
        valid = bounds["min"] <= count <= bounds["max"]
        counts[case_type] = count
        details[case_type] = {
            "count": count,
            "min": bounds["min"],
            "max": bounds["max"],
            "remaining": bounds["max"] - count,
            "valid": valid,
        }
        if not valid:
            violations.append(
                f"expected {bounds['min']}-{bounds['max']} {case_type}, got {count}"
            )
    return {
        "valid": not violations,
        "counts": counts,
        "types": details,
        "violations": violations,
    }


def _covers_contract(
    entries: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]
) -> bool:
    return (
        not _missing_categories(entries, schema["required_categories"])
        and not _missing_observations(entries, schema.get("required_observations", ()))
        and _cardinality(entries, schema)["valid"]
    )


def _minimal_reserves(
    entries: list[dict[str, Any]],
    reserves: list[dict[str, Any]],
    schema: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    for size in range(len(reserves) + 1):
        for chosen in combinations(reserves, size):
            if _covers_contract([*entries, *chosen], schema):
                return list(chosen)
    return None


def _reserve_candidates(
    reserves: Sequence[Mapping[str, Any]], missing: Sequence[str]
) -> dict[str, list[str]]:
    return {
        category: [
            _case_id(entry) for entry in reserves if entry["category"] == category
        ]
        for category in missing
    }


def _simulation_result(
    entries: list[dict[str, Any]],
    reclassified: dict[str, str | None],
    reserves: list[dict[str, Any]],
    selected: list[dict[str, Any]] | None,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    required = schema["required_categories"]
    requirements = schema.get("required_observations", ())
    missing = _missing_categories(entries, required)
    after = entries if selected is None else [*entries, *selected]
    return {
        "missing": missing,
        "reclassified": reclassified,
        "reserve_candidates": _reserve_candidates(reserves, missing),
        "selected_reserves": [] if selected is None else list(map(_case_id, selected)),
        "remaining_missing": _missing_categories(after, required),
        "missing_observations": _missing_observations(entries, requirements),
        "remaining_missing_observations": _missing_observations(after, requirements),
        "coverage_possible": selected is not None,
        "cardinality": {
            "current": _cardinality(entries, schema),
            "after_selection": None
            if selected is None
            else _cardinality(after, schema),
        },
    }


def simulate_category_coverage(
    manifest: Mapping[str, Any],
    patches: Mapping[str, Mapping[str, str]],
    reserves: Sequence[Mapping[str, Any]],
    *,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Simulate category gates and the minimal deterministic reserve takeover.

    ``patches`` maps existing case IDs to partial API-state mappings. Reserves
    use the same redacted shape as manifest cases; their recorded category is
    ignored and derived again. Inputs are never mutated.
    """
    entries, reclassified = _prepare_manifest(manifest, patches)
    existing_ids = {_case_id(entry) for entry in entries}
    reserve_entries = _prepare_reserves(reserves, existing_ids)
    selected = _minimal_reserves(entries, reserve_entries, schema)
    return _simulation_result(entries, reclassified, reserve_entries, selected, schema)

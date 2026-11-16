"""Offline tests for the pure golden category coverage simulator."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.golden import coverage_sim
from app.golden.coverage_sim import simulate_category_coverage

pytestmark = pytest.mark.unit

_SCENARIO_PATH = Path(__file__).parent / "fixtures" / "coverage_sim_pre_51_09.json"
_REAL_PATCHES = {
    "golden-address-003": {"dpe_state": "not_reported"},
    "golden-address-005": {"dpe_state": "not_reported"},
    "golden-address-007": {"dpe_state": "not_reported"},
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_manifest() -> dict[str, Any]:
    return _load_json(_SCENARIO_PATH)["manifest"]


def _scenario_schema() -> dict[str, Any]:
    return _load_json(_SCENARIO_PATH)["schema"]


def _reserve(case_id: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "type": "address",
        "invariants": {
            "db": {},
            "api": {
                "dpe_state": "value",
                "dvf_state": "not_reported",
            },
            "mapper": {},
        },
    }


def test_real_51_09_patch_predicts_the_no_dvf_blockage() -> None:
    result = simulate_category_coverage(
        _scenario_manifest(),
        _REAL_PATCHES,
        [],
        schema=_scenario_schema(),
    )

    assert result["missing"] == ["no_dvf"]
    assert result["reclassified"] == {
        "golden-address-003": "dataset_out_of_coverage",
        "golden-address-005": "no_dpe",
        "golden-address-007": "no_dpe",
    }
    assert result["reserve_candidates"] == {"no_dvf": []}
    assert result["selected_reserves"] == []
    assert result["coverage_possible"] is False
    assert result["cardinality"]["current"]["valid"] is True


def test_equivalent_reserves_use_minimum_then_lexicographic_choice() -> None:
    result = simulate_category_coverage(
        _scenario_manifest(),
        _REAL_PATCHES,
        [_reserve("golden-address-010"), _reserve("golden-address-009")],
        schema=_scenario_schema(),
    )

    assert result["missing"] == ["no_dvf"]
    assert result["reserve_candidates"] == {
        "no_dvf": ["golden-address-009", "golden-address-010"]
    }
    assert result["selected_reserves"] == ["golden-address-009"]
    assert result["remaining_missing"] == []
    assert result["coverage_possible"] is True
    assert result["cardinality"]["after_selection"]["counts"]["address"] == 7


def test_cardinality_blocks_an_otherwise_matching_reserve() -> None:
    schema = _scenario_schema()
    schema["cardinality"]["address"]["max"] = 6

    result = simulate_category_coverage(
        _scenario_manifest(),
        _REAL_PATCHES,
        [_reserve("golden-address-009")],
        schema=schema,
    )

    assert result["reserve_candidates"] == {"no_dvf": ["golden-address-009"]}
    assert result["selected_reserves"] == []
    assert result["coverage_possible"] is False
    assert result["cardinality"]["current"]["valid"] is True
    assert result["cardinality"]["current"]["types"]["address"]["remaining"] == 0
    assert result["cardinality"]["after_selection"] is None


def test_simulation_does_not_mutate_its_inputs() -> None:
    manifest = _scenario_manifest()
    schema = _scenario_schema()
    reserves = [_reserve("golden-address-009")]
    inputs_before = copy.deepcopy((manifest, _REAL_PATCHES, reserves, schema))

    simulate_category_coverage(
        manifest,
        _REAL_PATCHES,
        reserves,
        schema=schema,
    )

    assert (manifest, _REAL_PATCHES, reserves, schema) == inputs_before


def test_simulator_imports_the_shared_coverage_authorities() -> None:
    source = Path(coverage_sim.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "scripts.verify_golden_cases"
        for alias in node.names
    }
    local_functions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    assert {"derive_category", "observation_is_satisfied"} <= imported
    assert "derive_category" not in local_functions
    assert "observation_is_satisfied" not in local_functions


def test_simulator_is_pure_without_database_or_web_framework() -> None:
    source = Path(coverage_sim.__file__).read_text(encoding="utf-8").lower()

    for forbidden in ("sqlalchemy", "fastapi", "session", "httpx", "psycopg", "await "):
        assert forbidden not in source, (
            f"the simulator module must not mention {forbidden}"
        )

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from scripts import verify_golden_cases as golden

_FROZEN_DPE: dict[str, Any] = {
    "dpe_inventory_presence": "present",
    "dpe_attribution_status": "unassigned",
    "dpe_primary_diagnostic_present": False,
    "dpe_candidate_count": 0,
    "dpe_building_count": 4,
    "dpe_truncated": False,
    "dpe_nearby_diagnostic_present": False,
}


@pytest.mark.parametrize(
    ("key", "changed", "suffix"),
    [
        ("dpe_inventory_presence", "absent", " (observed absent)"),
        ("dpe_attribution_status", "absent", " (observed absent)"),
        ("dpe_primary_diagnostic_present", True, " (observed true)"),
        ("dpe_candidate_count", 1, " (observed 1)"),
        ("dpe_building_count", 5, " (observed 5)"),
        ("dpe_truncated", True, " (observed true)"),
        ("dpe_nearby_diagnostic_present", True, " (observed true)"),
    ],
)
def test_compare_dpe_observation_detects_each_frozen_invariant_drift(
    key: str, changed: Any, suffix: str
) -> None:
    observed = dict(_FROZEN_DPE)
    observed[key] = changed

    assert golden._compare_dpe_observation({"dpe": dict(_FROZEN_DPE)}, observed) == [
        f"dpe.{key}: mismatch{suffix}"
    ]


@pytest.mark.parametrize(
    ("missing_from", "expected_reason"),
    [
        ("manifest", "dpe.dpe_truncated: mismatch (observed false)"),
        ("observation", "dpe.dpe_truncated: mismatch"),
    ],
)
def test_compare_dpe_observation_requires_every_key_on_both_sides(
    missing_from: str,
    expected_reason: str,
) -> None:
    expected = {"dpe": dict(_FROZEN_DPE)}
    observed = dict(_FROZEN_DPE)
    target = expected["dpe"] if missing_from == "manifest" else observed
    target.pop("dpe_truncated")

    assert golden._compare_dpe_observation(expected, observed) == [expected_reason]


def test_verify_case_blocks_dpe_drift_and_publishes_no_freeze_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "case_id": "golden-address-test",
        "type": "address",
        "invariants": {
            "db": {"has_commune_row": True},
            "api": {"dpe_state": "value"},
            "dpe": dict(_FROZEN_DPE),
        },
    }
    qualification = {
        "case_id": entry["case_id"],
        "departement": "44",
        **deepcopy(_FROZEN_DPE),
        "carrier_for": ["dpe_multiple_or_building"],
    }
    qualification["dpe_building_count"] = 5
    proof = {
        "inventory_route": "verified",
        "document_route": "verified",
        "source_fields_equal": True,
        "no_store": True,
    }

    def collect(*args: Any, **kwargs: Any):
        return (
            {
                "db": {"has_commune_row": True},
                "api": {"dpe_state": "value"},
            },
            {},
            {},
            (qualification, proof),
        )

    def unexpected_patch(*args: Any, **kwargs: Any):
        raise AssertionError("a divergent case must not build freeze material")

    monkeypatch.setattr(golden, "collect_live_observation_projected", collect)
    monkeypatch.setattr(golden, "build_manifest_patch_entry", unexpected_patch)

    result = golden._verify_case(
        object(),
        object(),
        entry,
        {entry["case_id"]: {"query": "never-rendered-private-input"}},
    )

    assert result["ok"] is False
    assert result["reasons"][0] == ("dpe.dpe_building_count: mismatch (observed 5)")
    assert "dpe_qualification" not in result
    assert "dpe_documentary_proof" not in result
    assert "manifest_patch" not in result
    assert "never-rendered-private-input" not in str(result)

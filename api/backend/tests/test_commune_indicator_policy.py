import json

import pytest
from pydantic import ValidationError

from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_policy import RuntimeIndicatorPolicy


def payload() -> dict:
    pin = {
        "dataset_name": "dpe",
        "source_generation": "291f9c13-ed87-4d00-9dfa-2aa7613a20f7",
        "source_snapshot_sha256": "1" * 64,
        "cog_sha256": "2" * 64,
        "cog_version": "insee-cog-2026-01-01-identities",
        "policy_version": "reviewed-v1",
        "policy_sha256": "3" * 64,
        "evidence_sha256": "4" * 64,
        "scope": "preproduction",
        "checked_at": "2026-09-10T10:00:00Z",
    }
    return {
        "scope": "preproduction",
        "cog": {
            "path": "fixtures/cog.json",
            "root": ".",
            "sha256": "2" * 64,
            "version": "insee-cog-2026-01-01-identities",
        },
        "evidence_root": ".",
        "checked_at": "2026-09-10T10:00:00Z",
        "dpe": pin,
        "dvf": {
            **pin,
            "dataset_name": "dvf",
            "source_generation": "11111111-1111-4111-8111-111111111111",
        },
    }


def test_runtime_policy_is_closed_preproduction_and_independent() -> None:
    policy = RuntimeIndicatorPolicy.from_json(json.dumps(payload()))
    trust = policy.trust_inputs()
    assert trust.scope == "preproduction"
    assert trust.dpe is not None and trust.dvf is not None
    with pytest.raises(ValidationError):
        policy.scope = "synthetic"


@pytest.mark.parametrize(
    "fault", ["missing", "synthetic", "publication", "mismatch", "null_generation"]
)
def test_runtime_policy_fails_closed(fault: str) -> None:
    value = payload()
    raw = "" if fault == "missing" else value
    if fault in {"synthetic", "publication"}:
        value["scope"] = fault
    elif fault == "mismatch":
        value["dvf"]["cog_sha256"] = "9" * 64
    elif fault == "null_generation":
        value["dvf"]["source_generation"] = None
    with pytest.raises(ManifestRefused):
        RuntimeIndicatorPolicy.from_json(
            raw if isinstance(raw, str) else json.dumps(raw)
        )


def test_runtime_enablement_refuses_production() -> None:
    policy = RuntimeIndicatorPolicy.from_json(json.dumps(payload()))
    with pytest.raises(ManifestRefused, match="runtime_environment_refused"):
        policy.assert_runtime_environment("production")
    policy.assert_runtime_environment("preproduction")


def test_runtime_policy_rejects_duplicate_json_keys() -> None:
    raw = json.dumps(payload())
    duplicate = raw.replace(
        '"scope": "preproduction"', '"scope": "synthetic", "scope": "preproduction"', 1
    )
    with pytest.raises(ManifestRefused, match="runtime_policy_invalid"):
        RuntimeIndicatorPolicy.from_json(duplicate)

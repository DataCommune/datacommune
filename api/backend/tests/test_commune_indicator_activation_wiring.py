"""Activation stays explicit and its required PostGIS contract stays wired."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]


def test_activation_is_required_by_contract_ci():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/ci-data-contracts.yml").read_text()
    )
    jobs = workflow["jobs"]
    offline = "\n".join(
        str(step.get("run", "")) for step in jobs["offline-backend"]["steps"]
    )
    postgis = "\n".join(
        str(step.get("run", "")) for step in jobs["postgis-integration"]["steps"]
    )
    assert "tests/test_commune_indicator_activation_wiring.py" in offline
    assert "tests/integration/test_commune_indicator_activation.py" in postgis
    assert jobs["required-contracts"]["needs"]


def test_no_runtime_surface_imports_activation():
    forbidden = (
        "api/backend/app/etl/hooks.py",
        "api/backend/app/etl/runner.py",
        "api/backend/app/commune/router.py",
        "api/backend/app/config.py",
    )
    for relative in forbidden:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "indicator_activation" not in source
        assert "rebuild_and_activate_indicator_results" not in source

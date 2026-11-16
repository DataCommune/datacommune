"""Run the doubles test of ``infra/public/public-deploy.sh`` in the backend suite.

``infra/public/tests/test-public-deploy.sh`` proves that the public deployment
stops the old api before ``alembic upgrade head`` and starts a release again only
on a schema its migrations end at (WR-02 of 04.1-REVIEW-CIBLEE.md), against
doubles of ssh, pct, compose and docker: no hypervisor, no ssh, no network. Running it
here puts it in ``scripts/test-backend.ps1`` and in the ``test`` job of
``ci-api.yml``.

Played whole, the script took 74 s alone under Git Bash, nearly all of it
process spawns of the doubles (KI-19 shape). Its scenarios are played in five
groups side by side; every scenario still runs, and
``test_every_scenario_belongs_to_a_group`` fails if one is left out.
"""

from __future__ import annotations

import os

import pytest

from tests.shell_doubles import find_bash, run_groups, scenarios_of

SCRIPT = "infra/public/tests/test-public-deploy.sh"

# Scenario letters of each group, in the order the script plays them.
GROUPES = {
    "nominal": ["a", "b", "c", "d", "e"],
    "rollback": ["f", "g", "h", "i"],
    "backup": ["j", "k", "l", "l3", "l4", "l2"],
    "usage": ["m", "n"],
    "publication": ["o", "p"],
}


def test_every_scenario_belongs_to_a_group() -> None:
    scenarios = scenarios_of(SCRIPT)
    assert len(scenarios) >= 19, scenarios
    joues = [s for groupe in GROUPES.values() for s in groupe]
    assert sorted(joues) == sorted(scenarios)


def test_public_deploy_stops_api_before_migrating_on_doubles() -> None:
    bash = find_bash()
    if bash is None:
        if os.environ.get("CI"):
            pytest.fail("bash is required to run the deploy script doubles on CI")
        pytest.skip("bash is required to run the deploy script doubles")
    # PUBLIC_* would point the script or its doubles elsewhere (script, ids, dirs).
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("FAKE_", "PUBLIC_", "DEPLOY_GROUP"))
    }
    results = run_groups(bash, SCRIPT, env, "DEPLOY_GROUP", list(GROUPES), timeout=300)
    for groupe, (code, sortie, erreurs) in results.items():
        assert code == 0, f"{groupe}:\n{sortie}{erreurs}"
        lignes = sortie.strip().splitlines()
        assert lignes[-1] == "PUBLIC_DEPLOY_OK", f"{groupe}:\n{sortie}{erreurs}"
        assert lignes[-2] == "SCENARIOS " + " ".join(GROUPES[groupe]), groupe

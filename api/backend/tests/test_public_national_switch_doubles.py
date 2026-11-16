"""Run the doubles test of ``public-db-restore.sh --from-zfs-clone`` in the backend
suite.

``infra/public/tests/test-public-national-switch.sh`` proves the national
publication on the public CT by ZFS clone (plan 04.2-05): refusals, order of the
steps, public application rows carried and no work key, dry-run without writes,
automatic and manual rollback to the mounts of before, deletion only through
--drop-previous; against doubles of ssh, pct, zfs, zpool, pvesm and docker: no
hypervisor, no ssh, no network. Running it here puts it in ``scripts/test-backend.ps1``
and in the ``test`` job of ``ci-api.yml``. The SQL it plays is proven on a real
PostgreSQL by ``tests/integration/test_public_national_reset.py``.

KI-19: played whole, the script took 263 s alone under Git Bash for a 300 s
envelope, and went over it under load. The time is process spawns of the
doubles (each simulated SQL is pct, sh, docker ps, head and docker exec). The
script is now played in three groups, one case each; every scenario still runs,
and ``test_every_scenario_belongs_to_a_group`` fails if one is left out.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.shell_doubles import REPO, find_bash

SCRIPT = "infra/public/tests/test-public-national-switch.sh"

# Scenario letters of each group, in the order the script plays them.
GROUPES = {
    "chaine": ["a", "g", "h", "i"],
    "refus": ["b", "c", "d", "k", "j"],
    "replis": ["e", "f", "f2"],
}


def test_every_scenario_belongs_to_a_group() -> None:
    texte = (Path(REPO) / SCRIPT).read_text(encoding="utf-8")
    scenarios = re.findall(r"^# \(([a-z0-9]+)\) ", texte, flags=re.MULTILINE)
    assert len(scenarios) >= 12, scenarios
    joues = [s for groupe in GROUPES.values() for s in groupe]
    assert sorted(joues) == sorted(scenarios)


@pytest.mark.parametrize("groupe", sorted(GROUPES))
def test_public_national_switch_on_doubles(groupe: str) -> None:
    bash = find_bash()
    if bash is None:
        if os.environ.get("CI"):
            pytest.fail("bash is required to run the switch script doubles on CI")
        pytest.skip("bash is required to run the switch script doubles")
    # PUBLIC_* would point the script or its doubles elsewhere (script, ids, dirs).
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("FAKE_", "PUBLIC_", "SWITCH_"))
    }
    env["SWITCH_GROUP"] = groupe
    result = subprocess.run(
        [bash, SCRIPT],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lignes = result.stdout.strip().splitlines()
    assert lignes[-1] == "PUBLIC_SWITCH_TESTS_OK"
    assert lignes[-2] == "SCENARIOS " + " ".join(GROUPES[groupe])

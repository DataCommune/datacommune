"""Reconcile tests/integration/ (what exists) with CI (what actually runs).

``ci-api.yml`` ignores ``tests/integration`` entirely (the disposable PostGIS
harness is not provisioned there), so the ONLY workflow that executes these
files is ``ci-data-contracts.yml``, which passes an explicit path list to
``scripts/run_contract_integration.ps1``. A file present in the directory but
absent from that list is therefore executed by NO workflow -- the exact defect
found on 2026-08-05: the guard against the June widening regression existed on
disk and was played nowhere, while every signal stayed green.

This test closes the declared-vs-executed divergence in BOTH directions:

* every ``test_*.py`` under ``tests/integration/`` must appear in the workflow
  command (a new file cannot be silently orphaned);
* every ``tests/integration/`` path named by the workflow must exist on disk
  (a renamed/deleted file cannot leave a stale entry that pwsh would fail on).

It is collected by the global pytest suite (plain offline file reads), and
``ci-api.yml`` triggers on ``api/**``, so adding an integration test without
updating the workflow turns CI red on the same push.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# api/backend/tests -> backend -> api -> repo root == parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_DIR = _REPO_ROOT / "api" / "backend" / "tests" / "integration"
WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci-data-contracts.yml"

# Non-vacuity anchor (the 2026-08-05 lesson: a guard must be provably able to
# bite). This file IS the doctrine guard this contract was written for; if the
# glob below ever fails to see it, the test itself is broken and must fail
# rather than pass on an empty directory listing.
# ``test_rnb_liens_preuve_batiment.py`` was a second anchor until 2026-08-06,
# when the Lot C2 path it guarded was removed after measurement.
_KNOWN_GUARDS = frozenset({"test_rnb_liens_elargissement.py"})

# Local-only runs against producer files CI does not hold (the real BDNB
# exports, 1.4 GB, plan 04.7-19 T6), keyed to the variable that opts them in.
# Skipped without it, they would turn the workflow's disposable-DB gate red
# (scripts/contract_test_gate.py: every collected item must execute), so the
# workflow never lists them; they are played by hand through
# ``scripts/test-postgis.ps1 -Paths``. Closed list: each file skips on its
# variable, stays out of the workflow and is named by test-postgis.ps1.
LOCAL_OPT_IN: dict[str, str] = {"test_bdnb_zips_locaux.py": "DC_BDNB_ZIPS_DIR"}
POSTGIS_RUNNER = _REPO_ROOT / "scripts" / "test-postgis.ps1"


def _files_on_disk() -> set[str]:
    return {p.name for p in INTEGRATION_DIR.glob("test_*.py")}


def _paths_in_workflow() -> set[str]:
    """Every ``tests/integration/*.py`` path passed to a run step."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    listed: set[str] = set()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            for match in re.findall(
                r"tests/integration/\S+\.py", str(step.get("run", ""))
            ):
                listed.add(match)
    return listed


def test_glob_sees_the_known_doctrine_guards() -> None:
    files = _files_on_disk()
    assert files >= _KNOWN_GUARDS, (
        f"integration glob is blind (found only {sorted(files)}): "
        "this contract test cannot vouch for anything"
    )


def test_every_integration_test_file_is_run_by_the_workflow() -> None:
    listed = {path.rsplit("/", 1)[-1] for path in _paths_in_workflow()}
    orphans = _files_on_disk() - listed - set(LOCAL_OPT_IN)
    assert not orphans, (
        f"{sorted(orphans)} exist under tests/integration/ but no workflow "
        "runs them: add them to the run_contract_integration.ps1 call in "
        "ci-data-contracts.yml (ci-api.yml ignores the directory entirely)"
    )


def test_every_workflow_entry_exists_on_disk() -> None:
    stale = {
        path
        for path in _paths_in_workflow()
        if not (_REPO_ROOT / "api" / "backend" / path).is_file()
    }
    assert not stale, (
        f"{sorted(stale)} are passed to run_contract_integration.ps1 but do "
        "not exist: the postgis-integration job would fail on collection"
    )


def test_local_opt_in_files_skip_on_their_variable_and_stay_out_of_ci() -> None:
    listed = {path.rsplit("/", 1)[-1] for path in _paths_in_workflow()}
    runner = POSTGIS_RUNNER.read_text(encoding="utf-8")
    for name, variable in LOCAL_OPT_IN.items():
        source = (INTEGRATION_DIR / name).read_text(encoding="utf-8")
        assert f'os.environ.get("{variable}"' in source and "skipif" in source, (
            f"{name} is declared local opt-in but does not skip on {variable}"
        )
        assert name not in listed, (
            f"{name} is local opt-in: listed by the workflow, its skip would "
            "turn the disposable-DB gate red"
        )
        # The full run of test-postgis.ps1 reconciles disk and workflow too.
        assert f"'tests/integration/{name}'" in runner, (
            f"{name} is not set aside by scripts/test-postgis.ps1"
        )

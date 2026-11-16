"""Regression witnesses for COR-03: source triggers and real pytest exits."""

import os
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/ci-data-contracts.yml"
SOURCES = (
    "api/backend/app/ban/queries.py",
    "api/backend/app/demographie/backfill.py",
    "api/backend/app/etl/hooks.py",
    "api/backend/app/common/matviews.py",
    "api/backend/app/etl/runner.py",
    "api/backend/app/etl_revenus/pipeline.py",
    "api/backend/app/validation/base.py",
    "api/backend/migrations/versions/future.py",
)


def _github_glob(pattern):
    # GitHub path patterns: `*` stays inside one segment, `**` crosses them.
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def push_triggers(push, path):
    """Whether a push changing `path` runs the workflow: `paths` patterns apply
    in order, the last one matching decides, a `!` pattern excludes."""
    assert "paths-ignore" not in push
    triggered = False
    for pattern in push["paths"]:
        if _github_glob(pattern.removeprefix("!")).match(path):
            triggered = not pattern.startswith("!")
    return triggered


@pytest.mark.parametrize("source", SOURCES)
def test_sources_trigger_their_postgis_gate(source):
    # BaseLoader preserves the YAML 1.2 key "on" used by GitHub Actions.
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    push = workflow["on"]["push"]
    # Branch strategy `none` since plan 01-07: `master` is the only branch.
    assert push["branches"] == ["master"]
    # Since quick 261024-f9r the push filter drops prose only: every source
    # still reaches the PostGIS gate on master.
    assert push_triggers(push, source)
    # REPO-04: no kept job may route back to the stopped homelab runner.
    assert all(job["runs-on"] == "ubuntu-latest" for job in workflow["jobs"].values())
    pull_request = workflow["on"]["pull_request"]
    assert any(fnmatchcase(source, p) for p in pull_request["paths"])
    assert "postgis-integration" in workflow["jobs"]
    assert "postgis-integration" in workflow["jobs"]["required-contracts"]["needs"]


@pytest.mark.parametrize(
    ("path", "triggered"),
    [
        ("docs/DECISIONS.md", False),
        (".planning/STATE.md", False),
        ("docs/audit-stabilisation-2026-09-08/lot-11/recovery/probe.py", True),
        # Read by tests/integration/test_route_scope_conformity.py.
        (".planning/phases/02-donnees-reparees-rejouables/02-DATASETS.md", True),
        ("README.md", True),
        (".github/workflows/ci-data-contracts.yml", True),
        ("api/backend/.dockerignore", True),
        ("scripts/run_contract_integration.ps1", True),
    ],
)
def test_push_skips_only_prose_nobody_reads(path, triggered):
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert push_triggers(workflow["on"]["push"], path) is triggered


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("def test_ok(): assert True", 0),
        ("def test_bad(): assert False", 1),
        ("import pytest\ndef test_skip(): pytest.skip('required DB absent')", 1),
        ("import pytest\n@pytest.mark.xfail\ndef test_xfail(): assert False", 1),
        (
            "import pytest\npytest.skip('missing dependency', allow_module_level=True)",
            1,
        ),
        (
            "import pytest\n@pytest.fixture\n"
            "def db(): pytest.skip('required DB absent during setup')\n"
            "def test_setup(db): pass",
            1,
        ),
        (
            "import pytest\ndef test_ok(): assert True\n"
            "def test_skip(): pytest.skip('one required case omitted')",
            1,
        ),
        ("# no tests", 1),
    ],
)
@pytest.mark.parametrize("terminal", [True, False])
@pytest.mark.parametrize("collect_only", [False, True])
def test_required_gate_uses_real_process_exit(
    tmp_path, body, expected, terminal, collect_only
):
    probe = tmp_path / "test_probe.py"
    probe.write_text(body, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "api/backend"))
    # Options can be inherited by the wrapper through PYTEST_ADDOPTS.
    env["PYTEST_ADDOPTS"] = " ".join(
        option
        for option in (
            "" if terminal else "-p no:terminal",
            "--collect-only" if collect_only else "",
        )
        if option
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.contract_test_gate",
            "--confcutdir",
            str(tmp_path),
            "-o",
            "addopts=",
            str(probe),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == (1 if collect_only else expected), (
        result.stdout + result.stderr
    )


def test_wrapper_enforces_required_test_gate():
    wrapper = ROOT / "api/backend/scripts/run_contract_integration.ps1"
    assert "-p scripts.contract_test_gate" in wrapper.read_text()
    # The console-script entry point loads plugins before cwd is importable.
    assert "& uv run python -m pytest" in wrapper.read_text()


def test_final_job_rejects_skipped_or_failed_dependencies():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    gate = workflow["jobs"]["required-contracts"]
    assert gate["if"] == "${{ always() }}"
    assert set(gate["needs"]) == set(workflow["jobs"]) - {"required-contracts"}
    command = gate["steps"][0]["run"]
    for name in gate["needs"]:
        assert f"needs.{name}.result" in command
    assert "exit 1" in command

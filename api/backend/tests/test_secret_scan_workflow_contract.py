"""Static contract test for the secret-scan workflow (Phase 46, T4).

Parses ``.github/workflows/secret-scan.yml`` and locks the invariant that the
gitleaks scan runs on EVERY push/PR with NO ``paths:`` filter — a leaked
credential can live in any file, so scoping the trigger would let one slip
through unscanned (review fact #67/#68). It never runs the workflow; it asserts
on the committed YAML, so it is a pure offline read safely collected by pytest.

Also asserts the scan has fully moved OUT of security-audit.yml (which keeps its
paths-scoped dependency audit).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

# api/backend/tests -> backend -> api -> repo root == parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET_SCAN = _REPO_ROOT / ".github" / "workflows" / "secret-scan.yml"
SECURITY_AUDIT = _REPO_ROOT / ".github" / "workflows" / "security-audit.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on_section(wf: dict):
    # PyYAML (YAML 1.1) resolves the bare key ``on`` to the boolean ``True``.
    return wf["on"] if "on" in wf else wf[True]


def test_secret_scan_triggers_on_push_and_pull_request() -> None:
    on = _on_section(_load(SECRET_SCAN))
    assert "push" in on
    assert "pull_request" in on


def test_secret_scan_has_no_paths_filter() -> None:
    # The core invariant: no paths filter on any trigger, so a secret in ANY
    # file (frontend/src, api/backend/app, infra, ...) is still scanned.
    on = _on_section(_load(SECRET_SCAN))
    for trigger, spec in on.items():
        if isinstance(spec, dict):
            assert "paths" not in spec, f"trigger '{trigger}' must not filter by paths"
            assert "paths-ignore" not in spec


def test_secret_scan_job_fails_closed() -> None:
    wf = _load(SECRET_SCAN)
    job = wf["jobs"]["secret-scan"]
    run_blob = "\n".join(
        step.get("run", "") for step in job["steps"] if isinstance(step, dict)
    )
    assert "gitleaks" in run_blob
    assert "--exit-code 1" in run_blob  # any finding fails the job
    assert "--redact" in run_blob  # never surface the matched value


def test_gitleaks_removed_from_security_audit() -> None:
    # The scan moved out of the paths-scoped supply-chain workflow: no gitleaks
    # execution step remains (a doc comment pointing at secret-scan.yml is fine).
    wf = _load(SECURITY_AUDIT)
    assert "secret-scan" not in wf["jobs"]
    for job in wf["jobs"].values():
        run_blob = "\n".join(
            s.get("run", "") for s in job.get("steps", []) if isinstance(s, dict)
        )
        assert "gitleaks" not in run_blob

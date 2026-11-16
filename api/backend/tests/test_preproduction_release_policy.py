"""Fail-closed policy and workflow wiring for production promotion."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.test_stabilisation_ci_gate import push_triggers

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "qualify_preproduction.py"
SHA = "a" * 40
MASTER = "b" * 40


def _module():
    spec = importlib.util.spec_from_file_location("qualify_preproduction", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def valid_evidence() -> dict:
    return {
        "repository": "ianis/DataCommune",
        "target_ref": "refs/heads/dev",
        "release_sha": MASTER,
        "current_master_sha": MASTER,
        "requested_candidate_sha": SHA,
        "candidate_sha": SHA,
        "candidate_tree": "tree-1",
        "release_tree": "tree-1",
        "ci_runs": [
            {
                "workflow_name": name,
                "workflow_path": path,
                "event": "push",
                "head_branch": "dev",
                "head_sha": SHA,
                "conclusion": "success",
                "run_id": index,
                "run_attempt": 1,
            }
            for index, (name, path) in enumerate(
                (
                    ("CI API", ".github/workflows/ci-api.yml"),
                    ("CI Frontend", ".github/workflows/ci-frontend.yml"),
                    ("CI Data Contracts", ".github/workflows/ci-data-contracts.yml"),
                ),
                1,
            )
        ],
        "deployment": {
            "workflow_name": "CD Preproduction",
            "workflow_path": ".github/workflows/cd-preproduction.yml",
            "event": "workflow_run",
            "head_branch": "dev",
            "head_sha": SHA,
            "conclusion": "success",
            "run_id": 10,
            "run_attempt": 1,
            "artifact_expired": False,
        },
        "preprod": {
            "frontend_sha": SHA,
            "api_sha": SHA,
            "schema_head": "094",
            "schema_expected": "094",
            "health_status": 200,
            "access_authenticated": True,
        },
    }


def test_exact_candidate_is_allowed() -> None:
    decision = _module().evaluate(valid_evidence())
    assert decision.allowed is True
    assert decision.reasons == ()


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda e: e.update(target_ref="refs/heads/master"), "target_ref"),
        (lambda e: e.update(requested_candidate_sha="c" * 40), "candidate_sha"),
        (lambda e: e.update(candidate_tree="other"), "tree_mismatch"),
        (lambda e: e["ci_runs"][0].update(head_sha="c" * 40), "ci_sha"),
        (lambda e: e["ci_runs"][0].update(event="pull_request"), "ci_event"),
        (lambda e: e["ci_runs"][0].update(conclusion="skipped"), "ci_conclusion"),
        (
            lambda e: e["ci_runs"][0].update(
                workflow_path=".github/workflows/fake.yml"
            ),
            "ci_workflow",
        ),
        (lambda e: e["deployment"].update(artifact_expired=True), "artifact_expired"),
        (lambda e: e["deployment"].update(head_sha="c" * 40), "deployment_sha"),
        (lambda e: e["preprod"].update(health_status=302), "health_status"),
        (lambda e: e["preprod"].update(access_authenticated=False), "access_auth"),
        (lambda e: e["preprod"].update(frontend_sha="c" * 40), "frontend_sha"),
        (lambda e: e["preprod"].update(schema_head="093"), "schema_mismatch"),
    ],
)
def test_policy_rejects_invalid_evidence(mutate, reason: str) -> None:
    evidence = valid_evidence()
    mutate(evidence)
    decision = _module().evaluate(evidence)
    assert decision.allowed is False
    assert reason in decision.reasons


def test_missing_field_and_candidate_intent_fail_closed() -> None:
    evidence = valid_evidence()
    del evidence["requested_candidate_sha"]
    assert "missing:requested_candidate_sha" in _module().evaluate(evidence).reasons
    evidence = valid_evidence()
    evidence["ci_runs"].pop()
    assert "ci_missing:CI Data Contracts" in _module().evaluate(evidence).reasons


# The dispatch-only wiring guard of `cd-release.yml` (scope -> frontend -> api,
# no `if:` on either deploy job) was removed with that workflow at plan 01-07
# (D-05, `docs/REPO-STATE.md`): it released production through the
# `datacommune-ci` runner on the stopped hypervisor. The fail-closed policy of
# `scripts/qualify_preproduction.py` above is unchanged -- the script is still in
# the tree, so its guards stay.


def test_ci_api_runs_for_every_dev_sha_and_executes_policy_tests() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-api.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    # Every SHA that changes code or the policy script runs ci-api (quick
    # 261024-f9r only skips pushes touching only docs or the internal tracking).
    for changed in ("scripts/qualify_preproduction.py", "api/backend/app/main.py"):
        assert push_triggers(triggers["push"], changed)
    commands = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    assert "pytest tests/" in commands
    assert "--ignore=tests/test_preproduction_release_policy.py" not in commands


def test_fixture_mode_is_forbidden_in_github_actions(tmp_path: Path) -> None:
    fixture = tmp_path / "evidence.json"
    fixture.write_text(json.dumps(valid_evidence()))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture", str(fixture), "--fixtures-only"],
        env={**os.environ, "GITHUB_ACTIONS": "true"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "token" not in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(("status", "body"), [(1, "401"), (1, "403"), (0, "not-json")])
def test_github_collection_errors_fail_closed(
    monkeypatch, status: int, body: str
) -> None:
    module = _module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=status, stdout=body, stderr="secret-value"
        ),
    )
    with pytest.raises(RuntimeError, match="GitHub"):
        module._gh_json("repos/ianis/DataCommune/actions/runs")


def test_collect_requires_job_token(monkeypatch) -> None:
    module = _module()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="credentials"):
        module.collect("ianis/DataCommune", SHA, MASTER)


def test_manifest_identity_is_bound_to_github_run() -> None:
    module = _module()
    deployment = valid_evidence()["deployment"]
    manifest = {
        "version": 1,
        "repository": "ianis/DataCommune",
        "target_ref": "refs/heads/dev",
        "candidate_sha": SHA,
        "candidate_tree": "tree-1",
        "workflow_run_id": 10,
        "workflow_run_attempt": 1,
        "workflow_name": "CD Preproduction",
        "workflow_path": ".github/workflows/cd-preproduction.yml",
        "workflow_event": "workflow_run",
        "workflow_conclusion": "success",
        "frontend_sha": SHA,
        "api_sha": SHA,
        "schema_head": ["094"],
        "schema_expected": ["094"],
        "health_status": 200,
        "access_authenticated": True,
        "artifact_expired": False,
    }
    preprod = module._verified_manifest(
        manifest,
        repository="ianis/DataCommune",
        candidate_sha=SHA,
        candidate_tree="tree-1",
        deployment=deployment,
    )
    assert preprod["schema_head"] == ["094"]
    manifest["workflow_run_id"] = 999
    with pytest.raises(RuntimeError, match="identity mismatch"):
        module._verified_manifest(
            manifest,
            repository="ianis/DataCommune",
            candidate_sha=SHA,
            candidate_tree="tree-1",
            deployment=deployment,
        )


def test_collector_selects_latest_exact_run_and_checks_identity(monkeypatch) -> None:
    module = _module()
    path = ".github/workflows/ci-api.yml"
    monkeypatch.setattr(
        module,
        "_workflow",
        lambda repo, name, workflow_path: {
            "id": 42,
            "name": name,
            "path": workflow_path,
        },
    )
    older = {
        "id": 10,
        "run_attempt": 1,
        "workflow_id": 42,
        "repository": {"full_name": "ianis/DataCommune"},
        "event": "push",
        "head_branch": "dev",
        "head_sha": SHA,
        "conclusion": "failure",
    }
    latest = {**older, "id": 11, "conclusion": "success"}
    monkeypatch.setattr(
        module, "_gh_json", lambda endpoint: {"workflow_runs": [older, latest]}
    )
    run = module._successful_run("ianis/DataCommune", "CI API", path, SHA)
    assert run["run_id"] == 11
    latest["repository"] = {"full_name": "attacker/fork"}
    with pytest.raises(RuntimeError, match="identity"):
        module._successful_run("ianis/DataCommune", "CI API", path, SHA)

"""Fail-closed CI admission and fresh evidence for the recovery image drill."""

import os
import shutil
import subprocess
import sys
from fnmatch import fnmatchcase

import pytest
import yaml

from tests.integration.test_l11_recovery_image import ROOT, evidence_directory
from tests.test_stabilisation_ci_gate import push_triggers

WORKFLOW = ROOT / ".github/workflows/ci-data-contracts.yml"
IMAGE = "sha256:" + "a" * 64


def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_recovery_build_inputs_trigger_the_required_workflow():
    document = workflow()
    push = document["on"]["push"]
    assert push["branches"] == ["master"]
    for path in (
        "api/docker/Dockerfile",
        "api/backend/.dockerignore",
        "docs/audit-stabilisation-2026-09-08/lot-11/recovery/probe.py",
    ):
        assert push_triggers(push, path)
        assert any(
            fnmatchcase(path, pattern)
            for pattern in document["on"]["pull_request"]["paths"]
        )
    assert "recovery-image" in document["jobs"]
    assert "recovery-image" in document["jobs"]["required-contracts"]["needs"]


def test_recovery_job_builds_candidate_before_disposable_test():
    document = workflow()
    job = document["jobs"]["recovery-image"]
    # Hosted runner since plan 01-07 (REPO-04): `docker` is preinstalled on
    # `ubuntu-latest`, so the image build below needs no self-hosted box.
    assert job["runs-on"] == "ubuntu-latest"
    assert "environment" not in job
    assert "if" not in job and "continue-on-error" not in job
    assert job["defaults"]["run"]["working-directory"] == "api/backend"
    steps = job["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert "ref" not in steps[0].get("with", {})
    build = next(step for step in steps if step.get("id") == "build_recovery_image")
    run = next(step for step in steps if step.get("id") == "recovery")
    assert steps.index(build) < steps.index(run)
    assert "--iidfile" in build["run"]
    assert "-f api/docker/Dockerfile api/backend" in build["run"]
    assert build["working-directory"] == "."
    assert run["shell"] == "pwsh"
    assert "./scripts/run_contract_integration.ps1" in run["run"]
    assert "tests/integration/test_l11_recovery_image.py" in run["run"]
    assert run["env"]["L11_RECOVERY_IMAGE"] == (
        "${{ steps.build_recovery_image.outputs.image_id }}"
    )
    assert run["env"]["L11_RECOVERY_EVIDENCE_DIR"].startswith("${{ runner.temp }}/")
    assert "DATABASE_URL" not in run["env"]
    assert all("continue-on-error" not in step for step in steps)
    upload = next(
        s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "${{ success() && steps.recovery.outcome == 'success' }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "${{ runner.temp }}/" in upload["with"]["path"]
    gate = document["jobs"]["required-contracts"]
    assert "recovery-image" in gate["needs"]
    assert "needs.recovery-image.result" in gate["steps"][0]["run"]


@pytest.mark.parametrize("state", ["success", "failure", "cancelled", "skipped", ""])
def test_recovery_result_is_enforced_by_real_final_shell(tmp_path, state):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("CI shell contract requires Linux/WSL Bash")
    gate = workflow()["jobs"]["required-contracts"]
    assert "recovery-image" in gate["needs"]
    script = gate["steps"][0]["run"]
    for job in gate["needs"]:
        script = script.replace(
            "${{ needs." + job + ".result }}",
            state if job == "recovery-image" else "success",
        )
    result = subprocess.run(["bash", "-c", script], capture_output=True, timeout=5)
    assert (result.returncode == 0) is (state == "success")


@pytest.mark.parametrize("failure", ["none", "build", "inspect", "invalid_id"])
def test_build_shell_exports_only_a_verified_immutable_image(tmp_path, failure):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("CI shell contract requires Linux/WSL Bash")
    steps = workflow()["jobs"]["recovery-image"]["steps"]
    script = next(s["run"] for s in steps if s.get("id") == "build_recovery_image")
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\nmode=os.environ['FAILURE']\n"
        "if args[0]=='build':\n"
        "    if mode=='build': raise SystemExit(7)\n"
        "    Path(args[args.index('--iidfile')+1]).write_text(os.environ['IMAGE'])\n"
        "elif args[:2]==['image','inspect']:\n"
        "    if mode=='inspect': raise SystemExit(8)\n"
        "    assert args[-1]==os.environ['IMAGE']\n"
        "    print('mutable-tag' if mode=='invalid_id' else os.environ['IMAGE'])\n"
        "elif args[0]=='info' or args[:2]==['compose','version']: pass\n"
        "else: raise AssertionError(args)\n"
    )
    docker.chmod(0o755)
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_OUTPUT": str(output),
        "FAILURE": failure,
        "IMAGE": IMAGE,
    }
    result = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, timeout=10
    )
    assert (result.returncode == 0) is (failure == "none")
    assert output.exists() is (failure == "none")
    if output.exists():
        assert output.read_text() == f"image_id={IMAGE}\n"


def test_recovery_evidence_defaults_to_fresh_external_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("L11_RECOVERY_EVIDENCE_DIR", raising=False)
    output = evidence_directory(tmp_path)
    assert output == tmp_path / "recovery-evidence"
    with pytest.raises(FileExistsError):
        evidence_directory(tmp_path)


@pytest.mark.parametrize("target", ["relative-proof", str(ROOT / "docs")])
def test_recovery_evidence_rejects_relative_or_checkout_output(
    monkeypatch, tmp_path, target
):
    monkeypatch.setenv("L11_RECOVERY_EVIDENCE_DIR", target)
    with pytest.raises(ValueError):
        evidence_directory(tmp_path)


def test_recovery_evidence_never_overwrites_prior_results(monkeypatch, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "verification.json"
    sentinel.write_text("historical proof")
    monkeypatch.setenv("L11_RECOVERY_EVIDENCE_DIR", str(output))
    with pytest.raises(FileExistsError):
        evidence_directory(tmp_path)
    assert sentinel.read_text() == "historical proof"

#!/usr/bin/env python3
"""Qualify the exact preproduction candidate before a production release."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_CI = {
    "CI API": ".github/workflows/ci-api.yml",
    "CI Frontend": ".github/workflows/ci-frontend.yml",
    "CI Data Contracts": ".github/workflows/ci-data-contracts.yml",
}
DEPLOYMENT_NAME = "CD Preproduction"
DEPLOYMENT_PATH = ".github/workflows/cd-preproduction.yml"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...]


def _missing(evidence: dict[str, Any]) -> list[str]:
    fields = (
        "repository",
        "target_ref",
        "release_sha",
        "current_master_sha",
        "requested_candidate_sha",
        "candidate_sha",
        "candidate_tree",
        "release_tree",
        "ci_runs",
        "deployment",
        "preprod",
    )
    return [f"missing:{field}" for field in fields if not evidence.get(field)]


def _run_reasons(run: dict[str, Any], candidate: str, prefix: str) -> list[str]:
    reasons: list[str] = []
    expected = {
        "event": "push" if prefix == "ci" else "workflow_run",
        "head_branch": "dev",
        "head_sha": candidate,
        "conclusion": "success",
    }
    for field, value in expected.items():
        if run.get(field) != value:
            reasons.append(f"{prefix}_{'sha' if field == 'head_sha' else field}")
    if not isinstance(run.get("run_id"), int) or not isinstance(
        run.get("run_attempt"), int
    ):
        reasons.append(f"{prefix}_identity")
    return reasons


def evaluate(evidence: dict[str, Any]) -> Decision:
    """Return a deterministic, fail-closed decision over collected evidence."""
    reasons = _missing(evidence)
    if reasons:
        return Decision(False, tuple(reasons))

    candidate = evidence["candidate_sha"]
    if evidence["target_ref"] != "refs/heads/dev":
        reasons.append("target_ref")
    if evidence["requested_candidate_sha"] != candidate:
        reasons.append("candidate_sha")
    if evidence["release_sha"] != evidence["current_master_sha"]:
        reasons.append("stale_release")
    if evidence["candidate_tree"] != evidence["release_tree"]:
        reasons.append("tree_mismatch")

    runs = evidence["ci_runs"] if isinstance(evidence["ci_runs"], list) else []
    by_name = {run.get("workflow_name"): run for run in runs if isinstance(run, dict)}
    for name, path in REQUIRED_CI.items():
        run = by_name.get(name)
        if run is None:
            reasons.append(f"ci_missing:{name}")
            continue
        if run.get("workflow_path") != path:
            reasons.append("ci_workflow")
        reasons.extend(_run_reasons(run, candidate, "ci"))

    deployment = evidence["deployment"]
    if not isinstance(deployment, dict):
        reasons.append("deployment_missing")
    else:
        if (
            deployment.get("workflow_name") != DEPLOYMENT_NAME
            or deployment.get("workflow_path") != DEPLOYMENT_PATH
        ):
            reasons.append("deployment_workflow")
        reasons.extend(_run_reasons(deployment, candidate, "deployment"))
        if deployment.get("artifact_expired") is not False:
            reasons.append("artifact_expired")

    preprod = evidence["preprod"]
    if not isinstance(preprod, dict):
        reasons.append("preprod_missing")
    else:
        if preprod.get("health_status") != 200:
            reasons.append("health_status")
        if preprod.get("access_authenticated") is not True:
            reasons.append("access_auth")
        if preprod.get("frontend_sha") != candidate:
            reasons.append("frontend_sha")
        if preprod.get("api_sha") != candidate:
            reasons.append("api_sha")
        if not preprod.get("schema_head") or preprod.get("schema_head") != preprod.get(
            "schema_expected"
        ):
            reasons.append("schema_mismatch")
    return Decision(not reasons, tuple(dict.fromkeys(reasons)))


def _gh_json(endpoint: str) -> Any:
    result = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("GitHub evidence request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub returned unusable evidence") from exc


def _workflow(repo: str, name: str, path: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(path, safe="")
    value = _gh_json(f"repos/{repo}/actions/workflows/{encoded}")
    if (
        not isinstance(value, dict)
        or value.get("name") != name
        or value.get("path") != path
        or not value.get("id")
    ):
        raise RuntimeError("GitHub workflow identity is invalid")
    return value


def _successful_run(
    repo: str, name: str, path: str, sha: str, event: str = "push"
) -> dict[str, Any]:
    workflow = _workflow(repo, name, path)
    query = urllib.parse.urlencode(
        {
            "branch": "dev",
            "event": event,
            "head_sha": sha,
            "per_page": 100,
        }
    )
    payload = _gh_json(f"repos/{repo}/actions/workflows/{workflow['id']}/runs?{query}")
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    exact = [run for run in runs if run.get("head_sha") == sha]
    if not exact:
        raise RuntimeError(f"Missing exact run for {name}")
    run = max(
        exact,
        key=lambda value: (value.get("id", 0), value.get("run_attempt", 0)),
    )
    if (
        run.get("workflow_id") != workflow["id"]
        or run.get("repository", {}).get("full_name") != repo
    ):
        raise RuntimeError(f"Invalid run identity for {name}")
    return {
        "workflow_name": name,
        "workflow_path": path,
        "event": run.get("event"),
        "head_branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "conclusion": run.get("conclusion"),
        "run_id": run.get("id"),
        "run_attempt": run.get("run_attempt"),
    }


def _verified_manifest(
    manifest: Any,
    *,
    repository: str,
    candidate_sha: str,
    candidate_tree: str | None,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    """Bind producer claims to identities independently returned by GitHub."""
    if not isinstance(manifest, dict):
        raise RuntimeError("Preproduction evidence is unusable")
    expected = {
        "version": 1,
        "repository": repository,
        "target_ref": "refs/heads/dev",
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "workflow_run_id": deployment["run_id"],
        "workflow_run_attempt": deployment["run_attempt"],
        "workflow_name": DEPLOYMENT_NAME,
        "workflow_path": DEPLOYMENT_PATH,
        "workflow_event": "workflow_run",
        "workflow_conclusion": "success",
        "artifact_expired": False,
    }
    if any(manifest.get(field) != value for field, value in expected.items()):
        raise RuntimeError("Preproduction evidence identity mismatch")
    return {
        "frontend_sha": manifest.get("frontend_sha"),
        "api_sha": manifest.get("api_sha"),
        "schema_head": manifest.get("schema_head"),
        "schema_expected": manifest.get("schema_expected"),
        "health_status": manifest.get("health_status"),
        "access_authenticated": manifest.get("access_authenticated"),
    }


def collect(repository: str, candidate_sha: str, release_sha: str) -> dict[str, Any]:
    if not os.environ.get("GH_TOKEN"):
        raise RuntimeError("GitHub credentials are unavailable")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha) or not re.fullmatch(
        r"[0-9a-f]{40}", release_sha
    ):
        raise RuntimeError("Full lowercase commit SHAs are required")
    candidate = _gh_json(f"repos/{repository}/git/commits/{candidate_sha}")
    release = _gh_json(f"repos/{repository}/git/commits/{release_sha}")
    ref = _gh_json(f"repos/{repository}/git/ref/heads/master")
    cis = [
        _successful_run(repository, name, path, candidate_sha)
        for name, path in REQUIRED_CI.items()
    ]
    deployment = _successful_run(
        repository,
        DEPLOYMENT_NAME,
        DEPLOYMENT_PATH,
        candidate_sha,
        event="workflow_run",
    )
    artifacts = _gh_json(
        f"repos/{repository}/actions/runs/{deployment['run_id']}/artifacts"
    )
    matches = [
        artifact
        for artifact in artifacts.get("artifacts", [])
        if artifact.get("name") == "preproduction-qualification"
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise RuntimeError("Preproduction evidence artifact is missing or expired")
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            [
                "gh",
                "run",
                "download",
                str(deployment["run_id"]),
                "--repo",
                repository,
                "--name",
                "preproduction-qualification",
                "--dir",
                directory,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Preproduction evidence download failed")
        manifest_path = Path(directory) / "qualification.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Preproduction evidence is unusable") from exc
    candidate_tree = candidate.get("tree", {}).get("sha")
    preprod = _verified_manifest(
        manifest,
        repository=repository,
        candidate_sha=candidate_sha,
        candidate_tree=candidate_tree,
        deployment=deployment,
    )
    return {
        "repository": repository,
        "target_ref": "refs/heads/dev",
        "release_sha": release_sha,
        "current_master_sha": ref.get("object", {}).get("sha"),
        "requested_candidate_sha": candidate_sha,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "release_tree": release.get("tree", {}).get("sha"),
        "ci_runs": cis,
        "deployment": {**deployment, "artifact_expired": False},
        "preprod": preprod,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--release-sha")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--fixtures-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.fixture:
            if not args.fixtures_only or os.environ.get("GITHUB_ACTIONS") == "true":
                raise RuntimeError("Fixture mode is restricted to local tests")
            evidence = json.loads(args.fixture.read_text())
        elif args.repository and args.candidate_sha and args.release_sha:
            evidence = collect(args.repository, args.candidate_sha, args.release_sha)
        else:
            raise RuntimeError("Repository, candidate SHA and release SHA are required")
        decision = evaluate(evidence)
        print(json.dumps({"allowed": decision.allowed, "reasons": decision.reasons}))
        return 0 if decision.allowed else 1
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"qualification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

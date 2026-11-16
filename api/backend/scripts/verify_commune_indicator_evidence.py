#!/usr/bin/env python3
"""Fail-closed verification for Commune sketch, CI and served evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REQUIRED_WORKFLOWS = (
    "CI API",
    "CI Frontend",
    "CI Data Contracts",
    "Secret Scan",
)
SHA_RE = re.compile(r"[0-9a-f]{40}")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
SCHEMA_REVISION = "096_commune_indicator_build_activation"


class EvidenceError(ValueError):
    pass


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError("Evidence JSON is missing or invalid") from exc
    if not isinstance(value, dict):
        raise EvidenceError("Evidence root must be an object")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvidenceError("Evidence artifact is unreadable") from exc


def _full_sha(value: Any) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise EvidenceError("A full lowercase commit SHA is required")
    return value


def _png_dimensions(path: Path) -> tuple[int, int]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EvidenceError("Screenshot is unreadable") from exc
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise EvidenceError("Screenshot is not a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _bound_artifact(item: Any) -> Path:
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        raise EvidenceError("Artifact identity is incomplete")
    path = Path(item["path"])
    if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
        raise EvidenceError("Artifact SHA256 is invalid")
    if _sha256(path) != item["sha256"]:
        raise EvidenceError("Artifact SHA256 mismatch")
    return path


def verify_sketch(manifest_path: Path, expected_sha: str | None = None) -> None:  # noqa: C901
    manifest = _json_file(manifest_path)
    candidate = _full_sha(manifest.get("candidate_sha"))
    if expected_sha is not None and candidate != _full_sha(expected_sha):
        raise EvidenceError("Sketch candidate SHA mismatch")
    if manifest.get("version") != 1 or manifest.get("scope") != "preproduction":
        raise EvidenceError("Sketch must be preproduction evidence")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") != "real-http":
        raise EvidenceError("Sketch payload provenance is not real HTTP")
    if not all(
        isinstance(provenance.get(key), str) and provenance[key]
        for key in ("operator", "captured_at")
    ):
        raise EvidenceError("Sketch capture operator or timestamp is missing")
    if not str(provenance.get("base_url", "")).startswith("https://"):
        raise EvidenceError("Sketch capture base URL is not HTTPS")
    generations = manifest.get("generations")
    if not isinstance(generations, dict) or set(generations) != {"dvf", "dpe"}:
        raise EvidenceError("Sketch source generations are missing")
    payloads = manifest.get("payloads")
    screenshots = manifest.get("screenshots")
    if (
        not isinstance(payloads, list)
        or not payloads
        or not isinstance(screenshots, list)
        or not screenshots
    ):
        raise EvidenceError("Sketch artifacts are incomplete")
    for item in payloads:
        data = _json_file(_bound_artifact(item))
        rows = data.get("donnees")
        if not isinstance(rows, list) or not rows:
            raise EvidenceError("Sketch payload has no Commune row")
        for source in ("dvf", "dpe"):
            key = f"indicateurs_{source}"
            found = {
                row[key].get("generation")
                for row in rows
                if isinstance(row, dict) and isinstance(row.get(key), dict)
            }
            if found != {generations[source]}:
                raise EvidenceError(f"Sketch {source.upper()} generation mismatch")
    for item in screenshots:
        path = _bound_artifact(item)
        dimensions = _png_dimensions(path)
        if (
            dimensions != (375, item.get("height"))
            or item.get("width") != 375
            or not isinstance(item.get("height"), int)
            or item["height"] <= 0
        ):
            raise EvidenceError("Sketch screenshot is not the declared 375px capture")


def _run_gh(args: list[str]) -> Any:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise EvidenceError("GitHub evidence command failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvidenceError("GitHub evidence is invalid JSON") from exc


def gh_run_list(repository: str, sha: str) -> list[dict[str, Any]]:
    value = _run_gh(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repository,
            "--commit",
            sha,
            "--json",
            "databaseId,headSha,status,conclusion,workflowName",
        ]
    )
    if not isinstance(value, list):
        raise EvidenceError("GitHub run list is invalid")
    return value


def gh_run_view(repository: str, run_id: int) -> dict[str, Any]:
    value = _run_gh(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            repository,
            "--json",
            "headSha,status,conclusion,jobs",
        ]
    )
    if not isinstance(value, dict):
        raise EvidenceError("GitHub run view is invalid")
    return value


def verify_release_runs(sha: str, repository: str, requirements_path: Path) -> None:  # noqa: C901
    candidate = _full_sha(sha)
    requirements = _json_file(requirements_path)
    declared = requirements.get("workflows")
    if not isinstance(declared, list):
        raise EvidenceError("Required-runs manifest is invalid")
    by_name = {item.get("name"): item for item in declared if isinstance(item, dict)}
    if set(by_name) != set(REQUIRED_WORKFLOWS):
        raise EvidenceError("Required workflows must be the exact mandatory set")
    listed = gh_run_list(repository, candidate)
    for name in REQUIRED_WORKFLOWS:
        matching = [run for run in listed if run.get("workflowName") == name]
        if not matching:
            raise EvidenceError(f"Missing workflow: {name}")
        latest = max(matching, key=lambda run: run.get("databaseId", -1))
        if (
            latest.get("headSha") != candidate
            or latest.get("status") != "completed"
            or latest.get("conclusion") != "success"
        ):
            raise EvidenceError(f"Latest workflow attempt did not succeed: {name}")
        run_id = latest.get("databaseId")
        if not isinstance(run_id, int):
            raise EvidenceError("Workflow run identity is missing")
        viewed = gh_run_view(repository, run_id)
        if (
            viewed.get("headSha") != candidate
            or viewed.get("status") != "completed"
            or viewed.get("conclusion") != "success"
        ):
            raise EvidenceError("Run list and run view disagree")
        jobs = viewed.get("jobs")
        required_jobs = by_name[name].get("required_jobs")
        if (
            not isinstance(jobs, list)
            or not isinstance(required_jobs, list)
            or not required_jobs
        ):
            raise EvidenceError("Required job evidence is incomplete")
        jobs_by_name = {job.get("name"): job for job in jobs if isinstance(job, dict)}
        for job_name in required_jobs:
            job = jobs_by_name.get(job_name)
            if (
                job is None
                or job.get("status") != "completed"
                or job.get("conclusion") != "success"
            ):
                raise EvidenceError(f"Required job did not succeed: {job_name}")


def read_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            **headers,
            "Accept": "application/json",
            "User-Agent": "DataCommune-Evidence/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if (
                response.status != 200
                or response.headers.get_content_type() != "application/json"
            ):
                raise EvidenceError("Served endpoint did not return application JSON")
            raw = response.read(2_000_001)
    except (OSError, urllib.error.URLError) as exc:
        raise EvidenceError("Served endpoint request failed") from exc
    if len(raw) > 2_000_000:
        raise EvidenceError("Served response is unexpectedly large")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceError("Served response is invalid JSON") from exc
    if not isinstance(data, dict):
        raise EvidenceError("Served response is not an object")
    return data


def _assert_subset(actual: Any, expected: Any, path: str = "response") -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise EvidenceError(f"Expected object at {path}")
        for key, value in expected.items():
            if key not in actual:
                raise EvidenceError(f"Missing expected field at {path}.{key}")
            _assert_subset(actual[key], value, f"{path}.{key}")
    elif actual != expected:
        raise EvidenceError(f"Expected value mismatch at {path}")


def _version(payload: dict[str, Any], sha: str, *, require_schema: bool) -> None:
    if (
        payload.get("environment") != "preproduction"
        or payload.get("deployment_sha") != sha
    ):
        raise EvidenceError("Served version does not match the preproduction candidate")
    if require_schema and (
        payload.get("schema_actual") != SCHEMA_REVISION
        or payload.get("schema_expected") != SCHEMA_REVISION
    ):
        raise EvidenceError("Served schema is not migration 096")


def _require_indicator_expectations(expected: dict[str, Any]) -> None:
    for source in ("dvf", "dpe"):
        block = expected.get(f"indicateurs_{source}")
        if not isinstance(block, dict) or not isinstance(block.get("generation"), str):
            raise EvidenceError(f"Independent {source.upper()} generation is required")

        def walk(value: Any) -> tuple[bool, bool]:
            if not isinstance(value, dict):
                return False, False
            period = any(
                "periode" in key and isinstance(item, str) and item
                for key, item in value.items()
            )
            numeric = any(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value.values()
            )
            for item in value.values():
                child_period, child_numeric = walk(item)
                period = period or child_period
                numeric = numeric or child_numeric
            return period, numeric

        if walk(block) != (True, True):
            raise EvidenceError(
                f"Independent {source.upper()} period and value are required"
            )


def verify_served(
    sha: str,
    receipt_path: Path,
    base_url: str,
    expectations_path: Path,
    api_key: str,
    access: dict[str, str] | None = None,
) -> None:
    candidate = _full_sha(sha)
    receipt = _json_file(receipt_path)
    image_id = str(receipt.get("image_id", ""))
    if (
        receipt.get("version") != 1
        or receipt.get("candidate_sha") != candidate
        or IMAGE_ID_RE.fullmatch(image_id) is None
        or receipt.get("container_image_id") != image_id
        or receipt.get("image_label_or_tag")
        != f"datacommune-preprod-api:sha-{candidate}"
    ):
        raise EvidenceError(
            "Runtime receipt is not bound to an immutable candidate image"
        )
    deployment = receipt.get("deployment_run")
    if (
        not isinstance(deployment, dict)
        or deployment.get("head_sha") != candidate
        or deployment.get("conclusion") != "success"
    ):
        raise EvidenceError("Runtime receipt is not bound to successful CD")
    if receipt.get("schema") != SCHEMA_REVISION:
        raise EvidenceError("Runtime receipt schema is not 096")
    if not api_key:
        raise EvidenceError("Smoke API key is missing")
    headers = {"X-API-Key": api_key, **(access or {})}
    base = base_url.rstrip("/")
    frontend = read_json(f"{base}/__version", headers)
    proxy = read_json(f"{base}/__api/v1/health/version", headers)
    direct_url = receipt.get("api_base_url", base)
    direct = read_json(f"{str(direct_url).rstrip('/')}/v1/health/version", headers)
    _version(frontend, candidate, require_schema=False)
    _version(proxy, candidate, require_schema=True)
    _version(direct, candidate, require_schema=True)
    expected = _json_file(expectations_path)
    code = expected.get("code_insee")
    if not isinstance(code, str) or not re.fullmatch(r"\d{5}", code):
        raise EvidenceError("Smoke expectation code_insee is invalid")
    _require_indicator_expectations(expected)
    commune = read_json(
        f"{base}/__api/v1/commune?{urllib.parse.urlencode({'code_insee': code})}",
        headers,
    )
    rows = commune.get("donnees")
    if not isinstance(rows, list) or len(rows) != 1:
        raise EvidenceError("Served Commune response is not singular")
    _assert_subset(rows[0], expected)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EvidenceError(f"Required environment variable is missing: {name}")
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    sketch = sub.add_parser("sketch")
    sketch.add_argument("--manifest-env", required=True)
    release = sub.add_parser("release-runs")
    release.add_argument("--sha-env", required=True)
    release.add_argument("--repository-env", required=True)
    release.add_argument("--requirements-env", required=True)
    served = sub.add_parser("served")
    served.add_argument("--sha-env", required=True)
    served.add_argument("--runtime-receipt-env", required=True)
    served.add_argument("--base-url-env", required=True)
    served.add_argument("--expectations-env", required=True)
    served.add_argument("--api-key-env", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "sketch":
            verify_sketch(
                Path(_required_env(args.manifest_env)),
                os.environ.get("DC_EXPECTED_SHA"),
            )
        elif args.command == "release-runs":
            verify_release_runs(
                _required_env(args.sha_env),
                _required_env(args.repository_env),
                Path(_required_env(args.requirements_env)),
            )
        else:
            access = {
                "CF-Access-Client-Id": _required_env("CF_ACCESS_CLIENT_ID"),
                "CF-Access-Client-Secret": _required_env("CF_ACCESS_CLIENT_SECRET"),
            }
            verify_served(
                _required_env(args.sha_env),
                Path(_required_env(args.runtime_receipt_env)),
                _required_env(args.base_url_env),
                Path(_required_env(args.expectations_env)),
                _required_env(args.api_key_env),
                access,
            )
        print("evidence verified")
        return 0
    except (EvidenceError, OSError, KeyError, subprocess.SubprocessError):
        print("evidence verification failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

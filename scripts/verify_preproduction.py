"""Collect authenticated, live preproduction evidence without accepting redirects."""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def assert_anonymous_refused(url, opener=None):
    """Prove the public request cannot reach an application response."""
    opener = opener or urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "DataCommune-Preproduction-Check/1.0",
        },
    )
    try:
        with opener.open(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    if status not in {302, 401, 403}:
        raise ValueError("Anonymous access unexpectedly reached preproduction")
    return status


def read_json(url, credentials, opener=None):
    if not all(credentials.values()):
        raise ValueError("Missing Access service credentials")
    opener = opener or urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        url,
        headers={
            **credentials,
            "Accept": "application/json",
            "User-Agent": "DataCommune-Preproduction-Check/1.0",
        },
    )
    with opener.open(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError("Expected an authenticated HTTP 200")
        if response.headers.get_content_type() != "application/json":
            raise ValueError("Expected application JSON, not an Access login page")
        payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError("Unexpectedly large health response")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Expected an application object")
        return data


def validate_versions(frontend, api, candidate):
    if not re.fullmatch(r"[a-f0-9]{40}", candidate):
        raise ValueError("Expected a full candidate SHA")
    for payload in (frontend, api):
        if payload.get("environment") != "preproduction":
            raise ValueError("Wrong environment")
        if payload.get("deployment_sha") != candidate:
            raise ValueError("Frontend/API candidate mismatch")
    actual, expected = api.get("schema_actual"), api.get("schema_expected")
    if (
        not isinstance(actual, str)
        or not isinstance(expected, str)
        or not actual
        or actual != expected
    ):
        raise ValueError("Database schema is not at the candidate migration head")
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    credentials = {
        "CF-Access-Client-Id": os.environ.get("CF_ACCESS_CLIENT_ID", ""),
        "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_CLIENT_SECRET", ""),
    }
    assert_anonymous_refused("https://dev.datacommune.fr/__version")
    assert_anonymous_refused("https://dev-api.datacommune.fr/v1/health/version")
    frontend = read_json("https://dev.datacommune.fr/__version", credentials)
    # Verify the browser's API path through the proxy and its private Access token.
    api = read_json("https://dev.datacommune.fr/__api/v1/health/version", credentials)
    direct = read_json("https://dev-api.datacommune.fr/v1/health/version", credentials)
    schema = validate_versions(frontend, api, args.candidate_sha)
    direct_schema = validate_versions(frontend, direct, args.candidate_sha)
    if direct_schema != schema or direct != api:
        raise ValueError("Proxy and direct API version proofs disagree")
    tree = subprocess.run(
        ["git", "rev-parse", f"{args.candidate_sha}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", tree):
        raise ValueError("Invalid candidate tree")
    manifest = {
        "version": 1,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "target_ref": "refs/heads/dev",
        "candidate_sha": args.candidate_sha,
        "candidate_tree": tree,
        "workflow_run_id": int(os.environ["GITHUB_RUN_ID"]),
        "workflow_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "workflow_name": "CD Preproduction",
        "workflow_path": ".github/workflows/cd-preproduction.yml",
        "workflow_event": "workflow_run",
        # Eligibility also requires GitHub to independently report success.
        "workflow_conclusion": "success",
        "frontend_sha": frontend["deployment_sha"],
        "api_sha": api["deployment_sha"],
        "schema_head": schema,
        "schema_expected": api["schema_expected"],
        "health_status": 200,
        "access_authenticated": True,
        "artifact_expired": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Verified frontend, proxy, API and schema for {args.candidate_sha}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        # HTTP errors can carry Access headers/body: never print the exception itself.
        print(
            f"::error::Live preproduction verification failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        sys.exit(1)

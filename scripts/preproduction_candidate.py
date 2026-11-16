"""Admit only the current dev revision after all required push CI runs pass."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY = "DataCommune/datacommune"
REQUIRED_WORKFLOWS = (
    ".github/workflows/ci-api.yml",
    ".github/workflows/ci-frontend.yml",
    ".github/workflows/ci-data-contracts.yml",
)


def evaluate(event, runs, current_sha):
    run = event.get("workflow_run", {})
    if event.get("repository", {}).get("full_name") != REPOSITORY:
        return False, "foreign_repository"
    if run.get("head_repository", {}).get("full_name") != REPOSITORY:
        return False, "foreign_head_repository"
    if run.get("head_branch") != "dev" or run.get("event") != "push":
        return False, "not_dev_push"
    if run.get("conclusion") != "success" or run.get("path") not in REQUIRED_WORKFLOWS:
        return False, "untrusted_trigger"
    sha = run.get("head_sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or sha != current_sha:
        return False, "obsolete_revision"
    for path in REQUIRED_WORKFLOWS:
        matching = [
            item
            for item in runs
            if item.get("path") == path
            and item.get("head_sha") == sha
            and item.get("head_branch") == "dev"
            and item.get("event") == "push"
            and item.get("repository", {}).get("full_name") == REPOSITORY
        ]
        if not matching:
            return False, "missing_required_ci"
        latest = max(matching, key=lambda item: (item["id"], item["run_attempt"]))
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            return False, "required_ci_not_successful"
    return True, "qualified"


def gh_json(path):
    result = subprocess.run(
        ["gh", "api", path], check=False, capture_output=True, text=True
    )
    if result.returncode:
        raise RuntimeError("GitHub evidence collection failed")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--event", type=Path, default=os.environ.get("GITHUB_EVENT_PATH")
    )
    parser.add_argument("--assert-current", metavar="SHA")
    args = parser.parse_args()
    current = gh_json(f"repos/{REPOSITORY}/git/ref/heads/dev")["object"]["sha"]
    if args.assert_current:
        if (
            not re.fullmatch(r"[a-f0-9]{40}", args.assert_current)
            or current != args.assert_current
        ):
            raise RuntimeError("Candidate is no longer dev HEAD; deployment refused")
        return 0
    if not args.event:
        raise RuntimeError("A workflow_run event is required")
    event = json.loads(args.event.read_text(encoding="utf-8"))
    sha = event.get("workflow_run", {}).get("head_sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise RuntimeError("Invalid candidate SHA")
    runs = []
    # Refuse incomplete evidence rather than silently truncate a result set.
    for page in range(1, 11):
        batch = gh_json(
            f"repos/{REPOSITORY}/actions/runs?head_sha={sha}&event=push&per_page=100&page={page}"
        )["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise RuntimeError("Evidence pagination limit exceeded")
    allowed, reason = evaluate(event, runs, current)
    output = (
        f"eligible={'true' if allowed else 'false'}\n"
        f"candidate_sha={sha}\nreason={reason}\n"
    )
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        print(
            f"::error::{type(exc).__name__}: preproduction qualification failed",
            file=sys.stderr,
        )
        sys.exit(1)

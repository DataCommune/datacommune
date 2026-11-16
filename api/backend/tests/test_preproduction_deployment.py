"""Preproduction admission rejects unqualified revisions before deployment."""

import importlib.util
from pathlib import Path

import pytest
import yaml

from tests.test_stabilisation_ci_gate import push_triggers

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "preproduction_candidate", ROOT / "scripts/preproduction_candidate.py"
)
assert SPEC and SPEC.loader
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)

SHA = "a" * 40
REPO = "DataCommune/datacommune"


def evidence():
    event = {
        "repository": {"full_name": REPO},
        "workflow_run": {
            "head_repository": {"full_name": REPO},
            "head_branch": "dev",
            "head_sha": SHA,
            "event": "push",
            "conclusion": "success",
            "path": ".github/workflows/ci-api.yml",
        },
    }
    runs = [
        {
            "id": index,
            "run_attempt": 1,
            "head_sha": SHA,
            "head_branch": "dev",
            "event": "push",
            "conclusion": "success",
            "status": "completed",
            "path": path,
            "repository": {"full_name": REPO},
        }
        for index, path in enumerate(candidate.REQUIRED_WORKFLOWS, 1)
    ]
    return event, runs


def test_qualified_current_dev_is_allowed():
    event, runs = evidence()
    assert candidate.evaluate(event, runs, SHA)[0]


@pytest.mark.parametrize(
    "key,value",
    [
        ("head_branch", "master"),
        ("head_sha", "b" * 40),
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("path", ".github/workflows/fake.yml"),
    ],
)
def test_event_guard_rejects_without_effect(key, value):
    event, runs = evidence()
    event["workflow_run"][key] = value
    assert not candidate.evaluate(event, runs, SHA)[0]


def test_foreign_repository_rejected():
    event, runs = evidence()
    event["workflow_run"]["head_repository"]["full_name"] = "fork/datacommune"
    assert not candidate.evaluate(event, runs, SHA)[0]


@pytest.mark.parametrize("conclusion", [None, "failure", "cancelled", "skipped"])
def test_every_required_ci_must_succeed(conclusion):
    event, runs = evidence()
    runs[1]["conclusion"] = conclusion
    assert not candidate.evaluate(event, runs, SHA)[0]


def test_missing_ci_is_not_success():
    event, runs = evidence()
    assert not candidate.evaluate(event, runs[:-1], SHA)[0]


def test_newer_failed_attempt_invalidates_older_pass():
    event, runs = evidence()
    runs.append({**runs[0], "run_attempt": 2, "conclusion": "failure"})
    assert not candidate.evaluate(event, runs, SHA)[0]


def test_success_on_other_revision_does_not_qualify():
    event, runs = evidence()
    runs[0]["head_sha"] = "b" * 40
    assert not candidate.evaluate(event, runs, SHA)[0]


def workflow(filename):
    # BaseLoader preserves the YAML 1.2 event name `on` as a string.
    return yaml.load(
        (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
    )


def test_every_master_revision_gets_the_three_required_ci_runs():
    # Was "every dev revision" until plan 01-07. The `dev` branch and the
    # preproduction coordinator that consumed these three runs are gone; the
    # invariant worth keeping is narrower and still true: each of the three
    # required workflows runs on every push that touches code or CI, on the
    # only branch; since quick 261024-f9r a push touching only docs or the
    # internal tracking skips them.
    for path in candidate.REQUIRED_WORKFLOWS:
        push = workflow(Path(path).name)["on"]["push"]
        assert push["branches"] == ["master"]
        for changed in (path, "api/backend/app/main.py", "frontend/package.json"):
            assert push_triggers(push, changed)
        assert not push_triggers(push, ".planning/STATE.md")


# The two coordinator guards that lived here -- `cd-preproduction.yml` ordering
# (candidate -> api -> frontend -> qualify) and the `workflow_call`-only contract
# of `cd-api-dev.yml` / `cd-frontend-dev.yml` -- were removed with those three
# workflows at plan 01-07 (D-05, `docs/REPO-STATE.md`). They deployed a preprod CT on
# the stopped hypervisor through the `datacommune-preprod` runner. A contract test is
# removed with the workflow it describes, never on its own: what remains below
# describes `scripts/preproduction_candidate.py` and
# `api/docker-compose.preprod.yml`, both still in the tree.


def test_commune_runtime_policy_is_explicit_and_evidence_is_read_only():
    config = yaml.safe_load((ROOT / "api/docker-compose.preprod.yml").read_text())
    api = config["services"]["api"]
    assert api["environment"]["COMMUNE_INDICATORS_ENABLED"] == (
        "${COMMUNE_INDICATORS_ENABLED:-false}"
    )
    assert api["environment"]["COMMUNE_INDICATOR_POLICY"] == (
        "${COMMUNE_INDICATOR_POLICY:-}"
    )
    evidence = [v for v in api["volumes"] if ":/run/commune-indicators:" in v]
    assert len(evidence) == 1 and evidence[0].endswith(":ro")

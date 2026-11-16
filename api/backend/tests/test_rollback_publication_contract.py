"""The runtime image declares the publication protocol it was tested against.

Until plan 01-07 this file also replayed the two `rollback-api.yml` shell steps
(publication-admission guard before the IMAGE_TAG pointer) against a stubbed
`docker`. That workflow rolled production back through the `datacommune-prod`
runner on the stopped hypervisor and was removed with it (D-05,
`docs/REPO-STATE.md`); its contract test went with it, as a contract test
always does -- never before, never on its own.

What remains describes artifacts still in the tree: `api/docker/Dockerfile` and
`api/docker-compose.yml`. The label this asserts on is the one
`KI-P2` of `docs/KNOWN-ISSUES.md` cites, so the evidence that register points
at stays executable.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_runtime_image_declares_tested_publication_protocol():
    dockerfile = (ROOT / "api/docker/Dockerfile").read_text()
    runtime = dockerfile.rsplit("FROM ", 1)[1]
    assert 'LABEL org.datacommune.publication-contract="1"' in runtime
    compose = yaml.safe_load((ROOT / "api/docker-compose.yml").read_text())
    # Every BUILT service must come from the contract Dockerfile. Enumerating the
    # services instead of hardcoding a list keeps the guard from silently
    # shrinking: a new built service that skips the label fails here, and a
    # removed one (the APScheduler `scheduler`, plan 01-05) needs no edit.
    built = {
        name: service
        for name, service in compose["services"].items()
        if isinstance(service, dict) and "build" in service
    }
    assert built, "no built service in docker-compose.yml — the scan would be vacuous"
    for name, service in built.items():
        assert service["build"]["dockerfile"] == "../docker/Dockerfile", name

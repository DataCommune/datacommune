"""Execute image_reader against simulated Docker and HTTP only."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.integration import test_l11_recovery_image as recovery

pytestmark = pytest.mark.unit
CID = "c" * 64
FOREIGN_CID = "f" * 64
IMAGE = "sha256:" + "a" * 64
DB_OWNER = "owned-contract-db"


class DockerDouble:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []
        self.created = False
        self.removed = False
        self.name = None
        self.cid_path = None

    def __call__(self, argv, **kwargs):  # noqa: C901
        assert argv[0] == "docker", "No real subprocess is allowed"
        self.calls.append(list(argv))
        operation = argv[1]
        code, out, error = 0, b"", b""
        if operation == "run":
            self.name = argv[argv.index("--name") + 1]
            if "--cidfile" in argv:
                self.cid_path = Path(argv[argv.index("--cidfile") + 1])
                assert not self.cid_path.exists()
            if self.mode in ("collision", "no-creation"):
                code, error = (
                    125,
                    b"name collision"
                    if self.mode == "collision"
                    else b"run failed before creation",
                )
            else:
                self.created = True
                if self.cid_path and self.mode != "missing-cid":
                    value = "bad-id" if self.mode == "invalid-cid" else CID
                    self.cid_path.write_bytes((value + "\n").encode())
                out = (CID + "\n").encode()
                if self.mode in ("partial", "partial-cleanup-failure"):
                    code, error = 127, b"run failed after container creation"
        elif operation == "inspect":
            target = argv[-1]
            if target == DB_OWNER:
                out = json.dumps(
                    [
                        {
                            "Name": "/owned-contract-db",
                            "NetworkSettings": {"Networks": {"owned-network": {}}},
                        }
                    ]
                ).encode()
            elif self.mode == "inspect-failure":
                code, error = 1, b"inspection unavailable"
            elif self.removed:
                code, error = 1, b"No such container"
            else:
                assert target in (CID, self.name)
                value = "sha256:" + "b" * 64 if self.mode == "wrong-image" else IMAGE
                out = (value + "\n").encode()
        elif operation == "port":
            assert argv[2] in (CID, self.name)
            out = b"127.0.0.1:23456\n"
        elif operation == "logs":
            assert argv[-1] in (CID, self.name)
            out = b"fixture server unavailable"
        elif operation == "rm":
            if self.mode in (
                "cleanup-failure",
                "partial-cleanup-failure",
                "body-cleanup-failure",
            ):
                code, error = 1, b"cleanup failed"
            else:
                assert argv[-1] in (CID, self.name)
                self.removed = True
        else:
            raise AssertionError(f"Unexpected Docker operation: {argv}")
        return subprocess.CompletedProcess(argv, code, out, error)

    @property
    def removal_targets(self):
        return [argv[-1] for argv in self.calls if argv[1] == "rm"]


@pytest.fixture
def simulated(monkeypatch):
    def install(mode):
        docker = DockerDouble(mode)
        monkeypatch.setattr(recovery.subprocess, "run", docker)
        monkeypatch.setattr(
            recovery.httpx,
            "get",
            Mock(
                return_value=SimpleNamespace(
                    status_code=503 if mode == "unhealthy" else 200
                )
            ),
        )
        monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
        monkeypatch.setattr(recovery, "uuid4", lambda: SimpleNamespace(hex="f" * 32))
        return docker

    return install


def reader():
    return recovery.image_reader(
        IMAGE, DB_OWNER, "postgresql://fixture@127.0.0.1:23455/fixture", []
    )


def messages(error):
    if isinstance(error, BaseExceptionGroup):
        return " ".join(messages(child) for child in error.exceptions)
    return str(error)


def test_partial_creation_failure_removes_only_attested_container(simulated):
    docker = simulated("partial")
    with pytest.raises(AssertionError, match="run failed"), reader():
        pytest.fail("Failed run must not enter its body")
    assert docker.removal_targets == [CID]
    assert docker.removed


@pytest.mark.parametrize("mode", ["collision", "no-creation"])
def test_failed_run_without_creation_never_removes_name_or_foreign_id(simulated, mode):
    docker = simulated(mode)
    with pytest.raises(AssertionError) as caught, reader():
        pytest.fail("Failed run must not yield")
    assert not docker.created
    assert docker.removal_targets == []
    assert FOREIGN_CID not in [part for argv in docker.calls for part in argv]
    assert getattr(caught.value, "__notes__", [])


@pytest.mark.parametrize("mode", ["missing-cid", "invalid-cid"])
def test_success_without_trustworthy_cid_refuses_and_does_not_guess_cleanup(
    simulated, mode
):
    docker = simulated(mode)
    with pytest.raises((AssertionError, RuntimeError, ValueError)), reader():
        pytest.fail("An unattested reader must not yield")
    assert docker.removal_targets == []


def test_inspection_failure_still_removes_owned_id(simulated):
    docker = simulated("inspect-failure")
    with pytest.raises(AssertionError, match="inspection unavailable"), reader():
        pytest.fail("Failed inspection must not yield")
    assert docker.removal_targets == [CID]


@pytest.mark.parametrize("mode", ["unhealthy", "wrong-image"])
def test_reader_not_ready_or_wrong_image_still_removes_only_owned_id(simulated, mode):
    docker = simulated(mode)
    with pytest.raises(AssertionError), reader():
        pytest.fail("An unsuitable reader must not yield")
    assert docker.removal_targets == [CID]
    assert docker.removed
    if mode == "unhealthy":
        assert ["docker", "logs", CID] in docker.calls


def test_cleanup_failure_is_reported_after_success(simulated):
    docker = simulated("cleanup-failure")
    with pytest.raises(AssertionError, match="cleanup failed"), reader() as base:
        assert base == "http://127.0.0.1:23456"
    assert docker.removal_targets == [CID]


@pytest.mark.parametrize("mode", ["partial-cleanup-failure", "body-cleanup-failure"])
def test_initial_failure_and_cleanup_failure_are_both_preserved(simulated, mode):
    docker = simulated(mode)
    with pytest.raises(BaseExceptionGroup) as caught, reader():
        raise RuntimeError("body failure")
    details = messages(caught.value)
    assert "cleanup failed" in details
    assert (
        "run failed" if mode == "partial-cleanup-failure" else "body failure"
    ) in details
    assert docker.removal_targets == [CID]


def test_success_uses_owned_cid_for_every_reader_operation(simulated):
    docker = simulated("success")
    with reader() as base:
        assert base == "http://127.0.0.1:23456"
    assert docker.removal_targets == [CID]
    assert docker.removed
    for argv in docker.calls:
        if argv[1] == "run" or argv[-1] == DB_OWNER:
            continue
        assert CID in argv
        assert docker.name not in argv
    assert docker.cid_path is not None and not docker.cid_path.parent.exists()


def test_body_losing_cid_file_preserves_already_attested_cleanup(simulated):
    docker = simulated("success")
    with reader():
        assert docker.cid_path is not None
        docker.cid_path.unlink()
    assert docker.removal_targets == [CID]
    assert docker.removed

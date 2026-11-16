"""Native partial Docker start, only against resources owned by the PG harness."""

import hashlib
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from tests.integration import test_l11_recovery_image as recovery
from tests.integration.test_publication_restore import _owned_container

pytestmark = pytest.mark.destructive


def test_partial_start_attests_creation_and_removes_only_its_cid(  # noqa: C901
    pg_dsn, tmp_path, monkeypatch
):
    audit = []
    evidence = {"commands": audit, "rescue_cleanup": False}
    native_call = recovery.call
    reader_cid = None
    failure = None

    def inspect(cid):
        return json.loads(native_call(["docker", "inspect", cid], audit))[0]

    def is_absent(cid):
        # A daemon failure must not be accepted as evidence of deletion.
        native_call(["docker", "version", "--format", "{{.Server.Version}}"], audit)
        argv = ["docker", "inspect", cid]
        result = subprocess.run(argv, capture_output=True, timeout=30)
        audit.append(
            {
                "argv": argv,
                "exit": result.returncode,
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
            }
        )
        if result.returncode == 0:
            assert json.loads(result.stdout)[0]["Id"] == cid
            return False
        error = result.stderr.decode(errors="replace").lower()
        assert result.returncode == 1 and (
            f"no such object: {cid}" in error or f"no such container: {cid}" in error
        ), "Inspection failed without proving absence of the owned reader"
        return True

    try:
        owner = _owned_container(pg_dsn, audit)
        before = inspect(owner)
        assert before["Id"] == owner and before["State"]["Running"]
        image_id = before["Image"]
        evidence["owner"] = {"id": owner, "image": image_id, "running": True}
        missing_entrypoint = "/dc-missing-entrypoint-" + uuid4().hex

        def failed_native_start(argv, command_audit, env=None):
            nonlocal reader_cid
            if argv[:2] != ["docker", "run"]:
                return native_call(argv, command_audit, env)
            cid_path = Path(argv[argv.index("--cidfile") + 1])
            assert not cid_path.exists()
            # Keep the helper's private CID file/name, replace only run argv.
            # The already-present image cannot pull, contact a network, mount
            # anything or expose a port; exec fails during container start.
            actual = [
                "docker",
                "run",
                "-d",
                "--cidfile",
                str(cid_path),
                "--name",
                argv[argv.index("--name") + 1],
                "--pull=never",
                "--network",
                "none",
                "--entrypoint",
                missing_entrypoint,
                image_id,
            ]
            try:
                native_call(actual, command_audit)
            except AssertionError:
                assert command_audit[-1]["argv"] == actual
                assert command_audit[-1]["exit"] != 0
                cid = cid_path.read_text(encoding="ascii").strip()
                assert re.fullmatch(r"[0-9a-f]{64}", cid) and cid != owner
                reader_cid = cid  # Rescue is permitted only after this attestation.
                created = inspect(cid)
                assert created["Id"] == cid and created["Image"] == image_id
                assert created["State"]["Status"] == "created"
                assert not created["State"]["Running"] and created["Created"]
                evidence["partial_creation"] = {
                    "id": cid,
                    "image": created["Image"],
                    "status": created["State"]["Status"],
                    "created_at": created["Created"],
                }
                raise
            raise AssertionError("Missing entrypoint unexpectedly started successfully")

        monkeypatch.setattr(recovery, "call", failed_native_start)
        with (
            pytest.raises(AssertionError),
            recovery.image_reader(image_id, owner, pg_dsn, audit),
        ):
            pytest.fail("Partial native start must never yield a reader")
        assert reader_cid is not None and "partial_creation" in evidence
        assert is_absent(reader_cid), "image_reader left its partial container behind"
        assert ["docker", "rm", "-f", reader_cid] in [row["argv"] for row in audit]
        evidence["helper_cleanup_absent"] = True
        after = inspect(owner)
        assert after["Id"] == owner and after["State"]["Running"]
        evidence["owner_preserved"] = True
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            if reader_cid is not None and not is_absent(reader_cid):
                evidence["rescue_cleanup"] = True
                native_call(["docker", "rm", "-f", reader_cid], audit)
                assert is_absent(reader_cid), "Rescue did not remove the attested CID"
        except BaseException as cleanup_error:
            if failure is not None:
                raise BaseExceptionGroup(
                    "Native ownership proof and rescue cleanup both failed",
                    [failure, cleanup_error],
                ) from None
            raise
        finally:
            (tmp_path / "recovery-partial-start.json").write_bytes(
                json.dumps(evidence, indent=2).encode()
            )

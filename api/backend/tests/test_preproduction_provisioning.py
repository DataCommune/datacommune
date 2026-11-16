"""Execute the exact Ansible secret-generation shell without exposing values."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

PLAYBOOK = Path(__file__).resolve().parents[3] / "infra/preproduction.yml"
TASK_NAME = "Generate independent application secrets once inside the preproduction CT"
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="the exact provisioning shell is verified under Linux/WSL",
)


def _generation_script(secret_dir: Path) -> str:
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    task = next(item for item in play["tasks"] if item["name"] == TASK_NAME)
    script = task["ansible.builtin.command"]["argv"][-1]
    resolved = secret_dir.resolve()
    if os.name == "nt":
        drive = resolved.drive.rstrip(":").lower()
        tail = resolved.as_posix().split(":", 1)[1].lstrip("/")
        converted = f"/mnt/{drive}/{tail}"
    else:
        converted = str(resolved)
    return script.replace("{{ preprod_secrets_dir }}", converted)


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-ceu", script],
        check=False,
        capture_output=True,
        text=True,
    )


def test_repairs_missing_database_url_and_is_idempotent(tmp_path):
    expected = {}
    for name in (
        "postgres_password",
        "jwt_secret_key",
        "api_key_hmac_secret",
        "admin_password",
    ):
        value = f"existing-{name}-value"
        (tmp_path / name).write_text(value, encoding="utf-8")
        expected[name] = value

    script = _generation_script(tmp_path)
    first = _run(script)
    assert first.returncode == 0
    assert first.stdout == "changed=1\n"
    assert first.stderr == ""

    for name, value in expected.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == value
    database_url = (tmp_path / "database_url").read_text(encoding="utf-8")
    assert database_url.startswith("postgresql+asyncpg://datacommune_preprod:")
    assert expected["postgres_password"] in database_url
    assert database_url.endswith("@db:5432/datacommune_preprod\n")
    assert all(
        (tmp_path / name).stat().st_size > 0 for name in (*expected, "database_url")
    )
    assert (tmp_path / "postgres_password").stat().st_mode & 0o777 == 0o600
    for name in (
        "database_url",
        "jwt_secret_key",
        "api_key_hmac_secret",
        "admin_password",
    ):
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o444

    second = _run(script)
    assert second.returncode == 0
    assert second.stdout == "changed=0\n"
    assert second.stderr == ""
    assert (tmp_path / "database_url").read_text(encoding="utf-8") == database_url

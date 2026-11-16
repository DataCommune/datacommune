"""Secret input and narrow privilege contract for the dedicated probe key."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.provision_synthetic_probe_key import (
    KEY_NAME,
    ProvisioningError,
    qualified,
    read_key,
)


@pytest.mark.parametrize("suffix", [b"", b"\n", b"\r\n"])
def test_stdin_accepts_one_secret_without_transforming_it(suffix):
    raw = b"dc_live_" + b"x" * 43
    assert read_key(io.BytesIO(raw + suffix)) == raw.decode()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"dc_live_short",
        b"dc_anon_" + b"x" * 43,
        b"dc_live_" + b"x" * 43 + b"\nsecond",
        b"dc_live_" + b"x" * 300,
        b"\xff",
        b" dc_live_" + b"x" * 43,
    ],
)
def test_invalid_input_raises_only_a_constant_code(raw):
    with pytest.raises(ProvisioningError, match=r"^invalid_key_input$"):
        read_key(io.BytesIO(raw))


@pytest.mark.parametrize(
    "change",
    [
        {"name": "unrelated key"},
        {"quota_monthly": -1},
        {"quota_monthly": 10000},
        {"status": "revoked"},
        {"status": "pending"},
        {"revoked": True},
        {"user_id": 1},
        {"environment": "test"},
        {"is_default": True},
    ],
)
def test_other_keys_cannot_be_adopted_or_elevated(change):
    row = {
        "name": KEY_NAME,
        "quota_monthly": 500,
        "status": "active",
        "revoked": False,
        "user_id": None,
        "environment": "live",
        "is_default": False,
    }
    assert qualified(row)
    assert not qualified(row | change)


@pytest.mark.parametrize("option", [[], ["--previous-key-id"]])
def test_mistaken_secret_argument_is_not_echoed(option, monkeypatch, capsys):
    from scripts import provision_synthetic_probe_key as script

    secret = "dc_live_" + "x" * 43
    monkeypatch.setattr(script.sys, "argv", ["provision", *option, secret])
    assert script.main() == 2
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert '"code": "invalid_arguments"' in captured.out


def test_startup_configuration_failure_is_redacted_in_a_real_process():
    sentinel = "SYNTHETIC_CONFIGURATION_SENTINEL_1847"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    environment.update(
        {
            "DEBUG": sentinel,
            "DATABASE_URL": "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:1/synthetic",
        }
    )
    key = b"dc_live_" + b"x" * 43
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.provision_synthetic_probe_key",
            "--verify-only",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        input=key,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "unavailable",
        "code": "provisioning_failed",
    }
    assert result.stderr == b""
    assert sentinel.encode() not in result.stdout + result.stderr
    assert key not in result.stdout + result.stderr


# --- --recette profile (plan 03-13): public dc_anon_ key of the owner acceptance


def test_recette_profile_reads_only_a_public_key():
    from scripts.provision_synthetic_probe_key import RECETTE_KEY_PATTERN

    raw = b"dc_anon_" + b"y" * 43
    assert read_key(io.BytesIO(raw + b"\n"), RECETTE_KEY_PATTERN) == raw.decode()
    for refused in [b"dc_live_" + b"y" * 43, b"dc_anon_short"]:
        with pytest.raises(ProvisioningError, match=r"^invalid_key_input$"):
            read_key(io.BytesIO(refused), RECETTE_KEY_PATTERN)


def test_recette_profile_qualifies_only_its_own_name_and_quota():
    from scripts.provision_synthetic_probe_key import RECETTE_KEY_NAME, RECETTE_QUOTA

    row = {
        "name": RECETTE_KEY_NAME,
        "quota_monthly": RECETTE_QUOTA,
        "status": "active",
        "revoked": False,
        "user_id": None,
        "environment": "live",
        "is_default": False,
    }
    assert qualified(row, RECETTE_KEY_NAME, RECETTE_QUOTA)
    assert not qualified(row)
    assert not qualified(row | {"name": KEY_NAME}, RECETTE_KEY_NAME, RECETTE_QUOTA)


def test_recette_profile_refuses_a_staged_rotation(monkeypatch, capsys):
    from scripts import provision_synthetic_probe_key as script

    monkeypatch.setattr(
        script.sys, "argv", ["provision", "--recette", "--previous-key-id", "3"]
    )
    assert script.main() == 2
    assert '"code": "invalid_previous_key_id"' in capsys.readouterr().out

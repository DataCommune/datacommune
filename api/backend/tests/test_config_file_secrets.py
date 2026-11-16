"""`*_FILE` secrets: only the settings of the application are read from files.

A host variable such as ``SSL_CERT_FILE`` names a CA bundle for other tools;
copied into ``SSL_CERT`` (197 KiB in the cloud sessions) it exceeded the
128 KiB limit of one environment variable and every subprocess launch failed
with ``OSError: [Errno 7] Argument list too long``.
"""

import os

from app.config import _promote_file_secrets


def test_a_setting_is_read_from_its_file(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_text("value-from-file\n", encoding="utf-8")
    monkeypatch.setenv("JWT_SECRET_KEY", "")  # restored after the test
    monkeypatch.setenv("JWT_SECRET_KEY_FILE", str(secret))

    _promote_file_secrets(frozenset({"JWT_SECRET_KEY"}))

    assert os.environ["JWT_SECRET_KEY"] == "value-from-file"


def test_a_host_file_variable_is_never_copied(tmp_path, monkeypatch):
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text("x" * 200_000, encoding="utf-8")
    monkeypatch.setenv("SSL_CERT", "")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))

    _promote_file_secrets(frozenset({"JWT_SECRET_KEY"}))

    assert os.environ["SSL_CERT"] == ""

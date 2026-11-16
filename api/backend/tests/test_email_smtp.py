"""Transactional email over SMTP (Ianis, 2026-11-12: Migadu instead of Resend)."""

from email.message import EmailMessage
from typing import ClassVar

import pytest

from app.auth import email as email_mod
from app.config import settings


class _FakeSMTP:
    instances: ClassVar[list["_FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.logins: list[tuple[str, str]] = []
        self.sent: list[EmailMessage] = []
        self.started_tls = False
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logins.append((user, password))

    def send_message(self, msg: EmailMessage) -> None:
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(settings, "email_enabled", True)
    monkeypatch.setattr(settings, "email_transport", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_username", "noreply@example.test")
    monkeypatch.setattr(settings, "smtp_password", "placeholder-not-a-secret")
    monkeypatch.setattr(settings, "email_sender", "noreply@example.test")
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", _FakeSMTP)

    def _resend_must_not_run(_params):
        raise AssertionError("Resend called while the transport is SMTP")

    monkeypatch.setattr(email_mod.resend.Emails, "send", _resend_must_not_run)
    return _FakeSMTP


async def test_verification_email_goes_through_smtp(smtp):
    await email_mod.send_verification_email("dev@example.test", "tok123")

    assert len(smtp.instances) == 1
    conn = smtp.instances[0]
    assert (conn.host, conn.port) == ("smtp.example.test", 465)
    assert conn.logins == [("noreply@example.test", "placeholder-not-a-secret")]
    (msg,) = conn.sent
    assert msg["To"] == "dev@example.test"
    assert msg["From"] == "DataCommune <noreply@example.test>"
    assert "Verifiez votre email" in msg["Subject"]
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "/verify?token=tok123" in html


async def test_starttls_on_submission_port(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(email_mod.smtplib, "SMTP", smtp)

    await email_mod.send_reset_email("dev@example.test", "tok456")

    (conn,) = smtp.instances
    assert conn.port == 587
    assert conn.started_tls is True
    assert len(conn.sent) == 1


async def test_smtp_failure_counts_and_does_not_raise(smtp, monkeypatch):
    def _boom(self, msg):
        raise OSError("connection refused")

    monkeypatch.setattr(smtp, "send_message", _boom)
    monkeypatch.setattr(email_mod.asyncio, "sleep", _no_sleep)
    before = email_mod.email_failures_count()

    await email_mod.send_verification_email("dev@example.test", "tok")

    assert email_mod.email_failures_count() == before + 1


async def _no_sleep(_seconds: float) -> None:
    return None

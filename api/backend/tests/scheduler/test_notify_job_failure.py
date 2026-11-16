"""Unit tests for the systemd OnFailure alert hook (46-01, T6)."""

from __future__ import annotations

import pytest

from app.scheduler import notify_job_failure as njf

pytestmark = pytest.mark.unit


def test_no_channel_returns_false(monkeypatch):
    # Fail-closed: nothing configured to deliver to = a delivery failure.
    monkeypatch.setattr(njf.settings, "alert_urls", [], raising=False)
    assert njf.notify_job_failure("synthetic-probe") is False


def test_delivers_via_apprise(monkeypatch):
    monkeypatch.setattr(
        njf.settings, "alert_urls", ["json://example.test"], raising=False
    )
    sent: dict = {}

    class _FakeApprise:
        def add(self, url):
            sent["url"] = url

        def notify(self, *, title, body, notify_type):
            sent["title"] = title
            sent["body"] = body
            return True

    import apprise as real_apprise

    monkeypatch.setattr(real_apprise, "Apprise", _FakeApprise)
    assert njf.notify_job_failure("synthetic-probe") is True
    assert sent["url"] == "json://example.test"
    assert "synthetic-probe" in sent["title"]


def test_non_opaque_key_is_redacted(monkeypatch):
    # A non-opaque argument (potential private value) never reaches the alert.
    monkeypatch.setattr(
        njf.settings, "alert_urls", ["json://example.test"], raising=False
    )
    captured: dict = {}

    class _FakeApprise:
        def add(self, url):
            pass

        def notify(self, *, title, body, notify_type):
            captured["title"] = title
            captured["body"] = body
            return True

    import apprise as real_apprise

    monkeypatch.setattr(real_apprise, "Apprise", _FakeApprise)
    njf.notify_job_failure("12 rue des secrets")
    assert "rue des secrets" not in captured["title"]
    assert "rue des secrets" not in captured["body"]
    assert "unknown" in captured["title"]

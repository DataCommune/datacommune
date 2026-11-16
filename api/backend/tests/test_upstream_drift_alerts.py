"""Decision 1 (audit 2026-08-21): upstream schema-drift warnings must alert.

The ``_warn_unknown_fields`` validators log ``upstream_unknown_fields_dropped``
but that warning was wired to nothing. The shared choke point
``app.common.alerting.alert_upstream_unknown_fields`` must route it to the
existing Apprise channel (``ALERT_URLS``) with the same per-key cooldown as
ETL failure alerts (``app.etl.hooks.AlertHook``, 60 min default) — otherwise
the alert would fire on every request. Safe no-op without configuration.

Test strategy (hermetic by design): the cooldown mechanics are asserted
through the choke point with a fake clock and a fake Apprise, and the
validator wiring is asserted by spying on the choke-point function itself.
No test depends on import order or on module state built outside its own
patches, which made the first version environment sensitive in CI.
"""

import logging

import pytest

from app.common import alerting
from app.config import settings
from app.etl.hooks import AlertHook
from app.sirene.schemas import ResultatEntreprise

pytestmark = pytest.mark.unit

FAKE_URL = "json://example.test"


@pytest.fixture(autouse=True)
def _fresh_alert_state():
    alerting.reset_upstream_drift_alert_state()
    yield
    alerting.reset_upstream_drift_alert_state()


class _FakeApprise:
    def __init__(self):
        self.calls: list[dict] = []

    def add(self, url):
        self.calls.append({"url": url})

    def notify(self, *, title, body, notify_type):
        self.calls.append({"title": title, "body": body})


def _install_fake_apprise(monkeypatch):
    fake = _FakeApprise()
    import apprise as real_apprise

    monkeypatch.setattr(real_apprise, "Apprise", lambda: fake)
    return fake


def _freeze_hook_clock(monkeypatch, now_box: list[float]) -> None:
    """Pin the cooldown clock read by ``AlertHook._notify``."""
    import app.etl.hooks as hooks_mod

    monkeypatch.setattr(
        hooks_mod,
        "time",
        type("t", (), {"monotonic": staticmethod(lambda: now_box[0])}),
    )


def test_alerthook_contract_guard():
    """The shared channel must stay an AlertHook (same cooldown machinery)."""
    from app.common.alerting import _get_hook

    monkey_free_hook = alerting._hook
    try:
        alerting._hook = None
        assert isinstance(_get_hook(), AlertHook)
    finally:
        alerting._hook = monkey_free_hook


def test_noop_sans_config(monkeypatch):
    """Without ALERT_URLS the helper is a safe no-op: no Apprise call at all."""
    monkeypatch.setattr(settings, "alert_urls", [], raising=False)
    fake = _install_fake_apprise(monkeypatch)

    alerting.alert_upstream_unknown_fields("ResultatEntreprise", ["x"])

    assert fake.calls == []


def test_alerte_supprimee_pendant_cooldown_puis_repartie(monkeypatch):
    """Choke point: alert once, suppress within cooldown, fire again after."""
    monkeypatch.setattr(settings, "alert_urls", [FAKE_URL], raising=False)
    monkeypatch.setattr(settings, "alert_cooldown_minutes", 60, raising=False)
    fake = _install_fake_apprise(monkeypatch)
    now = [10_000.0]
    _freeze_hook_clock(monkeypatch, now)

    alerting.alert_upstream_unknown_fields("ResultatEntreprise", ["x"])
    assert len(fake.calls) == 2  # one add + one notify

    alerting.alert_upstream_unknown_fields("ResultatEntreprise", ["x"])
    assert len(fake.calls) == 2  # suppressed inside the cooldown window

    now[0] += 61 * 60
    alerting.alert_upstream_unknown_fields("ResultatEntreprise", ["x"])
    assert len(fake.calls) == 4  # second add + second notify


def test_validateur_appelle_le_choke_point(monkeypatch):
    """The proxy validator wires into the shared choke point with its args."""
    seen: list[tuple[str, list[str]]] = []

    def _spy(schema_name, unknown_fields):
        seen.append((schema_name, list(unknown_fields)))

    # Patch the name bound in the validator's own module (bound at import).
    import app.sirene.schemas as sirene_schemas

    monkeypatch.setattr(sirene_schemas, "alert_upstream_unknown_fields", _spy)

    ResultatEntreprise.model_validate(
        {"siren": "443061841", "champ_inconnu_upstream": {"nested": True}}
    )

    assert seen == [("ResultatEntreprise", ["champ_inconnu_upstream"])]


def test_warning_log_conserve_avec_alerte(monkeypatch, caplog):
    """The structured log warning stays in place alongside the alert."""
    monkeypatch.setattr(settings, "alert_urls", [FAKE_URL], raising=False)
    _install_fake_apprise(monkeypatch)
    with caplog.at_level(logging.WARNING):
        ResultatEntreprise.model_validate(
            {"siren": "443061841", "champ_inconnu_upstream": 1}
        )
    recs = [
        r for r in caplog.records if r.getMessage() == "upstream_unknown_fields_dropped"
    ]
    assert recs, "le warning structure doit rester emis"
    assert getattr(recs[0], "unknown_fields", None) == ["champ_inconnu_upstream"]
    assert getattr(recs[0], "schema_name", "") == "ResultatEntreprise"


def test_echec_apprise_ne_casse_pas_la_validation(monkeypatch):
    """An alerting failure must never turn schema validation into a 5xx."""

    class _BrokenApprise:
        def add(self, url):
            raise RuntimeError("apprise down")

    monkeypatch.setattr(settings, "alert_urls", [FAKE_URL], raising=False)
    import apprise as real_apprise

    monkeypatch.setattr(real_apprise, "Apprise", _BrokenApprise)

    instance = ResultatEntreprise.model_validate({"champ_inconnu_upstream": 1})
    assert instance.siren is None
    assert not hasattr(instance, "champ_inconnu_upstream")

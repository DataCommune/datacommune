"""Shared choke point for upstream schema-drift alerts (decision 1, audit
2026-08-21).

The ``_warn_unknown_fields`` validators of the proxy schema modules log
``upstream_unknown_fields_dropped`` but that warning was wired to nothing:
drift was only visible to whoever reads the logs. This module routes the
signal to the proven Apprise channel (``ALERT_URLS``) reusing the ETL
failure-alert machinery — ``app.etl.hooks.AlertHook`` and its per-key 60
minute cooldown — so a drifted upstream field pages once instead of once
per request.

Safe no-op when ALERT_URLS is unset (dev and tests run without it), and it
never raises: an alerting failure must not turn a schema-validation path
into a 5xx.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazily-built AlertHook; reset by tests via reset_upstream_drift_alert_state.
_hook = None


def _get_hook():
    global _hook
    if _hook is None:
        from app.config import settings
        from app.etl.hooks import AlertHook

        _hook = AlertHook(settings.alert_urls, settings.alert_cooldown_minutes)
    return _hook


def reset_upstream_drift_alert_state() -> None:
    """Drop the cached AlertHook (test seam; also re-reads settings)."""
    global _hook
    _hook = None


def alert_upstream_unknown_fields(schema_name: str, unknown_fields: list[str]) -> None:
    """Alert on fields dropped by a proxy schema, deduplicated by cooldown."""
    try:
        hook = _get_hook()
        if not hook.enabled:
            return
        hook.send_alert(
            schema_name,
            title=f"UPSTREAM SCHEMA DRIFT ({schema_name})",
            body=(
                f"{schema_name} dropped unknown upstream fields: "
                f"{', '.join(unknown_fields)}"
            ),
        )
    except Exception:
        logger.exception("upstream_drift_alert_failed")

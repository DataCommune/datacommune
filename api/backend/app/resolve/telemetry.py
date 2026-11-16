"""Resolution telemetry: closed-allowlist structured events (PRIV-01).

Consolidates the multi-layer redaction already enforced on the resolve path
(e5a662d5: bounded structured events, generic /v1/resolve boundary, silenced
``uvicorn.access``; 014f301c/WR-01: no ``str(exc)``) into ONE formal contract.

Redaction contract (D-11/D-12/D-13) — the Phase 46 synthetic probe consumes
these events and MUST be able to rely on every guarantee below:

- Every resolve event carries ONLY fields from :data:`ALLOWED_EVENT_FIELDS`.
  :func:`emit_resolve_event` fails closed (``ValueError``) on anything else.
- ``correlation_id`` is random per request (``uuid4().hex``), never derived
  from the input (no address hash — the demo address space is guessable),
  and is propagated to every event of the same request (router and multi).
- ``input_type`` is the detector vocabulary: ``adresse``/``gps``/``cadastre``.
- ``datasets`` holds VALIDATED dataset names only: members of the closed
  vocabulary (:func:`sanitize_datasets`); raw user input never reaches logs.
- ``outcome`` is the machine resolution issue
  (``exact``/``ambiguous``/``not_reported``/``out_of_coverage``).
- ``duration_ms`` is an integer duration.
- ``error_code`` is a bounded exception class name or normalized code —
  never ``str(exc)``, never ``exc_info`` on the resolve path (an exception
  text may embed an upstream URL, an address or coordinates).

Never logged, by construction: ``q``, addresses, lat/lon, BAN query/params,
parcel identifiers, raw upstream messages.

Event names used on the resolve path:

==========================  =================================================
``resolve_request``         request start (input_type, validated datasets)
``resolve_completed``       request end (outcome, duration_ms)
``resolve_failed``          request failure (bounded error_code)
``resolve_parcelle_snapped``nearest-parcel fallback applied (outcome)
``resolve_datasets_queried``multi gather timing (dataset_count, duration_ms)
``dataset_query_failed``    one dataset degraded (dataset, error_code)
``resolve_gather_exception``gather-level safety net (error_code)
``city_detection_failed``   BAN city lookup degraded (error_code)
``geocode_query_failed``    BAN geocode strategy degraded (error_code)
==========================  =================================================
"""

import logging
import uuid
from collections.abc import Iterable

# Closed field allowlist — the ONLY keys a resolve event may carry.
ALLOWED_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "correlation_id",
        "input_type",
        "datasets",
        "dataset_count",
        "dataset",
        "outcome",
        "duration_ms",
        "error_code",
    }
)


def new_correlation_id() -> str:
    """Random opaque id for one request — never derived from the input."""
    return uuid.uuid4().hex


def sanitize_datasets(raw: str | None, known: Iterable[str]) -> list[str]:
    """Project a user-supplied dataset list onto the closed vocabulary.

    Only names present in ``known`` survive (normalized, deduplicated, order
    preserved). Unknown names — potentially arbitrary user input — are
    dropped so they can never reach a log record.
    """
    if not raw:
        return []
    known_set = set(known)
    validated: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower().replace("-", "_")
        if name in known_set and name not in validated:
            validated.append(name)
    return validated


def error_code_for(exc: BaseException) -> str:
    """Bounded error code for an exception: its class name, nothing more."""
    return type(exc).__name__


def emit_resolve_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured resolve event restricted to the field allowlist.

    Fails closed: a non-allowlisted field raises ``ValueError`` instead of
    being logged. Call sites are covered by the sentinel gate
    (``tests/test_resolve_privacy.py``), so a violation is a programming
    error caught before it can ship.
    """
    unexpected = set(fields) - ALLOWED_EVENT_FIELDS
    if unexpected:
        raise ValueError(f"non-allowlisted telemetry fields: {sorted(unexpected)!r}")
    logger.log(level, event, extra=fields)

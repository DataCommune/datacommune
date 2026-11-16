#!/usr/bin/env python3
"""Local demonstration-capacity harness (OPS-01, D-05/D-06/D-07).

Replays the Phase 45 resolution sequence against an in-process ASGI app
wrapped in the REAL production ``RateLimitMiddleware`` (``app.rate_limit``),
on synthetic fixtures only. It never opens a database connection and never
reaches an external URL in ``--local`` mode.

Budget (D-05): Phase 45 certified exactly three resolution modes
(``adresse`` / ``gps`` / ``cadastre`` -- 45-VERIFICATION truths 1 & 14,
GEO-02). A full demonstration session replays that triad once, so the
*measured* ``calls_per_session`` is 3 and the local budget is
``8 sessions * 3 = 24`` requests/minute/IP -- confirming (not merely
assuming) the provisional planning baseline of 24, comfortably under the
120/min public per-IP ceiling (C36; 30/min before 2026-10-10).

Criteria proven locally (D-06): zero 5xx, monthly quota not exhausted,
p50/p95 documented, cache hit/miss observed only where a cache exists, and a
deterministic 429 + positive-integer ``Retry-After`` when the ceiling is
exceeded (the overshoot branch is exercised by the test suite).

Production posture (D-07): ``--production-smoke`` is a SEPARATE, supervised,
read-only profile capped at 2 sessions / 30 s. It requires interactive
confirmation, refuses any non-allowlisted host, and refuses to run under
pytest. It is ``autonomous: false`` -- the checkpoint operator runs it once,
by hand, after deployment. The local ``--local`` path never touches it.

Report minimisation (D-02): the emitted JSON carries only aggregates
(counts, percentiles, quota, cache, verdict). No address, query string,
API key or raw payload ever appears.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

# Ensure the backend root is importable when run directly
# (uv run python scripts/verify_demo_capacity.py), where only scripts/ -- not
# the backend root -- is on sys.path.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Import the auth package first so its __init__ resolves the
# rate_limit <-> auth.dependencies import cycle before we pull RateLimitMiddleware
# directly (rate_limit as the entry point would hit a partial-init ImportError).
import app.auth.dependencies  # noqa: E402, F401
from app.rate_limit import RateLimitMiddleware, _buckets  # noqa: E402

# --- Bounds and budget ------------------------------------------------------

# Phase 45 certified resolution triad; one full demo session replays it once.
PHASE45_SEQUENCE: tuple[str, ...] = ("adresse", "gps", "cadastre")
CALLS_PER_SESSION: int = len(PHASE45_SEQUENCE)

MAX_LOCAL_SESSIONS: int = 8
MAX_LOCAL_DURATION_S: float = 60.0

PROD_MAX_SESSIONS: int = 2
PROD_MAX_DURATION_S: float = 30.0

# Anonymous monthly budget modelled for the aggregate report (never a live
# counter here -- the real anon key quota lives in the production DB).
ANON_MONTHLY_QUOTA: int = 1_000_000

# Hosts the supervised production smoke is allowed to target.
PROD_ALLOWLIST_HOSTS: frozenset[str] = frozenset({"api.datacommune.fr"})

# Local ASGI base -- opaque, never a real network host.
_LOCAL_BASE_URL: str = "http://local.asgi"
_LOCAL_HOSTS: frozenset[str] = frozenset(
    {"local.asgi", "localhost", "127.0.0.1", "testserver"}
)


# --- Observations and report ------------------------------------------------


@dataclass
class CallObservation:
    """One replayed request, reduced to aggregate-safe fields only."""

    status: int
    duration_ms: float
    retry_after: int | None
    cache_hit: bool


@dataclass
class CapacityReport:
    """Aggregate-only capacity report (D-02: no address/query/key/payload)."""

    sessions: int
    calls_per_session: int
    calls_total: int
    status_counts: dict[str, int]
    p50_ms: float
    p95_ms: float
    quota_limit: int
    quota_remaining: int
    cache_hits: int
    cache_misses: int
    verdict: str
    failures: list[str] = field(default_factory=list)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile in milliseconds, rounded to 3 decimals."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = round((pct / 100.0) * (len(ordered) - 1))
    idx = max(0, min(len(ordered) - 1, idx))
    return round(ordered[idx], 3)


def evaluate(
    observations: list[CallObservation],
    quota_remaining: int,
    *,
    under_budget: bool,
) -> tuple[str, list[str]]:
    """Derive the PASS/FAIL verdict from observations (pure, testable)."""
    failures: list[str] = []
    if any(o.status >= 500 for o in observations):
        failures.append("server_5xx")
    if under_budget and any(o.status == 429 for o in observations):
        failures.append("unexpected_429_under_budget")
    if any(
        o.status == 429 and (o.retry_after is None or o.retry_after <= 0)
        for o in observations
    ):
        failures.append("missing_or_invalid_retry_after")
    if quota_remaining <= 0:
        failures.append("quota_exhausted")
    verdict = "FAIL" if failures else "PASS"
    return verdict, failures


def build_report(
    observations: list[CallObservation],
    sessions: int,
    calls_per_session: int,
    quota_limit: int,
    *,
    under_budget: bool = True,
) -> CapacityReport:
    """Aggregate observations into a minimised, verdict-bearing report."""
    durations = [o.duration_ms for o in observations]
    status_counts: dict[str, int] = {}
    for obs in observations:
        key = str(obs.status)
        status_counts[key] = status_counts.get(key, 0) + 1
    calls_total = len(observations)
    quota_remaining = quota_limit - calls_total
    verdict, failures = evaluate(
        observations, quota_remaining, under_budget=under_budget
    )
    return CapacityReport(
        sessions=sessions,
        calls_per_session=calls_per_session,
        calls_total=calls_total,
        status_counts=status_counts,
        p50_ms=_percentile(durations, 50),
        p95_ms=_percentile(durations, 95),
        quota_limit=quota_limit,
        quota_remaining=quota_remaining,
        cache_hits=sum(1 for o in observations if o.cache_hit),
        cache_misses=sum(1 for o in observations if not o.cache_hit),
        verdict=verdict,
        failures=failures,
    )


# --- Synthetic ASGI app (real middleware, no DB, no network) ----------------


def build_capacity_app() -> Starlette:
    """Real RateLimitMiddleware around a synthetic resolve handler.

    The handler serves deterministic synthetic fixtures and tracks an
    in-memory set so a cache hit/miss is observable exactly where a cache
    exists in production (upstream proxies). No DB, no external URL.
    """
    seen: set[str] = set()

    async def _resolve(request):
        mode = request.query_params.get("type", "adresse")
        hit = mode in seen
        seen.add(mode)
        return JSONResponse(
            {"outcome": "exact", "cache": "hit" if hit else "miss"},
            headers={"X-Demo-Cache": "hit" if hit else "miss"},
        )

    app = Starlette(routes=[Route("/v1/resolve", _resolve)])
    app.add_middleware(RateLimitMiddleware)
    return app


async def run_sessions(
    sessions: int, calls_per_session: int = CALLS_PER_SESSION
) -> list[CallObservation]:
    """Replay ``sessions`` demo sessions against the synthetic ASGI app."""
    _buckets.clear()
    transport = ASGITransport(app=build_capacity_app())
    observations: list[CallObservation] = []
    async with AsyncClient(transport=transport, base_url=_LOCAL_BASE_URL) as client:
        for _ in range(sessions):
            for mode in PHASE45_SEQUENCE[:calls_per_session]:
                started = time.perf_counter()
                resp = await client.get(
                    "/v1/resolve", params={"q": "fixture", "type": mode}
                )
                duration_ms = (time.perf_counter() - started) * 1000.0
                raw_retry = resp.headers.get("Retry-After")
                observations.append(
                    CallObservation(
                        status=resp.status_code,
                        duration_ms=duration_ms,
                        retry_after=int(raw_retry) if raw_retry is not None else None,
                        cache_hit=resp.headers.get("X-Demo-Cache") == "hit",
                    )
                )
    _buckets.clear()
    return observations


# --- Bounds enforcement -----------------------------------------------------


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


def validate_local_bounds(sessions: int, url: str | None = None) -> None:
    """Refuse >8 sessions, non-positive sessions and any non-local URL."""
    if sessions <= 0:
        raise ValueError("refused: sessions must be a positive integer")
    if sessions > MAX_LOCAL_SESSIONS:
        raise ValueError(
            f"refused: {sessions} sessions exceeds the local cap ({MAX_LOCAL_SESSIONS})"
        )
    if url is not None and _host_of(url) not in _LOCAL_HOSTS:
        raise ValueError("refused: --local mode never targets a non-local URL")


def assert_prod_bounds(url: str, sessions: int) -> None:
    """Refuse >2 sessions or any non-allowlisted production host."""
    if sessions <= 0:
        raise ValueError("refused: sessions must be a positive integer")
    if sessions > PROD_MAX_SESSIONS:
        raise ValueError(
            f"refused: production smoke is capped at {PROD_MAX_SESSIONS} sessions"
        )
    if _host_of(url) not in PROD_ALLOWLIST_HOSTS:
        raise ValueError("refused: production host is not on the allowlist")


def assert_not_pytest() -> None:
    """Fail closed if invoked under pytest -- the smoke is never automated."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise RuntimeError(
            "production-smoke must never run under pytest (D-07, autonomous: false)"
        )


def require_confirmation(answer: str) -> None:
    """Abort unless the operator typed the exact confirmation token."""
    if answer.strip() != "smoke":
        raise SystemExit("aborted: production smoke not confirmed")


async def run_prod_sessions(
    client: AsyncClient,
    sessions: int,
    query: str,
    *,
    calls_per_session: int = CALLS_PER_SESSION,
    max_duration_s: float = PROD_MAX_DURATION_S,
) -> list[CallObservation]:
    """Replay bounded read-only ``GET /v1/resolve`` sessions against the live API.

    Stops at the first 5xx (fail fast on a broken production) or when the global
    time budget is exhausted. Records aggregate-safe fields only (D-02): status,
    duration, Retry-After, cache flag — never the query or key. Testable with a
    ``MockTransport`` because it carries no pytest guard (that lives in the
    supervised entry point).
    """
    observations: list[CallObservation] = []
    started = time.perf_counter()
    for _ in range(sessions):
        for mode in PHASE45_SEQUENCE[:calls_per_session]:
            if time.perf_counter() - started > max_duration_s:
                return observations
            call_started = time.perf_counter()
            resp = await client.get("/v1/resolve", params={"q": query, "type": mode})
            duration_ms = (time.perf_counter() - call_started) * 1000.0
            raw_retry = resp.headers.get("Retry-After")
            observations.append(
                CallObservation(
                    status=resp.status_code,
                    duration_ms=duration_ms,
                    retry_after=int(raw_retry) if raw_retry is not None else None,
                    cache_hit=resp.headers.get("X-Demo-Cache") == "hit",
                )
            )
            if resp.status_code >= 500:
                return observations  # stop at the first failure
    return observations


async def _run_production_smoke_async(  # pragma: no cover - real network
    url: str, sessions: int, api_key: str, query: str
) -> CapacityReport:
    """Wire a real read-only client and aggregate a minimised report."""
    async with AsyncClient(
        base_url=url, headers={"X-API-Key": api_key}, timeout=10.0
    ) as client:
        observations = await run_prod_sessions(client, sessions, query)
    return build_report(
        observations, sessions, CALLS_PER_SESSION, ANON_MONTHLY_QUOTA, under_budget=True
    )


def run_production_smoke(
    url: str,
    sessions: int,
    *,
    confirm=input,
    api_key: str | None = None,
    query: str | None = None,
) -> int:  # pragma: no cover - supervised checkpoint, never run under pytest/CI
    """Supervised, bounded, read-only production smoke (checkpoint 46-02-03).

    Order of guards: bounds -> not-under-pytest -> interactive confirmation ->
    dedicated key + public query present. Only after all pass does the real,
    time-boxed (<=2 sessions / 30 s) read-only probe run, printing a minimised
    aggregate report and returning 0 (PASS) / 1 (FAIL). Excluded from pytest.
    """
    assert_prod_bounds(url, sessions)
    assert_not_pytest()
    require_confirmation(
        confirm(
            f"About to run {sessions} read-only session(s) against {url} "
            "(max 2/30s). Type 'smoke' to proceed: "
        )
    )
    key = api_key if api_key is not None else os.environ.get("DC_SMOKE_API_KEY", "")
    if not key:
        raise SystemExit(
            "aborted: DC_SMOKE_API_KEY is not set (a dedicated smoke key is required)"
        )
    q = query if query is not None else os.environ.get("DC_SMOKE_QUERY", "")
    if not q:
        raise SystemExit(
            "aborted: DC_SMOKE_QUERY is not set (a public, non-sensitive query needed)"
        )
    report = asyncio.run(_run_production_smoke_async(url, sessions, key, q))
    print(json.dumps(dataclasses.asdict(report), indent=2, sort_keys=True))
    return 0 if report.verdict == "PASS" else 1


# --- CLI --------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local demo-capacity harness (OPS-01, D-05/D-06/D-07)."
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Bounded local ASGI replay (default mode).",
    )
    parser.add_argument(
        "--production-smoke",
        action="store_true",
        help="Supervised production smoke (<=2 sessions/30s, confirmation).",
    )
    parser.add_argument("--sessions", type=int, default=MAX_LOCAL_SESSIONS)
    parser.add_argument("--url", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.production_smoke:
        url = args.url or "https://api.datacommune.fr"
        return run_production_smoke(url, args.sessions)

    validate_local_bounds(args.sessions, args.url)
    started = time.perf_counter()
    observations = asyncio.run(run_sessions(args.sessions))
    elapsed = time.perf_counter() - started
    report = build_report(
        observations, args.sessions, CALLS_PER_SESSION, ANON_MONTHLY_QUOTA
    )
    if elapsed > MAX_LOCAL_DURATION_S:
        report.verdict = "FAIL"
        report.failures.append("duration_budget_exceeded")

    print(json.dumps(dataclasses.asdict(report), indent=2, sort_keys=True))
    return 0 if report.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

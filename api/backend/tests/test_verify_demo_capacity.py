"""Contract tests for the local demo-capacity harness (OPS-01, D-05..D-07).

Covers: the measured 8-session budget PASS (zero 5xx, positive quota,
documented p95), the failure branches that MUST turn the verdict red (5xx,
429 under budget, missing Retry-After, exhausted quota), the bound refusals
(>8 local sessions, non-local URL, >2 production sessions, non-allowlisted
host, run-under-pytest), and report minimisation (no address/query/key/payload).
"""

import json

import httpx
import pytest

from scripts.verify_demo_capacity import (
    ANON_MONTHLY_QUOTA,
    CALLS_PER_SESSION,
    MAX_LOCAL_SESSIONS,
    PHASE45_SEQUENCE,
    CallObservation,
    assert_not_pytest,
    assert_prod_bounds,
    build_report,
    evaluate,
    require_confirmation,
    run_prod_sessions,
    run_sessions,
    validate_local_bounds,
)

pytestmark = pytest.mark.unit


def _ok(n: int) -> list[CallObservation]:
    return [
        CallObservation(status=200, duration_ms=1.0, retry_after=None, cache_hit=False)
        for _ in range(n)
    ]


# --- Budget ----------------------------------------------------------------


def test_calls_per_session_matches_phase45_triad():
    """D-05: the measured session replays exactly the Phase 45 triad -> 24/min."""
    assert PHASE45_SEQUENCE == ("adresse", "gps", "cadastre")
    assert CALLS_PER_SESSION == 3
    assert MAX_LOCAL_SESSIONS * CALLS_PER_SESSION == 24


# --- 8-session local PASS --------------------------------------------------


async def test_local_8_sessions_pass():
    """D-06: 8 local sessions -> zero 5xx, quota positive, p95 documented."""
    observations = await run_sessions(8)
    report = build_report(observations, 8, CALLS_PER_SESSION, ANON_MONTHLY_QUOTA)

    assert report.verdict == "PASS"
    assert report.failures == []
    assert report.calls_total == 24
    assert report.status_counts == {"200": 24}
    assert not any(o.status >= 500 for o in observations)
    assert report.quota_remaining > 0
    assert report.p95_ms >= 0.0
    # Cache observed only where it exists: first session misses the 3 modes,
    # the remaining 7 sessions hit them.
    assert report.cache_misses == 3
    assert report.cache_hits == 21


# --- Red branches: the verdict MUST fail ------------------------------------


def test_5xx_marks_fail():
    obs = [
        *_ok(23),
        CallObservation(status=503, duration_ms=1.0, retry_after=None, cache_hit=True),
    ]
    verdict, failures = evaluate(obs, quota_remaining=999, under_budget=True)
    assert verdict == "FAIL"
    assert "server_5xx" in failures


def test_unexpected_429_under_budget_marks_fail():
    obs = [
        *_ok(23),
        CallObservation(status=429, duration_ms=1.0, retry_after=42, cache_hit=False),
    ]
    verdict, failures = evaluate(obs, quota_remaining=999, under_budget=True)
    assert verdict == "FAIL"
    assert "unexpected_429_under_budget" in failures


def test_429_without_retry_after_marks_fail():
    obs = [
        CallObservation(status=429, duration_ms=1.0, retry_after=None, cache_hit=False)
    ]
    # Over budget so the 429 itself is expected, but a missing Retry-After is not.
    verdict, failures = evaluate(obs, quota_remaining=999, under_budget=False)
    assert verdict == "FAIL"
    assert "missing_or_invalid_retry_after" in failures


def test_quota_exhausted_marks_fail():
    verdict, failures = evaluate(_ok(24), quota_remaining=0, under_budget=True)
    assert verdict == "FAIL"
    assert "quota_exhausted" in failures


# --- Bound refusals ---------------------------------------------------------


def test_over_budget_local_sessions_refused():
    with pytest.raises(ValueError):
        validate_local_bounds(9)


def test_non_positive_sessions_refused():
    with pytest.raises(ValueError):
        validate_local_bounds(0)


def test_non_local_url_refused_in_local_mode():
    with pytest.raises(ValueError):
        validate_local_bounds(8, url="https://api.datacommune.fr")


def test_local_url_accepted():
    validate_local_bounds(8, url="http://127.0.0.1:8000")  # no raise


def test_production_smoke_refuses_over_2_sessions():
    with pytest.raises(ValueError):
        assert_prod_bounds("https://api.datacommune.fr", 3)


def test_production_smoke_refuses_non_allowlisted_host():
    with pytest.raises(ValueError):
        assert_prod_bounds("https://evil.example", 2)


def test_production_smoke_accepts_allowlisted_host_within_bounds():
    assert_prod_bounds("https://api.datacommune.fr", 2)  # no raise


def test_production_smoke_never_runs_under_pytest():
    # PYTEST_CURRENT_TEST is set for the duration of this test.
    with pytest.raises(RuntimeError):
        assert_not_pytest()


def test_require_confirmation_gate():
    require_confirmation("smoke")  # no raise


# --- Production smoke: the real read-only probe loop -----------------------


def _prod_client(responses: list[httpx.Response]) -> httpx.AsyncClient:
    """AsyncClient whose MockTransport replays *responses* in order (no network)."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/resolve"
        assert request.headers.get("x-api-key")  # dedicated key always sent
        return queue.pop(0)

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.datacommune.fr",
        headers={"X-API-Key": "dc_smoke_test"},
    )


async def test_prod_sessions_replays_all_calls():
    responses = [httpx.Response(200, json={"outcome": "exact"}) for _ in range(6)]
    async with _prod_client(responses) as client:
        obs = await run_prod_sessions(client, 2, "44109")
    assert len(obs) == 2 * CALLS_PER_SESSION
    assert all(o.status == 200 for o in obs)


async def test_prod_sessions_stops_at_first_5xx():
    responses = [
        httpx.Response(200),
        httpx.Response(503, headers={"Retry-After": "5"}),
        httpx.Response(200),
    ]
    async with _prod_client(responses) as client:
        obs = await run_prod_sessions(client, 2, "q")
    assert len(obs) == 2  # stopped right after the 503
    assert obs[-1].status == 503


async def test_prod_sessions_honours_time_budget():
    responses = [httpx.Response(200) for _ in range(6)]
    async with _prod_client(responses) as client:
        # An already-exhausted budget returns before any request is sent.
        obs = await run_prod_sessions(client, 2, "q", max_duration_s=-1.0)
    assert obs == []
    with pytest.raises(SystemExit):
        require_confirmation("no")


# --- Report minimisation (D-02) --------------------------------------------


async def test_report_contains_no_address_query_key_or_payload():
    observations = await run_sessions(8)
    report = build_report(observations, 8, CALLS_PER_SESSION, ANON_MONTHLY_QUOTA)
    import dataclasses

    dumped = json.dumps(dataclasses.asdict(report))

    forbidden = ["fixture", "adresse", "gps", "cadastre", "q=", "X-API-Key", "dc_"]
    for needle in forbidden:
        assert needle not in dumped, f"leak indicator present: {needle}"

    # Only aggregate keys are exposed.
    assert set(dataclasses.asdict(report).keys()) == {
        "sessions",
        "calls_per_session",
        "calls_total",
        "status_counts",
        "p50_ms",
        "p95_ms",
        "quota_limit",
        "quota_remaining",
        "cache_hits",
        "cache_misses",
        "verdict",
        "failures",
    }

"""Live tests for proxy availability checks (DQ-BIZ-02).

Wave 0 — RED stubs with live marker. Fail at EXECUTION by ImportError on symbols
not yet created in Wave 1 (ProxyProfile, check_proxy, PROXY_PROFILES).
Collection stays GREEN because all imports are inside each test body.

Marked `live` — excluded from CI verte. Run manually to verify real gov APIs:
  cd api/backend && uv run pytest tests/data_quality/test_dq_proxy_live.py -m live -v

Contract fixed here for plan 26-02:
- DQ-BIZ-02: check_proxy(profile) returns verdict="green" for each of the 6 gov
  proxies when their real endpoints are reachable.
"""

import pytest

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# DQ-BIZ-02 — live proxy calls (real endpoints, excluded from CI)
# ---------------------------------------------------------------------------


def test_proxy_live_bdnb() -> None:
    """Live: BDNB geocodage endpoint returns HTTP 200 with 'features' key."""
    from app.data_quality.proxy_run import PROXY_PROFILES, check_proxy

    profile = next(p for p in PROXY_PROFILES if p.name == "bdnb")
    result = check_proxy(profile)
    assert result.verdict == "green", (
        f"BDNB proxy smoke failed: status={result.status} "
        f"missing_keys={result.missing_keys} error={result.error}"
    )


def test_proxy_live_errial() -> None:
    """Live: ERRIAL cadastre endpoint HTTP 200 (smoke only, list may be empty)."""
    from app.data_quality.proxy_run import PROXY_PROFILES, check_proxy

    profile = next(p for p in PROXY_PROFILES if p.name == "errial")
    result = check_proxy(profile)
    assert result.verdict == "green", (
        f"ERRIAL proxy smoke failed: status={result.status} "
        f"missing_keys={result.missing_keys} error={result.error}"
    )


def test_proxy_live_georisques() -> None:
    """Live: Georisques radon endpoint returns HTTP 200 with 'data' key."""
    from app.data_quality.proxy_run import PROXY_PROFILES, check_proxy

    profile = next(p for p in PROXY_PROFILES if p.name == "georisques")
    result = check_proxy(profile)
    assert result.verdict == "green", (
        f"Georisques proxy smoke failed: status={result.status} "
        f"missing_keys={result.missing_keys} error={result.error}"
    )


def test_proxy_live_rnb() -> None:
    """Live: RNB buildings/closest endpoint returns HTTP 200 with 'results' key."""
    from app.data_quality.proxy_run import PROXY_PROFILES, check_proxy

    profile = next(p for p in PROXY_PROFILES if p.name == "rnb")
    result = check_proxy(profile)
    assert result.verdict == "green", (
        f"RNB proxy smoke failed: status={result.status} "
        f"missing_keys={result.missing_keys} error={result.error}"
    )


def test_proxy_live_sirene() -> None:
    """Live: Sirene near_point endpoint returns HTTP 200 with 'results' key."""
    from app.data_quality.proxy_run import PROXY_PROFILES, check_proxy

    profile = next(p for p in PROXY_PROFILES if p.name == "sirene")
    result = check_proxy(profile)
    assert result.verdict == "green", (
        f"Sirene proxy smoke failed: status={result.status} "
        f"missing_keys={result.missing_keys} error={result.error}"
    )


def test_proxy_live_urbanisme() -> None:
    """Live: Urbanisme zone-urba endpoint returns HTTP 200 with 'features' key."""
    from app.data_quality.proxy_run import PROXY_PROFILES, check_proxy

    profile = next(p for p in PROXY_PROFILES if p.name == "urbanisme")
    result = check_proxy(profile)
    assert result.verdict == "green", (
        f"Urbanisme proxy smoke failed: status={result.status} "
        f"missing_keys={result.missing_keys} error={result.error}"
    )

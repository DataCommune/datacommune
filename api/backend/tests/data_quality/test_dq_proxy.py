"""RED stub tests for proxy runner unit tests (DQ-BIZ-02 + DQ-INTEG-01).

Wave 0 — RED stubs. Fail at EXECUTION by ImportError on symbols not yet created
in Wave 1:
  - app.data_quality.proxy_run.ProxyProfile
  - app.data_quality.proxy_run.ProxyResult
  - app.data_quality.proxy_run.check_proxy
  - app.data_quality.proxy_run.write_proxy_report
  - app.data_quality.proxy_run.PROXY_PROFILES

Collection stays GREEN because all imports of those symbols are inside each test
body, never at module top-level.

Contract fixed here for plan 26-02:
- DQ-BIZ-02: check_proxy(profile) -> ProxyResult
  - verdict="green" when HTTP 200 + expected_keys present
  - verdict="red" when HTTP 4xx
  - verdict="red" when expected_keys absent from JSON body (missing_keys non-empty)
  - verdict="red" when httpx.TimeoutException raised
- DQ-INTEG-01: write_proxy_report(results, out_dir) creates PROXY.md
- D-07: proxy_run module must NOT import psycopg
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# DQ-BIZ-02 — check_proxy unit (mock httpx)
# ---------------------------------------------------------------------------


def test_proxy_green() -> None:
    """HTTP 200 + expected key present → ProxyResult(verdict='green')."""
    from app.data_quality.proxy_run import ProxyProfile, check_proxy

    profile = ProxyProfile(
        name="test_api",
        endpoint_url="https://example.gov/api/test",
        expected_keys=("results",),
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [{"id": 1}]}

    with patch("app.data_quality.proxy_run.httpx.get", return_value=mock_response):
        result = check_proxy(profile)

    assert result.verdict == "green", (
        f"Expected verdict='green' on HTTP 200 + key present, got '{result.verdict}'"
    )
    assert result.status == 200
    assert result.missing_keys == []
    assert result.error is None


def test_proxy_red_on_4xx() -> None:
    """HTTP 404 → ProxyResult(verdict='red')."""
    from app.data_quality.proxy_run import ProxyProfile, check_proxy

    profile = ProxyProfile(
        name="test_api",
        endpoint_url="https://example.gov/api/test",
        expected_keys=("results",),
    )

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.return_value = {}

    with patch("app.data_quality.proxy_run.httpx.get", return_value=mock_response):
        result = check_proxy(profile)

    assert result.verdict == "red", (
        f"Expected verdict='red' on HTTP 404, got '{result.verdict}'"
    )
    assert result.status == 404


def test_proxy_red_on_missing_key() -> None:
    """HTTP 200 but expected key absent → verdict='red', missing_keys non-empty."""
    from app.data_quality.proxy_run import ProxyProfile, check_proxy

    profile = ProxyProfile(
        name="georisques",
        endpoint_url="https://georisques.gouv.fr/api/v1/radon?code_insee=44109",
        expected_keys=("data",),
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}  # "data" key missing

    with patch("app.data_quality.proxy_run.httpx.get", return_value=mock_response):
        result = check_proxy(profile)

    assert result.verdict == "red", (
        f"Expected verdict='red' when 'data' key missing, got '{result.verdict}'"
    )
    assert "data" in result.missing_keys, (
        f"Expected 'data' in missing_keys, got {result.missing_keys}"
    )


def test_proxy_red_on_timeout() -> None:
    """httpx.TimeoutException → ProxyResult(verdict='red', error not None)."""
    import httpx

    from app.data_quality.proxy_run import ProxyProfile, check_proxy

    profile = ProxyProfile(
        name="slow_api",
        endpoint_url="https://slow.gov/api",
        expected_keys=("results",),
        timeout_s=1.0,
    )

    with patch(
        "app.data_quality.proxy_run.httpx.get",
        side_effect=httpx.TimeoutException("Request timed out"),
    ):
        result = check_proxy(profile)

    assert result.verdict == "red", (
        f"Expected verdict='red' on TimeoutException, got '{result.verdict}'"
    )
    assert result.status is None, (
        f"Expected status=None on timeout, got {result.status}"
    )
    assert result.error is not None, "Expected error message on timeout, got None"


# ---------------------------------------------------------------------------
# DQ-INTEG-01 — write_proxy_report creates PROXY.md
# ---------------------------------------------------------------------------


def test_write_proxy_report(tmp_path: Path) -> None:
    """write_proxy_report(results, out_dir) creates PROXY.md in out_dir."""
    from app.data_quality.proxy_run import (
        ProxyResult,
        write_proxy_report,
    )

    results = [
        ProxyResult(
            name="bdnb", status=200, verdict="green", missing_keys=[], error=None
        ),
        ProxyResult(
            name="rnb", status=404, verdict="red", missing_keys=[], error="HTTP 404"
        ),
    ]

    out_path = write_proxy_report(results, tmp_path)

    assert out_path.exists(), f"PROXY.md not created at {out_path}"
    assert out_path.name == "PROXY.md", (
        f"Expected filename 'PROXY.md', got '{out_path.name}'"
    )

    content = out_path.read_text(encoding="utf-8")
    assert "bdnb" in content, "PROXY.md missing 'bdnb' entry"
    assert "rnb" in content, "PROXY.md missing 'rnb' entry"


# ---------------------------------------------------------------------------
# D-07 enforcement — proxy_run must NOT import psycopg
# ---------------------------------------------------------------------------


def test_proxy_run_no_psycopg_import() -> None:
    """D-07: proxy_run module must not import psycopg.

    The proxy runner checks external API availability only — it must never
    open a local DB connection.
    """
    import app.data_quality.proxy_run as m

    assert not hasattr(m, "psycopg"), (
        "proxy_run must NOT import psycopg (D-07: runner proxy separé du runner "
        "dataset, ne lit JAMAIS de table locale). Found 'psycopg' on module."
    )

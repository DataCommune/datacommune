"""Proxy availability smoke checks — SEPARATE runner (D-07).

Checks HTTP 2xx + top-level field presence for each of the 6 gov-API proxies
(bdnb, errial, georisques, rnb, sirene, urbanisme).

This module NEVER imports psycopg or opens a local DB connection (D-07 lock).
All checks use httpx.get() synchronous calls — no AsyncClient (runner is sync).

Usage:
    python -m app.data_quality.proxy_run
    python -m app.data_quality.proxy_run -o /tmp/audit-proxy

Exit code:
    0  all proxies green
    1  at least one proxy red
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProxyProfile:
    """Configuration for one gov-API proxy smoke check."""

    name: str
    endpoint_url: str
    expected_keys: tuple[str, ...]
    timeout_s: float = 5.0


@dataclass
class ProxyResult:
    """Result of one proxy smoke check."""

    name: str
    status: int | None  # HTTP status code; None if connection failed
    verdict: Literal["green", "red"]
    missing_keys: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# 6 proxy profiles (D-06: smoke = HTTP 2xx + top-level key presence)
# ---------------------------------------------------------------------------

PROXY_PROFILES: list[ProxyProfile] = [
    ProxyProfile(
        name="bdnb",
        endpoint_url="https://api.bdnb.io/v1/bdnb/geocodage?q=Paris",
        expected_keys=("features",),
    ),
    ProxyProfile(
        name="errial",
        endpoint_url="https://errial.georisques.gouv.fr/api/cadastre/2.3522/48.8566",
        expected_keys=(),  # smoke = HTTP 200 only (list may be empty)
        timeout_s=10.0,
    ),
    ProxyProfile(
        name="georisques",
        endpoint_url="https://georisques.gouv.fr/api/v1/radon?code_insee=44109",
        expected_keys=("data",),
    ),
    ProxyProfile(
        name="rnb",
        endpoint_url=(
            "https://rnb-api.beta.gouv.fr/api/alpha/buildings/closest/"
            "?point=47.2184,1.5536&radius=50"
        ),
        expected_keys=("results",),
    ),
    ProxyProfile(
        name="sirene",
        endpoint_url=(
            "https://recherche-entreprises.api.gouv.fr/"
            "near_point?lat=47.2184&long=1.5536&radius=5&per_page=1"
        ),
        expected_keys=("results",),
    ),
    ProxyProfile(
        name="urbanisme",
        endpoint_url=(
            "https://apicarto.ign.fr/api/gpu/zone-urba"
            '?geom={"type":"Point","coordinates":[1.5536,47.2184]}'
        ),
        expected_keys=("features",),
        timeout_s=15.0,
    ),
]


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def check_proxy(profile: ProxyProfile) -> ProxyResult:
    """Perform a synchronous smoke check against one gov-API endpoint.

    Contract (D-06): HTTP 2xx + presence of all expected_keys in JSON body.
    Returns ProxyResult with verdict="green" or "red".
    Never opens a DB connection (D-07).
    """
    try:
        resp = httpx.get(profile.endpoint_url, timeout=profile.timeout_s)
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        return ProxyResult(
            name=profile.name,
            status=None,
            verdict="red",
            missing_keys=[],
            error=str(exc),
        )

    if resp.status_code >= 400:
        return ProxyResult(
            name=profile.name,
            status=resp.status_code,
            verdict="red",
            missing_keys=[],
            error=f"HTTP {resp.status_code}",
        )

    data = resp.json()
    missing = [k for k in profile.expected_keys if k not in data]
    verdict: Literal["green", "red"] = "red" if missing else "green"
    return ProxyResult(
        name=profile.name,
        status=resp.status_code,
        verdict=verdict,
        missing_keys=missing,
        error=None,
    )


def write_proxy_report(results: list[ProxyResult], out_dir: Path) -> Path:
    """Write proxy availability results to PROXY.md in out_dir (DQ-INTEG-01).

    Format: markdown table with one row per proxy.
    Returns the path to the written file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "PROXY.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Proxy availability check",
        f"Date : {timestamp}",
        "",
        "| Proxy | Status | Verdict | Missing keys |",
        "|-------|--------|---------|--------------|",
    ]
    for r in results:
        verdict_emoji = "🟢" if r.verdict == "green" else "🔴"
        missing_str = ", ".join(r.missing_keys) if r.missing_keys else "—"
        status_str = str(r.status) if r.status is not None else "N/A"
        lines.append(f"| {r.name} | {status_str} | {verdict_emoji} | {missing_str} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run all proxy smoke checks, write PROXY.md, return exit code.

    Exit 0 = all green. Exit 1 = at least one red.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Proxy availability smoke checks")
    parser.add_argument(
        "-o",
        "--out-dir",
        default=".planning/audit-proxy",
        help="Output directory for PROXY.md (default: .planning/audit-proxy)",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    results: list[ProxyResult] = []
    for profile in PROXY_PROFILES:
        log.info("[proxy] checking %s ...", profile.name)
        result = check_proxy(profile)
        results.append(result)
        log.info(
            "[proxy] %s → %s (status=%s)",
            result.name,
            result.verdict,
            result.status,
        )

    path = write_proxy_report(results, out_dir)
    log.info("[proxy] report written to %s", path)

    return 1 if any(r.verdict == "red" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

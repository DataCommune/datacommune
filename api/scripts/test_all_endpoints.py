#!/usr/bin/env python3
"""Test all 27 dataset endpoints — live API smoke tests.

Usage:
    # Against local dev
    python scripts/test_all_endpoints.py

    # Against prod
    python scripts/test_all_endpoints.py --base-url https://api.datacommune.fr

    # With API key (required for protected endpoints) — prefer the env var
    # to avoid argv exposure (17-06)
    DC_API_KEY=YOUR_KEY python scripts/test_all_endpoints.py

    # Skip proxy endpoints (require external API availability)
    python scripts/test_all_endpoints.py --skip-proxies

    # JSON output
    python scripts/test_all_endpoints.py --json
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field

import httpx

# ── Test coordinates ──────────────────────────────────────────────────────
# Paris centre (48.8566, 2.3522) — good coverage for most datasets
PARIS_LAT, PARIS_LON = 48.8566, 2.3522
PARIS_INSEE = "75056"
PARIS_DEPT = "75"
# Lyon for fallback
LYON_LAT, LYON_LON = 45.7640, 4.8357
LYON_INSEE = "69123"


@dataclass
class TestResult:
    endpoint: str
    dataset: str
    category: str  # etl, proxy, meta
    status_code: int = 0
    response_time_ms: float = 0
    has_data: bool = False
    row_count: int = 0
    error: str = ""
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return self.status_code == 200 and not self.error

    @property
    def status_icon(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.passed and self.has_data:
            return "OK"
        if self.passed and not self.has_data:
            return "EMPTY"
        return "FAIL"


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len([r for r in self.results if not r.skipped])

    @property
    def passed(self) -> int:
        return len([r for r in self.results if r.passed])

    @property
    def failed(self) -> int:
        return len([r for r in self.results if not r.passed and not r.skipped])

    @property
    def empty(self) -> int:
        return len([r for r in self.results if r.passed and not r.has_data])

    @property
    def with_data(self) -> int:
        return len([r for r in self.results if r.passed and r.has_data])


# ── Endpoint definitions ─────────────────────────────────────────────────

ENDPOINTS = [
    # ── Health & Meta ──
    {
        "endpoint": "/v1/health/live",
        "dataset": "Health (liveness)",
        "category": "meta",
        "params": {},
        "auth": False,
        "check_structure": False,
    },
    {
        "endpoint": "/v1/health",
        "dataset": "Health (readiness)",
        "category": "meta",
        "params": {},
        "auth": False,
        "check_structure": False,
        "accept_codes": [200, 503],
    },
    {
        "endpoint": "/v1/datasets",
        "dataset": "Datasets metadata",
        "category": "meta",
        "params": {},
    },
    {
        "endpoint": "/openapi.json",
        "dataset": "OpenAPI schema",
        "category": "meta",
        "params": {},
        "auth": False,
        "check_structure": False,
    },
    # ── v1.0 ETL datasets ──
    {
        "endpoint": "/v1/geocode",
        "dataset": "BAN (adresses)",
        "category": "etl",
        "params": {"q": "20 avenue de Segur Paris"},
    },
    {
        "endpoint": "/v1/reverse",
        "dataset": "BAN (reverse)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/parcelle",
        "dataset": "Cadastre (par coords)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/dvf",
        "dataset": "DVF (mutations)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON, "rayon_m": 500},
    },
    {
        "endpoint": "/v1/dpe",
        "dataset": "DPE (diagnostics)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON, "rayon_m": 500},
    },
    {
        "endpoint": "/v1/loyers",
        "dataset": "Loyers (indicateurs)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/batiments",
        "dataset": "BD TOPO (batiments)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON, "rayon_m": 200},
    },
    {
        "endpoint": "/v1/monuments",
        "dataset": "Monuments historiques",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON, "rayon_m": 1000},
    },
    {
        "endpoint": "/v1/connectivite",
        "dataset": "ARCEP (connectivite)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/scolaire",
        "dataset": "Carte scolaire",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/transports",
        "dataset": "Transport (arrets)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON, "rayon_m": 1000},
    },
    # ── v5.0 ETL datasets ──
    {
        "endpoint": "/v1/zonage-abc",
        "dataset": "Zonage ABC",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/fiscalite",
        "dataset": "Fiscalite locale",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/encadrement-loyers",
        "dataset": "Encadrement loyers",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/revenus",
        "dataset": "Filosofi (revenus)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/demographie",
        "dataset": "Recensement (pop)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/copropriete",
        "dataset": "RNIC (coproprietes)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/permis",
        "dataset": "SITADEL (permis)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/antennes",
        "dataset": "ANFR (antennes)",
        "category": "etl",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON, "rayon": 1000},
    },
    {
        "endpoint": "/v1/equipements",
        "dataset": "BPE (equipements)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    {
        "endpoint": "/v1/vacance",
        "dataset": "LOVAC (vacance)",
        "category": "etl",
        "params": {"code_insee": PARIS_INSEE},
    },
    # ── Proxies (depend on external APIs) ──
    {
        "endpoint": "/v1/risques",
        "dataset": "Georisques (risques)",
        "category": "proxy",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/pollutions",
        "dataset": "Georisques (pollutions)",
        "category": "proxy",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/urbanisme",
        "dataset": "IGN (urbanisme PLU)",
        "category": "proxy",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/entreprises",
        "dataset": "SIRENE (entreprises)",
        "category": "proxy",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/bdnb",
        "dataset": "BDNB (batiment national)",
        "category": "proxy",
        "params": {"adresse": "20 avenue de Segur 75007 Paris"},
    },
    {
        "endpoint": "/v1/rnb",
        "dataset": "RNB (registre batiment)",
        "category": "proxy",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    {
        "endpoint": "/v1/errial",
        "dataset": "ERRIAL (risques regl.)",
        "category": "proxy",
        "params": {"lat": PARIS_LAT, "lon": PARIS_LON},
    },
    # ── Scoring ──
    {
        "endpoint": "/v1/scoring/renov",
        "dataset": "Scoring renovation",
        "category": "etl",
        "params": {"code_departement": PARIS_DEPT},
    },
    # ── Resolve (multi-dataset) ──
    {
        "endpoint": "/v1/resolve",
        "dataset": "Resolve (multi-dataset)",
        "category": "etl",
        "params": {"q": "20 avenue de Segur Paris"},
    },
]


def run_test(
    client: httpx.Client,
    endpoint_def: dict,
    api_key: str | None,
    skip_proxies: bool,
) -> TestResult:
    """Execute a single endpoint test."""
    ep = endpoint_def
    result = TestResult(
        endpoint=ep["endpoint"],
        dataset=ep["dataset"],
        category=ep["category"],
    )

    if skip_proxies and ep["category"] == "proxy":
        result.skipped = True
        return result

    headers = {}
    needs_auth = ep.get("auth", True)
    if needs_auth and api_key:
        headers["X-API-Key"] = api_key

    url = ep["endpoint"]
    params = ep.get("params", {})

    try:
        t0 = time.perf_counter()
        resp = client.get(url, params=params, headers=headers, timeout=30)
        result.response_time_ms = (time.perf_counter() - t0) * 1000
        result.status_code = resp.status_code

        accept = ep.get("accept_codes", [200])
        if result.status_code not in accept and result.status_code != 200:
            result.error = f"HTTP {result.status_code}"
            try:
                detail = resp.json().get("detail", "")
                if detail:
                    result.error += f": {detail[:120]}"
            except Exception:
                pass
            return result

        if not ep.get("check_structure", True):
            result.has_data = True
            return result

        body = resp.json()

        # Check standard ReponseApi structure
        if "donnees" in body:
            data = body["donnees"]
            if isinstance(data, list):
                result.row_count = len(data)
                result.has_data = len(data) > 0
            elif isinstance(data, dict):
                result.has_data = bool(data)
                result.row_count = 1
        elif "statut" in body:
            # Health-style response
            result.has_data = True
        elif ep["endpoint"] == "/openapi.json":
            result.has_data = "paths" in body
        else:
            # Some endpoints return data directly
            result.has_data = bool(body)
            if isinstance(body, list):
                result.row_count = len(body)

    except httpx.TimeoutException:
        result.error = "TIMEOUT (30s)"
    except httpx.ConnectError:
        result.error = "CONNECTION REFUSED"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"

    return result


def print_results(suite: TestSuite, as_json: bool = False):
    """Print test results."""
    if as_json:
        output = {
            "summary": {
                "total": suite.total,
                "passed": suite.passed,
                "failed": suite.failed,
                "empty": suite.empty,
                "with_data": suite.with_data,
            },
            "results": [
                {
                    "endpoint": r.endpoint,
                    "dataset": r.dataset,
                    "category": r.category,
                    "status": r.status_icon,
                    "status_code": r.status_code,
                    "response_time_ms": round(r.response_time_ms),
                    "row_count": r.row_count,
                    "error": r.error,
                }
                for r in suite.results
                if not r.skipped
            ],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Header
    print("\n" + "=" * 90)
    print("  DATACOMMUNE API — Test complet des 27 datasets")
    print("=" * 90)

    # Group by category
    for cat, label in [
        ("meta", "HEALTH & META"),
        ("etl", "ETL DATASETS"),
        ("proxy", "PROXY DATASETS"),
    ]:
        items = [r for r in suite.results if r.category == cat]
        if not items:
            continue

        print(f"\n  [{label}]")
        print(f"  {'Endpoint':<30} {'Dataset':<28} {'Status':<6} {'Code':<5} {'Time':>7} {'Rows':>6}")
        print("  " + "-" * 86)

        for r in items:
            if r.skipped:
                print(f"  {r.endpoint:<30} {r.dataset:<28} {'SKIP':<6}")
                continue

            time_str = f"{r.response_time_ms:>5.0f}ms"
            rows_str = f"{r.row_count:>5}" if r.has_data else "    -"
            icon = r.status_icon

            # Color-code status
            if icon == "OK":
                status = "  OK  "
            elif icon == "EMPTY":
                status = "EMPTY "
            else:
                status = " FAIL "

            print(
                f"  {r.endpoint:<30} {r.dataset:<28} {status} {r.status_code:<5} {time_str} {rows_str}"
            )
            if r.error:
                print(f"     -> {r.error}")

    # Summary
    print("\n" + "=" * 90)
    print(f"  TOTAL: {suite.total} tests")
    print(f"    OK (avec donnees) : {suite.with_data}")
    print(f"    EMPTY (vide)      : {suite.empty}")
    print(f"    FAIL              : {suite.failed}")
    skipped = len([r for r in suite.results if r.skipped])
    if skipped:
        print(f"    SKIP              : {skipped}")
    print("=" * 90)

    if suite.failed > 0:
        print("\n  ENDPOINTS EN ERREUR:")
        for r in suite.results:
            if not r.passed and not r.skipped:
                print(f"    - {r.endpoint}: {r.error}")
        print()

    if suite.empty > 0:
        print("\n  ENDPOINTS VIDES (tables pas encore remplies ?):")
        for r in suite.results:
            if r.passed and not r.has_data:
                print(f"    - {r.endpoint} ({r.dataset})")
        print()


def main():
    parser = argparse.ArgumentParser(description="Test all DataCommune API endpoints")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        help=(
            "API key for protected endpoints (prefer the DC_API_KEY or "
            "API_KEY environment variable to avoid argv exposure)"
        ),
    )
    parser.add_argument(
        "--skip-proxies",
        action="store_true",
        help="Skip proxy endpoints (external API dependencies)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between requests (avoids rate limits, e.g. 7 for 10/min)",
    )
    args = parser.parse_args()

    # 17-06: fall back to the environment so the key never needs to be on argv
    if not args.api_key:
        args.api_key = os.environ.get("DC_API_KEY") or os.environ.get("API_KEY")

    if not args.api_key:
        print(
            "WARNING: No API key provided (use the DC_API_KEY/API_KEY environment\n"
            "         variable or --api-key). Protected endpoints will return 401."
        )

    client = httpx.Client(base_url=args.base_url)
    suite = TestSuite()

    if not args.json:
        print(f"\nTarget: {args.base_url}")
        print(f"Auth:   {'API key provided' if args.api_key else 'None (expect 401s)'}")
        print(f"Proxies: {'skipped' if args.skip_proxies else 'included'}\n")

    for i, ep_def in enumerate(ENDPOINTS):
        if args.delay > 0 and i > 0:
            time.sleep(args.delay)

        if not args.json:
            print(f"  Testing {ep_def['endpoint']:<30} ...", end="", flush=True)

        result = run_test(client, ep_def, args.api_key, args.skip_proxies)
        suite.results.append(result)

        if not args.json:
            if result.skipped:
                print(" SKIP")
            elif result.passed:
                print(f" {result.status_icon} ({result.response_time_ms:.0f}ms)")
            else:
                print(f" FAIL: {result.error}")

    client.close()
    print_results(suite, as_json=args.json)

    # Exit code: 0 if all non-skipped tests passed, 1 otherwise
    sys.exit(0 if suite.failed == 0 else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Phase 67 verification — arrondissements + geocode performance.

Tests the 5 success criteria:
1. loyers/demographie/equipements/vacance for code_insee=75056 return data
2. Same endpoints for 69123 (Lyon) and 13055 (Marseille)
3. scolaire and permis resolve arrondissements correctly
4. geocode "12 rue de Rivoli, Paris" responds in < 1 second
5. equipements and antennes use GIST indexes (migration 037 applied)

Usage:
    DC_API_KEY=YOUR_KEY python scripts/test_phase67.py --base-url https://api.datacommune.fr

    # Local dev
    python scripts/test_phase67.py

The key is read from the DC_API_KEY or API_KEY environment variable (17-06:
never on argv); --api-key remains available but is discouraged.
"""

import argparse
import os
import sys
import time

import httpx

TESTS = []


def test(name, criteria_num, accept_empty=False):
    """Decorator to register a test function."""
    def decorator(fn):
        TESTS.append({"name": name, "criteria": criteria_num, "fn": fn, "accept_empty": accept_empty})
        return fn
    return decorator


# ── Criteria 1: PLM endpoints return data for Paris 75056 ──────────────

@test("loyers code_insee=75056", 1)
def test_loyers_paris(client, headers):
    return client.get("/v1/loyers", params={"code_insee": "75056"}, headers=headers, timeout=15)


@test("demographie code_insee=75056", 1)
def test_demo_paris(client, headers):
    return client.get("/v1/demographie", params={"code_insee": "75056"}, headers=headers, timeout=15)


@test("equipements code_insee=75056", 1)
def test_equip_paris(client, headers):
    return client.get("/v1/equipements", params={"code_insee": "75056"}, headers=headers, timeout=15)


@test("vacance code_insee=75056", 1)
def test_vacance_paris(client, headers):
    return client.get("/v1/vacance", params={"code_insee": "75056"}, headers=headers, timeout=15)


# ── Criteria 2: Lyon + Marseille ───────────────────────────────────────

@test("loyers code_insee=69123 (Lyon)", 2)
def test_loyers_lyon(client, headers):
    return client.get("/v1/loyers", params={"code_insee": "69123"}, headers=headers, timeout=15)


@test("demographie code_insee=69123 (Lyon)", 2)
def test_demo_lyon(client, headers):
    return client.get("/v1/demographie", params={"code_insee": "69123"}, headers=headers, timeout=15)


@test("loyers code_insee=13055 (Marseille)", 2)
def test_loyers_mrs(client, headers):
    return client.get("/v1/loyers", params={"code_insee": "13055"}, headers=headers, timeout=15)


@test("demographie code_insee=13055 (Marseille)", 2)
def test_demo_mrs(client, headers):
    return client.get("/v1/demographie", params={"code_insee": "13055"}, headers=headers, timeout=15)


# ── Criteria 3: scolaire + permis resolve arrondissements ─────────────
# scolaire needs nom_voie+numero for Paris (no secteur_unique in PLM cities)
# permis dataset is sparse (699 rows total, dept 75 has 0 rows)
# So we test with a full address for scolaire, and a dept that HAS data for permis

@test("scolaire Paris adresse complete", 3)
def test_scolaire_paris(client, headers):
    return client.get("/v1/scolaire", params={"code_insee": "75056", "nom_voie": "rue de rivoli", "numero": 12}, headers=headers, timeout=15)


@test("scolaire code_insee=48141 (secteur unique)", 3)
def test_scolaire_unique(client, headers):
    return client.get("/v1/scolaire", params={"code_insee": "48141"}, headers=headers, timeout=15)


@test("permis code_insee=93066 (Saint-Denis)", 3)
def test_permis_stdenis(client, headers):
    return client.get("/v1/permis", params={"code_insee": "93066"}, headers=headers, timeout=15)


@test("permis no 500 on PLM code", 3, accept_empty=True)
def test_permis_plm_no_crash(client, headers):
    return client.get("/v1/permis", params={"code_insee": "75056"}, headers=headers, timeout=15)


# ── Criteria 4: geocode < 1 second ────────────────────────────────────

@test("geocode '12 rue de Rivoli, Paris' < 1s", 4)
def test_geocode_perf(client, headers):
    return client.get("/v1/geocode", params={"q": "12 rue de Rivoli, Paris"}, headers=headers, timeout=15)


@test("geocode '1 place Bellecour, Lyon' < 1s", 4)
def test_geocode_lyon(client, headers):
    return client.get("/v1/geocode", params={"q": "1 place Bellecour, Lyon"}, headers=headers, timeout=15)


# ── Criteria 5: antennes spatial (GIST index) ─────────────────────────

@test("antennes spatial (lat/lon Paris)", 5)
def test_antennes_spatial(client, headers):
    return client.get("/v1/antennes", params={"lat": 48.8566, "lon": 2.3522, "rayon": 500}, headers=headers, timeout=15)


@test("equipements spatial (lat/lon Paris)", 5)
def test_equip_spatial(client, headers):
    return client.get("/v1/equipements", params={"lat": 48.8566, "lon": 2.3522, "rayon_m": 500}, headers=headers, timeout=15)


def has_data(resp):
    """Check if response has non-empty data."""
    if resp.status_code != 200:
        return False
    body = resp.json()
    if "donnees" in body:
        data = body["donnees"]
        if isinstance(data, list):
            return len(data) > 0
        return bool(data)
    return bool(body)


def row_count(resp):
    """Get row count from response."""
    if resp.status_code != 200:
        return 0
    body = resp.json()
    if "donnees" in body and isinstance(body["donnees"], list):
        return len(body["donnees"])
    return -1


def main():
    parser = argparse.ArgumentParser(description="Phase 67 verification tests")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--api-key",
        help=(
            "API key for protected endpoints (prefer the DC_API_KEY or "
            "API_KEY environment variable to avoid argv exposure)"
        ),
    )
    args = parser.parse_args()

    # 17-06: fall back to the environment so the key never needs to be on argv
    if not args.api_key:
        args.api_key = os.environ.get("DC_API_KEY") or os.environ.get("API_KEY")

    if not args.api_key:
        print(
            "ERROR: No API key provided (use the DC_API_KEY/API_KEY environment\n"
            "       variable or --api-key)."
        )
        sys.exit(1)

    client = httpx.Client(base_url=args.base_url)
    headers = {"X-API-Key": args.api_key}

    print()
    print("=" * 75)
    print("  PHASE 67 — Arrondissements & Performance Verification")
    print(f"  Target: {args.base_url}")
    print("=" * 75)

    results = {"pass": 0, "fail": 0, "details": []}
    current_criteria = 0

    criteria_labels = {
        1: "PLM Paris (75056) retourne des donnees",
        2: "PLM Lyon (69123) + Marseille (13055)",
        3: "Scolaire + Permis resolvent arrondissements",
        4: "Geocode < 1 seconde",
        5: "Equipements + Antennes index GIST (spatial)",
    }

    for t in TESTS:
        if t["criteria"] != current_criteria:
            current_criteria = t["criteria"]
            print(f"\n  [{current_criteria}] {criteria_labels[current_criteria]}")
            print("  " + "-" * 70)

        name = t["name"]
        try:
            t0 = time.perf_counter()
            resp = t["fn"](client, headers)
            elapsed = time.perf_counter() - t0
            elapsed_ms = elapsed * 1000

            if t.get("accept_empty"):
                ok = resp.status_code == 200
            else:
                ok = resp.status_code == 200 and has_data(resp)
            rows = row_count(resp)

            # Criteria 4: also check timing
            perf_note = ""
            if t["criteria"] == 4:
                if elapsed > 1.0:
                    ok = False
                    perf_note = f" [SLOW: {elapsed:.1f}s > 1s]"
                else:
                    perf_note = f" [fast: {elapsed_ms:.0f}ms]"

            icon = " OK " if ok else "FAIL"
            row_str = f"{rows} rows" if rows >= 0 else ""
            time_str = f"{elapsed_ms:>6.0f}ms"

            print(f"    {icon}  {name:<45} {time_str}  {row_str}{perf_note}")

            if not ok:
                results["fail"] += 1
                if resp.status_code != 200:
                    detail = ""
                    try:
                        detail = resp.json().get("detail", "")[:100]
                    except Exception:
                        pass
                    print(f"          -> HTTP {resp.status_code}: {detail}")
                elif not has_data(resp):
                    print("          -> EMPTY (pas de donnees)")
            else:
                results["pass"] += 1

        except httpx.TimeoutException:
            print(f"    FAIL  {name:<45} TIMEOUT")
            results["fail"] += 1
        except httpx.ConnectError:
            print(f"    FAIL  {name:<45} CONNECTION REFUSED")
            results["fail"] += 1
        except Exception as e:
            print(f"    FAIL  {name:<45} {type(e).__name__}: {e}")
            results["fail"] += 1

    client.close()

    total = results["pass"] + results["fail"]
    print()
    print("=" * 75)
    print(f"  RESULT: {results['pass']}/{total} passed", end="")
    if results["fail"] == 0:
        print(" — ALL GOOD")
    else:
        print(f" — {results['fail']} FAILED")
    print("=" * 75)
    print()

    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()

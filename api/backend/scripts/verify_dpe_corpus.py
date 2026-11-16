#!/usr/bin/env python3
"""Collect private, read-only ADEME evidence; never adjudicate dwelling identity.

Run from api/backend with an explicit input file and private output directory::

    python scripts/verify_dpe_corpus.py --input /private/input.json \
        --output-dir /private/ademe-evidence

Input: {"cases": [{"case_id": "C01", "numero_dpe": ["2344E0000000A"]}]}.
Other input fields (including DataCommune URLs) are ignored. One bounded GET per
distinct number retrieves the requested diagnosis and its direct successors in
dpe03existant only. This is not a complete replacement-chain or new-build audit.
Successful complete observations are reused with their original timestamps.
--refresh re-observes them; --offline forbids HTTP; --seed-proof accepts existing
ADEME envelopes with source/observed_at/request_url/status/body keys and a query
explicitly covering both the number and its direct successors.

Only private output files contain source addresses/identifiers. Console output
contains counts and closed error codes. No application import, DB, credential,
environment proxy, authentication header, or DataCommune request is used.
Exit codes: 0 = complete collection (including documented absences),
1 = inaccessible or truncated observation, 2 = invalid arguments/input or I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

SOURCE = "dpe03existant"
ENDPOINT = f"https://data.ademe.fr/data-fair/api/v1/datasets/{SOURCE}/lines"
SCHEMA_VERSION = 1
MAX_DIAGNOSTICS = 100
MAX_CASES = 200
MAX_BYTES = 8 * 1024 * 1024
PAGE_SIZE = 121
REPO_ROOT = Path(__file__).resolve().parents[3]
DPE_PATTERN = re.compile(r"[A-Z0-9]{13}")
CASE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
TERM_PATTERN = re.compile(r'(numero_dpe|numero_dpe_remplace):"([A-Z0-9]{13})"')
FIELDS = (
    "numero_dpe",
    "numero_dpe_remplace",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
    "date_derniere_modification_dpe",
    "adresse_brut",
    "adresse_complete_brut",
    "nom_commune_brut",
    "code_postal_brut",
    "identifiant_ban",
    "adresse_ban",
    "numero_voie_ban",
    "nom_rue_ban",
    "nom_commune_ban",
    "code_postal_ban",
    "code_insee_ban",
    "code_departement_ban",
    "score_ban",
    "statut_geocodage",
    "type_batiment",
    "surface_habitable_logement",
    "etiquette_dpe",
    "etiquette_ges",
    "annee_construction",
    "periode_construction",
    "type_installation_chauffage",
    "type_energie_principale_chauffage",
    "conso_5_usages_ep",
    "conso_5_usages_par_m2_ep",
    "emission_ges_5_usages",
    "emission_ges_5_usages_par_m2",
    "cout_total_5_usages",
    "qualite_isolation_enveloppe",
    "id_rnb",
    "provenance_id_rnb",
    "numero_etage_appartement",
    "complement_adresse_logement",
)
UNITS = {
    "surface_habitable_logement": "m²",
    "conso_5_usages_ep": "kWhEP/an (total logement)",
    "conso_5_usages_par_m2_ep": "kWhEP/m²/an",
    "emission_ges_5_usages": "kgCO2e/an (total logement)",
    "emission_ges_5_usages_par_m2": "kgCO2e/m²/an",
    "cout_total_5_usages": "EUR/an",
}
ERROR_CODES = {
    "http_error",
    "timeout",
    "network_error",
    "invalid_json",
    "invalid_response",
    "response_too_large",
    "cache_miss",
}


class CorpusError(Exception):
    """Carry a closed error code, never an input or exception message."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CorpusError("invalid_arguments")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise CorpusError("file_too_large")
        return json.loads(content.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CorpusError("file_unreadable") from exc


def normalize_numbers(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_DIAGNOSTICS:
        raise CorpusError("invalid_diagnostic_numbers")
    if any(not isinstance(item, str) for item in values):
        raise CorpusError("invalid_diagnostic_numbers")
    numbers = [item.strip().upper() for item in values]
    if any(not DPE_PATTERN.fullmatch(item) for item in numbers):
        raise CorpusError("invalid_diagnostic_numbers")
    return numbers


def normalize_cases(payload: Any, limit: int = MAX_DIAGNOSTICS) -> list[dict]:
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise CorpusError("invalid_cases")
    normalized: list[dict] = []
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not CASE_PATTERN.fullmatch(case_id):
            raise CorpusError("invalid_case_id")
        if case_id in seen:
            raise CorpusError("duplicate_case_id")
        seen.add(case_id)
        numbers = normalize_numbers(case.get("numero_dpe"))
        normalized.append({"case_id": case_id, "numero_dpe": numbers})
    if len(distinct_numbers(normalized)) > limit:
        raise CorpusError("diagnostic_limit_exceeded")
    return normalized


def distinct_numbers(cases: list[dict]) -> list[str]:
    return sorted({number for case in cases for number in case["numero_dpe"]})


def request_url(number: str) -> str:
    if not DPE_PATTERN.fullmatch(number):
        raise CorpusError("invalid_diagnostic_numbers")
    params = {
        "qs": f'numero_dpe:"{number}" OR numero_dpe_remplace:"{number}"',
        "size": PAGE_SIZE,
        "sort": "numero_dpe",
        "select": ",".join(FIELDS),
    }
    return str(httpx.URL(ENDPOINT, params=params))


def query_scope(url: Any) -> dict[str, set[str]]:
    if not isinstance(url, str):
        raise CorpusError("invalid_proof_url")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "data.ademe.fr"
        or parsed.path != urlsplit(ENDPOINT).path
        or parsed.fragment
    ):
        raise CorpusError("invalid_proof_url")
    params = parse_qs(parsed.query, strict_parsing=True)
    if set(params) - {"qs", "size", "sort", "select"}:
        raise CorpusError("invalid_proof_url")
    if len(params.get("qs", [])) != 1:
        raise CorpusError("invalid_proof_query")
    scope: dict[str, set[str]] = {"numero_dpe": set(), "numero_dpe_remplace": set()}
    for term in params["qs"][0].split(" OR "):
        match = TERM_PATTERN.fullmatch(term)
        if not match:
            raise CorpusError("invalid_proof_query")
        scope[match[1]].add(match[2])
    return scope


def validate_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise CorpusError("invalid_proof_timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise CorpusError("invalid_proof_timestamp")
    return value


def clean_body(body: Any, scope: dict[str, set[str]]) -> dict:
    if not isinstance(body, dict) or type(body.get("total")) is not int:
        raise CorpusError("invalid_response")
    rows, total = body.get("results"), body["total"]
    if not isinstance(rows, list) or not 0 <= len(rows) <= total:
        raise CorpusError("invalid_response")
    cleaned = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("numero_dpe"), str):
            raise CorpusError("invalid_response")
        if not DPE_PATTERN.fullmatch(row["numero_dpe"]):
            raise CorpusError("invalid_response")
        if any(
            not isinstance(row[key], (str, int, float, bool, type(None)))
            or (isinstance(row[key], float) and not math.isfinite(row[key]))
            for key in FIELDS
            if key in row
        ):
            raise CorpusError("invalid_response")
        if (
            row["numero_dpe"] not in scope["numero_dpe"]
            and row.get("numero_dpe_remplace") not in scope["numero_dpe_remplace"]
        ):
            raise CorpusError("invalid_response")
        cleaned.append({key: row[key] for key in FIELDS if key in row})
    return {"total": total, "results": cleaned}


def clean_proof(proof: Any) -> dict:
    if not isinstance(proof, dict) or proof.get("source") != SOURCE:
        raise CorpusError("invalid_proof")
    scope = query_scope(proof.get("request_url"))
    clean = {
        "source": SOURCE,
        "observed_at": validate_timestamp(proof.get("observed_at")),
        "request_url": proof["request_url"],
        "status": proof.get("status"),
    }
    if clean["status"] is not None and type(clean["status"]) is not int:
        raise CorpusError("invalid_proof")
    if proof.get("error_code") in ERROR_CODES:
        clean["error_code"] = proof["error_code"]
    elif clean["status"] == 200:
        clean["body"] = clean_body(proof.get("body"), scope)
    else:
        clean["error_code"] = "http_error"
    return clean


def bounded_body(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_BYTES:
            raise CorpusError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_proof(client: httpx.Client, number: str) -> dict:
    proof: dict[str, Any] = {
        "source": SOURCE,
        "observed_at": utc_now(),
        "request_url": request_url(number),
        "status": None,
    }
    try:
        with client.stream("GET", proof["request_url"], follow_redirects=False) as resp:
            proof["status"] = resp.status_code
            if resp.status_code != 200:
                return {**proof, "error_code": "http_error"}
            proof["body"] = json.loads(bounded_body(resp))
        return clean_proof(proof)
    except httpx.TimeoutException:
        return {**proof, "body": None, "error_code": "timeout"}
    except httpx.HTTPError:
        return {**proof, "body": None, "error_code": "network_error"}
    except (ValueError, UnicodeError):
        return {**proof, "body": None, "error_code": "invalid_json"}
    except CorpusError as exc:
        code = (
            "response_too_large"
            if str(exc) == "response_too_large"
            else "invalid_response"
        )
        return {**proof, "body": None, "error_code": code}


def complete_for(proof: dict, number: str) -> bool:
    scope = query_scope(proof["request_url"])
    body = proof.get("body") or {}
    return (
        proof.get("status") == 200
        and "error_code" not in proof
        and number in scope["numero_dpe"] & scope["numero_dpe_remplace"]
        and body.get("total") == len(body.get("results", []))
    )


def write_json(path: Path, value: dict) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def load_seeds(paths: list[Path], numbers: list[str]) -> dict[str, dict]:
    seeds: dict[str, dict] = {}
    for path in paths:
        proof = clean_proof(read_json(path))
        for number in numbers:
            if complete_for(proof, number) and (
                number not in seeds
                or datetime.fromisoformat(proof["observed_at"])
                > datetime.fromisoformat(seeds[number]["observed_at"])
            ):
                seeds[number] = proof
    return seeds


def read_cached(path: Path, number: str) -> dict | None:
    if not path.exists():
        return None
    try:
        proof = clean_proof(read_json(path))
        if complete_for(proof, number):
            return proof
    except (CorpusError, ValueError, TypeError):
        pass
    return None


def missing_proof(number: str) -> dict:
    return {
        "source": SOURCE,
        "observed_at": None,
        "request_url": request_url(number),
        "status": None,
        "error_code": "cache_miss",
    }


def finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    return None


def factual_checks(row: dict, observed_at: str | None) -> dict:
    area = finite_number(row.get("surface_habitable_logement"))
    total = finite_number(row.get("conso_5_usages_ep"))
    ratio = round(total / area, 4) if total is not None and area and area > 0 else None
    expired = None
    try:
        if observed_at:
            expired = date.fromisoformat(row["date_fin_validite_dpe"]) < (
                datetime.fromisoformat(observed_at).date()
            )
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "numero_dpe": row["numero_dpe"],
        "missing_selected_fields": [key for key in FIELDS if key not in row],
        "computed_ep_per_m2_from_total_and_area": ratio,
        "declared_ep_per_m2": row.get("conso_5_usages_par_m2_ep"),
        "end_date_before_observation_date": expired,
    }


def evidence(number: str, proof: dict, origin: str) -> dict:
    body = proof.get("body") or {}
    primary = [row for row in body.get("results", []) if row["numero_dpe"] == number]
    successors = [
        row
        for row in body.get("results", [])
        if row.get("numero_dpe_remplace") == number
    ]
    complete = complete_for(proof, number)
    state = "found" if primary else "absent" if complete else "inaccessible"
    return {
        "numero_dpe": number,
        "state": state,
        "origin": origin,
        "source": SOURCE,
        "request_url": proof["request_url"],
        "observed_at": proof["observed_at"],
        "http_status": proof["status"],
        "error_code": proof.get("error_code"),
        "query_complete": complete,
        "primary_records": primary,
        "direct_successor_records": successors,
        "factual_checks": [
            factual_checks(row, proof["observed_at"]) for row in primary + successors
        ],
        "proof_sha256": hashlib.sha256(
            json.dumps(proof, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    }


def collect(
    cases: list[dict],
    output_dir: Path,
    client: httpx.Client,
    *,
    seeds: dict[str, dict] | None = None,
    offline: bool = False,
    refresh: bool = False,
    interval: float = 0.25,
) -> dict:
    cache_dir = output_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    observations: list[dict] = []
    requests = 0
    for number in distinct_numbers(cases):
        path = cache_dir / f"{number}.json"
        proof = None if refresh else read_cached(path, number)
        origin = "cache"
        if proof is None and not refresh:
            proof, origin = (seeds or {}).get(number), "seed"
        if proof is None and offline:
            proof, origin = missing_proof(number), "offline"
        if proof is None:
            if requests:
                time.sleep(interval)
            proof, origin = fetch_proof(client, number), "http"
            requests += 1
        if origin in {"seed", "http"}:
            write_json(path, proof)
        observations.append(evidence(number, proof, origin))
    report = build_report(cases, observations, requests)
    write_json(output_dir / "ademe-evidence.json", report)
    return report


def build_report(cases: list[dict], observations: list[dict], requests: int) -> dict:
    counts = Counter(item["state"] for item in observations)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "source": SOURCE,
        "scope": "requested numbers and direct successors in dpe03existant only",
        "identity_adjudication": "not_performed",
        "absence_scope": (
            "no exact number in a complete query of this source at observed_at"
        ),
        "cache_policy": (
            "original observation dates retained; use --refresh to re-observe"
        ),
        "proof_hash_scope": "normalized whitelisted envelope, not raw HTTP bytes",
        "units": UNITS,
        "summary": {
            "cases": len(cases),
            "occurrences": sum(len(case["numero_dpe"]) for case in cases),
            "distinct_diagnostics": len(observations),
            "http_requests": requests,
            "reused": sum(item["origin"] in {"cache", "seed"} for item in observations),
            "found": counts["found"],
            "absent": counts["absent"],
            "inaccessible": counts["inaccessible"],
            "incomplete_queries": sum(
                not item["query_complete"] for item in observations
            ),
        },
        "cases": cases,
        "diagnostics": observations,
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = SafeArgumentParser(description=__doc__, prog="verify_dpe_corpus")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed-proof", type=Path, action="append", default=[])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--max-diagnostics", type=int, default=MAX_DIAGNOSTICS)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args(argv)
    if args.offline and args.refresh:
        raise CorpusError("incompatible_arguments")
    if not 1 <= args.max_diagnostics <= MAX_DIAGNOSTICS:
        raise CorpusError("invalid_diagnostic_limit")
    if not 1 <= args.timeout <= 60 or not 0 <= args.interval <= 5:
        raise CorpusError("invalid_request_bounds")
    if args.output_dir.resolve().is_relative_to(REPO_ROOT):
        raise CorpusError("output_must_be_outside_repository")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        cases = normalize_cases(read_json(args.input), args.max_diagnostics)
        seeds = load_seeds(args.seed_proof, distinct_numbers(cases))
        with httpx.Client(
            timeout=args.timeout, trust_env=False, follow_redirects=False
        ) as client:
            report = collect(
                cases,
                args.output_dir,
                client,
                seeds=seeds,
                offline=args.offline,
                refresh=args.refresh,
                interval=args.interval,
            )
        print(json.dumps(report["summary"], sort_keys=True))
        return int(report["summary"]["incomplete_queries"] > 0)
    except CorpusError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print(json.dumps({"error": "invalid_input_or_io"}), file=sys.stderr)
    except Exception:  # noqa: BLE001
        # No traceback: transport/file failures can carry private input or URLs.
        print(json.dumps({"error": "collection_failed"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

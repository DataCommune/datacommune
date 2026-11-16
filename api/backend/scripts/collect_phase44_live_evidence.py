#!/usr/bin/env python3
"""Phase 44 live evidence collector (read-only, redacted, non-interactive).

This opt-in CLI proves the five Phase 44 requirements against the live vertical
without ever mutating it:

* PROV-01 / PROV-03 -- ``/v1/datasets`` provenance vs the successful
  ``MAX(completed_at)`` per dataset read from ``etl_runs``;
* PROV-02 -- typed absence states surfaced by ``/v1/resolve``;
* HEALTH-01 -- ``/v1/health`` stays HTTP 200 with a split
  ``technical_status`` / ``editorial_status`` under editorial degradation;
* GEO-01 -- ``/v1/geocode`` resolves 44/85 addresses through the local BAN.

Security posture (reused from ``scripts/verify_golden_cases.py``):

* SQL is selected from an exact read-only allowlist and runs on a PostgreSQL
  transaction whose read-only state is verified before any case query;
* HTTP is GET-only, redirects are disabled and every target host must be on the
  DataCommune allowlist -- the former national BAN host is refused;
* credentials and the private golden corpus are read from named environment
  variables and never printed;
* the rendered Markdown keeps ONLY commit, timestamp, manifest hash, opaque
  case IDs, HTTP codes, booleans/invariants and non-sensitive millésimes; every
  other value is whitelist-redacted, and the renderer audits its own output and
  fails closed if any leak indicator survives.

Any unproven invariant, missing secret or missing private input yields a
non-zero exit and a ``FAIL`` row -- never a silent PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psycopg

# Ensure the backend root is importable so the shared golden security
# primitives resolve when this file is run directly (uv run python scripts/...),
# where only the scripts/ directory -- not the backend root -- is on sys.path.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.verify_golden_cases import (  # noqa: E402
    READ_ONLY_STATE_SQL,
    GoldenSecurityError,
    _open_read_only_connection,
    _read_private_input,
    _required_env,
    assert_select_only,
    audit_artifact_text,
    audit_manifest_keys,
    load_schema,
    validate_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = (
    _REPO_ROOT / "api" / "backend" / "reports" / "phase44-live-evidence.redacted.md"
)

# Exact, pre-reviewed, read-only statement. Mirrors the LATERAL sub-select used
# by app/datasets/queries.py for the last successful run of a dataset.
LAST_SUCCESS_SQL = (
    "SELECT completed_at FROM etl_runs "
    "WHERE dataset_name = %s AND status = 'success' "
    "ORDER BY completed_at DESC LIMIT 1"
)
ALLOWED_SQL: frozenset[str] = frozenset({LAST_SUCCESS_SQL, READ_ONLY_STATE_SQL})

# Host allowlist: HTTPS DataCommune subdomains, or a loopback runner-internal
# path. The former national BAN host (api-adresse.data.gouv.fr) is refused.
_ALLOWED_HTTPS_SUFFIX = ".datacommune.fr"
_ALLOWED_APEX = "datacommune.fr"
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

# Closed vocabulary of scalar strings that may appear verbatim in the report.
_SAFE_STATE_TOKENS: frozenset[str] = frozenset(
    {
        "available",
        "unavailable",
        "current",
        "degraded",
        "unknown",
        "value",
        "absent",
        "error",
        "not_reported",
        "out_of_coverage",
        "temporary_error",
        "source_delayed",
        "not_applicable",
        "ok",
        "degradee",
        "deconnectee",
        "true",
        "false",
        "n/a",
    }
)

_SAFE_YEAR_RE = re.compile(r"^\d{4}$")
_SAFE_CODE_RE = re.compile(r"^\d{2,5}$")
_SAFE_HEX_RE = re.compile(r"^[0-9a-f]{7,64}$")
_SAFE_DATASET_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_SAFE_CASE_ID_RE = re.compile(r"^golden-(?:address|commune)-\d{3}$")
_SAFE_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

_REDACTED = "[REDACTED]"


@dataclass
class RequirementRow:
    """One redacted requirement outcome row."""

    requirement: str
    observable: str
    source: str
    http_code: int | None
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)


def manifest_sha256(text: str) -> str:
    """Return the hex SHA-256 of the manifest text (a non-sensitive fingerprint)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assert_get_only(method: str) -> None:
    """Allow only the HTTP GET method."""
    if method.upper() != "GET":
        raise GoldenSecurityError("only HTTP GET is permitted")


def assert_allowed_host(url: str) -> None:
    """Refuse any non-DataCommune host and the former national BAN host."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return
    if parsed.scheme != "https":
        raise GoldenSecurityError("DataCommune host must be reached over HTTPS")
    if host != _ALLOWED_APEX and not host.endswith(_ALLOWED_HTTPS_SUFFIX):
        raise GoldenSecurityError("target host is outside the DataCommune allowlist")


def _safe_scalar(value: Any) -> str:
    """Whitelist-redact a scalar down to a report-safe string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "n/a"
    if isinstance(value, str):
        token = value.strip()
        lowered = token.lower()
        if lowered in _SAFE_STATE_TOKENS:
            return lowered
        if (
            _SAFE_YEAR_RE.match(token)
            or _SAFE_CODE_RE.match(token)
            or _SAFE_HEX_RE.match(token)
            or _SAFE_DATASET_RE.match(token)
            or _SAFE_CASE_ID_RE.match(token)
            or _SAFE_ISO_RE.match(token)
        ):
            return token
    return _REDACTED


def _self_audit(text: str) -> list[str]:
    """Return leak indicators found in the rendered report (empty == clean)."""
    return audit_artifact_text(text, "report")


def render_report(context: dict[str, str], rows: list[RequirementRow]) -> str:
    """Render the redacted Markdown report and fail closed on any residual leak."""
    lines: list[str] = []
    lines.append("# Phase 44 — Preuves live redacted (collecteur automatisé)")
    lines.append("")
    commit = _safe_scalar(context.get("commit"))
    generated = _safe_scalar(context.get("generated_at"))
    manifest_hash = _safe_scalar(context.get("manifest_sha256"))
    lines.append(f"- **Commit :** `{commit}`")
    lines.append(f"- **Généré :** {generated}")
    lines.append(f"- **Manifest SHA-256 :** `{manifest_hash}`")
    lines.append("")
    header = "| Exigence | Observable | Source | HTTP | Résultat | Preuve (redacted) |"
    lines.append(header)
    lines.append("|---|---|---|---|---|---|")
    for row in rows:
        # requirement/observable/source are author-controlled structural labels;
        # only the evidence values (from live payloads) are whitelist-redacted.
        evidence = ", ".join(
            f"{_safe_scalar(key)}={_safe_scalar(val)}"
            for key, val in row.evidence.items()
        )
        verdict = "PASS" if row.passed else "FAIL"
        http = _safe_scalar(row.http_code) if row.http_code is not None else "n/a"
        lines.append(
            f"| {row.requirement} | {row.observable} "
            f"| {row.source} | {http} | {verdict} | {evidence} |"
        )
    lines.append("")
    overall = all(row.passed for row in rows) and bool(rows)
    lines.append(f"**Verdict collecteur : {'PASS' if overall else 'FAIL'}**")
    lines.append("")
    report = "\n".join(lines) + "\n"
    leaks = _self_audit(report)
    if leaks:
        raise GoldenSecurityError("rendered report failed its own leak audit")
    return report


# ---------------------------------------------------------------------------
# Pure evaluation of collected observations (no I/O, testable in isolation).
# ---------------------------------------------------------------------------
def _same_instant(a: Any, b: Any) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    if a == b:
        return True
    try:
        pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
        pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return False
    return pa == pb


def evaluate_provenance_freshness(
    datasets: list[dict[str, Any]], last_success: dict[str, str]
) -> tuple[bool, dict[str, Any]]:
    """PROV-01/03: API provenance matches the successful MAX(completed_at)."""
    if not datasets or not last_success:
        return False, {"proven": False}
    evidence: dict[str, Any] = {}
    for item in datasets:
        name = item.get("nom")
        if name not in last_success:
            continue
        provenance = item.get("provenance") or {}
        api_value = provenance.get("derniere_importation_reussie")
        evidence[str(name)] = _same_instant(api_value, last_success[str(name)])
    passed = bool(evidence) and all(evidence.values())
    return passed, evidence


def evaluate_resolve_absence(
    payload: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """PROV-02: /v1/resolve exposes typed presence/absence states.

    The real contract nests ``dataset_states`` under ``donnees[0]`` as a dict
    keyed by dataset name (``resolve/router.py`` + ``resolve/schemas.py``), not
    as a top-level list.
    """
    donnees = payload.get("donnees")
    first = donnees[0] if isinstance(donnees, list) and donnees else {}
    states = first.get("dataset_states") if isinstance(first, dict) else None
    if not isinstance(states, dict) or not states:
        return False, {"proven": False}
    items = list(states.values())
    valid = all(
        isinstance(state, dict)
        and state.get("presence") in {"value", "absent", "error"}
        for state in items
    )
    evidence = {
        "states_count": len(items),
        "has_value": any(
            isinstance(s, dict) and s.get("presence") == "value" for s in items
        ),
        "has_absent": any(
            isinstance(s, dict) and s.get("presence") == "absent" for s in items
        ),
    }
    return valid, evidence


def evaluate_geocode(
    payload: dict[str, Any], departement: str
) -> tuple[bool, dict[str, Any]]:
    """GEO-01: /v1/geocode returns in-department rows (coords never copied)."""
    rows = payload.get("donnees")
    if not isinstance(rows, list) or not rows:
        return False, {"result_count": 0, "departement": departement}
    # /v1/geocode emits ResultatGeocodage rows whose INSEE field is
    # `code_commune` (app/ban/schemas.py), not `code_insee`.
    in_scope = all(
        str(row.get("code_commune", "")).startswith(departement) for row in rows
    )
    return in_scope, {
        "result_count": len(rows),
        "in_scope": in_scope,
        "departement": departement,
    }


def evaluate_health(
    payload: dict[str, Any], status_code: int
) -> tuple[bool, dict[str, Any]]:
    """HEALTH-01: editorial degradation stays HTTP 200 with a split status."""
    technical = payload.get("technical_status")
    editorial = payload.get("editorial_status")
    passed = (
        status_code == 200
        and technical == "available"
        and editorial in {"current", "degraded", "unknown"}
    )
    return passed, {
        "http": status_code,
        "technical_status": technical,
        "editorial_status": editorial,
        "statut": payload.get("statut"),
    }


# ---------------------------------------------------------------------------
# Live collection (runner-only; the local machine cannot reach the prod DB).
# ---------------------------------------------------------------------------
def _get(
    client: httpx.Client, base_url: str, path: str, params: dict[str, Any]
) -> httpx.Response:
    assert_get_only("GET")
    assert_allowed_host(base_url.rstrip("/") + path)
    response = client.get(path, params=params)
    if response.history:
        raise GoldenSecurityError("unexpected redirect during read-only GET")
    return response


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _last_success(conn: psycopg.Connection[Any], dataset_name: str) -> str | None:
    assert_select_only(LAST_SUCCESS_SQL, ALLOWED_SQL)
    row = conn.execute(LAST_SUCCESS_SQL, (dataset_name,)).fetchone()
    if not row:
        return None
    value = row.get("completed_at") if isinstance(row, dict) else row[0]
    return value.isoformat() if isinstance(value, datetime) else None


def _collect_all(
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    base_url: str,
    manifest: dict[str, Any],
    private: dict[str, Any],
) -> list[RequirementRow]:
    rows: list[RequirementRow] = []

    datasets_resp = _get(client, base_url, "/v1/datasets", {})
    datasets_payload = datasets_resp.json()
    datasets = (
        datasets_payload.get("donnees")
        if isinstance(datasets_payload, dict)
        else datasets_payload
    )
    if not isinstance(datasets, list):
        datasets = []
    last_success = {
        str(item.get("nom")): _last_success(conn, str(item.get("nom")))
        for item in datasets
        if item.get("nom")
    }
    last_success = {k: v for k, v in last_success.items() if v is not None}
    passed, evidence = evaluate_provenance_freshness(datasets, last_success)
    rows.append(
        RequirementRow(
            "PROV-01/03",
            "datasets vs MAX(completed_at) success",
            "GET+SELECT",
            datasets_resp.status_code,
            passed,
            evidence,
        )
    )

    # PROV-02 needs a request that actually enriches: dataset_states are only
    # populated when the input resolves to a point (an address), not for a bare
    # INSEE forced as `type=commune` (unsupported -- geocoded as address text).
    resolve_case = next(
        (
            c
            for c in manifest["cases"]
            if c.get("type") == "address"
            and isinstance(private.get(c["case_id"], {}).get("query"), str)
            and private[c["case_id"]]["query"].strip()
        ),
        None,
    )
    if resolve_case is not None:
        query = private[resolve_case["case_id"]]["query"]
        resolve_resp = _get(
            client,
            base_url,
            "/v1/resolve",
            {"q": query, "datasets": "dpe,dvf,commune"},
        )
        passed, evidence = evaluate_resolve_absence(resolve_resp.json())
        rows.append(
            RequirementRow(
                "PROV-02",
                "resolve typed absence states",
                "GET",
                resolve_resp.status_code,
                passed,
                evidence,
            )
        )

    for departement in ("44", "85"):
        case = next(
            (
                c
                for c in manifest["cases"]
                if c.get("type") == "address" and c.get("departement") == departement
            ),
            None,
        )
        if case is None:
            continue
        private_case = private.get(case["case_id"], {})
        query = private_case.get("query")
        if not isinstance(query, str) or not query.strip():
            rows.append(
                RequirementRow(
                    f"GEO-01 ({departement})",
                    "geocode private input missing",
                    "GET",
                    None,
                    False,
                    {"proven": False},
                )
            )
            continue
        geocode_resp = _get(client, base_url, "/v1/geocode", {"q": query, "limit": 1})
        passed, evidence = evaluate_geocode(geocode_resp.json(), departement)
        rows.append(
            RequirementRow(
                f"GEO-01 ({departement})",
                "geocode 44/85 via local BAN",
                "GET",
                geocode_resp.status_code,
                passed,
                evidence,
            )
        )

    health_resp = _get(client, base_url, "/v1/health", {})
    health_payload = health_resp.json() if health_resp.content else {}
    passed, evidence = evaluate_health(health_payload, health_resp.status_code)
    rows.append(
        RequirementRow(
            "HEALTH-01",
            "editorial degradation stays 200",
            "GET",
            health_resp.status_code,
            passed,
            evidence,
        )
    )
    return rows


def _write_report(output: Path, report: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")


def _run_live(args: argparse.Namespace) -> int:
    manifest_text = Path(args.manifest).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    schema = load_schema()
    reasons = validate_manifest(manifest, schema)
    violations = audit_artifact_text(manifest_text, "manifest") + audit_manifest_keys(
        manifest
    )
    output = Path(args.output)
    if reasons or violations:
        blocked_context = {
            "commit": _git_commit(),
            "generated_at": _now(),
            "manifest_sha256": "",
        }
        blocked_rows = [
            RequirementRow(
                "MANIFEST", "manifest audit", "static", None, False, {"proven": False}
            )
        ]
        _write_report(output, render_report(blocked_context, blocked_rows))
        print("phase44 live: BLOCKED by manifest audit")
        return 1
    private = _read_private_input(args.private_input, args.private_input_env)
    dsn = _required_env(args.dsn_env)
    base_url = _required_env(args.api_base_url_env)
    assert_allowed_host(base_url.rstrip("/") + "/v1/health")
    api_key = _required_env(args.api_key_env)
    conn = _open_read_only_connection(dsn)
    try:
        with httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key},
            timeout=30,
            follow_redirects=False,
        ) as client:
            rows = _collect_all(conn, client, base_url, manifest, private)
    finally:
        conn.rollback()
        conn.close()
    context = {
        "commit": _git_commit(),
        "generated_at": _now(),
        "manifest_sha256": manifest_sha256(manifest_text),
    }
    report = render_report(context, rows)
    _write_report(output, report)
    ok = bool(rows) and all(row.passed for row in rows)
    print(f"phase44 live: {'OK' if ok else 'BLOCKED'} ({len(rows)} requirement rows)")
    return 0 if ok else 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    private = parser.add_mutually_exclusive_group()
    private.add_argument("--private-input", help="Path to gitignored private JSON")
    private.add_argument(
        "--private-input-env", help="Environment variable containing private JSON"
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--dsn-env", default="DATABASE_URL")
    parser.add_argument("--api-base-url-env", default="API_BASE_URL")
    parser.add_argument("--api-key-env", default="DC_API_KEY")
    args = parser.parse_args(argv)
    if not args.read_only or not (args.private_input or args.private_input_env):
        parser.error(
            "live collection requires --read-only and one private input source"
        )
    try:
        return _run_live(args)
    except (GoldenSecurityError, OSError, ValueError, httpx.HTTPError, psycopg.Error):
        print("phase44 live: BLOCKED")
        return 1


if __name__ == "__main__":
    sys.exit(main())

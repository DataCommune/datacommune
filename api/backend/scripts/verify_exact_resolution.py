#!/usr/bin/env python3
"""Phase 45 exact-resolution live verifier (read-only, redacted, fail-closed).

This opt-in CLI proves the Phase 45 canonical-identity requirements
(DATA-06, GEO-02, PRIV-01) against the live 44/85 vertical without ever
mutating it. It reuses the security posture proven by
``scripts/verify_golden_cases.py`` and
``scripts/collect_phase44_live_evidence.py``:

* SQL is selected from an exact read-only allowlist and runs on a PostgreSQL
  transaction whose read-only state is verified before any case query;
* HTTP replay is GET-only, redirects are refused and every target host must
  be on the DataCommune allowlist;
* credentials and the private golden inputs are read from named environment
  variables or the gitignored private corpus, and never printed;
* the single JSON report keeps ONLY opaque case/probe ids, categories, UTC
  timestamps, assertion counts and normalized status codes. Every other
  value is whitelist-redacted, and the renderer audits its own output and
  fails closed (``GoldenSecurityError``) if any leak indicator survives --
  including a street address, a precise coordinate, a query URL or a
  non-allowlisted residential identifier such as a parcel id.

Per address case the verifier asserts: the explicit machine outcome, the
canonical ``code_insee``, the cross-layer parcel invariant
``base.id_parcelle == datasets.cadastre.id_parcelle == dvf[*].id_parcelle``,
the equivalence of the address-mode and cadastre-mode identities (the
cadastre mode feeds DVF by the canonical id, never a centroid), and the
exclusion of a real neighbouring mutation observed through read-only SQL.
Probes supplied in the private input cover the ambiguous and out-of-coverage
categories. A missing invariant, missing private input or incomplete
44/85 x categories coverage yields NO-GO and a non-zero exit -- never a
silent skip (Plan 45-05, T-45-13/14/15/16).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg

# Ensure the backend root is importable so the shared golden security
# primitives resolve when this file is run directly (uv run python scripts/...),
# where only the scripts/ directory -- not the backend root -- is on sys.path.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.collect_phase44_live_evidence import (  # noqa: E402
    _git_commit,
    assert_allowed_host,
    assert_get_only,
    manifest_sha256,
)
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
    _REPO_ROOT / "api" / "backend" / "reports" / "exact-resolution-report.redacted.json"
)

# Exact, pre-reviewed, read-only statement: observe ONE mutation attached to a
# DIFFERENT parcel of the same commune (LEFT(id_parcelle, 5) is the INSEE
# prefix of the 14-char parcel id). The partition key ``code_departement``
# prunes the scan; the observed id stays in memory and NEVER enters the report.
NEIGHBOUR_MUTATION_SQL = (
    "SELECT id_mutation FROM mutations "
    "WHERE code_departement = %s AND LEFT(id_parcelle, 5) = %s "
    "AND id_parcelle <> %s "
    "ORDER BY date_mutation DESC LIMIT 1"
)
ALLOWED_SQL: frozenset[str] = frozenset({NEIGHBOUR_MUTATION_SQL, READ_ONLY_STATE_SQL})

# Closed set of normalized NO-GO codes -- the only failure vocabulary the
# report may carry (checkpoint 45-05-02 consumes these codes verbatim).
NO_GO_CODES: frozenset[str] = frozenset(
    {
        "empty_response",
        "outcome_missing",
        "outcome_mismatch",
        "code_insee_mismatch",
        "parcel_identity_missing",
        "parcel_identity_mismatch",
        "parcel_invariant_mismatch",
        "dvf_state_mismatch",
        "cadastre_equivalence_mismatch",
        "neighbour_mutation_leaked",
        "neighbour_not_observed",
        "ambiguous_silent_selection",
        "ambiguous_without_candidates",
        "absence_not_typed",
        "private_input_missing",
        "coverage_incomplete",
        "http_error",
        "manifest_audit_failed",
        "live_observation_failed",
        "live_replay_failed",
    }
)

# The four live categories the checkpoint must cover across 44 AND 85.
REQUIRED_COVERAGE: frozenset[str] = frozenset(
    {"exact_with_dvf", "exact_without_dvf", "ambiguous", "out_of_coverage"}
)
_REQUIRED_DEPARTEMENTS: frozenset[str] = frozenset({"44", "85"})

_ALLOWED_ISSUES: frozenset[str] = frozenset(
    {"exact", "ambiguous", "not_reported", "out_of_coverage"}
)
_PROBE_EXPECTED_ISSUES: frozenset[str] = frozenset(
    {"ambiguous", "not_reported", "out_of_coverage"}
)
_PROBE_MODES: frozenset[str] = frozenset({"adresse", "gps", "cadastre"})
_PROBE_ID_RE = re.compile(r"^probe-(?:44|85)-[a-z][a-z0-9-]{0,30}$")

# ---------------------------------------------------------------------------
# Whitelist redaction (report values are opaque-by-construction).
# ---------------------------------------------------------------------------
_REDACTED = "[REDACTED]"
_SAFE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SAFE_CASE_ID_RE = re.compile(r"^golden-(?:address|commune)-\d{3}$")
_SAFE_CODE_RE = re.compile(r"^\d{2,5}$")
_SAFE_HEX_RE = re.compile(r"^[0-9a-f]{7,64}$")
_SAFE_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d{1,6})?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_URL_RE = re.compile(r"(?i)https?://|[?&]q=|\bwww\.")

_SAFE_TOKENS: frozenset[str] = (
    frozenset(
        {
            "exact-resolution-report",
            "45",
            "PASS",
            "NO-GO",
            "n/a",
            "exact",
            "ambiguous",
            "not_reported",
            "out_of_coverage",
            "value",
            "absent",
            "error",
            "complete",
            "no_dpe",
            "no_dvf",
            "dataset_out_of_coverage",
            "exact_with_dvf",
            "exact_without_dvf",
            "address",
        }
    )
    | NO_GO_CODES
)


def safe_scalar(value: Any) -> Any:
    """Whitelist-redact a scalar down to a report-safe value.

    Booleans, ints and ``None`` are safe by type. Floats are NEVER emitted:
    they could be coordinates. Strings survive only when they belong to the
    closed vocabulary or match an opaque shape (case/probe id, ISO instant,
    short hex, 2-5 digit code). Everything else -- an address, a parcel id, a
    query URL, a credential -- collapses to ``[REDACTED]``.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        token = value.strip()
        if token in _SAFE_TOKENS:
            return token
        for pattern in (
            _SAFE_CASE_ID_RE,
            _PROBE_ID_RE,
            _SAFE_ISO_RE,
            _SAFE_HEX_RE,
            _SAFE_CODE_RE,
        ):
            if pattern.fullmatch(token):
                return token
    return _REDACTED


def _redact(value: Any) -> Any:
    """Recursively project a report structure onto the whitelist."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _SAFE_KEY_RE.fullmatch(key) is None:
                raise GoldenSecurityError("report key is outside the allowlist")
            redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return safe_scalar(value)


def audit_report_text(text: str) -> list[str]:
    """Scan the rendered report for leak indicators, URLs and q= params."""
    violations = audit_artifact_text(text, "report")
    if _URL_RE.search(text):
        violations.append("report: query_url")
    return violations


def render_report(report: Mapping[str, Any]) -> str:
    """Render the redacted JSON report and fail closed on any residual leak."""
    text = json.dumps(_redact(dict(report)), indent=2, ensure_ascii=False) + "\n"
    if audit_report_text(text):
        raise GoldenSecurityError("rendered report failed its own leak audit")
    return text


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------
@dataclass
class CaseRow:
    """Redacted outcome of one manifest address case."""

    case_id: str
    category: str
    departement: str
    coverage_key: str
    assertions: int = 0
    failures: list[str] = field(default_factory=list)
    neighbour_observed: bool = False
    neighbour_excluded: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass
class ProbeRow:
    """Redacted outcome of one private probe (ambiguous / out-of-coverage)."""

    probe_id: str
    departement: str
    expected_issue: str
    observed_issue: str | None
    assertions: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------------------
# Pure evaluation of collected payloads (no I/O, testable in isolation).
# ---------------------------------------------------------------------------
def _first_result(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    donnees = payload.get("donnees")
    if isinstance(donnees, list) and donnees and isinstance(donnees[0], Mapping):
        return donnees[0]
    return None


def _resolution_issue(payload: Mapping[str, Any]) -> str | None:
    meta = payload.get("meta")
    resolution = meta.get("resolution") if isinstance(meta, Mapping) else None
    issue = resolution.get("issue") if isinstance(resolution, Mapping) else None
    return issue if isinstance(issue, str) else None


def _cadastre_parcel(datasets: Any) -> str | None:
    block = datasets.get("cadastre") if isinstance(datasets, Mapping) else None
    if isinstance(block, list) and block:
        block = block[0]
    parcel = block.get("id_parcelle") if isinstance(block, Mapping) else None
    return parcel if isinstance(parcel, str) else None


def _dvf_rows(datasets: Any) -> list[Mapping[str, Any]]:
    rows = datasets.get("dvf") if isinstance(datasets, Mapping) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _dedupe(codes: list[str]) -> list[str]:
    return list(dict.fromkeys(codes))


def evaluate_exact_case(  # noqa: C901
    entry: Mapping[str, Any],
    payload: Mapping[str, Any],
    expected_parcel: str,
) -> CaseRow:
    """Assert the canonical identity of one manifest address case.

    Checks (one assertion each, fail-closed): non-empty response, explicit
    ``exact`` machine outcome, manifest ``code_insee``, canonical base
    parcel bound to the private expected id, cadastre-block equality, the
    manifest DVF state (with vs without DVF) and the per-row DVF parcel
    invariant. Every failure is a normalized NO-GO code -- never a skip.
    """
    api_invariants = entry.get("invariants", {}).get("api", {})
    expected_dvf = api_invariants.get("dvf_state", "value")
    row = CaseRow(
        case_id=str(entry.get("case_id", "")),
        category=str(entry.get("category", "")),
        departement=str(entry.get("departement", "")),
        coverage_key=(
            "exact_with_dvf" if expected_dvf == "value" else "exact_without_dvf"
        ),
    )
    result = _first_result(payload)
    row.assertions += 1
    if result is None:
        row.failures.append("empty_response")
        return row
    issue = _resolution_issue(payload)
    row.assertions += 1
    if issue is None:
        row.failures.append("outcome_missing")
    elif issue != "exact":
        row.failures.append("outcome_mismatch")
    row.assertions += 1
    if result.get("code_insee") != entry.get("code_insee"):
        row.failures.append("code_insee_mismatch")
    base = result.get("id_parcelle")
    row.assertions += 1
    if not isinstance(base, str) or not base:
        row.failures.append("parcel_identity_missing")
        row.failures = _dedupe(row.failures)
        return row
    row.assertions += 1
    if base != expected_parcel:
        row.failures.append("parcel_identity_mismatch")
    datasets = result.get("datasets")
    row.assertions += 1
    if _cadastre_parcel(datasets) != base:
        row.failures.append("parcel_invariant_mismatch")
    dvf = _dvf_rows(datasets)
    row.assertions += 1
    if expected_dvf == "value":
        if not dvf:
            row.failures.append("dvf_state_mismatch")
    elif dvf:
        row.failures.append("dvf_state_mismatch")
    row.assertions += 1
    if any(mutation.get("id_parcelle") != base for mutation in dvf):
        row.failures.append("parcel_invariant_mismatch")
    row.failures = _dedupe(row.failures)
    return row


def evaluate_cadastre_equivalence(
    address_payload: Mapping[str, Any],
    cadastre_payload: Mapping[str, Any],
) -> tuple[int, list[str]]:
    """Assert that the cadastre-mode identity equals the address-mode one.

    Same canonical parcel, same ``code_insee`` and the same DVF mutation set:
    combined with the offline SQL-shape gates (``dvf_par_id_parcelle`` takes
    no point), this proves the cadastre mode feeds DVF by the canonical id
    and never depends on a centroid.
    """
    assertions = 0
    failures: list[str] = []
    addr = _first_result(address_payload)
    cad = _first_result(cadastre_payload)
    assertions += 1
    if addr is None or cad is None:
        return assertions, ["empty_response"]
    assertions += 1
    if _resolution_issue(cadastre_payload) != "exact":
        failures.append("outcome_mismatch")
    assertions += 1
    base = addr.get("id_parcelle")
    if not isinstance(base, str) or cad.get("id_parcelle") != base:
        failures.append("cadastre_equivalence_mismatch")
    assertions += 1
    if addr.get("code_insee") != cad.get("code_insee"):
        failures.append("cadastre_equivalence_mismatch")
    assertions += 1
    addr_mutations = {row.get("id_mutation") for row in _dvf_rows(addr.get("datasets"))}
    cad_mutations = {row.get("id_mutation") for row in _dvf_rows(cad.get("datasets"))}
    if addr_mutations != cad_mutations:
        failures.append("cadastre_equivalence_mismatch")
    return assertions, _dedupe(failures)


def evaluate_neighbour_exclusion(
    payload_text: str, neighbour_mutation_id: str | None
) -> tuple[bool, bool, list[str]]:
    """Check that an observed neighbouring mutation never enters the payload.

    Returns ``(observed, excluded, failures)``. A neighbour that was not
    observed by SQL is not a per-case failure (small communes may have no
    other recorded sale) -- the run-level gate
    :func:`evaluate_neighbour_coverage` still requires at least one
    observed-and-excluded neighbour overall.
    """
    if neighbour_mutation_id is None:
        return False, False, []
    leaked = neighbour_mutation_id in payload_text
    return True, not leaked, (["neighbour_mutation_leaked"] if leaked else [])


def evaluate_neighbour_coverage(case_rows: list[CaseRow]) -> list[str]:
    """Require at least one real neighbour observed AND excluded in the run."""
    if any(row.neighbour_observed and row.neighbour_excluded for row in case_rows):
        return []
    return ["neighbour_not_observed"]


def evaluate_probe(
    probe_id: str,
    departement: str,
    expected_issue: str,
    payload: Mapping[str, Any],
) -> ProbeRow:
    """Assert one private probe (ambiguous or typed-absence category).

    The probe id must be opaque, the departement inside the demo scope and
    the expected issue inside the closed probe set -- anything else fails
    closed before any value could reach a report.
    """
    if _PROBE_ID_RE.fullmatch(probe_id) is None:
        raise GoldenSecurityError("probe id is not opaque")
    if departement not in _REQUIRED_DEPARTEMENTS:
        raise GoldenSecurityError("probe departement is outside the demo scope")
    if expected_issue not in _PROBE_EXPECTED_ISSUES:
        raise GoldenSecurityError("probe expected issue is outside the closed set")
    issue = _resolution_issue(payload)
    observed = issue if issue in _ALLOWED_ISSUES else None
    row = ProbeRow(
        probe_id=probe_id,
        departement=departement,
        expected_issue=expected_issue,
        observed_issue=observed,
    )
    row.assertions += 1
    if observed is None:
        row.failures.append("outcome_missing")
        return row
    row.assertions += 1
    if observed != expected_issue:
        row.failures.append("outcome_mismatch")
    if expected_issue == "ambiguous":
        result = _first_result(payload) or {}
        row.assertions += 1
        if result.get("id_parcelle") is not None:
            row.failures.append("ambiguous_silent_selection")
        row.assertions += 1
        if len(_candidates(payload, result)) < 2:
            row.failures.append("ambiguous_without_candidates")
    else:
        row.assertions += 1
        if payload.get("donnees") != []:
            row.failures.append("absence_not_typed")
    row.failures = _dedupe(row.failures)
    return row


def _candidates(payload: Mapping[str, Any], result: Mapping[str, Any]) -> list[Any]:
    meta = payload.get("meta")
    resolution = meta.get("resolution") if isinstance(meta, Mapping) else None
    candidates = (
        resolution.get("parcelles_candidates")
        if isinstance(resolution, Mapping)
        else None
    )
    if not isinstance(candidates, list):
        candidates = result.get("parcelles_candidates")
    return candidates if isinstance(candidates, list) else []


def evaluate_coverage(
    case_rows: list[CaseRow], probe_rows: list[ProbeRow]
) -> list[str]:
    """Require the four categories AND both departements among passing rows."""
    ok_cases = [row for row in case_rows if row.ok]
    ok_probes = [probe for probe in probe_rows if probe.ok]
    categories = {row.coverage_key for row in ok_cases} | {
        probe.expected_issue
        for probe in ok_probes
        if probe.expected_issue in REQUIRED_COVERAGE
    }
    departements = {row.departement for row in ok_cases} | {
        probe.departement for probe in ok_probes
    }
    covered = categories >= REQUIRED_COVERAGE and departements >= _REQUIRED_DEPARTEMENTS
    return [] if covered else ["coverage_incomplete"]


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def _case_row_dict(row: CaseRow) -> dict[str, Any]:
    return {
        "case_id": row.case_id,
        "category": row.category,
        "departement": row.departement,
        "coverage_key": row.coverage_key,
        "assertions": row.assertions,
        "failures": list(row.failures),
        "neighbour_observed": row.neighbour_observed,
        "neighbour_excluded": row.neighbour_excluded,
        "ok": row.ok,
    }


def _probe_row_dict(row: ProbeRow) -> dict[str, Any]:
    return {
        "probe_id": row.probe_id,
        "departement": row.departement,
        "expected_issue": row.expected_issue,
        "observed_issue": row.observed_issue,
        "assertions": row.assertions,
        "failures": list(row.failures),
        "ok": row.ok,
    }


def build_report(
    context: Mapping[str, str],
    case_rows: list[CaseRow],
    probe_rows: list[ProbeRow],
    extra_failures: list[str],
) -> dict[str, Any]:
    """Assemble the single redacted report with its PASS/NO-GO verdict."""
    ok = (
        bool(case_rows)
        and all(row.ok for row in case_rows)
        and all(probe.ok for probe in probe_rows)
        and not extra_failures
    )
    return {
        "report": "exact-resolution-report",
        "phase": "45",
        "generated_at": context.get("generated_at"),
        "commit": context.get("commit"),
        "manifest_sha256": context.get("manifest_sha256"),
        "redacted": True,
        "cases": [_case_row_dict(row) for row in case_rows],
        "probes": [_probe_row_dict(row) for row in probe_rows],
        "failures": list(extra_failures),
        "verdict": "PASS" if ok else "NO-GO",
    }


def report_exit_code(report: Mapping[str, Any]) -> int:
    """Non-zero unless the verdict is an explicit PASS."""
    return 0 if report.get("verdict") == "PASS" else 1


# ---------------------------------------------------------------------------
# Live collection (runner-only; the local machine cannot reach the prod DB).
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _get(
    client: httpx.Client, base_url: str, path: str, params: dict[str, Any]
) -> httpx.Response:
    assert_get_only("GET")
    assert_allowed_host(base_url.rstrip("/") + path)
    response = client.get(path, params=params)
    if response.history:
        raise GoldenSecurityError("unexpected redirect during read-only GET")
    return response


def _neighbour_mutation(
    conn: psycopg.Connection[Any], entry: Mapping[str, Any], parcel_id: str
) -> str | None:
    assert_select_only(NEIGHBOUR_MUTATION_SQL, ALLOWED_SQL)
    row = conn.execute(
        NEIGHBOUR_MUTATION_SQL,
        (entry["departement"], entry["code_insee"], parcel_id),
    ).fetchone()
    if not row:
        return None
    value = row.get("id_mutation") if isinstance(row, dict) else row[0]
    return value if isinstance(value, str) else None


def _failed_case(entry: Mapping[str, Any], code: str) -> CaseRow:
    dvf_state = entry.get("invariants", {}).get("api", {}).get("dvf_state", "value")
    return CaseRow(
        case_id=str(entry.get("case_id", "")),
        category=str(entry.get("category", "")),
        departement=str(entry.get("departement", "")),
        coverage_key=(
            "exact_with_dvf" if dvf_state == "value" else "exact_without_dvf"
        ),
        assertions=1,
        failures=[code],
    )


def _collect_case(
    conn: psycopg.Connection[Any],
    client: httpx.Client,
    base_url: str,
    entry: Mapping[str, Any],
    private_cases: Mapping[str, Any],
) -> CaseRow:
    private = private_cases.get(entry["case_id"])
    query = private.get("query") if isinstance(private, Mapping) else None
    parcel = private.get("id_parcelle") if isinstance(private, Mapping) else None
    if not isinstance(query, str) or not query.strip() or not isinstance(parcel, str):
        return _failed_case(entry, "private_input_missing")
    try:
        address_resp = _get(
            client,
            base_url,
            "/v1/resolve",
            {"q": query, "type": "adresse", "datasets": "cadastre,dvf"},
        )
        if address_resp.status_code != 200:
            return _failed_case(entry, "http_error")
        address_payload = address_resp.json()
        row = evaluate_exact_case(entry, address_payload, parcel)
        time.sleep(7)  # public API rate limit (see verify_golden_cases)
        cadastre_resp = _get(
            client,
            base_url,
            "/v1/resolve",
            {"q": parcel, "datasets": "cadastre,dvf"},
        )
        row.assertions += 1
        if cadastre_resp.status_code != 200:
            row.failures.append("http_error")
            cadastre_text = ""
        else:
            assertions, failures = evaluate_cadastre_equivalence(
                address_payload, cadastre_resp.json()
            )
            row.assertions += assertions
            row.failures.extend(failures)
            cadastre_text = cadastre_resp.text
        neighbour = _neighbour_mutation(conn, entry, parcel)
        observed, excluded, failures = evaluate_neighbour_exclusion(
            address_resp.text + cadastre_text, neighbour
        )
        row.assertions += 1
        row.neighbour_observed = observed
        row.neighbour_excluded = excluded
        row.failures.extend(failures)
        row.failures = _dedupe(row.failures)
        return row
    except (GoldenSecurityError, httpx.HTTPError, psycopg.Error, ValueError):
        return _failed_case(entry, "live_observation_failed")


def _collect_probes(
    client: httpx.Client, base_url: str, private_cases: Mapping[str, Any]
) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    for probe_id in sorted(private_cases):
        if not probe_id.startswith("probe-"):
            continue
        probe = private_cases[probe_id]
        if not isinstance(probe, Mapping):
            raise GoldenSecurityError("probe entry must be an object")
        mode = probe.get("mode")
        query = probe.get("query")
        departement = probe.get("departement")
        expected = probe.get("expected_issue")
        if mode not in _PROBE_MODES or not isinstance(query, str) or not query.strip():
            raise GoldenSecurityError("probe entry is incomplete")
        params: dict[str, Any] = {"q": query, "datasets": "cadastre,dvf"}
        if mode in {"adresse", "gps"}:
            params["type"] = mode
        time.sleep(7)  # public API rate limit
        try:
            response = _get(client, base_url, "/v1/resolve", params)
            if response.status_code != 200:
                row = evaluate_probe(str(probe_id), str(departement), str(expected), {})
                row.failures = ["http_error"]
            else:
                row = evaluate_probe(
                    str(probe_id), str(departement), str(expected), response.json()
                )
        except httpx.HTTPError:
            row = evaluate_probe(str(probe_id), str(departement), str(expected), {})
            row.failures = ["live_observation_failed"]
        rows.append(row)
    return rows


def _write_report(output: Path, text: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def _blocked_report(output: Path, code: str) -> None:
    context = {
        "generated_at": _now(),
        "commit": _git_commit(),
        "manifest_sha256": "n/a",
    }
    report = build_report(context, [], [], [code])
    _write_report(output, render_report(report))


def _run_live(args: argparse.Namespace) -> int:
    output = Path(args.output)
    manifest_text = Path(args.manifest).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    reasons = validate_manifest(manifest, load_schema())
    violations = audit_artifact_text(manifest_text, "manifest") + audit_manifest_keys(
        manifest
    )
    if reasons or violations:
        _blocked_report(output, "manifest_audit_failed")
        print("exact-resolution: BLOCKED by manifest audit")
        return 1
    private_cases = _read_private_input(args.private_input, args.private_input_env)
    dsn = _required_env(args.dsn_env)
    base_url = _required_env(args.api_base_url_env)
    assert_allowed_host(base_url.rstrip("/") + "/v1/resolve")
    api_key = _required_env(args.api_key_env)
    conn = _open_read_only_connection(dsn)
    try:
        with httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key},
            timeout=30,
            follow_redirects=False,
        ) as client:
            case_rows: list[CaseRow] = []
            address_entries = [
                entry for entry in manifest["cases"] if entry.get("type") == "address"
            ]
            for index, entry in enumerate(address_entries):
                if index:
                    time.sleep(7)  # public API rate limit
                case_rows.append(
                    _collect_case(conn, client, base_url, entry, private_cases)
                )
            probe_rows = _collect_probes(client, base_url, private_cases)
    finally:
        conn.rollback()
        conn.close()
    extra_failures = evaluate_coverage(case_rows, probe_rows)
    extra_failures += evaluate_neighbour_coverage(case_rows)
    context = {
        "generated_at": _now(),
        "commit": _git_commit(),
        "manifest_sha256": manifest_sha256(manifest_text),
    }
    report = build_report(context, case_rows, probe_rows, extra_failures)
    _write_report(output, render_report(report))
    print(
        f"exact-resolution: {report['verdict']} "
        f"({len(case_rows)} cases, {len(probe_rows)} probes)"
    )
    return report_exit_code(report)


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
            "live verification requires --read-only and one private input source"
        )
    try:
        return _run_live(args)
    except (
        GoldenSecurityError,
        OSError,
        ValueError,
        KeyError,
        httpx.HTTPError,
        psycopg.Error,
    ):
        # Writing the blocked report must never crash the guard itself.
        with contextlib.suppress(GoldenSecurityError, OSError):
            _blocked_report(Path(args.output), "live_replay_failed")
        print("exact-resolution: BLOCKED")
        return 1


if __name__ == "__main__":
    sys.exit(main())

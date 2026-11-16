"""Sentinel + fail-closed tests for the Phase 45 exact-resolution verifier.

Hermetic tests (no DB, no network) proving the harness contract of Plan 45-05:

* the single JSON report NEVER contains a private input, street address,
  precise coordinate, query URL, credential or non-allowlisted residential
  identifier (parcel id), even when a (simulated) payload leaks one;
* only exact read-only SELECT statements pass the SQL guard, and the runner
  source exposes no mutation nor admin surface;
* per-case evaluation asserts outcome, code_insee, the cross-layer parcel
  invariant, address/cadastre equivalence and neighbour-mutation exclusion;
* a missing invariant, missing private input or incomplete 44/85 category
  coverage yields NO-GO and a non-zero exit -- never a silent skip.

Failure messages reference synthetic case ids (CASE-45-*) only -- never a
real address (D-05).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.verify_exact_resolution import (
    ALLOWED_SQL,
    NEIGHBOUR_MUTATION_SQL,
    NO_GO_CODES,
    REQUIRED_COVERAGE,
    CaseRow,
    ProbeRow,
    audit_report_text,
    build_report,
    evaluate_cadastre_equivalence,
    evaluate_coverage,
    evaluate_exact_case,
    evaluate_neighbour_coverage,
    evaluate_neighbour_exclusion,
    evaluate_probe,
    render_report,
    report_exit_code,
    safe_scalar,
)
from scripts.verify_golden_cases import GoldenSecurityError, assert_select_only

# --- Sentinels that must NEVER survive into a rendered report ---------------
SENTINEL_ADDRESS = "12 rue de la Sentinelle"
SENTINEL_COORD = "46.123456, -1.654321"
SENTINEL_URL = "https://api.datacommune.fr/v1/resolve?q=12+rue+de+la+sentinelle"
SENTINEL_KEY = "dc_live_SENTINEL0123456789abcdef"
SENTINEL_DSN = "postgresql://user:sup3rsecret@192.0.2.5:5432/prod"
# A residential identifier (parcel id) is NOT allowlisted in the report.
SENTINEL_PARCEL = "44001000ZS0042"

_NEIGHBOUR_MUTATION = "CASE-45-MUT-NEIGHBOUR"
_OWN_MUTATION = "CASE-45-MUT-OWN"


def _entry(
    *,
    case_id: str = "golden-address-001",
    departement: str = "44",
    code_insee: str = "44001",
    category: str = "complete",
    dvf_state: str = "value",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "type": "address",
        "departement": departement,
        "code_insee": code_insee,
        "category": category,
        "invariants": {"api": {"dvf_state": dvf_state, "cadastre_state": "value"}},
    }


def _payload(
    *,
    issue: str | None = "exact",
    code_insee: str = "44001",
    base: str | None = SENTINEL_PARCEL,
    cadastre: str | None = SENTINEL_PARCEL,
    dvf_rows: list[dict[str, Any]] | None = None,
    candidates: list[str] | None = None,
    empty: bool = False,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if issue is not None:
        meta["resolution"] = {"issue": issue, "parcelles_candidates": candidates}
    if empty:
        return {"donnees": [], "meta": meta}
    if dvf_rows is None:
        dvf_rows = [{"id_mutation": _OWN_MUTATION, "id_parcelle": base}]
    result = {
        "code_insee": code_insee,
        "id_parcelle": base,
        "resolution": "ambiguous" if issue == "ambiguous" else "exact",
        "parcelles_candidates": candidates,
        "datasets": {
            "cadastre": {"id_parcelle": cadastre},
            "dvf": dvf_rows,
        },
    }
    return {"donnees": [result], "meta": meta}


def _ok_case_row() -> CaseRow:
    return evaluate_exact_case(_entry(), _payload(), SENTINEL_PARCEL)


def _context() -> dict[str, str]:
    return {
        "generated_at": "2026-07-21T19:00:00+00:00",
        "commit": "abcdef12",
        "manifest_sha256": "a" * 64,
    }


# ---------------------------------------------------------------------------
# Redaction / anti-leak sentinels
# ---------------------------------------------------------------------------


def test_report_redacts_every_sentinel() -> None:
    """CASE-45-LEAK-01: sentinel values smuggled under safe keys are erased."""
    report = build_report(_context(), [_ok_case_row()], [], [])
    report["cases"][0]["leak_a"] = SENTINEL_ADDRESS
    report["cases"][0]["leak_b"] = SENTINEL_COORD
    report["cases"][0]["leak_c"] = SENTINEL_URL
    report["cases"][0]["leak_d"] = SENTINEL_KEY
    report["cases"][0]["leak_e"] = SENTINEL_DSN
    report["cases"][0]["leak_f"] = SENTINEL_PARCEL
    text = render_report(report)
    for sentinel in (
        SENTINEL_ADDRESS,
        SENTINEL_COORD,
        SENTINEL_URL,
        SENTINEL_KEY,
        SENTINEL_DSN,
        SENTINEL_PARCEL,
    ):
        assert sentinel not in text, "CASE-45-LEAK-01"
    assert "sup3rsecret" not in text, "CASE-45-LEAK-01 password fragment"
    assert "[REDACTED]" in text, "CASE-45-LEAK-01 redaction marker"


def test_report_refuses_unsafe_keys() -> None:
    """CASE-45-LEAK-02: a key could smuggle data -- non-allowlisted keys fail."""
    report = build_report(_context(), [_ok_case_row()], [], [])
    report[SENTINEL_ADDRESS] = True  # type: ignore[index]
    with pytest.raises(GoldenSecurityError):
        render_report(report)


def test_report_audit_flags_query_urls_and_coordinates() -> None:
    """CASE-45-LEAK-03: the self-audit catches URLs, q= params and coords."""
    assert audit_report_text(f"payload {SENTINEL_URL}") != []
    assert audit_report_text("GET /v1/resolve?q=secret") != []
    assert audit_report_text(f"point: {SENTINEL_COORD}") != []
    assert audit_report_text('{"case_id": "golden-address-001", "ok": true}') == []


def test_report_keeps_only_opaque_fields() -> None:
    """CASE-45-LEAK-04: a nominal report carries ids, categories, UTC, counts
    -- and no parcel identifier, even though evaluation saw one."""
    row = _ok_case_row()
    assert row.ok, row.failures
    text = render_report(build_report(_context(), [row], [], []))
    assert "golden-address-001" in text
    assert "complete" in text
    assert "2026-07-21T19:00:00+00:00" in text
    assert '"assertions"' in text
    assert SENTINEL_PARCEL not in text, "CASE-45-LEAK-04 residential identifier"
    assert "[REDACTED]" not in text, "CASE-45-LEAK-04 nominal report is clean"


def test_safe_scalar_vocabulary() -> None:
    """CASE-45-LEAK-05: whitelist redaction -- closed vocabulary only."""
    for token in (
        "golden-address-001",
        "probe-85-boundary",
        "exact",
        "out_of_coverage",
        "complete",
        "PASS",
        "NO-GO",
        "44001",
        "2026-07-21T19:00:00+00:00",
        "parcel_invariant_mismatch",
    ):
        assert safe_scalar(token) == token, token
    assert safe_scalar(200) == 200
    assert safe_scalar(True) is True
    for leak in (
        SENTINEL_PARCEL,
        SENTINEL_ADDRESS,
        SENTINEL_URL,
        "46.123456",
        "1 impasse des Tests 44001",
    ):
        assert safe_scalar(leak) == "[REDACTED]", leak
    # Floats are never emitted: they could be coordinates.
    assert safe_scalar(46.123456) == "[REDACTED]"


# ---------------------------------------------------------------------------
# GET/SELECT-only surface
# ---------------------------------------------------------------------------


def test_sql_guard_allows_neighbour_select_and_refuses_the_rest() -> None:
    assert_select_only(NEIGHBOUR_MUTATION_SQL, ALLOWED_SQL)
    for forbidden in (
        "UPDATE mutations SET valeur_fonciere = 0",
        "DELETE FROM mutations",
        "INSERT INTO mutations VALUES (1)",
        "SELECT * FROM mutations",  # not the exact allowlisted text
        "REFRESH MATERIALIZED VIEW v_commune_synthese",
    ):
        with pytest.raises(GoldenSecurityError):
            assert_select_only(forbidden, ALLOWED_SQL)


def test_runner_source_has_no_mutation_nor_admin_surface() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "verify_exact_resolution.py"
    ).read_text(encoding="utf-8")
    for token in (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "ALTER ",
        "TRUNCATE",
        ".post(",
        ".put(",
        ".patch(",
        ".delete(",
        "/admin",
        "requests.",
    ):
        assert token not in source, f"forbidden surface: {token!r}"
    assert "follow_redirects=False" in source, "redirects must be refused"


# ---------------------------------------------------------------------------
# Per-case invariants (fail-closed, never skip)
# ---------------------------------------------------------------------------


def test_evaluate_exact_case_passes_on_coherent_payload() -> None:
    row = _ok_case_row()
    assert row.ok, row.failures
    assert row.failures == []
    assert row.assertions >= 5, "CASE-45-EVAL-01 assertion count"
    assert row.coverage_key == "exact_with_dvf"
    assert row.case_id == "golden-address-001"
    assert row.departement == "44"


def test_evaluate_exact_case_fails_closed_never_skips() -> None:
    entry = _entry()
    # Missing machine outcome -> NO-GO code, not a skip.
    row = evaluate_exact_case(entry, _payload(issue=None), SENTINEL_PARCEL)
    assert "outcome_missing" in row.failures
    # Empty response -> NO-GO code.
    row = evaluate_exact_case(entry, _payload(empty=True), SENTINEL_PARCEL)
    assert "empty_response" in row.failures
    # Wrong INSEE -> NO-GO code.
    row = evaluate_exact_case(entry, _payload(code_insee="85047"), SENTINEL_PARCEL)
    assert "code_insee_mismatch" in row.failures
    # Missing canonical parcel -> NO-GO code.
    row = evaluate_exact_case(entry, _payload(base=None), SENTINEL_PARCEL)
    assert "parcel_identity_missing" in row.failures
    # Wrong parcel vs the private expected id -> NO-GO code.
    row = evaluate_exact_case(entry, _payload(), "44001000ZS9999")
    assert "parcel_identity_mismatch" in row.failures
    # Every code is part of the closed normalized set.
    assert set(row.failures) <= NO_GO_CODES


def test_evaluate_exact_case_enforces_parcel_invariant() -> None:
    """CASE-45-EVAL-02: base == cadastre == dvf[*] or NO-GO."""
    entry = _entry()
    # Cadastre block diverges from the base identity.
    row = evaluate_exact_case(
        entry, _payload(cadastre="44001000ZS9999"), SENTINEL_PARCEL
    )
    assert "parcel_invariant_mismatch" in row.failures
    # A neighbour's mutation slipped into the DVF rows.
    leaky_rows = [
        {"id_mutation": _OWN_MUTATION, "id_parcelle": SENTINEL_PARCEL},
        {"id_mutation": _NEIGHBOUR_MUTATION, "id_parcelle": "44001000ZS9999"},
    ]
    row = evaluate_exact_case(entry, _payload(dvf_rows=leaky_rows), SENTINEL_PARCEL)
    assert "parcel_invariant_mismatch" in row.failures


def test_evaluate_exact_case_enforces_dvf_state() -> None:
    """CASE-45-EVAL-03: exact-with-DVF vs exact-without-DVF are both proven."""
    # Expected absent but rows present -> mismatch.
    row = evaluate_exact_case(
        _entry(dvf_state="not_reported", category="no_dvf"),
        _payload(),
        SENTINEL_PARCEL,
    )
    assert "dvf_state_mismatch" in row.failures
    assert row.coverage_key == "exact_without_dvf"
    # Expected present but rows empty -> mismatch.
    row = evaluate_exact_case(_entry(), _payload(dvf_rows=[]), SENTINEL_PARCEL)
    assert "dvf_state_mismatch" in row.failures
    # Typed absence expected and observed -> ok.
    row = evaluate_exact_case(
        _entry(dvf_state="not_reported", category="no_dvf"),
        _payload(dvf_rows=[]),
        SENTINEL_PARCEL,
    )
    assert row.ok, row.failures


def test_neighbour_exclusion_is_observed_and_enforced() -> None:
    clean_text = '{"donnees": [{"id_mutation": "CASE-45-MUT-OWN"}]}'
    observed, excluded, failures = evaluate_neighbour_exclusion(
        clean_text, _NEIGHBOUR_MUTATION
    )
    assert (observed, excluded, failures) == (True, True, [])
    leaky_text = clean_text + _NEIGHBOUR_MUTATION
    observed, excluded, failures = evaluate_neighbour_exclusion(
        leaky_text, _NEIGHBOUR_MUTATION
    )
    assert observed is True
    assert excluded is False
    assert failures == ["neighbour_mutation_leaked"]
    # No neighbour observed in SQL -> not a per-case failure...
    observed, excluded, failures = evaluate_neighbour_exclusion(clean_text, None)
    assert (observed, excluded, failures) == (False, False, [])
    # ...but zero observed-and-excluded neighbours overall is a NO-GO.
    row = _ok_case_row()
    assert evaluate_neighbour_coverage([row]) == ["neighbour_not_observed"]
    row.neighbour_observed = True
    row.neighbour_excluded = True
    assert evaluate_neighbour_coverage([row]) == []


def test_cadastre_equivalence_matches_address_identity() -> None:
    """CASE-45-EVAL-04: cadastre mode = same identity and same DVF set (the
    canonical id feeds DVF directly -- never a centroid re-resolution)."""
    address = _payload()
    cadastre = _payload()
    assertions, failures = evaluate_cadastre_equivalence(address, cadastre)
    assert failures == []
    assert assertions >= 4
    # Diverging DVF sets -> mismatch.
    other = _payload(
        dvf_rows=[{"id_mutation": "CASE-45-MUT-OTHER", "id_parcelle": SENTINEL_PARCEL}]
    )
    _, failures = evaluate_cadastre_equivalence(address, other)
    assert "cadastre_equivalence_mismatch" in failures
    # Diverging base identity -> mismatch.
    other = _payload(base="44001000ZS9999", cadastre="44001000ZS9999")
    _, failures = evaluate_cadastre_equivalence(address, other)
    assert "cadastre_equivalence_mismatch" in failures


# ---------------------------------------------------------------------------
# Probes (ambiguous / out-of-coverage categories)
# ---------------------------------------------------------------------------


def test_probe_ambiguous_requires_candidates_without_silent_pick() -> None:
    payload = _payload(
        issue="ambiguous",
        base=None,
        candidates=["44001000ZS0042", "44001000ZS0043"],
    )
    row = evaluate_probe("probe-44-boundary", "44", "ambiguous", payload)
    assert row.ok, row.failures
    assert row.observed_issue == "ambiguous"
    # A silently selected parcel under an ambiguous outcome is a NO-GO.
    silent = _payload(
        issue="ambiguous",
        candidates=["44001000ZS0042", "44001000ZS0043"],
    )
    row = evaluate_probe("probe-44-boundary", "44", "ambiguous", silent)
    assert "ambiguous_silent_selection" in row.failures
    # Fewer than two candidates cannot justify an ambiguity.
    single = _payload(issue="ambiguous", base=None, candidates=["44001000ZS0042"])
    row = evaluate_probe("probe-44-boundary", "44", "ambiguous", single)
    assert "ambiguous_without_candidates" in row.failures
    # Outcome mismatch is a NO-GO, never a skip.
    row = evaluate_probe("probe-44-boundary", "44", "ambiguous", _payload())
    assert "outcome_mismatch" in row.failures


def test_probe_out_of_coverage_requires_typed_absence() -> None:
    payload = _payload(issue="out_of_coverage", empty=True)
    row = evaluate_probe("probe-85-hors-perimetre", "85", "out_of_coverage", payload)
    assert row.ok, row.failures
    # A fabricated best-effort result instead of a typed absence -> NO-GO.
    fabricated = _payload(issue="out_of_coverage")
    row = evaluate_probe("probe-85-hors-perimetre", "85", "out_of_coverage", fabricated)
    assert "absence_not_typed" in row.failures


def test_probe_identity_must_be_opaque_and_scoped() -> None:
    payload = _payload(issue="ambiguous", base=None, candidates=["a", "b"])
    with pytest.raises(GoldenSecurityError):
        evaluate_probe(SENTINEL_ADDRESS, "44", "ambiguous", payload)
    with pytest.raises(GoldenSecurityError):
        evaluate_probe("probe-44-boundary", "75", "ambiguous", payload)
    with pytest.raises(GoldenSecurityError):
        evaluate_probe("probe-44-boundary", "44", "exact", payload)


# ---------------------------------------------------------------------------
# Coverage + verdict (NO-GO, never skip)
# ---------------------------------------------------------------------------


def _probe_row(probe_id: str, departement: str, expected: str) -> ProbeRow:
    return ProbeRow(
        probe_id=probe_id,
        departement=departement,
        expected_issue=expected,
        observed_issue=expected,
        assertions=2,
        failures=[],
    )


def test_coverage_requires_four_categories_across_44_and_85() -> None:
    cases = [
        evaluate_exact_case(_entry(), _payload(), SENTINEL_PARCEL),
        evaluate_exact_case(
            _entry(
                case_id="golden-address-003",
                dvf_state="not_reported",
                category="no_dvf",
            ),
            _payload(dvf_rows=[]),
            SENTINEL_PARCEL,
        ),
        evaluate_exact_case(
            _entry(case_id="golden-address-009", departement="85", code_insee="85001"),
            _payload(code_insee="85001"),
            SENTINEL_PARCEL,
        ),
    ]
    probes = [
        _probe_row("probe-44-boundary", "44", "ambiguous"),
        _probe_row("probe-85-hors-perimetre", "85", "out_of_coverage"),
    ]
    assert evaluate_coverage(cases, probes) == []
    # Missing ambiguous probe -> incomplete coverage.
    assert evaluate_coverage(cases, probes[1:]) == ["coverage_incomplete"]
    # Missing departement 85 -> incomplete coverage.
    only_44 = [cases[0], cases[1]]
    probes_44 = [_probe_row("probe-44-boundary", "44", "ambiguous")]
    assert evaluate_coverage(only_44, probes_44) == ["coverage_incomplete"]
    # A failing case never counts toward coverage: `broken` is the only
    # exact_with_dvf row here, so the category must be reported missing.
    broken = evaluate_exact_case(_entry(), _payload(empty=True), SENTINEL_PARCEL)
    assert evaluate_coverage([broken, cases[1]], probes) == ["coverage_incomplete"]
    assert {
        "exact_with_dvf",
        "exact_without_dvf",
        "ambiguous",
        "out_of_coverage",
    } == REQUIRED_COVERAGE


def test_build_report_verdict_and_exit_code() -> None:
    ok_row = _ok_case_row()
    report = build_report(_context(), [ok_row], [], [])
    assert report["verdict"] == "PASS"
    assert report["redacted"] is True
    assert report_exit_code(report) == 0
    # Any failing row -> NO-GO + non-zero exit.
    broken = evaluate_exact_case(_entry(), _payload(empty=True), SENTINEL_PARCEL)
    report = build_report(_context(), [ok_row, broken], [], [])
    assert report["verdict"] == "NO-GO"
    assert report_exit_code(report) == 1
    # Extra failures (coverage, neighbour) -> NO-GO.
    report = build_report(_context(), [ok_row], [], ["coverage_incomplete"])
    assert report["verdict"] == "NO-GO"
    assert report_exit_code(report) == 1
    # No case at all is never a PASS.
    report = build_report(_context(), [], [], [])
    assert report["verdict"] == "NO-GO"
    assert report_exit_code(report) == 1

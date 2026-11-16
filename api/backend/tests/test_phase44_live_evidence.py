"""Sentinel + fail-closed tests for the Phase 44 live evidence collector.

These tests are hermetic: they never open a live DB connection or perform a
real HTTP request. They prove the collector's security posture:

* the rendered Markdown report NEVER contains an API key, DSN, street address
  or precise coordinate, even when a (simulated) payload leaks one;
* only exact read-only SELECT statements pass the SQL guard;
* only HTTP GET against the DataCommune allowlist is permitted (the former
  national BAN host is refused);
* a missing secret, missing private input or unproven invariant fails closed
  with a non-sensitive message and a non-zero result -- never a silent PASS.
"""

from __future__ import annotations

import pytest

from scripts.collect_phase44_live_evidence import (
    ALLOWED_SQL,
    LAST_SUCCESS_SQL,
    RequirementRow,
    assert_allowed_host,
    assert_get_only,
    evaluate_geocode,
    evaluate_health,
    evaluate_provenance_freshness,
    evaluate_resolve_absence,
    manifest_sha256,
    render_report,
)
from scripts.verify_golden_cases import GoldenSecurityError, assert_select_only

# Sentinels that must NEVER survive into a rendered report.
SENTINEL_KEY = "dc_live_SENTINEL0123456789abcdef"
SENTINEL_ADDRESS = "12 rue de la Paix"
SENTINEL_COORD = "47.218371, -1.553621"
SENTINEL_DSN = "postgresql://user:sup3rsecret@192.0.2.5:5432/prod"


def _context() -> dict[str, str]:
    return {
        "commit": "efa6a337",
        "generated_at": "2026-07-21T09:00:00Z",
        "manifest_sha256": "a" * 64,
    }


def _leaky_rows() -> list[RequirementRow]:
    """Rows whose evidence deliberately smuggles every sentinel."""
    return [
        RequirementRow(
            requirement="PROV-01",
            observable="datasets vs last success",
            source="GET+SELECT",
            http_code=200,
            passed=True,
            evidence={
                "leaked_key": SENTINEL_KEY,
                "leaked_address": SENTINEL_ADDRESS,
                "leaked_coord": SENTINEL_COORD,
                "leaked_dsn": SENTINEL_DSN,
                "safe_bool": True,
            },
        )
    ]


def test_render_report_redacts_every_sentinel() -> None:
    report = render_report(_context(), _leaky_rows())
    for sentinel in (SENTINEL_KEY, SENTINEL_ADDRESS, SENTINEL_COORD, SENTINEL_DSN):
        assert sentinel not in report
    # "sup3rsecret" password fragment must not survive either.
    assert "sup3rsecret" not in report


def test_render_report_is_self_auditing() -> None:
    """The renderer refuses to emit output that still trips the leak scanner."""
    from scripts.collect_phase44_live_evidence import _self_audit

    report = render_report(_context(), _leaky_rows())
    # A clean report passes its own audit (no violations).
    assert _self_audit(report) == []


def test_render_report_keeps_opaque_and_boolean_evidence() -> None:
    rows = [
        RequirementRow(
            requirement="HEALTH-01",
            observable="editorial degradation stays 200",
            source="GET",
            http_code=200,
            passed=True,
            evidence={"technical_status": "available", "editorial_status": "degraded"},
        )
    ]
    report = render_report(_context(), rows)
    assert "HEALTH-01" in report
    assert "available" in report
    assert "degraded" in report
    assert "efa6a337" in report


def test_sql_guard_allows_last_success_and_refuses_mutation() -> None:
    # Exact allowlisted SELECT passes.
    assert_select_only(LAST_SUCCESS_SQL, ALLOWED_SQL)
    for forbidden in (
        "UPDATE etl_runs SET status = 'success'",
        "DELETE FROM etl_runs",
        "SELECT * FROM etl_runs",  # not the exact allowlisted text
        "REFRESH MATERIALIZED VIEW v_commune_synthese",
    ):
        with pytest.raises(GoldenSecurityError):
            assert_select_only(forbidden, ALLOWED_SQL)


def test_http_guard_refuses_non_get() -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        with pytest.raises(GoldenSecurityError):
            assert_get_only(method)
    assert_get_only("GET")  # does not raise


def test_host_guard_refuses_former_national_ban_and_off_allowlist() -> None:
    assert_allowed_host("https://api.datacommune.fr/v1/geocode")
    for bad in (
        "https://api-adresse.data.gouv.fr/search",
        "http://api.datacommune.fr/v1/geocode",  # non-TLS
        "https://evil.example.com/v1/geocode",
        "https://api.datacommune.fr.evil.com/v1/geocode",
    ):
        with pytest.raises(GoldenSecurityError):
            assert_allowed_host(bad)


def test_manifest_sha256_is_stable_hex() -> None:
    digest = manifest_sha256('{"schema_version":"1"}')
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_evaluate_provenance_freshness_fails_without_proof() -> None:
    # No datasets payload -> unproven -> FAIL.
    passed, evidence = evaluate_provenance_freshness([], {})
    assert passed is False
    # Matching last success -> PASS, evidence stays boolean/opaque.
    datasets = [
        {
            "nom": "ban",
            "provenance": {"derniere_importation_reussie": "2026-07-01T00:00:00Z"},
        }
    ]
    passed, evidence = evaluate_provenance_freshness(
        datasets, {"ban": "2026-07-01T00:00:00Z"}
    )
    assert passed is True
    assert evidence["ban"] is True


def test_evaluate_resolve_absence_requires_typed_states() -> None:
    passed, _ = evaluate_resolve_absence({"donnees": []})
    assert passed is False
    # Real contract: dataset_states is nested under donnees[0] as a dict keyed
    # by dataset name (resolve/router.py + resolve/schemas.py).
    payload = {
        "donnees": [
            {
                "code_commune": "44109",
                "dataset_states": {
                    "dpe": {"dataset": "dpe", "presence": "value"},
                    "dvf": {
                        "dataset": "dvf",
                        "presence": "absent",
                        "absence_reason": "not_reported",
                    },
                },
            }
        ],
    }
    passed, evidence = evaluate_resolve_absence(payload)
    assert passed is True
    assert evidence["states_count"] == 2
    # The former wrong shape (top-level list) must now FAIL.
    passed, _ = evaluate_resolve_absence(
        {"dataset_states": [{"dataset": "dpe", "presence": "value"}]}
    )
    assert passed is False


def test_evaluate_geocode_enforces_department_scope() -> None:
    # Empty results -> FAIL.
    passed, _ = evaluate_geocode({"donnees": []}, "44")
    assert passed is False
    # A row outside the department -> FAIL (coords never copied into evidence).
    # Fixtures mirror the real /v1/geocode contract: INSEE field is
    # `code_commune` (app/ban/schemas.py), not `code_insee`.
    ok_payload = {
        "donnees": [{"code_commune": "44109", "latitude": 47.2, "longitude": -1.5}]
    }
    passed, evidence = evaluate_geocode(ok_payload, "44")
    assert passed is True
    assert "latitude" not in evidence
    assert "longitude" not in evidence
    bad_payload = {"donnees": [{"code_commune": "75056"}]}
    passed, _ = evaluate_geocode(bad_payload, "44")
    assert passed is False


def test_evaluate_health_requires_200_and_split_status() -> None:
    passed, evidence = evaluate_health(
        {
            "statut": "degradee",
            "technical_status": "available",
            "editorial_status": "degraded",
        },
        200,
    )
    assert passed is True
    assert evidence["technical_status"] == "available"
    # 503 technical failure is NOT a HEALTH-01 pass for the editorial-degradation case.
    passed, _ = evaluate_health(
        {"statut": "deconnectee", "technical_status": "unavailable"}, 503
    )
    assert passed is False

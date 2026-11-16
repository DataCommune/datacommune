"""Contract tests for the fail-closed dependency-audit validator (SEC-01, D-08/D-09).

Covers: an untriaged high runtime finding turns the verdict red, an unknown
finding without any exception is refused, an incomplete exception is refused,
an exception whose horizon exceeds 30 days is refused, an expired exception is
refused, a fully-triaged inventory passes, an empty inventory passes, the
pip-audit/npm audit JSON parsers, and the fail-closed behaviour when a required
audit JSON is missing or malformed.
"""

from datetime import date

import pytest

from scripts.validate_dependency_audit import (
    MAX_EXCEPTION_DAYS,
    REQUIRED_EXCEPTION_FIELDS,
    AuditInputError,
    Finding,
    evaluate,
    load_json,
    main,
    parse_npm_audit,
    parse_pip_audit,
    validate_exception,
)

pytestmark = pytest.mark.unit

NOW = date(2026, 7, 22)


def _exception(**overrides: object) -> dict:
    base = {
        "advisory": "GHSA-xxxx-yyyy-zzzz",
        "package": "sharp",
        "ecosystem": "npm",
        "scope": "dev",
        "reachability": "not-reachable in shipped runtime bundle",
        "severity": "high",
        "owner": "Ianis",
        "justification": "build-only tooling; upgrade requires breaking wrangler bump",
        "opened_at": "2026-07-22",
        "expires_at": "2026-08-21",
        "reviewed_by_human": True,
    }
    base.update(overrides)
    return base


def _finding(**overrides: object) -> Finding:
    defaults = dict(
        ecosystem="npm",
        package="sharp",
        version="0.34.0",
        advisory="GHSA-xxxx-yyyy-zzzz",
        severity="high",
        # dev-scoped by default so it matches the default dev-scoped exception;
        # tests needing a runtime finding pass scope="runtime" explicitly.
        scope="dev",
    )
    defaults.update(overrides)
    return Finding(**defaults)


# --- verdict logic -----------------------------------------------------------


def test_high_runtime_finding_untriaged_fails() -> None:
    verdict = evaluate([_finding(scope="runtime", severity="high")], [], NOW)
    assert verdict.ok is False
    assert verdict.problems


def test_unknown_finding_without_exception_fails() -> None:
    # An advisory that no exception references at all must block (fail-closed).
    verdict = evaluate([_finding(advisory="GHSA-unknown-0000-0000")], [], NOW)
    assert verdict.ok is False


def test_incomplete_exception_fails() -> None:
    incomplete = _exception()
    del incomplete["owner"]
    verdict = evaluate([_finding()], [incomplete], NOW)
    assert verdict.ok is False


def test_exception_over_30_days_fails() -> None:
    over = _exception(opened_at="2026-07-22", expires_at="2026-09-22")
    errors = validate_exception(over, NOW)
    assert errors
    verdict = evaluate([_finding()], [over], NOW)
    assert verdict.ok is False


def test_exception_exactly_30_days_passes() -> None:
    # 30-day horizon is the inclusive boundary: (expires - opened).days == 30
    # must NOT trip the "exceeds 30 days" guard.
    boundary = _exception(opened_at="2026-07-01", expires_at="2026-07-31")
    assert validate_exception(boundary, NOW) == []
    verdict = evaluate([_finding()], [boundary], NOW)
    assert verdict.ok is True
    assert not verdict.problems


def test_exception_31_days_fails() -> None:
    # One day past the boundary: (expires - opened).days == 31 must block.
    over = _exception(opened_at="2026-07-01", expires_at="2026-08-01")
    errors = validate_exception(over, NOW)
    assert any("horizon exceeds" in problem for problem in errors)
    verdict = evaluate([_finding()], [over], NOW)
    assert verdict.ok is False


def test_expired_exception_fails() -> None:
    expired = _exception(opened_at="2026-06-01", expires_at="2026-06-30")
    errors = validate_exception(expired, NOW)
    assert errors
    verdict = evaluate([_finding()], [expired], NOW)
    assert verdict.ok is False


def test_valid_exception_passes() -> None:
    verdict = evaluate([_finding()], [_exception()], NOW)
    assert verdict.ok is True
    assert not verdict.problems


def test_empty_inventory_passes() -> None:
    verdict = evaluate([], [], NOW)
    assert verdict.ok is True


def test_valid_exception_within_30_days_has_no_errors() -> None:
    assert validate_exception(_exception(), NOW) == []
    assert MAX_EXCEPTION_DAYS == 30
    assert "owner" in REQUIRED_EXCEPTION_FIELDS
    assert "reviewed_by_human" in REQUIRED_EXCEPTION_FIELDS
    assert "ecosystem" in REQUIRED_EXCEPTION_FIELDS


def test_dev_exception_does_not_cover_runtime_finding() -> None:
    # A dev-scoped exception must NOT silence the same advisory at runtime.
    verdict = evaluate([_finding(scope="runtime")], [_exception(scope="dev")], NOW)
    assert verdict.ok is False
    assert verdict.problems


def test_unreviewed_exception_fails() -> None:
    # reviewed_by_human=false = human triage gate not signed → exception invalid.
    unreviewed = _exception(reviewed_by_human=False)
    assert validate_exception(unreviewed, NOW)
    verdict = evaluate([_finding()], [unreviewed], NOW)
    assert verdict.ok is False


def test_exception_missing_ecosystem_fails() -> None:
    incomplete = _exception()
    del incomplete["ecosystem"]
    assert validate_exception(incomplete, NOW)
    verdict = evaluate([_finding()], [incomplete], NOW)
    assert verdict.ok is False


# --- parsers -----------------------------------------------------------------


def test_parse_npm_audit_extracts_advisories() -> None:
    data = {
        "vulnerabilities": {
            "sharp": {
                "name": "sharp",
                "severity": "high",
                "via": [
                    {
                        "name": "sharp",
                        "severity": "high",
                        "url": "https://github.com/advisories/GHSA-f88m-g3jw-g9cj",
                        "source": 1124066,
                        "range": "<0.35.0",
                    }
                ],
            },
            "wrangler": {"name": "wrangler", "severity": "high", "via": ["miniflare"]},
        }
    }
    findings = parse_npm_audit(data, scope="dev")
    advisories = {f.advisory for f in findings}
    assert "GHSA-f88m-g3jw-g9cj" in advisories
    # A propagation-only node (via = list of strings) must not become a finding.
    assert all(f.package == "sharp" for f in findings)
    assert all(f.scope == "dev" for f in findings)


def test_parse_pip_audit_extracts_advisories() -> None:
    data = {
        "dependencies": [
            {"name": "alembic", "version": "1.18.3", "vulns": []},
            {
                "name": "somepkg",
                "version": "1.0.0",
                "vulns": [{"id": "PYSEC-2026-1", "fix_versions": ["1.0.1"]}],
            },
        ]
    }
    findings = parse_pip_audit(data, scope="runtime")
    assert len(findings) == 1
    assert findings[0].advisory == "PYSEC-2026-1"
    assert findings[0].package == "somepkg"
    assert findings[0].scope == "runtime"


def test_parse_pip_audit_empty_object_fails() -> None:
    # An empty {} lacks the 'dependencies' key → malformed, not "zero findings".
    with pytest.raises(AuditInputError):
        parse_pip_audit({}, scope="runtime")


def test_parse_npm_audit_empty_object_fails() -> None:
    # An empty {} lacks the 'vulnerabilities' key → malformed, not "zero findings".
    with pytest.raises(AuditInputError):
        parse_npm_audit({}, scope="dev")


# --- fail-closed input handling ---------------------------------------------


def test_load_json_missing_file_raises() -> None:
    with pytest.raises(AuditInputError):
        load_json("does-not-exist-9f3a.json")


def test_load_json_malformed_raises(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AuditInputError):
        load_json(str(bad))


def test_main_missing_input_returns_nonzero(tmp_path) -> None:
    # A missing required audit JSON must make the CLI fail closed (non-zero).
    exc = tmp_path / "exceptions.json"
    exc.write_text('{"exceptions": []}', encoding="utf-8")
    rc = main(
        [
            "--pip-audit-runtime",
            "missing-a.json",
            "--pip-audit-full",
            "missing-b.json",
            "--npm-audit-runtime",
            "missing-c.json",
            "--npm-audit-full",
            "missing-d.json",
            "--exceptions",
            str(exc),
        ]
    )
    assert rc != 0

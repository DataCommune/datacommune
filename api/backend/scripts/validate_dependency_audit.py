#!/usr/bin/env python3
"""Fail-closed dependency-audit verdict for SEC-01 (D-08/D-09).

Normalises ``pip-audit`` and ``npm audit`` JSON output plus a machine-readable
exception registry into a single ``PASS``/``FAIL`` verdict.

Fail-closed contract:

- a missing or malformed audit JSON is a FAIL, never an implicit "zero finding"
  (an empty ``{}`` object lacks the tool's structural key and blocks);
- every finding present in the audit output must be covered by a valid
  exception matched on ecosystem + advisory + package + scope, otherwise it
  blocks (an untriaged finding — of any severity — is a FAIL, and a
  critical/high finding is always blocking without triage);
- scope is part of the match key: a ``dev``-scoped exception can never silence
  the same advisory seen at ``runtime``;
- an exception missing any required field, not yet human-reviewed
  (``reviewed_by_human`` must be ``true``), expired, or whose horizon exceeds
  30 days is a FAIL.

The tool prints only advisory identifiers, package names, severities and
scopes. It never prints advisory descriptions, secret values or raw payloads.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date

MAX_EXCEPTION_DAYS = 30
BLOCKING_SEVERITIES = frozenset({"critical", "high"})
REQUIRED_EXCEPTION_FIELDS = (
    "ecosystem",
    "advisory",
    "package",
    "scope",
    "reachability",
    "owner",
    "justification",
    "opened_at",
    "expires_at",
    "reviewed_by_human",
)


class AuditInputError(Exception):
    """Raised when a required audit input is missing or unparseable."""


@dataclass(frozen=True)
class Finding:
    """A single normalised advisory hit against a locked dependency."""

    ecosystem: str
    package: str
    version: str
    advisory: str
    severity: str
    scope: str  # "runtime" | "dev"


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)


# --- input loading -----------------------------------------------------------


def load_json(path: str) -> dict:
    """Load a JSON file, raising ``AuditInputError`` on any failure.

    A missing tool output or malformed JSON must never be silently treated as
    "no vulnerabilities" — it is an explicit fail-closed signal.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise AuditInputError(f"audit input not found: {path}") from exc
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise AuditInputError(f"audit input unreadable: {path}") from exc


# --- parsers -----------------------------------------------------------------


def parse_pip_audit(data: dict, scope: str) -> list[Finding]:
    """Extract findings from ``pip-audit --format json`` output.

    pip-audit (OSV) does not always carry a severity; unknown severity is
    treated conservatively as blocking so an untriaged entry cannot slip
    through as low. A payload without the ``dependencies`` key is malformed
    (a real audit always emits it, even empty) and fails closed rather than
    parsing as "zero findings".
    """
    if "dependencies" not in data:
        raise AuditInputError("pip-audit JSON missing 'dependencies' key")
    findings: list[Finding] = []
    for dep in data.get("dependencies", []):
        name = dep.get("name", "")
        version = str(dep.get("version", ""))
        for vuln in dep.get("vulns", []):
            advisory = vuln.get("id") or ""
            if not advisory:
                continue
            severity = str(vuln.get("severity", "unknown")).lower()
            findings.append(
                Finding(
                    ecosystem="pypi",
                    package=name,
                    version=version,
                    advisory=advisory,
                    severity=severity,
                    scope=scope,
                )
            )
    return findings


def parse_npm_audit(data: dict, scope: str) -> list[Finding]:
    """Extract findings from ``npm audit --json`` (v2 schema) output.

    Only leaf ``via`` entries (objects carrying an advisory ``url``/``source``)
    become findings; string ``via`` entries are propagation nodes and are
    skipped to avoid double-counting. A payload without the ``vulnerabilities``
    key is malformed (a real audit always emits it, even empty) and fails
    closed rather than parsing as "zero findings".
    """
    if "vulnerabilities" not in data:
        raise AuditInputError("npm audit JSON missing 'vulnerabilities' key")
    findings: list[Finding] = []
    for pkg_name, node in data.get("vulnerabilities", {}).items():
        for via in node.get("via", []):
            if not isinstance(via, dict):
                continue
            advisory = _npm_advisory_id(via)
            if not advisory:
                continue
            findings.append(
                Finding(
                    ecosystem="npm",
                    package=via.get("name") or pkg_name,
                    version="",
                    advisory=advisory,
                    severity=str(via.get("severity", "unknown")).lower(),
                    scope=scope,
                )
            )
    return findings


def _npm_advisory_id(via: dict) -> str:
    """Derive a stable advisory identifier from an npm ``via`` object."""
    url = via.get("url", "")
    if "/advisories/" in url:
        return url.rsplit("/advisories/", 1)[1].strip("/")
    source = via.get("source")
    return f"npm-{source}" if source is not None else ""


def collect_findings(
    runtime_files: dict[str, dict],
    full_files: dict[str, dict],
) -> list[Finding]:
    """Merge runtime and full-tree audit outputs into scoped findings.

    Advisories present in the runtime view are scoped ``runtime``; advisories
    present only in the full (build/dev) tree are scoped ``dev``.
    """
    parsers = {"pypi": parse_pip_audit, "npm": parse_npm_audit}

    runtime: dict[tuple[str, str], Finding] = {}
    for ecosystem, data in runtime_files.items():
        for finding in parsers[ecosystem](data, scope="runtime"):
            runtime[(finding.advisory.lower(), finding.package.lower())] = finding

    merged: dict[tuple[str, str], Finding] = dict(runtime)
    for ecosystem, data in full_files.items():
        for finding in parsers[ecosystem](data, scope="dev"):
            key = (finding.advisory.lower(), finding.package.lower())
            if key not in merged:  # runtime scope wins over dev
                merged[key] = finding
    return list(merged.values())


# --- exception validation ----------------------------------------------------


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_exception(exc: dict, now: date) -> list[str]:
    """Return a list of structural/temporal problems for one exception.

    An empty list means the exception is valid.
    """
    problems: list[str] = []
    for required in REQUIRED_EXCEPTION_FIELDS:
        if required == "reviewed_by_human":
            # A boolean gate: absent or false means the human triage checkpoint
            # has not signed off, so the exception is not yet valid.
            if exc.get(required) is not True:
                problems.append("reviewed_by_human must be true (human triage gate)")
        elif not exc.get(required):
            problems.append(f"missing field '{required}'")

    opened = _parse_date(exc.get("opened_at"))
    expires = _parse_date(exc.get("expires_at"))
    if exc.get("opened_at") and opened is None:
        problems.append("opened_at is not an ISO date")
    if exc.get("expires_at") and expires is None:
        problems.append("expires_at is not an ISO date")

    if expires is not None:
        if expires < now:
            problems.append(f"expired ({expires.isoformat()})")
        horizon_anchor = opened if opened is not None else now
        if (expires - horizon_anchor).days > MAX_EXCEPTION_DAYS:
            problems.append(f"horizon exceeds {MAX_EXCEPTION_DAYS} days")
    return problems


# --- verdict -----------------------------------------------------------------


def evaluate(
    findings: list[Finding],
    exceptions: list[dict],
    now: date,
) -> Verdict:
    """Compute the fail-closed verdict over findings and exceptions."""
    problems: list[str] = []
    valid: dict[tuple[str, str, str, str], dict] = {}

    for exc in exceptions:
        key = (
            str(exc.get("ecosystem", "")).lower(),
            str(exc.get("advisory", "")).lower(),
            str(exc.get("package", "")).lower(),
            str(exc.get("scope", "")).lower(),
        )
        errors = validate_exception(exc, now)
        if errors:
            problems.append(
                f"exception {exc.get('advisory', '?')}/{exc.get('package', '?')}: "
                + "; ".join(errors)
            )
            continue
        valid[key] = exc

    for finding in findings:
        key = (
            finding.ecosystem.lower(),
            finding.advisory.lower(),
            finding.package.lower(),
            finding.scope.lower(),
        )
        if key not in valid:
            problems.append(
                f"untriaged finding {finding.advisory} "
                f"({finding.package}, {finding.severity}, {finding.scope})"
            )

    return Verdict(ok=not problems, problems=problems)


# --- CLI ---------------------------------------------------------------------


def _load_exceptions(path: str) -> list[dict]:
    data = load_json(path)
    exceptions = data.get("exceptions", [])
    if not isinstance(exceptions, list):
        raise AuditInputError("exceptions registry: 'exceptions' must be a list")
    return exceptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed dependency-audit verdict (SEC-01). Normalises "
            "pip-audit and npm audit JSON plus an exception registry into a "
            "single PASS/FAIL. Missing input, untriaged finding, or an "
            "expired/incomplete/over-30-day exception all block."
        )
    )
    parser.add_argument("--pip-audit-runtime", required=True)
    parser.add_argument("--pip-audit-full", required=True)
    parser.add_argument("--npm-audit-runtime", required=True)
    parser.add_argument("--npm-audit-full", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument(
        "--now",
        default=None,
        help="ISO date used as the temporal anchor (default: today, UTC).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = _parse_date(args.now) or date.today()

    try:
        exceptions = _load_exceptions(args.exceptions)
        runtime_files = {
            "pypi": load_json(args.pip_audit_runtime),
            "npm": load_json(args.npm_audit_runtime),
        }
        full_files = {
            "pypi": load_json(args.pip_audit_full),
            "npm": load_json(args.npm_audit_full),
        }
        # Parsing happens inside the fail-closed boundary: a malformed payload
        # (e.g. an empty {}) raised by a parser must return non-zero, not crash.
        findings = collect_findings(runtime_files, full_files)
    except AuditInputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    verdict = evaluate(findings, exceptions, now)

    if verdict.ok:
        print(f"PASS: {len(findings)} finding(s), all triaged; no untreated risk.")
        return 0

    print("FAIL: dependency-audit verdict blocked:", file=sys.stderr)
    for problem in verdict.problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

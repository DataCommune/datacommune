#!/usr/bin/env python3
"""Fail-closed validator for the v3.5 certification report (CERT-01, D-14/D-15/D-16).

Three independent, composable checks, all fail-closed:

- ``--preflight <phase-46-dir>`` — confirms the 19 upstream SUMMARY files
  (43-01..09, 44-01..05, 45-01..05) plus their three ``*-VERIFICATION.md``
  (``status: passed``) exist, and that the five 46-01..05 SUMMARY files exist.
  A missing file or a VERIFICATION not marked ``passed`` is a hard stop —
  never a warning.
- ``--validate <manifest.json>`` — checks the certification manifest: exactly
  the 22 requirement IDs declared in ``REQUIREMENTS.md`` (bijection, no
  duplicate, no unknown extra ID), every entry carries a non-empty
  environment/timestamp/command/commit SHA, an ``artifact_path`` that exists
  on disk, a ``sha256`` that matches the *current* bytes of that artifact
  (tamper/staleness detection), and a status restricted to the literal set
  ``{"PASS", "FAIL", "MISSING"}`` — no other spelling (e.g. ``"N/A"``) is
  silently treated as a pass.
- ``--p0-p1-register <register.md>`` — any row whose status does not
  normalise to a closed-equivalent token (``CLOSED``/``CLOTURE``/``CLOS``)
  blocks: an ``OPEN`` row, an ``UNKNOWN`` row, or any unrecognised spelling
  all fail closed, on purpose.
- ``--report <CERTIFICATION-v3.5.md>`` — only evaluated when combined with
  ``--validate``; the report's own requirement table must match the manifest
  ID-for-ID and status-for-status (no report/manifest divergence), the report
  must carry the literal string ``Verdict humain: EN ATTENTE``, and the report
  text must not contain a live/anon API key value, a high-precision
  coordinate, or a French street-address pattern.

This CLI is read-only: it never writes to ROADMAP.md, STATE.md or
REQUIREMENTS.md. It never prints a raw evidence value — only requirement IDs,
statuses, paths and hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field

ALLOWED_STATUSES = frozenset({"PASS", "FAIL", "MISSING"})
REQUIRED_ENTRY_FIELDS = (
    "id",
    "status",
    "environment",
    "timestamp",
    "sha",
    "command",
    "artifact_path",
    "sha256",
)
CLOSED_STATUS_TOKENS = frozenset({"CLOSED", "CLOTURE", "CLOS"})

# Phase 43/44/45 live under fixed, project-specific slugs (siblings of the
# phase-46 directory passed to --preflight).
PHASE_43_DIRNAME = "DC-43-contrats-s-mantiques-golden-cases"
PHASE_44_DIRNAME = "DC-44-provenance-fra-cheur-g-ocodage-maintenu"
PHASE_45_DIRNAME = "DC-45-r-solution-exacte-v-rit-publique-confidentialit"

_PREFLIGHT_SPECS = (
    (43, PHASE_43_DIRNAME, range(1, 10), True),
    (44, PHASE_44_DIRNAME, range(1, 6), True),
    (45, PHASE_45_DIRNAME, range(1, 6), True),
)

_VERIFICATION_PASSED_RE = re.compile(r"(?m)^status:\s*passed\s*$")
_TRACEABILITY_ROW_RE = re.compile(r"^\|\s*([A-Z]+-\d+)\s*\|")
_REPORT_ROW_RE = re.compile(
    r"^\|[^|]*?\b([A-Z]+-\d+)\b[^|]*\|.*?\b(PASS|FAIL|MISSING)\b", re.IGNORECASE
)
_VERDICT_LITERAL = "Verdict humain: EN ATTENTE"

# Heuristic leak patterns for the report scan: a live/anon key *value* (not a
# bare mention of the prefix), a high-precision decimal coordinate, and a
# French street-address pattern (number + voie keyword).
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api-key-value", re.compile(r"\bdc_(?:live|anon)_[A-Za-z0-9]{8,}\b")),
    ("high-precision-coordinate", re.compile(r"-?\d{1,3}\.\d{5,}")),
    (
        "street-address",
        re.compile(
            r"\b\d{1,4}\s+(?:rue|avenue|boulevard|impasse|chemin|all[ée]e|place)\b",
            re.IGNORECASE,
        ),
    ),
)


class CertificationInputError(Exception):
    """Raised when a required certification input is missing or unparseable."""


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)


# --- path helpers -------------------------------------------------------------


def repo_root_from_manifest(manifest_path: str) -> str:
    """Derive the repo root from a manifest living at
    ``<root>/.planning/phases/DC-46-.../file.json``.
    """
    phase_dir = os.path.dirname(os.path.abspath(manifest_path))
    phases_dir = os.path.dirname(phase_dir)
    planning_dir = os.path.dirname(phases_dir)
    return os.path.dirname(planning_dir)


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# --- REQUIREMENTS.md bijection -------------------------------------------------


def parse_requirement_ids(requirements_md_text: str) -> list[str]:
    """Extract requirement IDs from the ``## Traceability v3.5`` table only.

    Restricting to that section avoids matching unrelated tables (e.g. "Out
    of Scope") that also start rows with ``|``.
    """
    marker = "## Traceability v3.5"
    idx = requirements_md_text.find(marker)
    if idx == -1:
        raise CertificationInputError(
            "REQUIREMENTS.md missing '## Traceability v3.5' section"
        )
    section = requirements_md_text[idx:]
    ids: list[str] = []
    for line in section.splitlines():
        match = _TRACEABILITY_ROW_RE.match(line.strip())
        if match:
            ids.append(match.group(1))
    return ids


def load_requirement_ids(requirements_md_path: str) -> set[str]:
    try:
        with open(requirements_md_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise CertificationInputError(
            f"REQUIREMENTS.md not found: {requirements_md_path}"
        ) from exc
    ids = parse_requirement_ids(text)
    if not ids:
        raise CertificationInputError("REQUIREMENTS.md: zero requirement IDs parsed")
    return set(ids)


# --- preflight (43-45 closed + 46-01..05 present) ------------------------------


def preflight_problems(phase46_dir: str) -> list[str]:
    """Return preflight problems; an empty list means preflight passes."""
    problems: list[str] = []
    phases_root = os.path.dirname(os.path.normpath(phase46_dir))

    for phase_num, dirname, numbers, need_verification in _PREFLIGHT_SPECS:
        phase_dir = os.path.join(phases_root, dirname)
        for n in numbers:
            summary_path = os.path.join(phase_dir, f"{phase_num}-{n:02d}-SUMMARY.md")
            if not os.path.isfile(summary_path):
                problems.append(f"MISSING SUMMARY: {summary_path}")
        if need_verification:
            verification_path = os.path.join(phase_dir, f"{phase_num}-VERIFICATION.md")
            if not os.path.isfile(verification_path):
                problems.append(f"MISSING VERIFICATION: {verification_path}")
            else:
                with open(verification_path, encoding="utf-8") as handle:
                    text = handle.read()
                if not _VERIFICATION_PASSED_RE.search(text):
                    problems.append(
                        f"NOT PASSED (status != passed): {verification_path}"
                    )

    for n in range(1, 6):
        summary_path = os.path.join(phase46_dir, f"46-{n:02d}-SUMMARY.md")
        if not os.path.isfile(summary_path):
            problems.append(f"MISSING SUMMARY: {summary_path}")

    return problems


# --- manifest validation --------------------------------------------------------


def load_manifest(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise CertificationInputError(f"manifest not found: {path}") from exc
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise CertificationInputError(f"manifest unreadable: {path}") from exc
    if "requirements" not in data or not isinstance(data["requirements"], list):
        raise CertificationInputError("manifest malformed: missing 'requirements' list")
    return data


def validate_manifest(
    manifest: dict, required_ids: set[str], repo_root: str
) -> Verdict:
    problems: list[str] = []
    entries = manifest["requirements"]

    ids: list[str] = []
    for entry in entries:
        entry_id = entry.get("id", "<missing-id>")
        ids.append(entry_id)

        missing_fields = [f for f in REQUIRED_ENTRY_FIELDS if not entry.get(f)]
        if missing_fields:
            problems.append(
                f"{entry_id}: missing/empty field(s) {', '.join(missing_fields)}"
            )
            continue  # remaining checks need those fields

        status = entry["status"]
        if status not in ALLOWED_STATUSES:
            problems.append(
                f"{entry_id}: status '{status}' is not one of PASS|FAIL|MISSING "
                "(no automatic conversion to PASS)"
            )
        elif status != "PASS":
            problems.append(f"{entry_id}: status is {status} (blocks certification)")

        artifact_path = entry["artifact_path"]
        artifact_abs = os.path.join(repo_root, artifact_path)
        if not os.path.isfile(artifact_abs):
            problems.append(
                f"{entry_id}: artifact_path does not exist: {artifact_path}"
            )
            continue

        actual_hash = sha256_file(artifact_abs)
        if actual_hash != entry["sha256"]:
            problems.append(
                f"{entry_id}: sha256 mismatch for {artifact_path} "
                "(evidence stale or tampered)"
            )

    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate requirement ID(s): {', '.join(duplicates)}")

    id_set = set(ids)
    missing_ids = sorted(required_ids - id_set)
    extra_ids = sorted(id_set - required_ids)
    if missing_ids:
        problems.append(
            f"missing requirement ID(s) vs REQUIREMENTS.md: {', '.join(missing_ids)}"
        )
    if extra_ids:
        problems.append(
            f"unknown requirement ID(s) not in REQUIREMENTS.md: {', '.join(extra_ids)}"
        )

    return Verdict(ok=not problems, problems=problems)


def manifest_status_by_id(manifest: dict) -> dict[str, str]:
    return {
        entry.get("id", ""): entry.get("status", "")
        for entry in manifest["requirements"]
        if entry.get("id")
    }


# --- P0/P1 register --------------------------------------------------------------


def _normalise_status_token(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return stripped.strip().upper()


def _split_table_row(line: str) -> list[str]:
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell)


def parse_markdown_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse the first ``|``-delimited table found in ``text``.

    Returns ``(header_cells, data_rows)``. Returns ``([], [])`` when no table
    is present (an empty register is a legitimate "zero open blocker" state).
    """
    lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    if not lines:
        return [], []
    header = _split_table_row(lines[0])
    rows: list[list[str]] = []
    for line in lines[1:]:
        cells = _split_table_row(line)
        if _is_separator_row(cells):
            continue
        rows.append(cells)
    return header, rows


def validate_register(register_text: str) -> Verdict:
    header, rows = parse_markdown_table(register_text)
    if not rows:
        return Verdict(ok=True, problems=[])

    status_idx = None
    id_idx = 0
    for idx, name in enumerate(header):
        normalised = _normalise_status_token(name)
        if normalised in {"STATUT", "STATUS"}:
            status_idx = idx
        if normalised == "ID":
            id_idx = idx
    if status_idx is None:
        raise CertificationInputError(
            "P0/P1 register: no 'Statut'/'Status' column found"
        )

    problems: list[str] = []
    for row in rows:
        if status_idx >= len(row):
            continue
        entry_id = row[id_idx] if id_idx < len(row) else "<unknown>"
        token = _normalise_status_token(row[status_idx])
        if token not in CLOSED_STATUS_TOKENS:
            problems.append(
                f"P0/P1 register: {entry_id} status '{row[status_idx]}' is not closed"
            )

    return Verdict(ok=not problems, problems=problems)


# --- report validation -------------------------------------------------------------


def scan_report_leaks(report_text: str) -> list[str]:
    problems: list[str] = []
    for name, pattern in _LEAK_PATTERNS:
        if pattern.search(report_text):
            problems.append(f"report contains a probable {name} (redaction required)")
    return problems


def validate_report(report_text: str, manifest: dict) -> Verdict:
    problems: list[str] = []

    if _VERDICT_LITERAL not in report_text:
        problems.append(f"report missing literal verdict line: '{_VERDICT_LITERAL}'")

    report_status: dict[str, str] = {}
    for line in report_text.splitlines():
        match = _REPORT_ROW_RE.match(line.strip())
        if match:
            report_status[match.group(1)] = match.group(2).upper()

    manifest_status = manifest_status_by_id(manifest)
    missing_in_report = sorted(set(manifest_status) - set(report_status))
    if missing_in_report:
        problems.append(
            f"report table missing requirement row(s): {', '.join(missing_in_report)}"
        )
    for req_id, status in manifest_status.items():
        report_value = report_status.get(req_id)
        if report_value is not None and report_value != status:
            problems.append(
                f"{req_id}: report/manifest divergence "
                f"(manifest={status}, report={report_value})"
            )

    problems.extend(scan_report_leaks(report_text))
    return Verdict(ok=not problems, problems=problems)


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed v3.5 certification validator (CERT-01). --preflight "
            "checks the 19 upstream SUMMARY + 3 VERIFICATION(passed) + 5 "
            "phase-46 SUMMARY files exist. --validate checks the 22-ID "
            "bijection with REQUIREMENTS.md, per-requirement evidence hash "
            "and PASS|FAIL|MISSING status. --p0-p1-register and --report are "
            "optional additional gates evaluated alongside --validate."
        )
    )
    parser.add_argument(
        "--preflight",
        metavar="PHASE_46_DIR",
        help="Path to the DC-46-* phase directory.",
    )
    parser.add_argument("--validate", metavar="MANIFEST_JSON")
    parser.add_argument("--p0-p1-register", metavar="REGISTER_MD")
    parser.add_argument("--report", metavar="REPORT_MD")
    parser.add_argument(
        "--requirements",
        metavar="REQUIREMENTS_MD",
        default=None,
        help=(
            "Override REQUIREMENTS.md path "
            "(default: <repo_root>/.planning/REQUIREMENTS.md)."
        ),
    )
    return parser


def _run_preflight(phase46_dir: str) -> int:
    problems = preflight_problems(phase46_dir)
    if problems:
        print("PREFLIGHT FAIL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        "PREFLIGHT PASS: 19 upstream SUMMARY + 3 VERIFICATION(passed) "
        "+ 5 phase-46 SUMMARY present."
    )
    return 0


def _run_validate(args: argparse.Namespace) -> int:  # noqa: C901
    manifest_path = args.validate
    try:
        manifest = load_manifest(manifest_path)
        repo_root = repo_root_from_manifest(manifest_path)
        requirements_path = args.requirements or os.path.join(
            repo_root, ".planning", "REQUIREMENTS.md"
        )
        required_ids = load_requirement_ids(requirements_path)
    except CertificationInputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    verdict = validate_manifest(manifest, required_ids, repo_root)

    register_verdict = Verdict(ok=True)
    if args.p0_p1_register:
        try:
            with open(args.p0_p1_register, encoding="utf-8") as handle:
                register_text = handle.read()
        except OSError:
            print(
                f"FAIL: P0/P1 register not found: {args.p0_p1_register}",
                file=sys.stderr,
            )
            return 2
        try:
            register_verdict = validate_register(register_text)
        except CertificationInputError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2

    statuses = manifest_status_by_id(manifest)
    for req_id in sorted(statuses):
        print(f"{req_id}: {statuses[req_id]}")

    all_problems = list(verdict.problems) + list(register_verdict.problems)
    if all_problems:
        print("NOT_ELIGIBLE:", file=sys.stderr)
        for problem in all_problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.report:
        try:
            with open(args.report, encoding="utf-8") as handle:
                report_text = handle.read()
        except OSError:
            print(f"FAIL: report not found: {args.report}", file=sys.stderr)
            return 2
        report_verdict = validate_report(report_text, manifest)
        if not report_verdict.ok:
            print("REPORT FAIL:", file=sys.stderr)
            for problem in report_verdict.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1

    print(f"ELIGIBLE: {len(statuses)}/22 PASS, zero P0/P1 open.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.preflight:
        return _run_preflight(args.preflight)
    if args.validate:
        return _run_validate(args)

    print("FAIL: one of --preflight or --validate is required.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

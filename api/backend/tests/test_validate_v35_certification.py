"""Contract tests for the fail-closed v3.5 certification validator (CERT-01).

Covers: REQUIREMENTS.md bijection parsing, the 43-45 preflight (missing
SUMMARY, VERIFICATION not passed, all-present pass), manifest validation
(duplicate ID, missing/extra ID vs REQUIREMENTS.md, wrong sha256 tamper
detection, FAIL/MISSING/unknown-status blocking, no automatic conversion to
PASS), the P0/P1 register (OPEN/UNKNOWN blocks, CLOSED/empty passes), and the
report validator (missing verdict literal, report/manifest divergence, leak
patterns, and the fully-green end-to-end CLI flow).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.validate_v35_certification import (
    ALLOWED_STATUSES,
    CertificationInputError,
    main,
    parse_markdown_table,
    parse_requirement_ids,
    preflight_problems,
    repo_root_from_manifest,
    scan_report_leaks,
    sha256_file,
    validate_manifest,
    validate_register,
    validate_report,
)

pytestmark = pytest.mark.unit

TRACEABILITY_MD = """# Requirements

## Out of Scope

| Feature | Reason |
|---|---|
| Something | Not now |

## Traceability v3.5

| Requirement | Phase | Status |
|---|---:|---|
| DATA-01 | Phase 43 | Complete |
| DATA-02 | Phase 43 | Complete |
| HEALTH-02 | Phase 46 | Pending |

**Coverage:** 3 exigences
"""

TWENTY_TWO_IDS = [f"REQ-{n:02d}" for n in range(1, 23)]


def _requirements_md_text(ids: list[str]) -> str:
    rows = "\n".join(f"| {rid} | Phase 46 | Complete |" for rid in ids)
    return f"""# Requirements

## Traceability v3.5

| Requirement | Phase | Status |
|---|---:|---|
{rows}
"""


# --- REQUIREMENTS.md bijection parsing ---------------------------------------


def test_parse_requirement_ids_only_traceability_section() -> None:
    ids = parse_requirement_ids(TRACEABILITY_MD)
    assert ids == ["DATA-01", "DATA-02", "HEALTH-02"]
    # The Out-of-Scope table's rows must not leak in (no "Requirement"-shaped ID there).


def test_parse_requirement_ids_missing_section_raises() -> None:
    with pytest.raises(CertificationInputError):
        parse_requirement_ids("# Requirements\n\nno traceability section here\n")


# NOTE: a former test asserted that the LIVE tracking requirements carried the 22
# v3.5 ids. It was removed once v3.5 was certified and archived (8df8e22b): the live
# file is purged at every milestone boundary, so the assertion could only ever hold
# while v3.5 was the active milestone. The certification artefact itself is frozen and
# signed (CERTIFICATION-v3.5.md, sha256 manifest, ELIGIBLE 22/22) — the parser it
# relies on stays covered by the fixture-based tests above and below.


# --- preflight ----------------------------------------------------------------


def _make_phase_dir(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_summaries(phase_dir: Path, phase_num: int, numbers: range) -> None:
    for n in numbers:
        path = phase_dir / f"{phase_num}-{n:02d}-SUMMARY.md"
        path.write_text("ok", encoding="utf-8")


def _write_verification(phase_dir: Path, phase_num: int, passed: bool) -> None:
    status = "passed" if passed else "gaps_found"
    (phase_dir / f"{phase_num}-VERIFICATION.md").write_text(
        f"---\nstatus: {status}\n---\nbody\n", encoding="utf-8"
    )


def _build_complete_fixture_tree(root: Path) -> Path:
    phases_root = root / ".planning" / "phases"
    p43 = _make_phase_dir(phases_root, "DC-43-contrats-s-mantiques-golden-cases")
    _write_summaries(p43, 43, range(1, 10))
    _write_verification(p43, 43, passed=True)

    p44 = _make_phase_dir(phases_root, "DC-44-provenance-fra-cheur-g-ocodage-maintenu")
    _write_summaries(p44, 44, range(1, 6))
    _write_verification(p44, 44, passed=True)

    p45 = _make_phase_dir(
        phases_root, "DC-45-r-solution-exacte-v-rit-publique-confidentialit"
    )
    _write_summaries(p45, 45, range(1, 6))
    _write_verification(p45, 45, passed=True)

    p46 = _make_phase_dir(phases_root, "DC-46-robustesse-certification-finale")
    _write_summaries(p46, 46, range(1, 6))
    return p46


def test_preflight_all_present_passes(tmp_path: Path) -> None:
    p46 = _build_complete_fixture_tree(tmp_path)
    assert preflight_problems(str(p46)) == []


def test_preflight_missing_summary_fails(tmp_path: Path) -> None:
    p46 = _build_complete_fixture_tree(tmp_path)
    (p46 / "46-03-SUMMARY.md").unlink()
    problems = preflight_problems(str(p46))
    assert any("46-03-SUMMARY.md" in p for p in problems)


def test_preflight_verification_not_passed_fails(tmp_path: Path) -> None:
    p46 = _build_complete_fixture_tree(tmp_path)
    phases_root = tmp_path / ".planning" / "phases"
    p44 = phases_root / "DC-44-provenance-fra-cheur-g-ocodage-maintenu"
    _write_verification(p44, 44, passed=False)
    problems = preflight_problems(str(p46))
    assert any("NOT PASSED" in p and "44-VERIFICATION" in p for p in problems)


def test_preflight_missing_verification_file_fails(tmp_path: Path) -> None:
    p46 = _build_complete_fixture_tree(tmp_path)
    phases_root = tmp_path / ".planning" / "phases"
    p43 = phases_root / "DC-43-contrats-s-mantiques-golden-cases"
    (p43 / "43-VERIFICATION.md").unlink()
    problems = preflight_problems(str(p46))
    assert any("MISSING VERIFICATION" in p and "43-VERIFICATION" in p for p in problems)


# --- manifest validation -------------------------------------------------------


def _manifest_entry(
    repo_root: Path, req_id: str, status: str = "PASS", **overrides
) -> dict:
    artifact_rel = f"evidence/{req_id}.md"
    artifact_abs = repo_root / artifact_rel
    artifact_abs.parent.mkdir(parents=True, exist_ok=True)
    if not artifact_abs.exists():
        artifact_abs.write_text(f"evidence for {req_id}", encoding="utf-8")
    entry = {
        "id": req_id,
        "status": status,
        "environment": "prod-legacy",
        "timestamp": "2026-07-23T00:00:00Z",
        "sha": "abc1234",
        "command": "pytest tests/",
        "artifact_path": artifact_rel,
        "sha256": sha256_file(str(artifact_abs)),
    }
    entry.update(overrides)
    return entry


def _full_manifest(repo_root: Path, ids: list[str], **per_id_overrides) -> dict:
    entries = []
    for rid in ids:
        overrides = per_id_overrides.get(rid, {})
        entries.append(_manifest_entry(repo_root, rid, **overrides))
    return {"generated_at": "2026-07-23T00:00:00Z", "requirements": entries}


def test_validate_manifest_all_pass_is_eligible(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is True
    assert verdict.problems == []


def test_validate_manifest_duplicate_id_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    manifest["requirements"].append(_manifest_entry(tmp_path, TWENTY_TWO_IDS[0]))
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("duplicate" in p for p in verdict.problems)


def test_validate_manifest_missing_id_fails(tmp_path: Path) -> None:
    ids = TWENTY_TWO_IDS[:-1]  # drop the last required ID
    manifest = _full_manifest(tmp_path, ids)
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("missing requirement ID" in p for p in verdict.problems)


def test_validate_manifest_unknown_extra_id_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, [*TWENTY_TWO_IDS, "EXTRA-01"])
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("unknown requirement ID" in p for p in verdict.problems)


def test_validate_manifest_wrong_sha256_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    manifest["requirements"][0]["sha256"] = hashlib.sha256(b"tampered").hexdigest()
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("sha256 mismatch" in p for p in verdict.problems)


def test_validate_manifest_missing_artifact_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    manifest["requirements"][0]["artifact_path"] = "does/not/exist.md"
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("does not exist" in p for p in verdict.problems)


def test_validate_manifest_fail_status_blocks(tmp_path: Path) -> None:
    manifest = _full_manifest(
        tmp_path, TWENTY_TWO_IDS, **{TWENTY_TWO_IDS[0]: {"status": "FAIL"}}
    )
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("status is FAIL" in p for p in verdict.problems)


def test_validate_manifest_missing_status_blocks(tmp_path: Path) -> None:
    manifest = _full_manifest(
        tmp_path, TWENTY_TWO_IDS, **{TWENTY_TWO_IDS[0]: {"status": "MISSING"}}
    )
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("status is MISSING" in p for p in verdict.problems)


def test_validate_manifest_na_status_not_autopassed(tmp_path: Path) -> None:
    manifest = _full_manifest(
        tmp_path, TWENTY_TWO_IDS, **{TWENTY_TWO_IDS[0]: {"status": "N/A"}}
    )
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("not one of PASS|FAIL|MISSING" in p for p in verdict.problems)
    assert frozenset({"PASS", "FAIL", "MISSING"}) == ALLOWED_STATUSES


def test_validate_manifest_missing_field_blocks(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    del manifest["requirements"][0]["environment"]
    verdict = validate_manifest(manifest, set(TWENTY_TWO_IDS), str(tmp_path))
    assert verdict.ok is False
    assert any("missing/empty field" in p for p in verdict.problems)


# --- repo_root_from_manifest ---------------------------------------------------


def test_repo_root_from_manifest(tmp_path: Path) -> None:
    manifest_path = (
        tmp_path
        / ".planning"
        / "phases"
        / "DC-46-robustesse-certification-finale"
        / "46-CERTIFICATION-MANIFEST.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{}", encoding="utf-8")
    assert repo_root_from_manifest(str(manifest_path)) == str(tmp_path)


# --- P0/P1 register -------------------------------------------------------------


def test_register_empty_table_passes() -> None:
    verdict = validate_register("# P0/P1 register\n\nNo blockers.\n")
    assert verdict.ok is True


def test_register_all_closed_passes() -> None:
    text = """
| ID | Severite | Statut |
|---|---|---|
| P1-01 | P1 | CLOSED |
| P1-02 | P1 | Cloture |
"""
    verdict = validate_register(text)
    assert verdict.ok is True


def test_register_open_row_blocks() -> None:
    text = """
| ID | Severite | Statut |
|---|---|---|
| P1-01 | P1 | OPEN |
"""
    verdict = validate_register(text)
    assert verdict.ok is False
    assert any("P1-01" in p for p in verdict.problems)


def test_register_unknown_row_blocks() -> None:
    text = """
| ID | Severite | Statut |
|---|---|---|
| P1-01 | P1 | UNKNOWN |
"""
    verdict = validate_register(text)
    assert verdict.ok is False


def test_register_missing_status_column_raises() -> None:
    text = """
| ID | Severite |
|---|---|
| P1-01 | P1 |
"""
    with pytest.raises(CertificationInputError):
        validate_register(text)


def test_parse_markdown_table_no_table_returns_empty() -> None:
    header, rows = parse_markdown_table("just prose, no table here")
    assert header == []
    assert rows == []


# --- report validation -------------------------------------------------------------


def _report_text(
    manifest: dict, verdict_line: str = "Verdict humain: EN ATTENTE"
) -> str:
    lines = ["# CERTIFICATION-v3.5", "", "| ID | Statut |", "|---|---|"]
    for entry in manifest["requirements"]:
        lines.append(f"| {entry['id']} | {entry['status']} |")  # noqa: PERF401
    lines.append("")
    lines.append(verdict_line)
    return "\n".join(lines)


def test_validate_report_matches_manifest_passes(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    report = _report_text(manifest)
    verdict = validate_report(report, manifest)
    assert verdict.ok is True


def test_validate_report_missing_verdict_literal_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    report = _report_text(manifest, verdict_line="Verdict humain: CERTIFIE")
    verdict = validate_report(report, manifest)
    assert verdict.ok is False
    assert any("verdict line" in p for p in verdict.problems)


def test_validate_report_divergence_from_manifest_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    report = _report_text(manifest)
    # Flip one status in the report only.
    report = report.replace(
        f"| {TWENTY_TWO_IDS[0]} | PASS |", f"| {TWENTY_TWO_IDS[0]} | FAIL |"
    )
    verdict = validate_report(report, manifest)
    assert verdict.ok is False
    assert any("divergence" in p for p in verdict.problems)


def test_validate_report_missing_row_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    report = _report_text(manifest)
    report = report.replace(f"| {TWENTY_TWO_IDS[-1]} | PASS |\n", "")
    verdict = validate_report(report, manifest)
    assert verdict.ok is False
    assert any("missing requirement row" in p for p in verdict.problems)


def test_scan_report_leaks_detects_live_key_value() -> None:
    problems = scan_report_leaks("clé utilisée: dc_live_ab12cd34ef56gh78")
    assert any("api-key-value" in p for p in problems)


def test_scan_report_leaks_detects_high_precision_coordinate() -> None:
    problems = scan_report_leaks("point observé -1.87421234, 47.21809876")
    assert any("high-precision-coordinate" in p for p in problems)


def test_scan_report_leaks_detects_street_address() -> None:
    problems = scan_report_leaks("adresse test: 12 rue de la Paix")
    assert any("street-address" in p for p in problems)


def test_scan_report_leaks_clean_text_has_no_problems() -> None:
    clean = "case_id opaque golden-address-001, resolution=exact"
    assert scan_report_leaks(clean) == []


def test_validate_report_with_leak_fails(tmp_path: Path) -> None:
    manifest = _full_manifest(tmp_path, TWENTY_TWO_IDS)
    report = _report_text(manifest) + "\n12 rue de la Paix\n"
    verdict = validate_report(report, manifest)
    assert verdict.ok is False
    assert any("street-address" in p for p in verdict.problems)


# --- end-to-end CLI -------------------------------------------------------------


def _write_manifest_and_register(
    tmp_path: Path, ids: list[str], statuses: dict[str, str] | None = None
) -> tuple[Path, Path, Path]:
    per_id = {rid: {"status": status} for rid, status in (statuses or {}).items()}
    manifest = _full_manifest(tmp_path, ids, **per_id)
    phase_46_dirname = "DC-46-robustesse-certification-finale"
    manifest_dir = tmp_path / ".planning" / "phases" / phase_46_dirname
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "46-CERTIFICATION-MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    requirements_path = tmp_path / ".planning" / "REQUIREMENTS.md"
    requirements_path.write_text(_requirements_md_text(ids), encoding="utf-8")

    register_path = manifest_dir / "46-P0-P1-REGISTER.md"
    register_path.write_text("# P0/P1\n\nAucun blocker.\n", encoding="utf-8")

    return manifest_path, requirements_path, register_path


def test_main_validate_full_green_flow_exits_zero(tmp_path: Path) -> None:
    manifest_path, requirements_path, register_path = _write_manifest_and_register(
        tmp_path, TWENTY_TWO_IDS
    )
    rc = main(
        [
            "--validate",
            str(manifest_path),
            "--p0-p1-register",
            str(register_path),
            "--requirements",
            str(requirements_path),
        ]
    )
    assert rc == 0


def test_main_validate_one_fail_status_exits_nonzero(tmp_path: Path) -> None:
    manifest_path, requirements_path, register_path = _write_manifest_and_register(
        tmp_path, TWENTY_TWO_IDS, statuses={TWENTY_TWO_IDS[0]: "FAIL"}
    )
    rc = main(
        [
            "--validate",
            str(manifest_path),
            "--p0-p1-register",
            str(register_path),
            "--requirements",
            str(requirements_path),
        ]
    )
    assert rc != 0


def test_main_preflight_missing_dir_exits_nonzero(tmp_path: Path) -> None:
    rc = main(["--preflight", str(tmp_path / "does-not-exist")])
    assert rc != 0


def test_main_preflight_complete_tree_exits_zero(tmp_path: Path) -> None:
    p46 = _build_complete_fixture_tree(tmp_path)
    rc = main(["--preflight", str(p46)])
    assert rc == 0


def test_main_requires_preflight_or_validate() -> None:
    rc = main([])
    assert rc != 0

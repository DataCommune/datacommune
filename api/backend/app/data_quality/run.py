"""Audit entry point — generic orchestrator (default) + legacy auditors (--legacy).

Modes:
    Generic (default):  iterates PROFILES, dispatches geo/volume/business checkers,
                        writes per-dataset reports + MATRIX.md, exits ≠0 on ERROR.
    Legacy (--legacy):  runs the 5 hand-written DatasetAuditor subclasses,
                        writes AUDIT-SUMMARY.md, always exits 0.

Usage:
    python -m app.data_quality.run                     # generic, all 21 datasets
    python -m app.data_quality.run demographie         # generic, one dataset
    python -m app.data_quality.run -o /tmp/dq                   # custom output dir
    python -m app.data_quality.run --legacy                     # legacy, all 5 auditors
    python -m app.data_quality.run --legacy demographie         # legacy, one auditor

Manual command (D-08):
    python -m app.data_quality.run -o <out_dir>

    Produces MATRIX.md + per-dataset reports in <out_dir> in <30 s (local DB).
    Default output dir: .planning/audit-dq
    Scheduler container: override via env DQ_OUT_DIR (default /tmp/audit-dq).
    Settings reference: Settings.dq_out_dir (read by the daily scheduler job).
"""

import argparse
import sys
from pathlib import Path

import psycopg

from app.config import settings
from app.data_quality.base import AuditResult, DatasetAuditor
from app.data_quality.checkers.ban import BanAuditor
from app.data_quality.checkers.batiments import BatimentsAuditor
from app.data_quality.checkers.demographie import DemographieAuditor
from app.data_quality.checkers.dpe import DpeAuditor
from app.data_quality.checkers.dvf import DvfAuditor
from app.data_quality.geo_bounds import audit_geo_bounds
from app.data_quality.profiles import (
    PROFILES,
    DatasetProfile,
    check_duplicates,
    check_format,
    check_freshness,
    check_integrity,
    check_nulls,
    check_temporal,
    check_volume,
)
from app.data_quality.report import write_matrix, write_report, write_summary
from app.scope import InstanceScope, load_scope_sync

# D-04: 5 hand-written auditors preserved behind --legacy, never part of default run.
AUDITORS: dict[str, type[DatasetAuditor]] = {
    "demographie": DemographieAuditor,
    "dpe": DpeAuditor,
    "dvf": DvfAuditor,
    "ban": BanAuditor,
    "batiments": BatimentsAuditor,
}


# NOTE: _sync_dsn() below is dead code — duplicates get_sync_dsn() from base.py (C1).
# Signalled per surgical rule: do NOT remove until a dedicated cleanup task exists.
def _sync_dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _audit_profile(  # noqa: C901
    conn: psycopg.Connection, profile: DatasetProfile, scope: InstanceScope
) -> tuple[AuditResult, list[str]]:
    """Dispatch to applicable dimension checkers; return merged AuditResult + dims_run.

    D-05: calls audit_geo_bounds() the function directly — never
    the geo_bounds module's main.
    Pitfall 1: only overwrites row_count from volume if geo did not set it (== 0).
    Pitfall 2: dimensions_run is populated by the orchestrator, not by the checkers.
    """
    result = AuditResult(dataset=profile.dataset, table=profile.table, row_count=0)
    dims_run: list[str] = []

    if profile.geo is not None:
        geo_result = audit_geo_bounds(conn, profile.geo)
        result.row_count = geo_result.row_count
        result.findings.extend(geo_result.findings)
        result.stats.update(geo_result.stats)
        dims_run.append("geo")

    if profile.volume is not None:
        row_count, vol_findings = check_volume(conn, profile, scope)
        # Pitfall 1: do not overwrite row_count already set by geo
        if result.row_count == 0:
            result.row_count = row_count
        result.findings.extend(vol_findings)
        dims_run.append("volume")

    if profile.freshness is not None:
        result.findings.extend(check_freshness(conn, profile))
        dims_run.append("freshness")

    if profile.nulls is not None:
        result.findings.extend(check_nulls(conn, profile, stats=result.stats))
        dims_run.append("nulls")

    if profile.temporal is not None:
        result.findings.extend(check_temporal(conn, profile))
        dims_run.append("temporal")

    if profile.duplicates is not None:
        result.findings.extend(check_duplicates(conn, profile))
        dims_run.append("duplicates")

    if profile.format_rules:  # empty tuple is falsy — N/A
        result.findings.extend(check_format(conn, profile))
        dims_run.append("format")

    if profile.integrity is not None:
        result.findings.extend(check_integrity(conn, profile))
        dims_run.append("integrity")

    if profile.business_hook is not None:
        result.findings.extend(profile.business_hook(conn, profile))
        dims_run.append("business")

    return result, dims_run


def _run_generic(args: argparse.Namespace) -> int:
    """Generic mode: iterate PROFILES, dispatch dimensions, write MATRIX.md.

    Returns 1 if any pilot has ≥1 ERROR finding, 0 otherwise (D-03/DQ-CORE-04).
    Returns 2 for unknown dataset arguments (validation gate — T-23-05).
    """
    targets: list[str] = args.datasets or list(PROFILES)
    unknown = [d for d in targets if d not in PROFILES]
    if unknown:
        print(f"Unknown profiles: {unknown}. Known: {list(PROFILES)}")
        return 2

    out_dir = Path(args.out)
    dsn = _sync_dsn()
    results: list[AuditResult] = []

    with psycopg.connect(dsn) as conn:
        conn.set_read_only(True)  # T-23-06: read-only connection
        # Volume floors follow the instance scope in the database (D-21).
        scope = load_scope_sync(conn)
        if scope.is_empty:
            # No departement hosted: every floor would be 0, every table pass.
            print("[dq] périmètre vide: aucun département hébergé, audit refusé")
            return 2
        for name in targets:
            print(f"[dq] auditing {name}...", flush=True)
            profile = PROFILES[name]
            result, dims_run = _audit_profile(conn, profile, scope)
            result.dimensions_run = dims_run  # Pitfall 2: set BEFORE write_report
            path = write_report(result, out_dir)
            print(
                f"  -> {len(result.errors)} ERR, "
                f"{len(result.warnings)} WARN "
                f"[{', '.join(dims_run)}] -> {path}"
            )
            results.append(result)

    matrix_path = write_matrix(results, out_dir)
    print(f"[dq] matrix written to {matrix_path}")
    # D-03/D-05: exit ≠0 on rouge — copied from geo_bounds.py:347
    return 1 if any(r.errors for r in results) else 0


def _run_legacy(args: argparse.Namespace) -> int:
    """Legacy mode: run the 5 hand-written auditors (D-04).

    Behaviour is identical to the original main() — always returns 0.
    Output goes to .planning/audit-datasets/ by default (different from generic).
    """
    targets: list[str] = args.datasets or list(AUDITORS.keys())
    unknown = [d for d in targets if d not in AUDITORS]
    if unknown:
        print(f"Unknown auditors: {unknown}. Known: {list(AUDITORS.keys())}")
        return 2

    out_dir = Path(args.out)
    dsn = _sync_dsn()
    results: list[AuditResult] = []

    with psycopg.connect(dsn) as conn:
        conn.set_read_only(True)
        for name in targets:
            print(f"[audit] running {name}...", flush=True)
            auditor = AUDITORS[name]()
            result = auditor.run(conn)
            path = write_report(result, out_dir)
            print(
                f"  -> {len(result.errors)} ERR, "
                f"{len(result.warnings)} WARN, "
                f"{len(result.infos)} INFO -> {path}"
            )
            results.append(result)

    summary_path = write_summary(results, out_dir)
    print(f"[audit] summary written to {summary_path}")
    return 0  # legacy: always 0 (original behaviour preserved)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "DataCommune data-quality audit. "
            "Default: generic mode (PROFILES → matrix + exit code). "
            "Use --legacy for hand-written auditors."
        )
    )
    parser.add_argument(
        "datasets",
        nargs="*",
        help="Datasets to audit (default: all registered for the selected mode).",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=".planning/audit-dq",
        help=(
            "Output directory for reports "
            "(generic default: .planning/audit-dq; "
            "legacy default: use --legacy -o .planning/audit-datasets)."
        ),
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Run hand-written auditors (ban, batiments, demographie, dpe, dvf).",
    )
    args = parser.parse_args()

    if args.legacy:
        # Legacy mode keeps its own default output dir for backward compat (pitfall 4)
        if args.out == ".planning/audit-dq":
            args.out = ".planning/audit-datasets"
        return _run_legacy(args)

    return _run_generic(args)


if __name__ == "__main__":
    sys.exit(main())

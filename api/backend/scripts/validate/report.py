"""Console and JSON reporting for validation results."""

from __future__ import annotations

import json
from dataclasses import asdict

from .base import ValidationReport


def print_reports(reports: list[ValidationReport], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([asdict(r) for r in reports], indent=2, ensure_ascii=False))
        return

    for report in reports:
        print(f"\n{'=' * 60}")
        print(f"  Dataset : {report.dataset}")
        print(f"  Echantillon : {report.sample_size}")
        print(f"{'=' * 60}")
        print(
            f"  Concordances : {report.ok}  |  "
            f"Divergences : {report.divergences}  |  "
            f"Non trouves : {report.not_found}  |  "
            f"Erreurs API : {report.api_errors}"
        )

        for row in report.rows:
            if row.status == "ok":
                continue
            print(f"\n  [{row.status.upper()}] id={row.row_id}")
            if row.error:
                print(f"    Erreur : {row.error}")
            for f in row.fields:
                if not f.match:
                    print(f"    {f.name} : {f.detail}")

    # Summary
    total_ok = sum(r.ok for r in reports)
    total_div = sum(r.divergences for r in reports)
    total_nf = sum(r.not_found for r in reports)
    total_err = sum(r.api_errors for r in reports)
    total = total_ok + total_div + total_nf + total_err

    print(f"\n{'=' * 60}")
    print("  BILAN GLOBAL")
    print(f"{'=' * 60}")
    print(f"  Total : {total}")
    print(f"  Concordances : {total_ok} ({total_ok * 100 // max(total, 1)}%)")
    print(f"  Divergences  : {total_div}")
    print(f"  Non trouves  : {total_nf}")
    print(f"  Erreurs API  : {total_err}")
    print()

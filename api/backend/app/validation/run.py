"""Fail-closed CLI with one durable verdict per requested control."""

import json
import logging
import sys

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)


def main() -> int:  # noqa: C901
    from app.config import settings
    from app.validation.base import registry
    from app.validation.drift import detect_drift
    from app.validation.validators import ban, cadastre, dpe, dvf  # noqa: F401

    logging.basicConfig(level=logging.INFO)
    console = Console()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    names = sys.argv[1:] or registry.list_validators()
    checks = []
    if not names:
        checks.append(
            dict(
                dataset="registry",
                control="selection",
                status="not_checked",
                required=True,
                notes="No validators registered",
            )
        )
    for name in names:
        comparison = dict(
            dataset=name,
            control="source_comparison",
            status="not_checked",
            required=True,
        )
        drift_check = dict(
            dataset=name, control="drift", status="not_checked", required=True
        )
        checks.extend([comparison, drift_check])
        cls = registry.get(name)
        if cls is None:
            comparison["notes"] = "Unknown validator"
            continue
        try:
            result = cls().run(dsn)
            comparison.update(
                status=result.status,
                local_row_count=result.local_row_count,
                source_row_count=result.source_row_count,
                discrepancy_pct=result.discrepancy_pct,
                notes=result.notes,
            )
            if result.status == "not_applicable":
                comparison["required"] = False
        except Exception as exc:
            comparison.update(status="unavailable", notes=type(exc).__name__)
            logger.exception("Validator '%s' failed", name)
            continue
        try:
            drift = detect_drift(dsn, name, detailed=True)
            if drift is None:
                drift_check.update(
                    status="not_checked", notes="No structured drift verdict"
                )
            else:
                drift_check.update(drift)
                if drift["status"] == "not_applicable":
                    drift_check["required"] = False
        except Exception as exc:
            drift_check.update(status="unavailable", notes=type(exc).__name__)
            logger.exception("Drift '%s' unavailable", name)

    table = Table(title="Validation Results")
    for title in ("Dataset", "Control", "Status", "Required"):
        table.add_column(title)
    for check in checks:
        table.add_row(
            check["dataset"], check["control"], check["status"], str(check["required"])
        )
    console.print(table)
    failed = any(c["required"] and c["status"] != "passed" for c in checks)
    # A registry with only optional, inapplicable controls is not a validation.
    for name in names:
        if not any(c["dataset"] == name and c["status"] == "passed" for c in checks):
            failed = True
    summary = dict(
        expected=len(checks),
        completed=sum(
            c["status"] in ("passed", "failed", "not_applicable") for c in checks
        ),
        checks=checks,
        passed=not failed,
    )
    print("VALIDATION_SUMMARY=" + json.dumps(summary, ensure_ascii=True))
    return 1 if failed else 0

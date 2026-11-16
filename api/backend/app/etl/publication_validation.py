"""Required publication NULL policy, preserving optional-column WARN semantics."""

import json
from dataclasses import replace

import psycopg

from app.data_quality.profiles import PROFILES, DatasetProfile, NullSpec, check_nulls
from app.etl.validation import persist_validation_run


def validate_publication(dsn, dataset, table, *, row_count_before, row_count_after):
    profile = PROFILES.get(dataset)
    if profile is None or profile.table != table:
        profile = DatasetProfile(dataset=dataset, table=table)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.set_read_only(True)
        if profile.nulls is None:
            columns = conn.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid=to_regclass(%s) "
                "AND attnum>0 AND NOT attisdropped AND attnotnull ORDER BY attnum",
                (table,),
            ).fetchall()
            # No guessed domain constraint: only enforced DDL requirements.
            profile = replace(profile, nulls=NullSpec(tuple(c[0] for c in columns), ()))
        stats = {}
        findings = check_nulls(conn, profile, stats=stats)
    passed = not any(f.severity == "ERROR" for f in findings)
    # Persist the exact stock denominator and a bounded, payload-free observation.
    run_id = persist_validation_run(
        dsn,
        dataset,
        row_count_before=row_count_before,
        row_count_after=row_count_after,
        null_rate_findings=[
            {"severity": f.severity, "category": f.category, "count": f.count}
            for f in findings
        ],
        passed=passed,
        blocked_swap=False,
        notes=json.dumps(
            {"dimensions": ["declared_or_ddl_required_nulls"], "observation": stats}
        ),
    )
    if not passed:
        raise RuntimeError("Required publication validation rejected the stock")
    return run_id

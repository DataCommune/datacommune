"""IN-06 (review 04.4): the DVF validator compares ``mutations`` with the lines
read by the published run, without tolerance.

Since D-01 no source line is dropped, so a loss of 14 % of the lines, which
the former 15 % threshold against the tabular API let pass, must fail. The
source is ``total_rows_loaded`` of the latest successful run: a run still
running or a failed run is never taken as the source. The stages are those a
real run journals (work base, run 52, 2026-10-22): no swap step among them.
Minimal tables in an owned child database (``etl_runs`` of migration 013, a
one-column ``mutations``).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_validator_dvf.py
"""

import json
from unittest.mock import patch

import psycopg
import pytest

from app.validation.validators.dvf import DvfValidator

# Stage names journaled by the real run 52 (no swap nor publication step).
ETAPES = [
    {"stage_name": name, "rows_affected": rows, "success": True, "error": None}
    for name, rows in (
        ("restore_sources", None),
        ("create_staging", None),
        ("staging_stock", 100),
        ("candidate_stock", 100),
        ("validate_data", None),
    )
]


@pytest.fixture(scope="module")
def base(isolated_pg_dsn):
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("""
            CREATE TABLE etl_runs (
                id BIGSERIAL PRIMARY KEY,
                dataset_name VARCHAR(50) NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                total_rows_loaded BIGINT DEFAULT 0,
                stages JSONB
            )
        """)
        conn.execute("CREATE TABLE mutations (id BIGINT)")
    return isolated_pg_dsn


def _etat(dsn: str, lignes: int, runs: list[tuple[str, str, int, list]]) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE etl_runs, mutations")
        conn.execute("INSERT INTO mutations SELECT generate_series(1, %s)", (lignes,))
        for i, (dataset, status, total, stages) in enumerate(runs):
            conn.execute(
                "INSERT INTO etl_runs (dataset_name, status, total_rows_loaded, "
                "stages, completed_at) VALUES (%s, %s, %s, %s::jsonb, "
                "'2026-10-22'::timestamptz + %s * interval '1 hour')",
                (dataset, status, total, json.dumps(stages), i),
            )


def _valider(dsn: str):
    with patch("app.etl.validation.persist_validation_run"):
        return DvfValidator().run(dsn)


PUBLIE = ("dvf", "success", 100, ETAPES)


def test_every_line_of_the_published_run_served_passes(base):
    _etat(base, 100, [PUBLIE])

    result = _valider(base)

    assert (result.passed, result.source_row_count, result.local_row_count) == (
        True,
        100,
        100,
    )


def test_a_loss_of_fourteen_percent_fails(base):
    """Passed under the former 15 % threshold."""
    _etat(base, 86, [PUBLIE])

    result = _valider(base)

    assert result.passed is False
    assert result.status == "failed"


def test_one_line_missing_fails(base):
    _etat(base, 99, [PUBLIE])

    assert _valider(base).passed is False


def test_later_runs_without_publication_are_not_the_source(base):
    _etat(
        base,
        100,
        [
            PUBLIE,
            ("dvf", "running", 0, []),
            ("dvf", "failed", 40, ETAPES),
            ("dpe", "success", 7, ETAPES),
        ],
    )

    result = _valider(base)

    assert (result.passed, result.source_row_count) == (True, 100)


def test_without_a_published_run_the_comparison_is_unavailable(base):
    _etat(base, 100, [("dvf", "failed", 100, ETAPES), ("dvf", "running", 0, [])])

    result = _valider(base)

    assert result.passed is False
    assert result.status == "unavailable"

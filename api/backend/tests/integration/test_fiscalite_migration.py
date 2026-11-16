"""Fiscalite migration DB entry point (Phase 43, Plans 02+04).

Proves the disposable PostGIS harness can run the FULL Alembic chain from an
empty database to `head` — not just resolve the head revision (that was
covered by 43-01's test_postgis_harness.py) — and (Plan 43-04) that the
read-only preflight (`scripts/preflight_semantic_contracts.py`) and the
fiscalite percent-points migration (073_semantic_contracts) enforce the same
fail-closed invariants (T-43-03/T-43-04) before/at mutation time.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/run_contract_integration.ps1 \
        tests/integration/test_fiscalite_migration.py
"""

from __future__ import annotations

import re

import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.commune.matview_ddl import DROP_SQL
from scripts.preflight_semantic_contracts import (
    RATE_COLUMNS,
    PreflightReport,
    run_preflight,
)
from scripts.preflight_semantic_contracts import (
    main as preflight_main,
)

# Revision right before 073_semantic_contracts — the ratio-state schema
# (NUMERIC(6,5)) the preflight/migration preconditions are exercised against.
_PRE_073_REVISION = "072_mutations_idx"


def _restore_pre_073_schema(alembic_config: AlembicConfig, pg_dsn: str) -> None:
    """Put the shared harness on 072, regardless of the previous test module.

    The full integration suite also exercises migrations that upgrade the
    session-scoped database to ``head``.  ``command.upgrade(..., 072)`` is a
    no-op from that state, so relying on file ordering made these tests fail
    when the whole directory was collected.  Read the actual revision and
    move in the required direction explicitly.
    """
    with (
        psycopg.connect(pg_dsn, connect_timeout=5) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT to_regclass('public.alembic_version')")
        version_table = cur.fetchone()[0]
        current_revision = None
        if version_table is not None:
            cur.execute("SELECT version_num FROM alembic_version")
            row = cur.fetchone()
            current_revision = row[0] if row else None

    if current_revision is None:
        command.upgrade(alembic_config, _PRE_073_REVISION)
    elif current_revision != _PRE_073_REVISION:
        command.downgrade(alembic_config, _PRE_073_REVISION)


def _insert_fiscalite_row(
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    *,
    code_insee: str,
    annee: int,
    taux_tfpb: float,
    taux_tfpnb: float,
    taux_th_rs: float,
    taux_teom: float,
) -> None:
    """Insert + commit a synthetic fiscalite_locale row.

    Committed (not left in pg_conn's uncommitted transaction) because
    run_preflight() opens its own separate connection — only committed rows
    are visible across connections in PostgreSQL's MVCC.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO fiscalite_locale "
            "(code_insee, nom_commune, code_departement, annee, "
            " taux_tfpb, taux_tfpnb, taux_th_rs, taux_teom) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                code_insee,
                "Commune synthetique",
                code_insee[:2],
                annee,
                taux_tfpb,
                taux_tfpnb,
                taux_th_rs,
                taux_teom,
            ),
        )
    pg_conn.commit()


def _delete_fiscalite_rows(
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    code_insee_values: tuple[str, ...],
) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM fiscalite_locale WHERE code_insee = ANY(%s)",
            (list(code_insee_values),),
        )
    pg_conn.commit()


# v_commune_synthese depends on fiscalite_locale, so ALTER COLUMN TYPE needs the
# matview gone, then back. It is restored from the definition captured right
# before the DROP — NOT from app.commune.matview_ddl.CREATE_SQL. That runtime
# DDL describes the matview at `head` (since 57-09 it reads columns of
# v_commune_dvf that only migration 088 creates), while this harness sits at
# 072_mutations_idx: replaying head DDL on a pre-088 schema is what turned
# ci-data-contracts red on 2026-08-25 (`column dvf.prix_m2_median_maison does
# not exist`). Capturing the live definition keeps the fixture correct for
# whatever the 072-era matview looks like, now and after the next migration.
_SYNTHESE_DDL: dict[str, str | list[str]] = {}


def _set_tfpb_storage(
    pg_conn: psycopg.Connection,  # type: ignore[type-arg]
    *,
    points: bool,
) -> None:
    """Create or restore a structurally mixed type state in the disposable DB."""
    with pg_conn.cursor() as cur:
        if points:
            cur.execute("SELECT pg_get_viewdef('v_commune_synthese'::regclass, true)")
            row = cur.fetchone()
            assert row is not None and row[0], "v_commune_synthese absent avant DROP"
            _SYNTHESE_DDL["viewdef"] = str(row[0]).rstrip().rstrip(";")
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'v_commune_synthese' ORDER BY indexname"
            )
            _SYNTHESE_DDL["indexes"] = [r[0] for r in cur.fetchall()]
            cur.execute(DROP_SQL)
            cur.execute(
                "ALTER TABLE fiscalite_locale ALTER COLUMN taux_tfpb "
                "TYPE NUMERIC(5,2) USING ROUND(taux_tfpb * 100, 2)"
            )
        else:
            cur.execute(
                "ALTER TABLE fiscalite_locale ALTER COLUMN taux_tfpb "
                "TYPE NUMERIC(6,5) USING ROUND(taux_tfpb / 100, 5)"
            )
            viewdef = _SYNTHESE_DDL.get("viewdef")
            assert isinstance(viewdef, str), (
                "restauration demandée sans capture préalable (points=True d'abord)"
            )
            cur.execute(f"CREATE MATERIALIZED VIEW v_commune_synthese AS {viewdef}")
            for sql in _SYNTHESE_DDL.get("indexes", []):
                cur.execute(sql)
    pg_conn.commit()


# -- Preflight (Plan 43-04, task 1) -------------------------------------------
#
# scripts/preflight_semantic_contracts.py is exercised against the ratio-state
# schema (pre-073) with synthetic rows — never against real data. Every test
# below commits its own rows (run_preflight opens a separate connection, so
# uncommitted pg_conn writes would be invisible to it) and deletes them in a
# `finally` block so tests remain independent of each other.
#
# Ordering note: pg_dsn/the disposable container are shared (session-scoped)
# across every test in this file, and Alembic only ever moves forward
# (there is no automatic downgrade). Every test below that needs the
# pre-073 (ratio, NUMERIC(6,5)) schema is therefore placed BEFORE the
# "full chain to head" tests further down — once any test upgrades to
# head, the DB stays at head for the rest of the session.


@pytest.fixture
def _pre_073_schema(alembic_config: AlembicConfig, pg_dsn: str) -> None:
    """Restore the ratio-state schema (NUMERIC(6,5)) for each test."""
    _restore_pre_073_schema(alembic_config, pg_dsn)


def test_preflight_accepts_homogeneous_ratio_state(
    _pre_073_schema: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=1.0689,
        taux_tfpnb=5.5116,
        taux_th_rs=0.7197,
        taux_teom=0.73,
    )
    try:
        report = run_preflight(pg_dsn)
        assert report.ok, report.reasons
        assert report.distribution_state == "ratio"
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


def test_preflight_rejects_mixed_storage_types_even_when_empty(
    _pre_073_schema: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    _set_tfpb_storage(pg_conn, points=True)
    try:
        report = run_preflight(pg_dsn)
        assert not report.ok
        assert report.distribution_state == "mixed_types"
        assert any("types de stockage mixtes" in reason for reason in report.reasons)
    finally:
        _set_tfpb_storage(pg_conn, points=False)


def test_preflight_rejects_target_overflow(
    _pre_073_schema: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=0.4001,
        taux_tfpnb=9.99999,
        taux_th_rs=0.0,
        taux_teom=0.0987,
    )
    try:
        report = run_preflight(pg_dsn)
        assert not report.ok
        assert report.distribution_state == "invalid"
        assert report.counts["taux_tfpnb"]["target_overflow"] == 1
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


def test_preflight_rejects_negative_rate(
    _pre_073_schema: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=-0.0001,
        taux_tfpnb=0.5516,
        taux_th_rs=0.7197,
        taux_teom=0.73,
    )
    try:
        report = run_preflight(pg_dsn)
        assert not report.ok
        assert report.distribution_state == "invalid"
        assert report.counts["taux_tfpb"]["negative"] == 1
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


def test_preflight_rejects_destructive_precision_loss(
    _pre_073_schema: None, pg_conn: psycopg.Connection, pg_dsn: str
) -> None:
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=0.12345,
        taux_tfpnb=0.5516,
        taux_th_rs=0.7197,
        taux_teom=0.73,
    )
    try:
        report = run_preflight(pg_dsn)
        assert not report.ok
        assert report.distribution_state == "invalid"
        assert report.counts["taux_tfpb"]["target_precision_loss"] == 1
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


def test_preflight_report_includes_observed_type_precision_and_scale(
    _pre_073_schema: None, pg_dsn: str
) -> None:
    report = run_preflight(pg_dsn)
    assert set(report.column_types.keys()) == set(RATE_COLUMNS)
    for data_type, precision, scale in report.column_types.values():
        assert data_type == "numeric"
        assert (precision, scale) == (6, 5)


def test_preflight_never_issues_a_write_statement(
    _pre_073_schema: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI is SELECT-only: spy on every cursor.execute() it issues and
    assert none of them is a write statement."""
    executed: list[str] = []
    original_execute = psycopg.Cursor.execute

    def _spy_execute(
        self: psycopg.Cursor,  # type: ignore[type-arg]
        query: object,
        *args: object,
        **kwargs: object,
    ):
        executed.append(query if isinstance(query, str) else str(query))
        return original_execute(self, query, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(psycopg.Cursor, "execute", _spy_execute)

    run_preflight(pg_dsn)

    assert executed, "preflight n'a exécuté aucune requête"
    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE)\b", re.IGNORECASE
    )
    violations = [q for q in executed if forbidden.search(q)]
    assert not violations, f"preflight a émis des instructions d'écriture: {violations}"


def test_preflight_cli_reads_dsn_from_named_environment_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    synthetic_dsn = "postgresql://synthetic-user:synthetic-pass@example.invalid/db"
    observed: list[str] = []

    def fake_preflight(dsn: str) -> PreflightReport:
        observed.append(dsn)
        return PreflightReport(ok=True)

    monkeypatch.setenv("TEST_CONTRACT_DSN", synthetic_dsn)
    monkeypatch.setattr(
        "scripts.preflight_semantic_contracts.run_preflight", fake_preflight
    )

    assert preflight_main(["--dsn-env", "TEST_CONTRACT_DSN"]) == 0
    captured = capsys.readouterr()
    assert observed == [synthetic_dsn]
    assert synthetic_dsn not in captured.out
    assert synthetic_dsn not in captured.err


# -- Migration 073_semantic_contracts (Plan 43-04, task 2) -------------------
#
# Same ordering constraint as the preflight block above: these tests need
# the pre-073 (ratio) schema, so they must run before anything that upgrades
# to `head`. Within this block, the blocking (mixed-state) test runs FIRST
# because it is the only one that leaves the DB at 072 afterwards -- its
# migration attempt fails and rolls back. The successful-conversion test
# runs LAST because it genuinely advances the DB to head.


def test_migration_blocks_on_mixed_storage_types_before_any_mutation(
    _pre_073_schema: None, alembic_config: AlembicConfig, pg_conn: psycopg.Connection
) -> None:
    """One target column among legacy columns is a certain partial migration."""
    _set_tfpb_storage(pg_conn, points=True)
    try:
        with pytest.raises(RuntimeError, match="types de stockage mixtes"):
            command.upgrade(alembic_config, "head")

        with pg_conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            (version,) = cur.fetchone()

            cur.execute(
                "SELECT column_name, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'fiscalite_locale' "
                "AND column_name = ANY(%s)",
                (list(RATE_COLUMNS),),
            )
            types = {name: (precision, scale) for name, precision, scale in cur}

            cur.execute(
                "SELECT count(*) FROM pg_matviews "
                "WHERE matviewname = 'v_commune_synthese'"
            )
            (matview_count,) = cur.fetchone()

        assert version == _PRE_073_REVISION
        assert types["taux_tfpb"] == (5, 2)
        assert {types[col] for col in RATE_COLUMNS if col != "taux_tfpb"} == {(6, 5)}
        assert matview_count == 0
    finally:
        _set_tfpb_storage(pg_conn, points=False)


def test_migration_blocks_target_overflow_before_any_mutation(
    _pre_073_schema: None, alembic_config: AlembicConfig, pg_conn: psycopg.Connection
) -> None:
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=0.4001,
        taux_tfpnb=9.99999,
        taux_th_rs=0.0,
        taux_teom=0.0987,
    )
    try:
        with pytest.raises(RuntimeError, match="representable"):
            command.upgrade(alembic_config, "head")

        with pg_conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            (version,) = cur.fetchone()

            cur.execute(
                "SELECT numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'fiscalite_locale' AND column_name = 'taux_tfpb'"
            )
            precision, scale = cur.fetchone()

            cur.execute(
                "SELECT count(*) FROM fiscalite_locale WHERE code_insee = '44109'"
            )
            (row_count,) = cur.fetchone()

        assert version == _PRE_073_REVISION
        assert (precision, scale) == (6, 5)
        assert row_count == 1
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


@pytest.mark.parametrize(
    ("taux_tfpb", "error_match"),
    [
        (-0.0001, "negative"),
        (0.12345, "exactement"),
    ],
)
def test_migration_blocks_invalid_rate_before_any_mutation(
    _pre_073_schema: None,
    alembic_config: AlembicConfig,
    pg_conn: psycopg.Connection,
    taux_tfpb: float,
    error_match: str,
) -> None:
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=taux_tfpb,
        taux_tfpnb=0.5516,
        taux_th_rs=0.7197,
        taux_teom=0.73,
    )
    try:
        with pytest.raises(RuntimeError, match=error_match):
            command.upgrade(alembic_config, "head")

        with pg_conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            (version,) = cur.fetchone()
            cur.execute(
                "SELECT numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'fiscalite_locale' "
                "AND column_name = 'taux_tfpb'"
            )
            precision_scale = cur.fetchone()

        assert version == _PRE_073_REVISION
        assert precision_scale == (6, 5)
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


def test_migration_converts_high_legacy_rates_to_points_exactly_once(
    _pre_073_schema: None,
    alembic_config: AlembicConfig,
    pg_conn: psycopg.Connection,
    pg_dsn: str,
) -> None:
    """Observed-shape high legacy rates convert once without a false mixed verdict."""
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=1.0689,
        taux_tfpnb=5.5116,
        taux_th_rs=0.7197,
        taux_teom=0.73,
    )
    try:
        command.upgrade(alembic_config, "head")

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT taux_tfpb, taux_tfpnb, taux_th_rs, taux_teom "
                "FROM fiscalite_locale WHERE code_insee = %s",
                ("44109",),
            )
            taux_tfpb, taux_tfpnb, taux_th_rs, taux_teom = cur.fetchone()

            cur.execute(
                "SELECT numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'fiscalite_locale' AND column_name = 'taux_tfpb'"
            )
            precision, scale = cur.fetchone()

            cur.execute(
                "SELECT matviewname FROM pg_matviews "
                "WHERE matviewname = 'v_commune_synthese'"
            )
            matview_exists = cur.fetchone() is not None

            cur.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'v_commune_synthese' "
                "AND indexname = 'idx_commune_synthese_code_insee'"
            )
            index_exists = cur.fetchone() is not None

        assert float(taux_tfpb) == pytest.approx(106.89)
        assert float(taux_tfpnb) == pytest.approx(551.16)
        assert float(taux_th_rs) == pytest.approx(71.97)
        assert float(taux_teom) == pytest.approx(73.00)
        assert (precision, scale) == (5, 2)
        assert matview_exists, "v_commune_synthese absent après upgrade"
        assert index_exists, "index de v_commune_synthese absent après upgrade"
        report = run_preflight(pg_dsn)
        assert report.ok, report.reasons
        assert report.distribution_state == "points"
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))


# -- Full chain to head -------------------------------------------------------
#
# From here on, tests upgrade forward to `head` (which now includes
# 073_semantic_contracts) — placed after the preflight block above per the
# ordering note (Alembic never downgrades automatically within the shared
# session-scoped harness).


def test_alembic_upgrade_head_applies_cleanly(
    alembic_config: AlembicConfig, pg_dsn: str
) -> None:
    """The full migration chain reaches head on a fresh disposable DB."""
    command.upgrade(alembic_config, "head")

    script = ScriptDirectory.from_config(alembic_config)
    expected_head = script.get_current_head()

    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            (actual_head,) = cur.fetchone()
        assert actual_head == expected_head
    finally:
        conn.close()


def test_fiscalite_locale_table_exists_after_upgrade(
    alembic_config: AlembicConfig, pg_dsn: str
) -> None:
    """fiscalite_locale exists with its four rate columns after upgrade head.

    Baseline structural check (column existence only) -- type/precision and
    value-conversion postconditions are covered separately by
    test_migration_converts_high_legacy_rates_to_points_exactly_once above.
    """
    command.upgrade(alembic_config, "head")

    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'fiscalite_locale' "
                "AND column_name IN "
                "('taux_tfpb', 'taux_tfpnb', 'taux_th_rs', 'taux_teom')"
            )
            columns = {row[0] for row in cur.fetchall()}
        assert columns == {"taux_tfpb", "taux_tfpnb", "taux_th_rs", "taux_teom"}
    finally:
        conn.close()


def test_upgrade_head_is_idempotent_when_rerun(
    alembic_config: AlembicConfig, pg_dsn: str
) -> None:
    """Re-running `upgrade head` on an already-migrated DB is a clean no-op.

    Mirrors the no-op requirement D-02 places on the future fiscalite
    migration, proven here at the harness level before 43-04 adds the
    fiscalite-specific pre/postconditions.
    """
    command.upgrade(alembic_config, "head")
    command.upgrade(alembic_config, "head")  # must not raise

    script = ScriptDirectory.from_config(alembic_config)
    expected_head = script.get_current_head()

    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            (actual_head,) = cur.fetchone()
        assert actual_head == expected_head
    finally:
        conn.close()


def test_downgrade_converts_points_back_to_ratio(
    alembic_config: AlembicConfig, pg_conn: psycopg.Connection
) -> None:
    """Rollback test (T-43-04 mitigation): downgrading 073 converts a
    points-state row back to a fraction and rebuilds v_commune_synthese.

    Runs last -- the DB is already at head from the tests above (Alembic
    never downgrades automatically), so this is the one place in the file
    that legitimately calls `command.downgrade`.
    """
    # Do not depend on collection order: another test may have left the
    # shared database on the pre-073 revision.
    command.upgrade(alembic_config, "head")
    _insert_fiscalite_row(
        pg_conn,
        code_insee="44109",
        annee=2024,
        taux_tfpb=106.89,
        taux_tfpnb=551.16,
        taux_th_rs=71.97,
        taux_teom=73.0,
    )
    try:
        command.downgrade(alembic_config, _PRE_073_REVISION)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT taux_tfpb, taux_tfpnb, taux_th_rs, taux_teom "
                "FROM fiscalite_locale WHERE code_insee = %s",
                ("44109",),
            )
            taux_tfpb, taux_tfpnb, taux_th_rs, taux_teom = cur.fetchone()

            cur.execute(
                "SELECT numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'fiscalite_locale' AND column_name = 'taux_tfpb'"
            )
            precision, scale = cur.fetchone()

            cur.execute(
                "SELECT matviewname FROM pg_matviews "
                "WHERE matviewname = 'v_commune_synthese'"
            )
            matview_exists = cur.fetchone() is not None

        assert float(taux_tfpb) == pytest.approx(1.0689)
        assert float(taux_tfpnb) == pytest.approx(5.5116)
        assert float(taux_th_rs) == pytest.approx(0.7197)
        assert float(taux_teom) == pytest.approx(0.73)
        assert (precision, scale) == (6, 5)
        assert matview_exists, "v_commune_synthese absent après downgrade"
    finally:
        _delete_fiscalite_rows(pg_conn, ("44109",))
        # Restore head so this test leaves the shared harness in the same
        # forward state every other test in this file assumes.
        command.upgrade(alembic_config, "head")

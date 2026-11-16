"""Commune population and real post-swap rebuild in disposable PostGIS."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.common.matviews import recreate_missing_matviews
from app.commune import matview_ddl
from app.commune.dependencies import COMMUNE_SOURCES
from app.commune.queries import commune_synthese_par_codes
from app.config import settings
from app.data_quality.checkers.dpe import DpeAuditor
from app.etl.hooks import DependentMatviewHook
from app.etl.schemas import EtlResult
from app.scoring import matview_ddl as scoring_ddl
from tests.integration.publication_fixture import install_publication_journal


@pytest.fixture(scope="module")
def pg_dsn(isolated_pg_dsn):
    """Keep rebuild DROP lookups from resolving another suite's public views."""
    return isolated_pg_dsn


# All sources of the real commune/scoring DDL exist, even when empty. Each test
# owns an isolated schema; other integration files may own public source tables.
_TABLES = {
    "diagnostics_dpe": """
        numero_dpe text, code_insee text, etiquette_dpe text,
        annee_construction int, source_state text NOT NULL DEFAULT 'unknown',
        is_tombstone boolean NOT NULL DEFAULT false, regime_dpe text,
        date_fin_validite_dpe date, numero_dpe_remplace text,
        date_etablissement_dpe date, surface_habitable numeric,
        type_installation_chauffage text, code_postal text, code_departement text,
        position geometry(Point,4326)
    """,
    # 04.7-09: the DVF view reads the rows of a sale spread over several
    # communes, to keep one spread over the arrondissements of a city.
    "mutations_grain": """
        code_departement text, id_mutation text,
        code_commune text, date_mutation date, valeur_fonciere numeric,
        surface_bati_totale numeric, classe_mutation text, est_vente boolean,
        hors_stats_vefa boolean, nb_communes int
    """,
    "mutations": """
        code_departement text, id_mutation text, code_commune text,
        id_parcelle text, date_mutation date, valeur_fonciere numeric,
        adresse_numero text, adresse_nom_voie text
    """,
    # 04.7-10: the departement view, rebuilt with the chain, reads the
    # membership of a code in the referential.
    "geo_communes": "code text, departement_code text",
    "parcelles": """
        id_parcelle text, commune text, contenance numeric,
        geometrie geometry(MultiPolygon,4326)
    """,
    "demographie": """
        code_insee text, nom_commune text, code_departement text, code_region text,
        population int, nb_menages int, nb_logements int, nb_logements_vacants int,
        densite numeric, superficie numeric, annee int
    """,
    "revenus": """
        code_insee text, nom_departement text, nom_region text,
        revenu_median numeric, taux_pauvrete numeric, indice_gini numeric,
        part_imposee numeric, annee int
    """,
    "zonage_abc": "code_insee text, zone text, reclassement boolean",
    "vacance": """
        code_commune text, nom_departement text, nom_region text,
        taux_vacance numeric, nb_vacants_plus_2ans int, annee int,
        nb_logements_prives int, nb_logements_vacants int
    """,
    "fiscalite_locale": """
        code_insee text, nom_departement text, nom_region text,
        taux_tfpb numeric, taux_th_rs numeric, taux_teom numeric,
        nom_epci text, annee int
    """,
    "indicateurs_loyers": """
        code_insee text, type_habitat text, loyer_m2 numeric, annee int
    """,
    "equipements": "code_commune text, nb_equipements int, annee int",
    "carte_scolaire": "code_insee text, code_rne text",
    "monuments_historiques": "code_insee text, reference text",
    "foncier_pm": """
        code_commune text, numero_siren text, code_parcelle text,
        contenance_parcelle numeric
    """,
    # One row per building since plan 04.1-16, offers in the dictionary.
    "eligibilite_fixe": "code_insee text, id_imb text, offres smallint[]",
    "eligibilite_offres": """
        code smallint, operateur text, technologie text, debit_max text
    """,
    "matview_metadata": """
        matview_name text PRIMARY KEY, last_refresh_at timestamptz,
        last_refresh_duration_seconds float, last_refresh_status text,
        last_error text
    """,
}


@pytest.fixture
def commune_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    schema = "consumer_" + uuid4().hex
    connect = psycopg.connect
    with connect(pg_dsn, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        admin.execute(f"CREATE SCHEMA {schema}")
    options = f"-c search_path={schema},public -c statement_timeout=15000"
    conn = connect(pg_dsn, options=options, autocommit=True)
    try:
        for name, columns in _TABLES.items():
            conn.execute(f"CREATE TABLE {name} ({columns})")
        install_publication_journal(conn)
        conn.execute("""
            INSERT INTO demographie (code_insee, code_departement, annee)
            VALUES ('44999', '44', 2026), ('85999', '85', 2026)
        """)
        monkeypatch.setattr(settings, "database_url", pg_dsn)
        # Keep real independent hook connections, redirect only their schema.
        monkeypatch.setattr(
            psycopg, "connect", lambda dsn: connect(dsn, options=options)
        )
        yield conn
    finally:
        conn.close()
        with connect(pg_dsn, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def _hook() -> None:
    DependentMatviewHook().on_run_complete(
        "dpe",
        EtlResult(
            dataset_name="dpe",
            stages=[],
            total_rows_loaded=8,
            total_duration_seconds=0,
            success=True,
        ),
    )


def _seed(conn: psycopg.Connection, table: str = "diagnostics_dpe") -> None:
    assert table in {"diagnostics_dpe", "diagnostics_dpe_new"}
    conn.execute(f"""
        INSERT INTO {table} (
            numero_dpe, code_insee, etiquette_dpe, annee_construction,
            source_state, is_tombstone, regime_dpe, date_fin_validite_dpe,
            numero_dpe_remplace
        ) VALUES
        ('present-existing', '44999', 'A', 2000, 'present', false,
         'existant', '2035-01-01', NULL),
        ('present-new', '44999', 'F', 2002, 'present', false,
         'neuf', '2035-01-01', NULL),
        ('legacy', '44999', 'B', 2004, 'unknown', false, NULL, NULL, NULL),
        ('expired', '44999', 'G', 2006, 'present', false,
         'existant', '2020-01-01', NULL),
        ('replacement', '44999', 'C', 2008, 'present', false,
         'neuf', '2035-01-01', 'present-existing'),
        ('unknown-label', '44999', NULL, 2010, 'present', false,
         'neuf', '2035-01-01', NULL),
        ('tombstone', '44999', 'G', 1900, 'unknown', true,
         NULL, NULL, NULL),
        ('absent', '44999', 'G', 1900, 'absent_from_complete_scope', false,
         'existant', NULL, NULL),
        ('deactivated', '44999', 'G', 1900, 'explicitly_deactivated', false,
         'neuf', NULL, NULL),
        ('no-known-class', '85999', NULL, 2024, 'present', false,
         'neuf', '2035-01-01', NULL)
    """)


def _aggregates(conn: psycopg.Connection, view: str) -> list:
    assert view in {"v_commune_dpe", "v_commune_synthese"}
    return conn.execute(f"""
        SELECT code_insee, dpe_passoires_pct, dpe_median,
               annee_construction_mediane FROM {view} ORDER BY code_insee
    """).fetchall()


_EXPECTED = [("44999", Decimal("40.0"), "C", 2004), ("85999", None, None, 2024)]


def test_dpe_auditor_full_run_binds_label_array_with_psycopg3(
    commune_db: psycopg.Connection,
) -> None:
    # Exercise the real driver through the final audit check, not a SQL mock.
    commune_db.execute("""
        ALTER TABLE diagnostics_dpe
            ADD COLUMN etiquette_ges text,
            ADD COLUMN conso_5_usages_ep numeric,
            ADD COLUMN emission_ges_5_usages numeric,
            ADD COLUMN type_batiment text,
            ADD COLUMN source_observations jsonb NOT NULL DEFAULT '[]'
    """)
    labels = ["A", "B", "C", "D", "E", "F", "G", "N", None, "Z"]
    with commune_db.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO diagnostics_dpe (
                numero_dpe, code_insee, code_departement, code_postal,
                date_etablissement_dpe, annee_construction, surface_habitable,
                etiquette_dpe, etiquette_ges, conso_5_usages_ep,
                emission_ges_5_usages, type_batiment
            ) VALUES (%s, %s, '44', '44999', '2025-01-01', 2000, 80,
                      %s, %s, 100, 10, 'maison')
            """,
            [
                (
                    f"audit-fixture-{index}",
                    "00000" if label == "Z" else "44999",
                    label,
                    label,
                )
                for index, label in enumerate(labels)
            ],
        )

    result = DpeAuditor().run(commune_db)

    assert result.row_count == len(labels)
    assert result.stats["documentary_count"] == len(labels)
    assert [
        (finding.title, finding.count)
        for finding in result.findings
        if finding.category == "format"
    ] == [
        ("Valeurs invalides dans `etiquette_dpe`", 1),
        ("Valeurs invalides dans `etiquette_ges`", 1),
    ]
    # The final cross-dataset check ran after both parameterized label checks.
    assert [
        finding.count for finding in result.findings if finding.category == "integrity"
    ] == [1]


@pytest.mark.parametrize(
    ("consumption", "emissions", "invalid_count"),
    [
        (Decimal("25000"), Decimal("3000"), 0),
        (Decimal("0"), Decimal("0"), 0),
        (Decimal("-1"), Decimal("-1"), 1),
        (Decimal("NaN"), Decimal("Infinity"), 1),
        (None, None, 0),
    ],
)
def test_dpe_annual_values_have_no_intensity_ceiling(
    commune_db: psycopg.Connection,
    consumption: Decimal | None,
    emissions: Decimal | None,
    invalid_count: int,
) -> None:
    from app.data_quality.base import AuditResult

    commune_db.execute("""
        ALTER TABLE diagnostics_dpe
            ADD COLUMN conso_5_usages_ep numeric,
            ADD COLUMN emission_ges_5_usages numeric
    """)
    commune_db.execute(
        "INSERT INTO diagnostics_dpe (conso_5_usages_ep, emission_ges_5_usages) "
        "VALUES (%s, %s)",
        (consumption, emissions),
    )
    result = AuditResult(dataset="dpe", table="diagnostics_dpe", row_count=1)
    auditor = DpeAuditor()
    auditor._check_range_conso(commune_db, result)
    auditor._check_range_ges(commune_db, result)

    assert [finding.count for finding in result.warnings] == (
        [invalid_count, invalid_count] if invalid_count else []
    )
    assert len(result.infos) == 2
    for field in ("conso_5_usages_ep", "emission_ges_5_usages"):
        assert result.stats[f"{field}_annual_upper_bound"] == "not_evaluated"
    assert all("alias" in finding.description for finding in result.infos)
    # Observation never rewrites source totals or invents a surface ratio.
    assert commune_db.execute(
        "SELECT conso_5_usages_ep::text, emission_ges_5_usages::text "
        "FROM diagnostics_dpe"
    ).fetchone() == (
        str(consumption) if consumption is not None else None,
        str(emissions) if emissions is not None else None,
    )


def test_commune_population_keeps_legacy_expired_replaced_and_unknown_label(
    commune_db: psycopg.Connection,
) -> None:
    _seed(commune_db)
    _hook()
    # Replay the canonical source scan with count/histogram projections: no
    # new public indicator is added to the commune view by this proof.
    ddl = dict(matview_ddl.INTERMEDIATE_MATVIEWS)["v_commune_dpe"]
    population_scan = ddl.split("FROM diagnostics_dpe", 1)[1].split(
        "GROUP BY code_insee", 1
    )[0]
    histogram = commune_db.execute(
        "SELECT etiquette_dpe, count(*) FROM diagnostics_dpe "
        + population_scan
        + " GROUP BY etiquette_dpe ORDER BY etiquette_dpe NULLS LAST"
    ).fetchall()
    assert histogram == [("A", 1), ("B", 1), ("C", 1), ("F", 1), ("G", 1), (None, 2)]
    assert sum(count for _, count in histogram) == 7
    assert (
        commune_db.execute("SELECT count(*) FROM diagnostics_dpe").fetchone()[0] == 10
    )
    assert _aggregates(commune_db, "v_commune_dpe") == _EXPECTED
    assert _aggregates(commune_db, "v_commune_synthese") == _EXPECTED


def _swap(conn: psycopg.Connection) -> tuple[int, int]:
    old_oid = conn.execute("SELECT 'diagnostics_dpe'::regclass::oid").fetchone()[0]
    conn.execute("CREATE TABLE diagnostics_dpe_new (LIKE diagnostics_dpe)")
    _seed(conn, "diagnostics_dpe_new")
    with conn.transaction():
        conn.execute("ALTER TABLE diagnostics_dpe RENAME TO diagnostics_dpe_old")
        conn.execute("ALTER TABLE diagnostics_dpe_new RENAME TO diagnostics_dpe")
    new_oid = conn.execute("SELECT 'diagnostics_dpe'::regclass::oid").fetchone()[0]
    assert old_oid != new_oid
    return old_oid, new_oid


def _metadata(conn: psycopg.Connection) -> tuple:
    return conn.execute("""
        SELECT last_refresh_status, last_refresh_at, last_error
        FROM matview_metadata WHERE matview_name = 'v_commune_synthese'
    """).fetchone()


def test_real_hook_rebinds_new_oid_and_recreates_indexes_and_metadata(
    commune_db: psycopg.Connection,
) -> None:
    conn = commune_db
    conn.execute("""
        INSERT INTO diagnostics_dpe (code_insee, etiquette_dpe)
        VALUES ('44999', 'G')
    """)
    _hook()
    before = _metadata(conn)
    old_oid, new_oid = _swap(conn)
    # REFRESH is a negative control: it still reads the retired OID.
    conn.execute("REFRESH MATERIALIZED VIEW v_commune_dpe")
    assert _aggregates(conn, "v_commune_dpe") == [
        ("44999", Decimal("100.0"), "G", None)
    ]
    _hook()
    assert _aggregates(conn, "v_commune_dpe") == _EXPECTED
    assert _aggregates(conn, "v_commune_synthese") == _EXPECTED
    dependencies = conn.execute("""
        SELECT DISTINCT dep.refobjid
        FROM pg_depend dep JOIN pg_rewrite rw ON rw.oid = dep.objid
        WHERE rw.ev_class = 'v_commune_dpe'::regclass
    """).fetchall()
    assert (new_oid,) in dependencies
    assert (old_oid,) not in dependencies
    indexes = conn.execute("""
        SELECT tablename, indexname FROM pg_indexes
        WHERE schemaname = current_schema() AND tablename LIKE 'v_commune_%'
    """).fetchall()
    expected_indexes = {
        (name, f"idx_{name}_code_insee")
        for name, _ in matview_ddl.INTERMEDIATE_MATVIEWS
    } | {
        ("v_commune_synthese", "idx_commune_synthese_code_insee"),
        ("v_commune_synthese", "idx_commune_synthese_dept"),
        # The yearly price series (migration 115) is rebuilt by the same hook.
        (matview_ddl.ANNUEL_VIEW, "idx_commune_dvf_annuel_code_classe_annee"),
    }
    assert set(indexes) == expected_indexes
    after = _metadata(conn)
    assert after[0] == "success" and after[1] > before[1] and after[2] is None
    conn.execute("DROP TABLE diagnostics_dpe_old CASCADE")
    assert _aggregates(conn, "v_commune_synthese") == _EXPECTED


def test_rebuild_failure_rolls_back_views_and_does_not_advance_freshness(
    commune_db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = commune_db
    _hook()
    before = _metadata(conn)
    old_views = _aggregates(conn, "v_commune_synthese")
    _swap(conn)
    original_indexes = matview_ddl.INDEX_SQLS
    monkeypatch.setattr(
        matview_ddl,
        "INDEX_SQLS",
        (*original_indexes, "SELECT nonexistent_rebuild_column"),
    )
    with pytest.raises(psycopg.errors.UndefinedColumn):
        _hook()
    assert _aggregates(conn, "v_commune_synthese") == old_views
    failed = _metadata(conn)
    assert failed[0] == "failed" and failed[1] == before[1]
    assert "nonexistent_rebuild_column" in failed[2]
    assert conn.execute("SELECT count(*) FROM diagnostics_dpe").fetchone()[0] == 10
    monkeypatch.setattr(matview_ddl, "INDEX_SQLS", original_indexes)
    _hook()
    assert _aggregates(conn, "v_commune_synthese") == _EXPECTED
    assert _metadata(conn)[0] == "success"


async def _read(conn: psycopg.Connection, codes: list[str]) -> list[dict]:
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    engine = create_async_engine(
        settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with AsyncSession(engine) as session:
            # Every source hosted: the barrier reads all thirteen publications.
            return await commune_synthese_par_codes(
                session, codes, list(COMMUNE_SOURCES)
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("status", ["failed", "pending"])
async def test_consumer_refuses_failed_metadata_even_for_missing_commune(
    commune_db: psycopg.Connection,
    status: str,
) -> None:
    _seed(commune_db)
    _hook()
    commune_db.execute(
        """
        UPDATE matview_metadata SET last_refresh_status = %s,
            last_error = 'private synthetic failure detail'
        WHERE matview_name = 'v_commune_synthese'
    """,
        (status,),
    )
    for codes in (["44999"], ["00000"], []):
        with pytest.raises(HTTPException) as raised:
            await _read(commune_db, codes)
        assert raised.value.status_code == 503
        assert raised.value.detail == "Synthèse communale temporairement indisponible."


async def test_consumer_refuses_old_oid_despite_success_then_reads_rebuilt_view(
    commune_db: psycopg.Connection,
) -> None:
    conn = commune_db
    _hook()
    _swap(conn)
    assert _metadata(conn)[0] == "success"
    with pytest.raises(HTTPException) as raised:
        await _read(conn, ["44999"])
    assert raised.value.status_code == 503
    _hook()
    rows = await _read(conn, ["44999"])
    assert len(rows) == 1 and rows[0]["dpe_passoires_pct"] == 40.0
    assert rows[0]["dpe_median"] == "C"
    assert await _read(conn, ["00000"]) == []
    assert await _read(conn, []) == []
    conn.execute("DELETE FROM matview_metadata")
    assert len(await _read(conn, ["44999"])) == 1
    conn.execute("ALTER TABLE diagnostics_dpe RENAME TO diagnostics_dpe_other")
    conn.execute("CREATE TABLE diagnostics_dpe (LIKE diagnostics_dpe_other)")
    with pytest.raises(HTTPException):
        await _read(conn, ["44999"])


async def test_consumer_refuses_synthese_bound_to_retired_intermediate(
    commune_db: psycopg.Connection,
) -> None:
    conn = commune_db
    _seed(conn)
    _hook()
    conn.execute("ALTER MATERIALIZED VIEW v_commune_dpe RENAME TO retired_dpe")
    conn.execute(dict(matview_ddl.INTERMEDIATE_MATVIEWS)["v_commune_dpe"])
    with pytest.raises(HTTPException) as raised:
        await _read(conn, ["44999"])
    assert raised.value.status_code == 503


async def test_consumer_refuses_missing_view_after_old_table_cascade(
    commune_db: psycopg.Connection,
) -> None:
    conn = commune_db
    _hook()
    _swap(conn)
    conn.execute("DROP TABLE diagnostics_dpe_old CASCADE")
    with pytest.raises(HTTPException) as raised:
        await _read(conn, ["44999"])
    assert raised.value.status_code == 503
    assert raised.value.detail == "Synthèse communale temporairement indisponible."
    _hook()
    assert (await _read(conn, ["44999"]))[0]["dpe_median"] == "C"


async def test_cleanup_repair_restores_freshness_in_same_transaction(
    commune_db: psycopg.Connection,
) -> None:
    conn = commune_db
    _hook()
    _swap(conn)
    conn.execute("CREATE MATERIALIZED VIEW communes_lookup AS SELECT 1 AS id")
    conn.execute("""
        UPDATE matview_metadata SET last_refresh_status = 'failed',
            last_error = 'synthetic rebuild failure'
        WHERE matview_name = 'v_commune_synthese'
    """)
    before = _metadata(conn)
    with conn.transaction():
        conn.execute("DROP TABLE diagnostics_dpe_old CASCADE")
        rebuilt = recreate_missing_matviews(conn, logging.getLogger(__name__))
        assert "v_commune_synthese" in rebuilt
    after = _metadata(conn)
    assert after[0] == "success" and after[1] > before[1] and after[2] is None
    assert (await _read(conn, ["44999"]))[0]["dpe_median"] == "C"


@pytest.mark.parametrize("failed_view", ["commune", "scoring"])
def test_cleanup_repair_failure_does_not_publish_success_metadata(
    commune_db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    failed_view: str,
) -> None:
    conn = commune_db
    _hook()
    _swap(conn)
    conn.execute("CREATE MATERIALIZED VIEW communes_lookup AS SELECT 1 AS id")
    conn.execute("""
        UPDATE matview_metadata SET last_refresh_status = 'failed',
            last_error = 'synthetic rebuild failure'
        WHERE matview_name = 'v_commune_synthese'
    """)
    before = _metadata(conn)
    target = matview_ddl if failed_view == "commune" else scoring_ddl
    monkeypatch.setattr(
        target,
        "INDEX_SQLS",
        (*target.INDEX_SQLS, "SELECT nonexistent_repair_column"),
    )
    if failed_view == "scoring":
        # The quarantined surface is no longer rebuilt or required for DPE.
        with conn.transaction():
            conn.execute("DROP TABLE diagnostics_dpe_old CASCADE")
            rebuilt = recreate_missing_matviews(conn, logging.getLogger(__name__))
        assert "scoring_renov_target" not in rebuilt
        assert _metadata(conn)[0] == "success"
    else:
        with pytest.raises(psycopg.errors.UndefinedColumn), conn.transaction():
            conn.execute("DROP TABLE diagnostics_dpe_old CASCADE")
            recreate_missing_matviews(conn, logging.getLogger(__name__))
        assert _metadata(conn) == before
        assert conn.execute("SELECT to_regclass('diagnostics_dpe_old')").fetchone()[0]


async def test_consumer_does_not_mask_unrelated_sql_defects(
    commune_db: psycopg.Connection,
) -> None:
    _hook()
    commune_db.execute("""
        ALTER TABLE matview_metadata
        RENAME COLUMN last_refresh_status TO unexpected_column
    """)
    with pytest.raises(DBAPIError) as raised:
        await _read(commune_db, ["44999"])
    assert raised.value.orig.sqlstate == "42703"

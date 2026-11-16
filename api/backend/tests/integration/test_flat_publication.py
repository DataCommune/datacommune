"""COR-04: actual flat-stage publications on disposable PostGIS only.

Fixtures are synthetic, using the migrated production schema and real stage/swap.
They extend the audit's RevenusPipeline 42P10 counterexample to all nine paths.
"""

from __future__ import annotations

import csv
import importlib
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from psycopg import sql

from app.common.unqualified_surfaces import SurfaceQuarantinedError

CASES = [
    (
        "revenus",
        "RevenusPipeline",
        {"code_insee": "44999", "annee": "2021", "revenu_median": "21000"},
        "revenu_median",
        "22000",
    ),
    (
        "permis",
        "PermisPipeline",
        {"numero_dossier": "COR04-TEST", "annee": "2021", "nb_logements_crees": "2"},
        "nb_logements_crees",
        "3",
    ),
    (
        "loyers",
        "LoyersPipeline",
        {
            "code_insee": "44999",
            "annee": "2021",
            "type_habitat": "appartement",
            "loyer_m2": "12",
        },
        "loyer_m2",
        "13",
    ),
    (
        "demographie",
        "DemographiePipeline",
        {"code_insee": "44999", "annee": "2021", "population": "100"},
        "population",
        "110",
    ),
    (
        "encadrement_loyers",
        "EncadrementLoyersPipeline",
        {
            "annee": "2021",
            "ville": "Paris",
            "numero_quartier": "1",
            "nb_pieces": "2",
            "epoque_construction": "avant 1946",
            "type_location": "non meuble",
            "loyer_reference": "20",
            "longitude": "2.35",
            "latitude": "48.85",
        },
        "loyer_reference",
        "21",
    ),
    (
        "copropriete",
        "CoproprietePipeline",
        {"numero_immatriculation": "COR04-TEST", "nb_lots_total": "10"},
        "nb_lots_total",
        "11",
    ),
    (
        "fiscalite",
        "FiscalitePipeline",
        {"code_insee": "44999", "annee": "2021", "taux_tfpb": "20"},
        "taux_tfpb",
        "21",
    ),
    (
        "vacance",
        "VacancePipeline",
        {
            "code_commune": "44999",
            "annee": "2021",
            "nb_logements_prives": "100",
            "nb_logements_vacants": "0",
            "taux_vacance": "0",
        },
        "nb_logements_vacants",
        "1",
    ),
    (
        "zonage_abc",
        "ZonageAbcPipeline",
        {"code_insee": "44999", "zone": "B1"},
        "zone",
        "B2",
    ),
]


@pytest.fixture(scope="module")
def migrated_schema(pg_dsn):
    from pathlib import Path

    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", pg_dsn)
    command.upgrade(cfg, "head")
    # Keep migrated templates outside search_path: real swaps drop/rename local
    # sequences and indexes and must never resolve a template's names instead.
    tables = []
    with psycopg.connect(pg_dsn) as conn:
        conn.execute("CREATE SCHEMA cor04_templates")
        for name, class_name, *_ in CASES:
            module = importlib.import_module(f"app.etl_{name}.pipeline")
            pipeline_cls = getattr(module, class_name)
            table = pipeline_cls.__new__(pipeline_cls).production_table_name
            tables.append(table)
            conn.execute(
                sql.SQL("ALTER TABLE public.{} SET SCHEMA cor04_templates").format(
                    sql.Identifier(table)
                )
            )
    try:
        yield
    finally:
        with psycopg.connect(pg_dsn) as conn:
            for table in tables:
                conn.execute(
                    sql.SQL("ALTER TABLE cor04_templates.{} SET SCHEMA public").format(
                        sql.Identifier(table)
                    )
                )
            conn.execute("DROP SCHEMA cor04_templates")


@pytest.fixture(params=CASES, ids=[case[0] for case in CASES])
def publication(request, pg_dsn, migrated_schema, monkeypatch, tmp_path):
    name, class_name, row, metric, changed = request.param
    module = importlib.import_module(f"app.etl_{name}.pipeline")
    pipeline_cls = getattr(module, class_name)
    # Keep the historical adversarial rows: even a pre-existing object cannot
    # publish them while rent control is quarantined (COR-13).
    pipeline = (
        pipeline_cls.__new__(pipeline_cls)
        if name == "encadrement_loyers"
        else pipeline_cls()
    )
    schema = "cor04_" + uuid4().hex
    connect = psycopg.connect
    options = f"-c search_path={schema},public -c statement_timeout=10000"
    with connect(pg_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.{} (LIKE cor04_templates.{} INCLUDING ALL)"
            ).format(
                sql.Identifier(schema),
                sql.Identifier(pipeline.production_table_name),
                sql.Identifier(pipeline.production_table_name),
            )
        )
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.etl_old_tables "
                "(LIKE public.etl_old_tables INCLUDING ALL)"
            ).format(sql.Identifier(schema))
        )
    monkeypatch.setattr(
        psycopg, "connect", lambda dsn, **kw: connect(dsn, options=options, **kw)
    )
    monkeypatch.setattr(pipeline, "get_sync_dsn", lambda: pg_dsn)

    def stage(rows):
        path = tmp_path / "synthetic.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=module.OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        assert pipeline.stage(path) == len(rows)

    def snapshot():
        with connect(pg_dsn, options=options) as conn:
            table = pipeline.production_table_name
            rows = conn.execute(
                sql.SQL("SELECT to_jsonb(t) FROM {} t ORDER BY id").format(
                    sql.Identifier(table)
                )
            ).fetchall()
            indexes = conn.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname=%s AND tablename=%s ORDER BY indexname",
                (schema, table),
            ).fetchall()
            constraints = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid=%s::regclass AND contype IN ('p','u') ORDER BY 1",
                (table,),
            ).fetchall()
            persistence = conn.execute(
                "SELECT relpersistence FROM pg_class WHERE oid=%s::regclass", (table,)
            ).fetchone()[0]
            return rows, indexes, constraints, persistence

    # Seed a distinct published generation directly; stage and swap remain real.
    seed = {k: v for k, v in row.items() if k not in {"longitude", "latitude"}}
    seed[metric] = changed
    with connect(pg_dsn, options=options) as conn:
        conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(pipeline.production_table_name),
                sql.SQL(",").join(map(sql.Identifier, seed)),
                sql.SQL(",").join(sql.Placeholder() for _ in seed),
            ),
            list(seed.values()),
        )
        if name == "encadrement_loyers":
            conn.execute(
                "UPDATE encadrement_loyers SET geom = "
                "ST_SetSRID(ST_MakePoint(2.35, 48.85), 4326)"
            )
    try:
        yield pipeline, row, metric, changed, stage, snapshot
    finally:
        with connect(pg_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_exact_duplicates_publish_twice_with_stable_constraints_and_indexes(
    publication,
):
    pipeline, row, metric, _, stage, snapshot = publication
    before = snapshot()
    if pipeline.dataset_name == "encadrement_loyers":
        with pytest.raises(SurfaceQuarantinedError):
            stage([row])
        with pytest.raises(SurfaceQuarantinedError):
            pipeline.swap()
        assert snapshot() == before
        return
    results = []
    for _ in range(2):
        stage([row, row])
        pipeline.swap()
        result = snapshot()
        rows, indexes, constraints, persistence = result
        assert len(rows) == 1
        actual = rows[0][0][metric]
        assert actual == (
            float(row[metric]) if isinstance(actual, (int, float)) else row[metric]
        )
        assert constraints == before[2]
        assert persistence == "p"
        assert all("staging" not in name for name, _ in indexes)
        results.append(result)
    assert results[0] == results[1]


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_contradictory_duplicates_reject_and_keep_previous_generation(
    publication, reverse
):
    pipeline, row, metric, changed, stage, snapshot = publication
    before = snapshot()
    if pipeline.dataset_name == "encadrement_loyers":
        with pytest.raises(SurfaceQuarantinedError):
            stage([row])
        with pytest.raises(SurfaceQuarantinedError):
            pipeline.swap()
        assert snapshot() == before
        return
    rows = [row, {**row, metric: changed}]
    stage(list(reversed(rows)) if reverse else rows)
    with pytest.raises(psycopg.errors.UniqueViolation):
        pipeline.swap()
    assert snapshot() == before


def test_invalid_cast_rejects_without_replacing_previous_generation(publication):
    pipeline, row, _, _, stage, snapshot = publication
    before = snapshot()
    if pipeline.dataset_name == "encadrement_loyers":
        with pytest.raises(SurfaceQuarantinedError):
            stage([row])
        with pytest.raises(SurfaceQuarantinedError):
            pipeline.swap()
        assert snapshot() == before
        return
    bad_column = "annee" if "annee" in row else "nb_lots_total"
    if pipeline.dataset_name == "zonage_abc":
        # Zone width is constrained by the actual migrated production schema.
        bad_column = "zone"
    stage([{**row, bad_column: "invalid-value"}])
    error = (
        psycopg.errors.StringDataRightTruncation
        if bad_column == "zone"
        else psycopg.errors.InvalidTextRepresentation
    )
    with pytest.raises(error):
        pipeline.swap()
    assert snapshot() == before


@pytest.mark.parametrize("publication", [CASES[4]], indirect=True, ids=["encadrement"])
@pytest.mark.parametrize(
    "contradictory", [False, True], ids=["identical", "conflicting"]
)
def test_nullable_neighborhood_does_not_hide_ambiguous_prices(
    publication, contradictory
):
    pipeline, row, metric, changed, stage, snapshot = publication
    before = snapshot()
    if pipeline.dataset_name == "encadrement_loyers":
        with pytest.raises(SurfaceQuarantinedError):
            stage([row])
        with pytest.raises(SurfaceQuarantinedError):
            pipeline.swap()
        assert snapshot() == before
        return
    unknown = {**row, "numero_quartier": ""}
    stage([unknown, {**unknown, metric: changed} if contradictory else unknown])
    if contradictory:
        with pytest.raises(RuntimeError, match=r"Ambiguous.*neighborhood"):
            pipeline.swap()
        assert snapshot() == before
    else:
        pipeline.swap()
        assert len(snapshot()[0]) == 1
        assert snapshot()[0][0][0]["numero_quartier"] is None


def test_empty_generation_keeps_existing_volume_guard(publication):
    pipeline, row, _, _, stage, snapshot = publication
    before = snapshot()
    if pipeline.dataset_name == "encadrement_loyers":
        with pytest.raises(SurfaceQuarantinedError):
            stage([row])
        with pytest.raises(SurfaceQuarantinedError):
            pipeline.swap()
        assert snapshot() == before
        return
    stage([])
    with pytest.raises(RuntimeError, match="Swap aborted"):
        pipeline.swap()
    assert snapshot() == before


@pytest.mark.parametrize("publication", [CASES[3]], indirect=True, ids=["demographie"])
def test_plm_aggregation_runs_after_constraints_without_duplicate_inflation(
    publication,
):
    pipeline, row, _, _, stage, snapshot = publication
    paris1 = {**row, "code_insee": "75101", "population": "100"}
    paris2 = {**row, "code_insee": "75102", "population": "120"}
    stage([paris1, paris1, paris2])
    pipeline.swap()
    rows = {r[0]["code_insee"]: r[0] for r in snapshot()[0]}
    assert set(rows) == {"75101", "75102", "75056"}
    assert rows["75056"]["population"] == 220


@pytest.mark.parametrize("publication", [CASES[4]], indirect=True, ids=["encadrement"])
def test_quarantine_preserves_existing_encadrement_geometry(publication):
    _pipeline, row, _, _, stage, snapshot = publication
    before = snapshot()
    with pytest.raises(SurfaceQuarantinedError):
        stage([row, row])
    assert snapshot() == before

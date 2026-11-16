"""Real migration, closed JSON and staging proofs on disposable PostGIS only."""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.etl.runner import PipelineRunner
from app.etl_dpe.pipeline import DpePipeline
from tests.test_etl_dpe_sources import install_source_api


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    # DPE reads its departements from the bound instance scope (44/85,
    # within the migration 091 lock), for direct calls and runners alike.
    bind_departements(("44", "85"), DpePipeline)


@pytest.fixture(scope="module")
def pg_dsn(isolated_pg_dsn):
    """Keep this module's destructive public fixtures out of the shared schema."""
    return isolated_pg_dsn


MIGRATION = "091_dpe_source_lifecycle"
# Plan 04-15: columns of owner decisions C1 to C3 (04-DECISIONS-COLONNES-DPE.md).
COLUMNS_MIGRATION = "100_dpe_source_columns"
# Plan 04.1-13: the column the import fills with the raw-address attachment.
RAW_ADDRESS_MIGRATION = "104_dpe_rattachement_adresse_brute"
COLUMNS_0415 = {
    "version_dpe": "2.4",
    "date_derniere_modification_dpe": "2024-05-02",
    "date_reception_dpe": "2024-04-30",
    "date_visite_diagnostiqueur": "2024-04-12",
    "conso_5_usages_par_m2_ep": "226.10",
    "emission_ges_5_usages_par_m2": "43.5",
    "numero_dpe_immeuble_associe": "2444E0000099Z",
    "numero_immatriculation_copropriete": "AB1234567",
}
BACKEND = Path(__file__).resolve().parents[2]
LIFECYCLE_COLUMNS = {
    "source_observations",
    "source_state",
    "source_snapshot_at",
    "is_tombstone",
    "lifecycle_facts",
    "revision_evidence",
}
FACTS = {"replacement_state": "not_checked", "graph_issues": [], "date_issues": []}
REVISION = {
    "status": "unverified",
    "kind": None,
    "revision_date": None,
    "verified_at": None,
    "evidence_sha256": None,
    "official_source": None,
}
OBSERVATION = {
    "source_slug": "dpe03existant",
    "department": "44",
    "source_version": "a" * 64,
    "observed_at": "2026-09-05T12:00:00Z",
    "last_seen_at": "2026-09-05T12:00:00Z",
    "state": "present",
    "import_id": "fixture-generation",
}
# Actual head-090 column types, copied from the existing local schema (no rows).
_CREATE_RELATION = """
CREATE TABLE public.diagnostics_dpe (
    id bigserial PRIMARY KEY,
    numero_dpe text NOT NULL UNIQUE,
    code_departement character varying(3),
    date_etablissement_dpe date,
    annee_construction integer,
    type_batiment character varying(50),
    surface_habitable numeric(12,2),
    etiquette_dpe character(1),
    etiquette_ges character(1),
    type_installation_chauffage character varying(100),
    conso_5_usages_ep numeric(14,2),
    emission_ges_5_usages numeric(14,2),
    code_postal character varying(5),
    code_insee character varying(5),
    nom_commune text,
    longitude double precision,
    latitude double precision,
    "position" public.geometry(Point,4326),
    periode_construction character varying(50),
    cout_total_5_usages numeric(12,2),
    type_energie_principale_chauffage character varying(100),
    qualite_isolation_enveloppe character varying(50),
    numero_etage_appartement integer,
    complement_adresse_logement text,
    identifiant_ban character varying(50),
    numero_dpe_remplace text,
    date_fin_validite_dpe date,
    id_rnb character varying(32),
    provenance_id_rnb text,
    score_ban numeric,
    adresse_brut text,
    adresse_complete_brut text,
    nom_commune_brut text,
    code_postal_brut text,
    adresse_ban text,
    numero_voie_ban text,
    nom_rue_ban text,
    statut_geocodage text,
    regime_dpe text
);
CREATE INDEX idx_dpe_code_departement
    ON public.diagnostics_dpe USING btree (code_departement);
CREATE INDEX idx_dpe_code_insee
    ON public.diagnostics_dpe USING btree (code_insee);
CREATE INDEX idx_dpe_date_etablissement
    ON public.diagnostics_dpe USING btree (date_etablissement_dpe);
CREATE INDEX idx_dpe_etiquette_dpe
    ON public.diagnostics_dpe USING btree (etiquette_dpe);
CREATE INDEX idx_dpe_identifiant_ban
    ON public.diagnostics_dpe USING btree (identifiant_ban);
CREATE INDEX idx_dpe_position
    ON public.diagnostics_dpe USING gist ("position");
CREATE INDEX idx_dpe_position_geog
    ON public.diagnostics_dpe USING gist (public.geography("position"));
"""


def _migration(name=MIGRATION):
    path = BACKEND / "migrations" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate(connection, direction="upgrade", name=MIGRATION):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(name), direction)()


def _cleanup(connection):
    for relation in (
        "diagnostics_dpe_staging",
        "diagnostics_dpe_old",
        "_dpe_staging_flat",
    ):
        connection.execute(text(f"DROP TABLE IF EXISTS {relation} CASCADE"))
    connection.execute(text("DROP TABLE IF EXISTS diagnostics_dpe CASCADE"))
    for signature in (
        "dc_dpe_source_coherent(jsonb, text, timestamptz)",
        "dc_dpe_lifecycle_valid(jsonb)",
        "dc_dpe_observations_valid(jsonb)",
        "dc_dpe_utc_timestamp(text)",
        "dc_dpe_ordered_flags(jsonb, text[])",
        "dc_dpe_json_keys(jsonb, text[])",
    ):
        connection.execute(text(f"DROP FUNCTION IF EXISTS public.{signature}"))


@pytest.fixture
def source_database(pg_dsn):
    engine = create_engine(pg_dsn.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as connection:
        _cleanup(connection)
        connection.execute(text(_CREATE_RELATION))
        connection.execute(
            text("""
            INSERT INTO diagnostics_dpe (
                numero_dpe, code_departement, identifiant_ban, code_insee,
                etiquette_dpe, surface_habitable, date_etablissement_dpe,
                date_fin_validite_dpe, regime_dpe, position, adresse_brut
            ) VALUES (
                'DPE-SYNTHETIC-LEGACY', '44', '44999_0001_00001', '44999',
                'D', 75.5, DATE '2024-01-01', DATE '2034-01-01', 'existant',
                ST_SetSRID(ST_MakePoint(-1.5, 47.2), 4326), 'PRIVATE-OLD-CONTENT'
            )
        """)
        )
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            _cleanup(connection)
            connection.commit()
    engine.dispose()


def _row(connection):
    return connection.execute(
        text("SELECT to_jsonb(d) FROM diagnostics_dpe d")
    ).scalar_one()


def _present(connection):
    connection.execute(
        text("""
        UPDATE diagnostics_dpe SET
            source_observations = CAST(:observations AS jsonb),
            source_state = 'present', source_snapshot_at = '2026-09-05T12:00:00Z'
    """),
        {"observations": json.dumps([OBSERVATION])},
    )


def test_revision_is_in_the_single_linear_migration_chain(alembic_config):
    script = ScriptDirectory.from_config(alembic_config)
    heads = script.get_heads()
    assert len(heads) == 1
    chain = list(script.walk_revisions(base="base", head=heads[0]))
    assert MIGRATION in {revision.revision for revision in chain}
    assert all(not isinstance(revision.down_revision, tuple) for revision in chain)
    assert (
        script.get_revision("092_loyers_prediction_metadata").down_revision == MIGRATION
    )
    assert script.get_revision("093_permis_source_dates").down_revision == (
        "092_loyers_prediction_metadata"
    )
    assert _migration().down_revision == "090_dpe_adresse_brute_ademe"


def test_additive_migration_roundtrip_preserves_legacy(source_database):
    connection = source_database
    before = _row(connection)
    for _ in range(2):
        _migrate(connection)
        after = _row(connection)
        assert {
            key: value for key, value in after.items() if key not in LIFECYCLE_COLUMNS
        } == before
        assert after["source_observations"] == []
        assert after["source_state"] == "unknown"
        assert after["source_snapshot_at"] is None
        assert after["is_tombstone"] is False
        assert after["lifecycle_facts"] == FACTS
        assert after["revision_evidence"] == REVISION
        assert (
            connection.execute(
                text("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema='public' AND table_name='diagnostics_dpe'
              AND column_name='position'
        """)
            ).scalar_one()
            == "YES"
        )
        _migrate(connection, "downgrade")
        assert _row(connection) == before
    _migrate(connection)
    _present(connection)
    assert _row(connection)["source_state"] == "present"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_observations", {}),
        ("source_observations", [{**OBSERVATION, "private_payload": "SECRET"}]),
        ("source_observations", [OBSERVATION, OBSERVATION]),
        ("source_observations", [{**OBSERVATION, "department": "75"}]),
        ("source_observations", [{**OBSERVATION, "source_slug": "unreviewed"}]),
        ("source_observations", [{**OBSERVATION, "source_version": True}]),
        ("source_observations", [{**OBSERVATION, "source_version": "not-a-hash"}]),
        (
            "source_observations",
            [{**OBSERVATION, "observed_at": "2026-02-30T12:00:00Z"}],
        ),
        (
            "source_observations",
            [{**OBSERVATION, "observed_at": "2026-09-05T12:00:00+01:00"}],
        ),
        (
            "source_observations",
            [{**OBSERVATION, "last_seen_at": "2026-09-06T12:00:00Z"}],
        ),
        ("source_observations", [{**OBSERVATION, "import_id": ""}]),
        ("lifecycle_facts", {**FACTS, "private_edge": "SECRET"}),
        ("lifecycle_facts", {**FACTS, "replacement_state": "current"}),
        ("lifecycle_facts", {**FACTS, "graph_issues": ["cycle", "cycle"]}),
        ("lifecycle_facts", {**FACTS, "graph_issues": ["fork", "cycle"]}),
        ("lifecycle_facts", {**FACTS, "graph_issues": ["SECRET"]}),
        (
            "lifecycle_facts",
            {**FACTS, "date_issues": ["invalid_end_date", "invalid_end_date"]},
        ),
        ("lifecycle_facts", {**FACTS, "date_issues": [True]}),
        ("revision_evidence", {**REVISION, "official_source": "SECRET"}),
        ("revision_evidence", {**REVISION, "status": "verified_attestation"}),
        ("revision_evidence", {"status": "unverified"}),
    ],
)
def test_closed_json_constraints_reject_malformed_facts(source_database, field, value):
    connection = source_database
    _migrate(connection)
    _present(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text(f"UPDATE diagnostics_dpe SET {field}=CAST(:value AS jsonb)"),
            {"value": json.dumps(value)},
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "source_state='invented'",
        "source_state='present', source_snapshot_at=NULL",
        "source_state='present', source_observations='[]'::jsonb",
        "source_state='unknown', source_snapshot_at='2026-09-05T12:00:00Z'",
        "is_tombstone=true",
    ],
)
def test_cross_field_invariants_reject_unproven_states(source_database, assignment):
    connection = source_database
    _migrate(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text(f"UPDATE diagnostics_dpe SET {assignment}"))


def test_tombstone_requires_purge_and_retains_only_metadata(source_database):
    connection = source_database
    _migrate(connection)
    _present(connection)
    absent = [{**OBSERVATION, "state": "absent_from_complete_scope"}]
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("""
            UPDATE diagnostics_dpe SET source_state='absent_from_complete_scope',
                is_tombstone=true, source_observations=CAST(:observations AS jsonb)
        """),
            {"observations": json.dumps(absent)},
        )
    keep = {
        "id",
        "numero_dpe",
        "identifiant_ban",
        "code_departement",
        "code_insee",
        "numero_dpe_remplace",
        "regime_dpe",
    } | LIFECYCLE_COLUMNS
    purge = ", ".join(f'"{key}"=NULL' for key in _row(connection) if key not in keep)
    connection.execute(
        text(f"""
        UPDATE diagnostics_dpe SET {purge}, source_state='absent_from_complete_scope',
            is_tombstone=true, source_observations=CAST(:observations AS jsonb)
    """),
        {"observations": json.dumps(absent)},
    )
    row = _row(connection)
    assert row["identifiant_ban"] == "44999_0001_00001"
    assert row["is_tombstone"] is True
    assert all(value is None for key, value in row.items() if key not in keep)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE diagnostics_dpe SET lifecycle_facts=CAST(:facts AS jsonb)"),
            {"facts": json.dumps({**FACTS, "date_issues": ["invalid_end_date"]})},
        )
    # Schema rollback cannot resurrect intentionally purged documentary content.
    _migrate(connection, "downgrade")
    assert _row(connection)["adresse_brut"] is None
    _migrate(connection)
    assert _row(connection)["source_state"] == "unknown"


def test_infinite_snapshot_rejected_even_with_present_observation(source_database):
    connection = source_database
    _migrate(connection)
    _present(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("UPDATE diagnostics_dpe SET source_snapshot_at='infinity'")
        )


def test_first_complete_absence_keeps_legacy_provenance_unknown(source_database):
    connection = source_database
    _migrate(connection)
    connection.execute(
        text("""
        INSERT INTO diagnostics_dpe (
            numero_dpe, identifiant_ban, code_insee, code_departement,
            source_state, source_snapshot_at, is_tombstone
        ) VALUES ('DPE-SYNTHETIC-ABSENT', '44999_0001_00002', '44999', '44',
            'absent_from_complete_scope', '2026-09-05T12:00:00Z', true)
    """)
    )
    assert (
        connection.execute(
            text("""
        SELECT source_observations FROM diagnostics_dpe
        WHERE numero_dpe='DPE-SYNTHETIC-ABSENT'
    """)
        ).scalar_one()
        == []
    )


def test_known_graph_and_invalid_date_flags_can_coexist(source_database):
    connection = source_database
    _migrate(connection)
    _present(connection)
    facts = {
        "replacement_state": "replaced",
        "graph_issues": ["cycle", "fork"],
        "date_issues": ["invalid_establishment_date", "invalid_end_date"],
    }
    connection.execute(
        text("UPDATE diagnostics_dpe SET lifecycle_facts=CAST(:facts AS jsonb)"),
        {"facts": json.dumps(facts)},
    )
    assert _row(connection)["lifecycle_facts"] == facts


def test_explicit_deactivation_requires_a_future_verified_channel(source_database):
    connection = source_database
    _migrate(connection)
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("""
            INSERT INTO diagnostics_dpe (
                numero_dpe, code_departement, source_state,
                source_snapshot_at, is_tombstone
            ) VALUES ('DPE-SYNTHETIC-NO-ATTESTATION', '44',
                'explicitly_deactivated', '2026-09-05T12:00:00Z', true)
        """)
        )


def _transport(monkeypatch, directory, change=None):
    """Reuse source fixtures, altering only synthetic documentary rows."""
    if change is not None:
        real_transport = httpx.MockTransport

        def wrap(handler):
            def respond(request):
                response = handler(request)
                if (
                    request.url.path.endswith("/lines")
                    and request.url.params.get("size") != "1"
                    and response.status_code == 200
                ):
                    body = response.json()
                    for row in body.get("results", []):
                        change(row, request.url.path.split("/")[5])
                    return httpx.Response(200, json=body)
                return response

            return real_transport(respond)

        monkeypatch.setattr(httpx, "MockTransport", wrap)
    install_source_api(monkeypatch, directory)


def _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path, change=None):
    _migrate(connection)
    _migrate(connection, name=COLUMNS_MIGRATION)
    _migrate(connection, name=RAW_ADDRESS_MIGRATION)
    connection.execute(
        text("""
        CREATE TABLE IF NOT EXISTS etl_old_tables (
            id SERIAL PRIMARY KEY, table_name TEXT NOT NULL UNIQUE,
            dataset_name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    )
    connection.commit()
    _transport(monkeypatch, tmp_path, change)
    monkeypatch.setattr(DpePipeline, "get_sync_dsn", staticmethod(lambda: pg_dsn))
    monkeypatch.setattr(DpePipeline, "min_staging_rows_final", property(lambda _: 1))
    return DpePipeline()


def _stage_all(pipeline, pg_dsn):
    files = pipeline.download()
    pipeline.create_staging_table(pg_dsn)
    for path in files:
        PipelineRunner()._process_file(pipeline, path, [], logging.getLogger(__name__))
    return files


def _served_snapshot(connection):
    oid = connection.execute(
        text("SELECT 'diagnostics_dpe'::regclass::oid")
    ).scalar_one()
    rows = (
        connection.execute(
            text("""
        SELECT to_jsonb(d) FROM diagnostics_dpe d ORDER BY numero_dpe
    """)
        )
        .scalars()
        .all()
    )
    connection.commit()  # Release read locks before a separate connection swaps.
    return oid, rows


def test_real_four_pair_staging_swap_minimizes_history_and_restores_indexes(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
):
    connection = source_database
    pipeline = _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path)
    before_oid, before_rows = _served_snapshot(connection)
    try:
        assert len(_stage_all(pipeline, pg_dsn)) == 4
        pipeline.swap()
        oid, rows = _served_snapshot(connection)
        assert oid != before_oid
        assert len(rows) == len({row["id"] for row in rows}) == 9
        assert sum(row["source_state"] == "present" for row in rows) == 8
        retired = next(row for row in rows if row["is_tombstone"])
        assert retired["numero_dpe"] == before_rows[0]["numero_dpe"]
        assert retired["adresse_brut"] is None and retired["position"] is None
        assert retired["source_observations"] == []
        indexes = set(
            connection.execute(
                text("""
            SELECT indexname FROM pg_indexes WHERE tablename='diagnostics_dpe'
        """)
            ).scalars()
        )
        assert {
            "idx_dpe_identifiant_ban",
            "idx_dpe_position_geog",
        } <= indexes
        # Migration 107 dropped the planar GiST index; the DDL above still
        # carries it (the base before 107): the publication must not rebuild it.
        assert "idx_dpe_position" not in indexes
        # Nor under another name: no index of its shape (planar GiST).
        definitions = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE tablename='diagnostics_dpe'")
        ).scalars()
        assert not [d for d in definitions if d.endswith('USING gist ("position")')]
        assert not any("staging" in name for name in indexes)
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM pg_constraint
            WHERE conrelid='diagnostics_dpe'::regclass AND contype IN ('p','u')
        """)
            ).scalar_one()
            == 2
        )
        assert connection.execute(
            text("""
            SELECT last_value >= (SELECT max(id) FROM diagnostics_dpe)
            FROM diagnostics_dpe_id_seq
        """)
        ).scalar_one()
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM etl_old_tables WHERE table_name='diagnostics_dpe_old'
        """)
            ).scalar_one()
            == 1
        )
    finally:
        pipeline.close()


@pytest.mark.parametrize("abort_publication", [False, True])
def test_global_graph_is_prepared_only_on_staging_before_atomic_publication(
    source_database, pg_dsn, monkeypatch, tmp_path, abort_publication
):
    connection = source_database
    predecessor = "DPE-SYNTHETIC-LEGACY"

    def link_to_legacy(row, _source):
        row["numero_dpe_remplace"] = predecessor

    pipeline = _prepare_pipeline(
        connection, pg_dsn, monkeypatch, tmp_path, link_to_legacy
    )
    connection.execute(
        text("UPDATE diagnostics_dpe SET numero_dpe=:number"), {"number": predecessor}
    )
    connection.commit()
    before = _served_snapshot(connection)
    original_swap = pipeline.swap_tables

    def inspect_staging(dsn):
        assert _served_snapshot(connection) == before
        staged = (
            connection.execute(
                text(
                    "SELECT numero_dpe, lifecycle_facts, is_tombstone "
                    "FROM diagnostics_dpe_staging ORDER BY numero_dpe"
                )
            )
            .mappings()
            .all()
        )
        connection.commit()
        assert len(staged) == 9
        assert all(row["lifecycle_facts"]["graph_issues"] == ["fork"] for row in staged)
        old = next(row for row in staged if row["numero_dpe"] == predecessor)
        assert old["is_tombstone"]
        assert old["lifecycle_facts"]["replacement_state"] == "replaced"
        if abort_publication:
            raise RuntimeError("synthetic publication interruption")
        return original_swap(dsn)

    monkeypatch.setattr(pipeline, "swap_tables", inspect_staging)
    try:
        _stage_all(pipeline, pg_dsn)
        if abort_publication:
            with pytest.raises(
                RuntimeError, match="synthetic publication interruption"
            ):
                pipeline.swap()
            assert _served_snapshot(connection) == before
        else:
            pipeline.swap()
            oid, rows = _served_snapshot(connection)
            assert oid != before[0]
            assert all(
                row["lifecycle_facts"]["graph_issues"] == ["fork"] for row in rows
            )
    finally:
        pipeline.close()


def test_logged_candidate_rows_are_written_once_before_publication(
    source_database, pg_dsn, monkeypatch, tmp_path
):
    """The candidate is logged and published as is (04.1-04): every row
    version left on it is dead space and WAL. Reconciliation and lifecycle
    facts must reach it in one INSERT, never as an UPDATE of every row
    (04.1-20: diagnostics_dpe twice its size, WAL +60 % on run 37)."""
    connection = source_database
    pipeline = _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path)
    original_swap = pipeline.swap_tables
    seen = {}

    def inspect_staging(dsn):
        # ctid (page, item): one INSERT fills items 1..k of each page, so the
        # last items sum to the row count; an UPDATE leaves dead items behind.
        seen.update(
            connection.execute(
                text("""
            SELECT sum(rows)::int AS rows, sum(last_item)::int AS items
            FROM (
                SELECT count(*) AS rows, max((ctid::text::point)[1]) AS last_item
                FROM diagnostics_dpe_staging
                GROUP BY (ctid::text::point)[0]
            ) pages
        """)
            )
            .mappings()
            .one()
        )
        connection.commit()
        return original_swap(dsn)

    monkeypatch.setattr(pipeline, "swap_tables", inspect_staging)
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
    finally:
        pipeline.close()
    assert seen["rows"] == 9
    assert seen["items"] == seen["rows"], seen


def test_missing_source_before_swap_keeps_previous_generation(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
):
    connection = source_database
    pipeline = _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path)
    before = _served_snapshot(connection)
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline._import_manifest = replace(
            pipeline.import_manifest,
            files=pipeline.import_manifest.files[:-1],
        )
        with pytest.raises(ValueError):
            pipeline.swap()
        assert _served_snapshot(connection) == before
    finally:
        pipeline.close()


def test_raw_collision_aborts_before_transformation_or_rename(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
):
    connection = source_database

    def collide(row, _slug):
        row["numero_dpe"] = "DPE-SYNTHETIC-COLLISION"

    pipeline = _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path, collide)
    before = _served_snapshot(connection)
    try:
        with pytest.raises(ValueError, match="collision"):
            _stage_all(pipeline, pg_dsn)
        assert _served_snapshot(connection) == before
    finally:
        pipeline.close()


@pytest.mark.parametrize("historical", [False, True])
def test_raw_geometry_rejection_never_becomes_a_false_source_absence(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
    historical,
):
    """A row without source geometry is kept without position (DATA-02).

    Plan 02-16 changed the expectation from "rejected but never counted
    absent" to "stored with a null position, never counted absent": the
    intention (never a false absence) is kept and a geometry is never borrowed,
    neither from BAN nor from the previous stored row.
    """
    connection = source_database
    without_geometry = []

    def remove_geometry(row, slug):
        if (
            slug == "dpe03existant"
            and row["code_departement_ban"] == "44"
            and row["numero_dpe"].endswith("-0")
        ):
            if historical:
                row["numero_dpe"] = "DPE-SYNTHETIC-LEGACY"
            row["coordonnee_cartographique_x_ban"] = None
            without_geometry.append(row["numero_dpe"])

    pipeline = _prepare_pipeline(
        connection, pg_dsn, monkeypatch, tmp_path, remove_geometry
    )
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
        _, rows = _served_snapshot(connection)
        kept = {row["numero_dpe"]: row for row in rows}
        assert len(rows) == (8 if historical else 9)
        assert sum(row["source_state"] == "present" for row in rows) == 8
        for number in set(without_geometry):
            assert kept[number]["source_state"] == "present"
            assert kept[number]["is_tombstone"] is False
            assert kept[number]["position"] is None
            assert kept[number]["longitude"] is kept[number]["latitude"] is None
    finally:
        pipeline.close()


def _duplicate_cross_source(row, slug):
    if slug == "dpe02neuf" and row["code_departement_ban"] == "44":
        row["numero_dpe"] = row["numero_dpe"].replace("dpe02neuf", "dpe03existant")


def test_exact_cross_source_copies_merge_before_unique_constraint(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
):
    connection = source_database
    pipeline = _prepare_pipeline(
        connection,
        pg_dsn,
        monkeypatch,
        tmp_path,
        _duplicate_cross_source,
    )
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
        _, rows = _served_snapshot(connection)
        assert len(rows) == 7
        merged = [row for row in rows if len(row["source_observations"]) == 2]
        assert len(merged) == 2
        assert all(row["regime_dpe"] is None for row in merged)
        assert all(row["source_state"] == "present" for row in merged)
    finally:
        pipeline.close()


def test_invalid_source_date_survives_as_flag_not_a_failing_sql_cast(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
):
    connection = source_database

    def invalid_date(row, _slug):
        row["date_etablissement_dpe"] = "invalid-source-date"
        row["date_fin_validite_dpe"] = "2036-01-01"

    pipeline = _prepare_pipeline(
        connection, pg_dsn, monkeypatch, tmp_path, invalid_date
    )
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
        _, rows = _served_snapshot(connection)
        present = [row for row in rows if not row["is_tombstone"]]
        assert len(present) == 8
        assert all(row["date_etablissement_dpe"] is None for row in present)
        assert all(
            row["lifecycle_facts"]["date_issues"] == ["invalid_establishment_date"]
            for row in present
        )
    finally:
        pipeline.close()


@pytest.mark.parametrize("duplicates", [False, True])
def test_rename_failure_rolls_back_relation_and_can_retry_complete_staging(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
    duplicates,
):
    connection = source_database
    pipeline = _prepare_pipeline(
        connection,
        pg_dsn,
        monkeypatch,
        tmp_path,
        _duplicate_cross_source if duplicates else None,
    )
    before = _served_snapshot(connection)
    original = pipeline.rename_indexes_after_swap

    def fail_after_renames(_cursor):
        raise RuntimeError("synthetic rename failure")

    try:
        files = _stage_all(pipeline, pg_dsn)
        monkeypatch.setattr(pipeline, "rename_indexes_after_swap", fail_after_renames)
        with pytest.raises(RuntimeError, match="synthetic rename failure"):
            pipeline.swap()
        assert _served_snapshot(connection) == before
        # Plan 04.1-04: the flat table is dropped before the candidate's
        # indexes, so a retry restages the same sources (README, resume rule).
        assert not PipelineRunner._staging_table_exists(
            pg_dsn, pipeline.flat_staging_table_name
        )
        monkeypatch.setattr(pipeline, "rename_indexes_after_swap", original)
        pipeline.create_staging_table(pg_dsn)
        for path in files:
            PipelineRunner()._process_file(
                pipeline, path, [], logging.getLogger(__name__)
            )
        pipeline.swap()
        _, rows = _served_snapshot(connection)
        assert len(rows) == (7 if duplicates else 9)
    finally:
        pipeline.close()


@pytest.mark.parametrize("previous_retired", [False, True])
def test_document_move_or_reappearance_uses_only_current_payload(
    source_database,
    pg_dsn,
    monkeypatch,
    tmp_path,
    previous_retired,
):
    connection = source_database

    def move(row, slug):
        if (
            slug == "dpe03existant"
            and row["code_departement_ban"] == "85"
            and row["numero_dpe"].endswith("-0")
        ):
            row.update(
                numero_dpe="DPE-SYNTHETIC-LEGACY",
                identifiant_ban="85999_0002_00008",
                code_insee_ban="85999",
                etiquette_dpe="E",
                surface_habitable_logement=81.5,
            )

    pipeline = _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path, move)
    old_observation = {
        **OBSERVATION,
        "source_slug": "dpe02neuf",
        "observed_at": "2026-09-01T12:00:00Z",
        "last_seen_at": "2026-09-01T12:00:00Z",
        "state": "absent_from_complete_scope" if previous_retired else "present",
    }
    assignments = ""
    if previous_retired:
        keep = {
            "id",
            "numero_dpe",
            "identifiant_ban",
            "code_departement",
            "code_insee",
            "numero_dpe_remplace",
            "regime_dpe",
        } | LIFECYCLE_COLUMNS
        assignments = (
            ", ".join(f'"{key}"=NULL' for key in _row(connection) if key not in keep)
            + ", "
        )
    connection.execute(
        text(f"""
        UPDATE diagnostics_dpe SET {assignments}
            source_observations=CAST(:observations AS jsonb), source_state=:state,
            source_snapshot_at='2026-09-01T12:00:00Z', is_tombstone=:retired,
            regime_dpe='neuf'
    """),
        {
            "observations": json.dumps([old_observation]),
            "state": old_observation["state"],
            "retired": previous_retired,
        },
    )
    connection.commit()
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
        _, rows = _served_snapshot(connection)
        matching = [row for row in rows if row["numero_dpe"] == "DPE-SYNTHETIC-LEGACY"]
        assert len(rows) == 8 and len(matching) == 1
        current = matching[0]
        assert current["code_departement"] == "85"
        assert current["identifiant_ban"] == "85999_0002_00008"
        assert current["etiquette_dpe"] == "E" and current["surface_habitable"] == 81.5
        assert current["date_etablissement_dpe"] is None
        assert current["source_state"] == "present" and not current["is_tombstone"]
        assert current["regime_dpe"] is None
        assert {item["state"] for item in current["source_observations"]} == {
            "present",
            "absent_from_complete_scope",
        }
        assert not any(row["identifiant_ban"] == "44999_0001_00001" for row in rows)
    finally:
        pipeline.close()


def test_0415_columns_migration_roundtrip_preserves_legacy(source_database):
    connection = source_database
    _migrate(connection)
    before = _row(connection)
    for _ in range(2):
        _migrate(connection, name=COLUMNS_MIGRATION)
        after = _row(connection)
        assert {key: after[key] for key in COLUMNS_0415} == dict.fromkeys(COLUMNS_0415)
        assert {k: v for k, v in after.items() if k not in COLUMNS_0415} == before
        _migrate(connection, "downgrade", name=COLUMNS_MIGRATION)
        assert _row(connection) == before
    assert _migration(COLUMNS_MIGRATION).down_revision == "099_geo_arrondissements"


def test_0415_source_columns_reach_the_served_relation_exactly(
    source_database, pg_dsn, monkeypatch, tmp_path
):
    """Red before 04-15: the 8 source values were dropped before staging (C1)
    or never requested (C2, C3). Stored as published: text for C1 and C3,
    exact numeric for C2 (scale kept, no bound)."""
    connection = source_database

    def with_values(row, _slug):
        row.update(COLUMNS_0415)

    pipeline = _prepare_pipeline(connection, pg_dsn, monkeypatch, tmp_path, with_values)
    try:
        _stage_all(pipeline, pg_dsn)
        pipeline.swap()
        columns = ", ".join(f"{name}::text" for name in COLUMNS_0415)
        stored = connection.execute(
            text(
                f"SELECT is_tombstone, {columns} FROM diagnostics_dpe "
                "ORDER BY numero_dpe"
            )
        ).all()
        connection.commit()
        present = [row[1:] for row in stored if not row[0]]
        retired = [row[1:] for row in stored if row[0]]
        assert len(present) == 8
        assert set(present) == {tuple(COLUMNS_0415.values())}
        # The retired legacy document keeps no description (tombstone rule).
        assert retired == [(None,) * len(COLUMNS_0415)]
    finally:
        pipeline.close()

"""Real isolated PostGIS registry; synthetic decisions, no production caller."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.commune import indicator_storage as storage
from app.commune.dependencies import PUBLICATION_LOCK_ID
from app.commune.indicator_manifest import (
    IndicatorNotQualified,
    ManifestRefused,
    StoredManifest,
    manifest_sha256,
)

G1 = UUID("11111111-1111-4111-8111-111111111111")
G2 = UUID("22222222-2222-4222-8222-222222222222")


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(isolated_pg_dsn):
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS commune_indicator_build_activations, "
            "commune_indicator_result_cells, "
            "commune_indicator_result_builds, commune_indicator_manifests, "
            "etl_publications, "
            "diagnostics_dpe, mutations, mutations_grain, retired_source CASCADE; "
            "DROP FUNCTION IF EXISTS forbid_result_changes(); "
            "DROP FUNCTION IF EXISTS reject_indicator_rewrite()"
        )
        for name in ("diagnostics_dpe", "mutations", "mutations_grain"):
            conn.execute("CREATE TABLE " + name + " (id bigint)")
            conn.execute("INSERT INTO " + name + " VALUES (1)")
        publication_migration = module_at(
            "storage_migration094",
            Path(__file__).parents[2]
            / "migrations/versions/094_publication_generations.py",
        )
        publication_migration.op = SimpleNamespace(execute=conn.execute)
        publication_migration.upgrade()
        storage_migration = module_at(
            "storage_migration095",
            Path(__file__).parents[2]
            / "migrations/versions/095_commune_indicator_storage.py",
        )
        storage_migration.op = SimpleNamespace(execute=conn.execute)
        storage_migration.upgrade()
        activation_migration = module_at(
            "storage_migration096",
            Path(__file__).parents[2]
            / "migrations/versions/096_commune_indicator_build_activation.py",
        )
        activation_migration.op = SimpleNamespace(execute=conn.execute)
        activation_migration.upgrade()
        # Head schema of the cells: every hostable departement (plan 04.1-10).
        national_migration = module_at(
            "storage_migration103",
            Path(__file__).parents[2]
            / "migrations/versions/103_commune_indicator_cells_national.py",
        )
        national_migration.op = SimpleNamespace(execute=conn.execute)
        national_migration.upgrade()
    with psycopg.connect(isolated_pg_dsn) as conn:
        yield conn, isolated_pg_dsn


def publish(conn, dataset="dpe", generation=G1, status="published", **changes):
    table, relations = {
        "dpe": ("diagnostics_dpe", ["diagnostics_dpe"]),
        "dvf": ("mutations", ["mutations", "mutations_grain"]),
    }[dataset]
    oids = {
        name: conn.execute(
            "SELECT to_regclass(%s)::oid", ("public." + name,)
        ).fetchone()[0]
        for name in relations
    }
    values = {"table_name": table, "relation_oids": oids, "row_count": 1, **changes}
    conn.execute(
        """INSERT INTO etl_publications
        (dataset_name,generation,table_name,status,relation_oids,row_count,completed_at)
        VALUES (%s,%s,%s,%s,%s,%s,NOW())""",
        (
            dataset,
            generation,
            values["table_name"],
            status,
            Jsonb(values["relation_oids"]),
            values["row_count"],
        ),
    )


def decision(directory, generation=G1, dataset="dpe", state=None):
    # Reuse only the existing pure synthetic fixture producer, never DB mocks.
    fixtures = module_at(
        "synthetic_manifest_inputs",
        Path(__file__).parents[1] / "test_commune_indicator_manifest.py",
    )
    payload, context = fixtures.fixture(directory, generation, dataset)
    if state:
        # Explicit newer reviewed fixture, with its own files and external pins.
        for value in payload["indicators"].values():
            value.update(
                status=state, rule_version=None, reason_code="synthetic_later_decision"
            )
        policy = json.loads((directory / "policy.json").read_bytes())
        policy["indicators"] = payload["indicators"]
        policy_raw = json.dumps(policy).encode()
        ph = hashlib.sha256(policy_raw).hexdigest()
        evidence = json.loads((directory / "review.json").read_bytes())
        evidence.update(
            indicators=payload["indicators"],
            policy_sha256=ph,
            qualified_at="2026-09-09T11:00:00Z",
        )
        evidence_raw = json.dumps(evidence).encode()
        eh = hashlib.sha256(evidence_raw).hexdigest()
        (directory / "policy.json").write_bytes(policy_raw)
        (directory / "review.json").write_bytes(evidence_raw)
        payload["policy"]["sha256"] = ph
        payload["evidence"]["sha256"] = eh
        payload["qualified_at"] = evidence["qualified_at"]
        context = context.model_copy(
            update={"policy_sha256": ph, "evidence_sha256": eh}
        )
    record = StoredManifest(
        dataset_name=dataset,
        source_generation=generation,
        manifest=payload,
        manifest_sha256=manifest_sha256(payload),
        qualified_at=payload["qualified_at"],
    )
    return record, context


def lock(conn):
    conn.execute("SELECT pg_advisory_lock(%s)", (PUBLICATION_LOCK_ID,))


def append(conn, record, context, directory):
    return storage.append_manifest(
        conn, record=record, context=context, evidence_root=directory
    )


def test_exact_replay_and_caller_rollback_are_visible_from_another_connection(
    db, tmp_path
):
    conn, dsn = db
    publish(conn)
    conn.commit()
    record, context = decision(tmp_path)
    lock(conn)
    first = append(conn, record, context, tmp_path)
    replay = append(conn, record, context, tmp_path)
    assert (
        first.registry_id == replay.registry_id
        and first.inserted
        and not replay.inserted
    )
    assert not hasattr(
        replay, "manifest"
    )  # Receipt cannot bypass latest-decision selection.
    with psycopg.connect(dsn) as observer:
        assert (
            observer.execute(
                "SELECT count(*) FROM commune_indicator_manifests"
            ).fetchone()[0]
            == 0
        )
    conn.rollback()
    assert (
        conn.execute("SELECT count(*) FROM commune_indicator_manifests").fetchone()[0]
        == 0
    )
    persisted = append(conn, record, context, tmp_path)
    conn.commit()
    with psycopg.connect(dsn) as observer:
        assert (
            observer.execute("SELECT id FROM commune_indicator_manifests").fetchone()[0]
            == persisted.registry_id
        )
    loaded = storage.load_current_manifest(
        conn, context=context, evidence_root=tmp_path
    )
    loaded.manifest.require_indicator("dpe.document_count")
    assert (
        loaded.source.generation == G1
        and loaded.manifest_sha256 == record.manifest_sha256
    )


@pytest.mark.parametrize("lock_kind", ["none", "shared", "wrong", "two_integer"])
def test_append_requires_the_correct_existing_exclusive_lock(db, tmp_path, lock_kind):
    conn, _ = db
    publish(conn)
    record, context = decision(tmp_path)
    if lock_kind == "shared":
        conn.execute("SELECT pg_advisory_lock_shared(%s)", (PUBLICATION_LOCK_ID,))
    elif lock_kind == "wrong":
        conn.execute("SELECT pg_advisory_lock(%s)", (PUBLICATION_LOCK_ID + 1,))
    elif lock_kind == "two_integer":
        conn.execute(
            "SELECT pg_advisory_lock(%s::int,%s::int)",
            (PUBLICATION_LOCK_ID >> 32, PUBLICATION_LOCK_ID & 0xFFFFFFFF),
        )
    with pytest.raises(ManifestRefused, match="caller_maintenance_lock_required"):
        append(conn, record, context, tmp_path)
    assert (
        conn.execute("SELECT count(*) FROM commune_indicator_manifests").fetchone()[0]
        == 0
    )


def test_exclusive_lock_on_another_connection_does_not_admit_a_candidate_hook(
    db, tmp_path
):
    conn, dsn = db
    publish(conn)
    conn.commit()
    record, context = decision(tmp_path)
    lock(conn)
    with (
        psycopg.connect(dsn) as other,
        pytest.raises(ManifestRefused, match="caller_maintenance_lock_required"),
    ):
        append(other, record, context, tmp_path)


@pytest.mark.parametrize("dataset", ["dpe", "dvf"])
def test_legacy_generation_is_not_invented(db, tmp_path, dataset):
    conn, _ = db
    record, context = decision(tmp_path, dataset=dataset)
    lock(conn)
    with pytest.raises(ManifestRefused, match="source_generation_unqualified"):
        append(conn, record, context, tmp_path)


@pytest.mark.parametrize("state", ["failed", "pending"])
def test_latest_publication_attempt_blocks_older_published_stock(db, tmp_path, state):
    conn, _ = db
    publish(conn)
    record, context = decision(tmp_path)
    lock(conn)
    append(conn, record, context, tmp_path)
    publish(conn, generation=G2, status=state)
    for operation in (
        lambda: append(conn, record, context, tmp_path),
        lambda: storage.load_current_manifest(
            conn, context=context, evidence_root=tmp_path
        ),
    ):
        with pytest.raises(ManifestRefused, match="source_publication_not_ready"):
            operation()


def test_g1_never_qualifies_g2_then_explicit_g2_fixture_works(db, tmp_path):
    conn, _ = db
    old, old_context = decision(tmp_path / "old")
    current, context = decision(tmp_path / "current", G2)
    publish(conn)
    lock(conn)
    append(conn, old, old_context, tmp_path / "old")
    publish(conn, generation=G2)
    with pytest.raises(ManifestRefused, match="source_generation_unqualified"):
        storage.load_current_manifest(
            conn, context=old_context, evidence_root=tmp_path / "old"
        )
    with pytest.raises(ManifestRefused, match="source_generation_unqualified"):
        storage.load_current_manifest(
            conn, context=context, evidence_root=tmp_path / "current"
        )
    append(conn, current, context, tmp_path / "current")
    loaded = storage.load_current_manifest(
        conn, context=context, evidence_root=tmp_path / "current"
    )
    assert loaded.source.generation == loaded.manifest.source_generation == G2
    assert conn.execute("SELECT count(*) FROM etl_publications").fetchone()[0] == 2


@pytest.mark.parametrize("state", ["no_go", "pending"])
def test_later_decision_by_id_supersedes_qualified_even_with_earlier_timestamp(
    db, tmp_path, state
):
    conn, _ = db
    publish(conn)
    good, old_context = decision(tmp_path / "good")
    later, context = decision(tmp_path / "later", state=state)
    lock(conn)
    first = append(conn, good, old_context, tmp_path / "good")
    last = append(conn, later, context, tmp_path / "later")
    assert (
        last.registry_id > first.registry_id and later.qualified_at < good.qualified_at
    )
    replay = append(conn, good, old_context, tmp_path / "good")
    assert not replay.inserted and replay.registry_id == first.registry_id
    with pytest.raises(ManifestRefused):
        storage.load_current_manifest(
            conn, context=old_context, evidence_root=tmp_path / "good"
        )
    loaded = storage.load_current_manifest(
        conn, context=context, evidence_root=tmp_path / "later"
    )
    assert loaded.registry_id == last.registry_id
    with pytest.raises(IndicatorNotQualified) as outcome:
        loaded.manifest.require_indicator("dpe.document_count")
    assert outcome.value.state == state


@pytest.mark.parametrize(
    "dataset,relation",
    [("dpe", "diagnostics_dpe"), ("dvf", "mutations"), ("dvf", "mutations_grain")],
)
def test_actual_source_swap_invalidates_oid_identity(db, dataset, relation):
    conn, _ = db
    publish(conn, dataset=dataset)
    before = storage.read_published_source(conn, dataset)
    conn.execute("ALTER TABLE " + relation + " RENAME TO retired_source")
    conn.execute("CREATE TABLE " + relation + " (id bigint)")
    conn.execute("INSERT INTO " + relation + " VALUES (1)")
    assert before.recorded_row_count == 1
    with pytest.raises(ManifestRefused, match="source_publication_not_ready"):
        storage.read_published_source(conn, dataset)


@pytest.mark.parametrize(
    "changes",
    [
        {"table_name": "mutations"},
        {"relation_oids": {}},
        {"relation_oids": {"diagnostics_dpe": 1, "unrelated": 2}},
        {"relation_oids": {"diagnostics_dpe": "1"}},
        {"row_count": None},
    ],
)
def test_incomplete_or_wrong_publication_identity_is_refused(db, changes):
    conn, _ = db
    publish(conn, **changes)
    with pytest.raises(ManifestRefused, match="source_publication_not_ready"):
        storage.read_published_source(conn, "dpe")


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "publication"),
        ("source_snapshot_sha256", "f" * 64),
        ("policy_sha256", "f" * 64),
        ("evidence_sha256", "f" * 64),
        ("dataset_name", "dvf"),
    ],
)
def test_external_trust_pins_are_not_rebuilt_from_the_record(
    db, tmp_path, field, value
):
    conn, _ = db
    publish(conn)
    record, context = decision(tmp_path)
    lock(conn)
    bad = context.model_copy(update={field: value})
    with pytest.raises(ManifestRefused):
        append(conn, record, bad, tmp_path)
    assert (
        conn.execute("SELECT count(*) FROM commune_indicator_manifests").fetchone()[0]
        == 0
    )


def test_canonical_hash_tamper_and_existing_hash_collision_are_refused(db, tmp_path):
    conn, _ = db
    publish(conn)
    record, context = decision(tmp_path)
    lock(conn)
    with pytest.raises(ManifestRefused):
        append(
            conn,
            record.model_copy(update={"manifest_sha256": "f" * 64}),
            context,
            tmp_path,
        )
    # Deliberately corrupted fixture row: same hash, different envelope timestamp.
    conn.execute(
        """INSERT INTO commune_indicator_manifests
        (dataset_name,source_generation,manifest,manifest_sha256,qualified_at)
        VALUES (%s,%s,%s,%s,'2026-09-09T11:00:00Z')""",
        (record.dataset_name, G1, Jsonb(record.manifest), record.manifest_sha256),
    )
    with pytest.raises(ManifestRefused, match="manifest_collision"):
        append(conn, record, context, tmp_path)
    with pytest.raises(ManifestRefused):
        storage.load_current_manifest(conn, context=context, evidence_root=tmp_path)


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE commune_indicator_manifests SET qualified_at=NOW()",
        "DELETE FROM commune_indicator_manifests",
        "TRUNCATE commune_indicator_manifests",
    ],
)
def test_fixture_database_enforces_append_only(db, tmp_path, operation):
    conn, _ = db
    publish(conn)
    record, context = decision(tmp_path)
    lock(conn)
    append(conn, record, context, tmp_path)
    with (
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
        conn.transaction(),
    ):
        conn.execute(operation)
    assert (
        conn.execute("SELECT count(*) FROM commune_indicator_manifests").fetchone()[0]
        == 1
    )


def test_autocommit_refused_and_no_advisory_lock_acquired_by_reads(db, tmp_path):
    conn, dsn = db
    publish(conn)
    conn.commit()
    record, context = decision(tmp_path)
    with (
        psycopg.connect(dsn, autocommit=True) as other,
        pytest.raises(ManifestRefused, match="caller_transaction_required"),
    ):
        append(other, record, context, tmp_path)
    storage.read_published_source(conn, "dpe")
    assert (
        conn.execute(
            "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
            "AND locktype='advisory'"
        ).fetchone()[0]
        == 0
    )


def test_postgres_oid_json_needs_an_explicit_numeric_cast(db):
    conn, _ = db
    raw, numeric = conn.execute(
        "SELECT jsonb_build_object('oid','diagnostics_dpe'::regclass::oid), "
        "jsonb_build_object('oid','diagnostics_dpe'::regclass::oid::bigint)"
    ).fetchone()
    assert type(raw["oid"]) is str and type(numeric["oid"]) is int
    assert int(raw["oid"]) == numeric["oid"]


def test_supplied_connection_row_factory_does_not_change_the_lock_contract(
    db, tmp_path
):
    conn, dsn = db
    publish(conn)
    conn.commit()
    record, context = decision(tmp_path)
    with psycopg.connect(dsn, row_factory=dict_row) as caller:
        lock(caller)
        receipt = append(caller, record, context, tmp_path)
        loaded = storage.load_current_manifest(
            caller, context=context, evidence_root=tmp_path
        )
        assert loaded.registry_id == receipt.registry_id


def test_other_dataset_generation_cannot_be_registered_as_dpe(db, tmp_path):
    conn, _ = db
    publish(conn, dataset="dvf")
    record, context = decision(tmp_path)
    lock(conn)
    with pytest.raises(ManifestRefused, match="source_generation_unqualified"):
        append(conn, record, context, tmp_path)
    assert (
        conn.execute("SELECT count(*) FROM commune_indicator_manifests").fetchone()[0]
        == 0
    )

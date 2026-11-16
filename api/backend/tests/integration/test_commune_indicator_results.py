"""Guarded synthetic result persistence on a disposable PostgreSQL only."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.commune import indicator_results as results
from app.commune.indicator_context import build_indicator_context
from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_projector import project_indicator_blocks
from app.etl.publication import Publication
from tests.integration import test_commune_indicator_projector as fixture
from tests.integration import test_commune_indicator_storage as registry
from tests.integration.test_commune_indicator_projector import (
    NOW,
    REQUEST,
    prepare,
    register,
)

# New per-module database of the wrapper; no source fixture is modified.
db = fixture.db
source = fixture.source


@pytest.fixture
def store(source):
    conn, dsn = source
    conn.execute("DROP TABLE IF EXISTS caller_marker CASCADE")
    conn.commit()
    yield conn, dsn


def counts(conn):
    return tuple(
        conn.execute(
            "SELECT (SELECT count(*) FROM "
            "commune_indicator_result_builds), (SELECT count(*) FROM "
            "commune_indicator_result_cells)"
        ).fetchone()
    )


def allow_corruption(conn):
    # Explicit test-only superuser tampering in this disposable fixture.
    conn.execute(
        "ALTER TABLE commune_indicator_result_cells DISABLE TRIGGER immutable_results"
    )
    conn.execute(
        "ALTER TABLE commune_indicator_result_builds DISABLE TRIGGER immutable_results"
    )


@pytest.mark.parametrize("n", [0, 4, 5])
def test_roundtrip_exact_920_and_fresh_context_without_aggregate(
    store, tmp_path, monkeypatch, n
):
    conn, dsn = store
    inputs, ctx = prepare(conn, tmp_path, n)
    expected = [
        r.public_dump()
        for r in project_indicator_blocks(conn, context=ctx, request=REQUEST)
    ]
    receipt = results.build_and_store(conn, context=ctx, request=REQUEST)
    assert (
        receipt.inserted and receipt.commune_count == 460 and receipt.cell_count == 920
    )
    with psycopg.connect(dsn) as observer:
        assert counts(observer) == (0, 0)
    conn.commit()
    with psycopg.connect(dsn) as observer:
        assert counts(observer) == (1, 920)
    registry.lock(conn)
    fresh = build_indicator_context(
        conn, trust=inputs.model_copy(update={"checked_at": NOW + timedelta(hours=1)})
    )
    monkeypatch.setattr(
        results,
        "project_indicator_blocks",
        lambda *a, **k: pytest.fail("reader recalculated"),
    )
    loaded = results.read_build(conn, build_id=receipt.build_id, context=fresh)
    assert loaded.public_dump() == expected
    assert not hasattr(loaded, "header") and not hasattr(loaded, "bindings")
    public = loaded.public_dump()
    public[0]["indicateurs_dpe"] = {"private": "changed"}
    assert loaded.public_dump() == expected
    assert all(
        "binding" not in str(r) and "registry_id" not in str(r) for r in expected
    )


def test_replay_is_exact_and_outer_rollback_retains_caller_ownership(store, tmp_path):
    conn, _ = store
    _, ctx = prepare(conn, tmp_path)
    first = results.build_and_store(conn, context=ctx, request=REQUEST)
    again = results.build_and_store(conn, context=ctx, request=REQUEST)
    assert first.build_id == again.build_id and again.inserted is False
    assert counts(conn) == (1, 920)
    ctx.assert_current(conn)
    conn.rollback()
    assert counts(conn) == (0, 0)


@pytest.mark.parametrize("stage", ["header", "cell", "python"])
def test_savepoint_cleans_partial_write_even_when_caller_commits(
    store, tmp_path, monkeypatch, stage
):
    conn, dsn = store
    _, ctx = prepare(conn, tmp_path)
    conn.execute("CREATE TABLE caller_marker(value integer)")
    conn.execute("INSERT INTO caller_marker VALUES (1)")
    if stage in ("header", "cell"):
        table = (
            "commune_indicator_result_builds"
            if stage == "header"
            else "commune_indicator_result_cells"
        )
        condition = "TRUE" if stage == "header" else "NEW.code_insee='44109'"
        conn.execute(f"""CREATE OR REPLACE FUNCTION reject_partial_result()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF {condition} THEN
              RAISE EXCEPTION 'synthetic_write_failure' USING ERRCODE='P0001';
            END IF;
            RETURN NEW; END $$;
            CREATE TRIGGER reject_partial AFTER INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_partial_result()""")
        expected = psycopg.errors.RaiseException
    else:
        original = type(ctx).assert_current

        def final_failure(self, connection):
            original(self, connection)
            if counts(connection) == (1, 920):
                raise RuntimeError("synthetic_final_check_failure")

        monkeypatch.setattr(type(ctx), "assert_current", final_failure)
        expected = RuntimeError
    with pytest.raises(expected):
        results.build_and_store(conn, context=ctx, request=REQUEST)
    assert counts(conn) == (0, 0)
    conn.commit()
    with psycopg.connect(dsn) as observer:
        assert counts(observer) == (0, 0)
        assert observer.execute("SELECT count(*) FROM caller_marker").fetchone()[0] == 1


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "cell_deleted",
        "key_replaced",
        "public_changed",
        "binding_changed",
        "header_changed",
        "oversized",
    ],
)
def test_exact_build_missing_and_corruption_never_fallback(store, tmp_path, fault):
    conn, _ = store
    _, ctx = prepare(conn, tmp_path)
    receipt = results.build_and_store(conn, context=ctx, request=REQUEST)
    chosen = receipt.build_id
    allow_corruption(conn)
    if fault == "missing":
        chosen += 1000
    elif fault == "cell_deleted":
        conn.execute(
            (
                "DELETE FROM commune_indicator_result_cells WHERE build_id=%s"
                " AND dataset_name='dpe' AND code_insee='44001'"
            ),
            (chosen,),
        )
    elif fault == "key_replaced":
        conn.execute(
            (
                "UPDATE commune_indicator_result_cells SET code_insee='44999'"
                " WHERE build_id=%s AND dataset_name='dpe' AND "
                "code_insee='44001'"
            ),
            (chosen,),
        )
    elif fault == "public_changed":
        conn.execute(
            (
                "UPDATE commune_indicator_result_cells SET "
                "public_payload='{}' WHERE build_id=%s AND code_insee='44001'"
            ),
            (chosen,),
        )
    elif fault == "binding_changed":
        conn.execute(
            (
                "UPDATE commune_indicator_result_cells SET "
                "private_binding='{}' WHERE build_id=%s AND "
                "code_insee='44001'"
            ),
            (chosen,),
        )
    elif fault == "header_changed":
        conn.execute(
            (
                "UPDATE commune_indicator_result_builds SET header=header || "
                "%s WHERE id=%s"
            ),
            (Jsonb({"private_extra": "PRIVATE_SENTINEL_1847"}), chosen),
        )
    else:
        conn.execute(
            (
                "UPDATE commune_indicator_result_cells SET "
                "public_payload=jsonb_build_object('padding',repeat('x',70000))"
                " WHERE build_id=%s AND code_insee='44001'"
            ),
            (chosen,),
        )
    with pytest.raises(results.ResultStoreRefused) as failure:
        results.read_build(conn, build_id=chosen, context=ctx)
    assert failure.value.code == (
        "result_build_missing" if fault == "missing" else "result_build_integrity"
    )
    assert "PRIVATE_SENTINEL" not in str(failure.value)


@pytest.mark.parametrize(
    "table", ["commune_indicator_result_builds", "commune_indicator_result_cells"]
)
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE", "TRUNCATE"])
def test_fixture_tables_really_refuse_rewrites(store, tmp_path, table, verb):
    conn, _ = store
    _, ctx = prepare(conn, tmp_path)
    results.build_and_store(conn, context=ctx, request=REQUEST)
    statement = (
        (
            f"UPDATE {table} SET "
            + (
                "header=header"
                if table.endswith("builds")
                else "public_payload=public_payload"
            )
        )
        if verb == "UPDATE"
        else f"{verb} " + ("FROM " if verb == "DELETE" else "") + table
    )
    with pytest.raises(psycopg.Error), conn.transaction():
        conn.execute(statement)


@pytest.mark.parametrize("dataset", ["dvf", "dpe"])
@pytest.mark.parametrize("change", ["no_go", "no_pins"])
def test_new_decision_or_lost_qualification_is_stale(store, tmp_path, dataset, change):
    conn, _ = store
    inputs, ctx = prepare(conn, tmp_path)
    receipt = results.build_and_store(conn, context=ctx, request=REQUEST)
    pins = (
        None
        if change == "no_pins"
        else register(conn, tmp_path, inputs, dataset, state="no_go")
    )
    fresh = build_indicator_context(
        conn,
        trust=inputs.model_copy(
            update={dataset: pins, "checked_at": NOW + timedelta(hours=1)}
        ),
    )
    with pytest.raises(results.ResultStoreRefused, match="result_build_stale"):
        results.read_build(conn, build_id=receipt.build_id, context=fresh)


def test_registry_observed_even_when_decision_is_none(store, tmp_path):
    conn, _ = store
    inputs, _ctx = prepare(conn, tmp_path)
    absent = build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": None})
    )
    assert (
        absent.datasets[0].registry_identity is not None
        and absent.datasets[0].decision is None
    )
    receipt = results.build_and_store(conn, context=absent, request=REQUEST)
    register(conn, tmp_path, inputs, "dpe", state="no_go")
    fresh = build_indicator_context(conn, trust=inputs.model_copy(update={"dpe": None}))
    with pytest.raises(results.ResultStoreRefused, match="result_build_stale"):
        results.read_build(conn, build_id=receipt.build_id, context=fresh)


def test_absence_to_first_loaded_qualification_is_stale(store, tmp_path):
    conn, _ = store
    inputs, _ctx = prepare(conn, tmp_path)
    absent = build_indicator_context(
        conn, trust=inputs.model_copy(update={"dpe": None})
    )
    receipt = results.build_and_store(conn, context=absent, request=REQUEST)
    fresh = build_indicator_context(conn, trust=inputs)
    with pytest.raises(results.ResultStoreRefused, match="result_build_stale"):
        results.read_build(conn, build_id=receipt.build_id, context=fresh)


def test_candidate_pending_history_is_not_rewritten_after_publish(store, tmp_path):
    conn, dsn = store
    inputs, _ctx = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    with Publication(
        dsn,
        SimpleNamespace(dataset_name="dpe", production_table_name="diagnostics_dpe"),
    ) as pub:
        pub.observe_stock()
        with pub.required_work() as work:
            pending = build_indicator_context(
                work.connection, trust=inputs, publication_work=work
            )
            receipt = results.build_and_store(
                work.connection, context=pending, request=REQUEST
            )
            before = work.connection.execute(
                "SELECT header FROM commune_indicator_result_builds WHERE id=%s",
                (receipt.build_id,),
            ).fetchone()[0]
            assert all(
                r["indicateurs_dpe"]["etat"] == "not_qualified"
                for r in results.read_build(
                    work.connection, build_id=receipt.build_id, context=pending
                ).public_dump()
            )
            pub.publish(lambda _: None)
    with psycopg.connect(dsn) as reader:
        registry.lock(reader)
        fresh = build_indicator_context(reader, trust=inputs)
        with pytest.raises(results.ResultStoreRefused, match="result_build_stale"):
            results.read_build(reader, build_id=receipt.build_id, context=fresh)
        assert (
            reader.execute(
                "SELECT header FROM commune_indicator_result_builds WHERE id=%s",
                (receipt.build_id,),
            ).fetchone()[0]
            == before
        )


def test_two_invalid_pin_sets_are_equivalent_absences_but_valid_pins_expire(
    store, tmp_path
):
    conn, _ = store
    inputs, _ = prepare(conn, tmp_path)

    def wrong(digest):
        return inputs.model_copy(
            update={"dpe": inputs.dpe.model_copy(update={"policy_sha256": digest * 64})}
        )

    first = build_indicator_context(conn, trust=wrong("c"))
    second = build_indicator_context(conn, trust=wrong("d"))
    assert first.datasets[0].decision is second.datasets[0].decision is None
    assert first.datasets[0].refusal == second.datasets[0].refusal
    receipt = results.build_and_store(conn, context=first, request=REQUEST)
    assert (
        results.read_build(
            conn, build_id=receipt.build_id, context=second
        ).public_dump()[0]["indicateurs_dpe"]["etat"]
        == "not_qualified"
    )
    valid = build_indicator_context(conn, trust=inputs)
    with pytest.raises(results.ResultStoreRefused, match="result_build_stale"):
        results.read_build(conn, build_id=receipt.build_id, context=valid)


def test_new_published_generation_is_stale_without_any_previous_fallback(
    store, tmp_path
):
    conn, _ = store
    inputs, ctx = prepare(conn, tmp_path)
    receipt = results.build_and_store(conn, context=ctx, request=REQUEST)
    registry.publish(conn, generation=UUID("33333333-3333-4333-8333-333333333333"))
    fresh = build_indicator_context(conn, trust=inputs)
    with pytest.raises(results.ResultStoreRefused, match="result_build_stale"):
        results.read_build(conn, build_id=receipt.build_id, context=fresh)


@pytest.mark.parametrize("action", ["commit", "rollback", "unlock", "oid"])
def test_stale_owner_and_technical_oid_faults_are_not_public_absences(
    store, tmp_path, action
):
    conn, _ = store
    _inputs, ctx = prepare(conn, tmp_path)
    receipt = results.build_and_store(conn, context=ctx, request=REQUEST)
    if action == "commit":
        conn.commit()
        conn.execute("SELECT 1")
    elif action == "rollback":
        conn.rollback()
        conn.execute("SELECT 1")
    elif action == "unlock":
        conn.execute("SELECT pg_advisory_unlock_all()")
    else:
        conn.execute("ALTER TABLE diagnostics_dpe RENAME TO retired_dpe")
        conn.execute("CREATE TABLE diagnostics_dpe(id bigint)")
    with pytest.raises(ManifestRefused):
        results.read_build(conn, build_id=receipt.build_id, context=ctx)
    with pytest.raises(ManifestRefused):
        results.build_and_store(conn, context=ctx, request=REQUEST)


def test_exact_read_old_build_after_newer_build_and_dict_row(store, tmp_path):
    from psycopg.rows import dict_row

    conn, _ = store
    _, ctx = prepare(conn, tmp_path)
    conn.row_factory = dict_row
    first = results.build_and_store(conn, context=ctx, request=REQUEST)
    later = results.build_and_store(
        conn,
        context=ctx,
        request=replace(REQUEST, calculated_at=NOW + timedelta(hours=1)),
    )
    assert first.build_id != later.build_id
    old = results.read_build(conn, build_id=first.build_id, context=ctx).public_dump()
    new = results.read_build(conn, build_id=later.build_id, context=ctx).public_dump()
    assert (
        old[0]["indicateurs_dpe"]["date_calcul"]
        != new[0]["indicateurs_dpe"]["date_calcul"]
    )
    replay = results.build_and_store(conn, context=ctx, request=REQUEST)
    assert replay.build_id == first.build_id and not replay.inserted


def test_921_cells_refused_without_fetching_unbounded_results(store, tmp_path):
    conn, _ = store
    _, ctx = prepare(conn, tmp_path)
    receipt = results.build_and_store(conn, context=ctx, request=REQUEST)
    conn.execute(
        (
            "INSERT INTO commune_indicator_result_cells SELECT "
            "build_id,dataset_name,'44999',public_payload,private_binding"
            " FROM commune_indicator_result_cells WHERE build_id=%s AND "
            "dataset_name='dpe' AND code_insee='44001'"
        ),
        (receipt.build_id,),
    )
    with pytest.raises(results.ResultStoreRefused, match="result_build_integrity"):
        results.read_build(conn, build_id=receipt.build_id, context=ctx)


def test_digest_collision_never_substitutes_complete_equality(
    store, tmp_path, monkeypatch
):
    conn, _ = store
    _, ctx = prepare(conn, tmp_path)
    monkeypatch.setattr(results, "_digest", lambda value: "0" * 64)
    first = results.build_and_store(conn, context=ctx, request=REQUEST)
    with pytest.raises(results.ResultStoreRefused, match="result_build_integrity"):
        results.build_and_store(
            conn,
            context=ctx,
            request=replace(REQUEST, calculated_at=NOW + timedelta(hours=1)),
        )
    assert counts(conn) == (1, 920)
    assert results.read_build(conn, build_id=first.build_id, context=ctx).public_dump()[
        0
    ]["indicateurs_dpe"]["date_calcul"] == NOW.isoformat().replace("+00:00", "Z")

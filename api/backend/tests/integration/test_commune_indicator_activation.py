"""Durable internal activation of exact synthetic indicator builds."""

import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace

import psycopg
import pytest

from app.commune import indicator_binding as binding
from app.commune.indicator_manifest import ManifestRefused
from app.commune.indicator_projector import ProjectionRequest
from app.etl.publication import Publication, maintenance_connection
from tests.integration.test_commune_indicator_projector import NOW, prepare

pytest_plugins = ("tests.integration.test_commune_indicator_results",)


def test_rebuild_activate_revoke_and_reactivate_follow_last_event(store, tmp_path):
    from app.commune import indicator_activation as activation

    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    request_a = ProjectionRequest(NOW, "synthetic")
    request_b = ProjectionRequest(NOW.replace(hour=NOW.hour + 1), "synthetic")
    with maintenance_connection(dsn) as owner:
        first = binding.rebuild_and_activate_indicator_results(
            owner, trust=trust, request=request_a
        )
    with maintenance_connection(dsn) as owner:
        second = binding.rebuild_and_activate_indicator_results(
            owner, trust=trust, request=request_b
        )
        assert second.build_id > first.build_id
        activation.revoke_active_build(owner)
    with (
        maintenance_connection(dsn) as owner,
        pytest.raises(activation.ActiveBuildRefused, match="active_build_revoked"),
    ):
        activation.read_active_build(owner, trust=trust)
    with maintenance_connection(dsn) as owner:
        activation.activate_build(owner, build_id=first.build_id, trust=trust)
    with maintenance_connection(dsn) as owner:
        loaded = activation.read_active_build(owner, trust=trust).public_dump()
        assert loaded[0]["code_insee"] == "44001"


def test_revoke_requires_owner_but_does_not_read_sources(store, tmp_path):
    from app.commune import indicator_activation as activation

    conn, _ = store
    conn.execute("SELECT 1")
    with pytest.raises(Exception, match="caller_maintenance_lock_required"):
        activation.revoke_active_build(conn)


@pytest.mark.parametrize(
    "fault", ["expired", "autocommit", "other_connection", "repeatable_read"]
)
def test_revoke_rejects_every_non_owner_connection_without_event(store, fault):
    from app.commune import indicator_activation as activation
    from tests.integration import test_commune_indicator_storage as registry

    conn, dsn = store
    if fault == "expired":
        registry.lock(conn)
        conn.commit()
        conn.execute("SELECT pg_advisory_unlock_all()")
        conn.commit()
        conn.execute("SELECT 1")
        target = conn
    elif fault == "autocommit":
        target = psycopg.connect(dsn, autocommit=True)
    elif fault == "other_connection":
        registry.lock(conn)
        target = psycopg.connect(dsn)
    else:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        registry.lock(conn)
        target = conn
    try:
        with pytest.raises(ManifestRefused):
            activation.revoke_active_build(target)
        assert (
            target.execute(
                "SELECT count(*) FROM commune_indicator_build_activations"
            ).fetchone()[0]
            == 0
        )
    finally:
        if target is not conn:
            target.close()


def test_activation_post_write_failure_rolls_back_only_its_event(
    store, tmp_path, monkeypatch
):
    from app.commune import indicator_activation as activation
    from app.commune import indicator_results as results
    from tests.integration import test_commune_indicator_storage as registry

    conn, _ = store
    trust, context = prepare(conn, tmp_path)
    receipt = results.build_and_store(
        conn, context=context, request=ProjectionRequest(NOW, "synthetic")
    )
    original = activation.read_build
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("post_write_canary")
        return original(*args, **kwargs)

    monkeypatch.setattr(activation, "read_build", fail_second)
    with pytest.raises(RuntimeError, match="post_write_canary"):
        activation.activate_build(conn, build_id=receipt.build_id, trust=trust)
    conn.execute("CREATE TABLE caller_marker(id int)")
    conn.commit()
    with psycopg.connect(store[1]) as observer:
        assert (
            observer.execute(
                "SELECT count(*) FROM commune_indicator_build_activations"
            ).fetchone()[0]
            == 0
        )
        assert observer.execute("SELECT to_regclass('caller_marker')").fetchone()[0]
    registry.lock(conn)


def test_rebuild_refuses_idle_connection_even_with_session_lock(store, tmp_path):
    from app.commune import indicator_binding as binding
    from tests.integration import test_commune_indicator_storage as registry

    conn, _ = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    registry.lock(conn)
    conn.commit()
    with pytest.raises(Exception, match="owner_transaction_required"):
        binding.rebuild_and_activate_indicator_results(
            conn, trust=trust, request=ProjectionRequest(NOW, "synthetic")
        )
    assert conn.info.transaction_status.name == "IDLE"


def test_revoke_survives_failed_latest_source_without_reading_build(store, tmp_path):
    from uuid import UUID

    from app.commune import indicator_activation as activation
    from tests.integration import test_commune_indicator_storage as registry

    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    with maintenance_connection(dsn) as owner:
        binding.rebuild_and_activate_indicator_results(
            owner, trust=trust, request=ProjectionRequest(NOW, "synthetic")
        )
    with maintenance_connection(dsn) as owner:
        registry.publish(
            owner,
            generation=UUID("33333333-3333-4333-8333-333333333333"),
            status="failed",
        )
        activation.revoke_active_build(owner)
    with (
        maintenance_connection(dsn) as owner,
        pytest.raises(activation.ActiveBuildRefused, match="active_build_revoked"),
    ):
        activation.read_active_build(owner, trust=trust)


@pytest.mark.parametrize("replay", [False, True])
def test_rebuild_failure_rolls_back_build_or_replay_event_but_keeps_caller_work(
    store, tmp_path, monkeypatch, replay
):
    from app.commune import indicator_results as results

    conn, dsn = store
    trust, context = prepare(conn, tmp_path)
    if replay:
        results.build_and_store(
            conn, context=context, request=ProjectionRequest(NOW, "synthetic")
        )
    before = conn.execute(
        "SELECT count(*) FROM commune_indicator_result_builds"
    ).fetchone()[0]
    conn.execute("CREATE TABLE caller_marker(id int)")
    original = binding.activate_build

    def fail_after_event(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("activation_canary")

    monkeypatch.setattr(binding, "activate_build", fail_after_event)
    with pytest.raises(RuntimeError, match="activation_canary"):
        binding.rebuild_and_activate_indicator_results(
            conn, trust=trust, request=ProjectionRequest(NOW, "synthetic")
        )
    conn.commit()
    with psycopg.connect(dsn) as observer:
        assert (
            observer.execute(
                "SELECT count(*) FROM commune_indicator_result_builds"
            ).fetchone()[0]
            == before
        )
        assert (
            observer.execute(
                "SELECT count(*) FROM commune_indicator_build_activations"
            ).fetchone()[0]
            == 0
        )
        assert observer.execute("SELECT to_regclass('caller_marker')").fetchone()[0]


@pytest.mark.parametrize("fault", ["no_go", "new_generation", "oid", "cog", "pins"])
def test_active_reader_revalidates_latest_registry_and_source(store, tmp_path, fault):
    from uuid import UUID

    from app.commune import indicator_activation as activation
    from tests.integration import test_commune_indicator_storage as registry
    from tests.integration.test_commune_indicator_projector import register

    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    with maintenance_connection(dsn) as owner:
        binding.rebuild_and_activate_indicator_results(
            owner, trust=trust, request=ProjectionRequest(NOW, "synthetic")
        )
    with maintenance_connection(dsn) as owner:
        if fault == "no_go":
            register(owner, tmp_path, trust, "dpe", state="no_go")
        elif fault == "new_generation":
            registry.publish(
                owner,
                generation=UUID("33333333-3333-4333-8333-333333333333"),
            )
        else:
            if fault == "oid":
                owner.execute("ALTER TABLE diagnostics_dpe RENAME TO retired_dpe")
                owner.execute("CREATE TABLE diagnostics_dpe(id bigint)")
            elif fault == "cog":
                trust = trust.model_copy(
                    update={"cog": trust.cog.model_copy(update={"sha256": "0" * 64})}
                )
            else:
                trust = trust.model_copy(
                    update={
                        "dpe": trust.dpe.model_copy(
                            update={
                                "source_generation": UUID(
                                    "44444444-4444-4444-8444-444444444444"
                                )
                            }
                        )
                    }
                )
    with (
        maintenance_connection(dsn) as owner,
        pytest.raises(Exception, match=r"stale|unqualified|not_ready|cog_hash"),
    ):
        activation.read_active_build(owner, trust=trust)


@pytest.mark.parametrize("fault", ["header", "cell"])
def test_active_reader_rejects_corrupted_exact_build(store, tmp_path, fault):
    from app.commune import indicator_activation as activation
    from tests.integration.test_commune_indicator_results import allow_corruption

    conn, _ = store
    trust, _ = prepare(conn, tmp_path)
    built = binding.rebuild_and_activate_indicator_results(
        conn, trust=trust, request=ProjectionRequest(NOW, "synthetic")
    )
    allow_corruption(conn)
    if fault == "header":
        conn.execute(
            "UPDATE commune_indicator_result_builds SET header=header || "
            '\'{"scope": "tampered"}\'::jsonb WHERE id=%s',
            (built.build_id,),
        )
    else:
        conn.execute(
            "UPDATE commune_indicator_result_cells SET public_payload='{}' "
            "WHERE build_id=%s AND dataset_name='dpe' AND code_insee='44001'",
            (built.build_id,),
        )
    with pytest.raises(Exception, match=r"integrity|corrupt|stale"):
        activation.read_active_build(conn, trust=trust)


def test_activation_rejects_source_none_and_non_synthetic_scope(store, tmp_path):
    from app.commune import indicator_activation as activation
    from app.commune import indicator_results as results
    from app.commune.indicator_context import build_indicator_context
    from tests.integration import test_commune_indicator_storage as registry
    from tests.integration.test_commune_indicator_context import trust as make_trust

    conn, _ = store
    inputs = make_trust(tmp_path)
    registry.lock(conn)
    context = build_indicator_context(conn, trust=inputs)
    receipt = results.build_and_store(
        conn, context=context, request=ProjectionRequest(NOW, "synthetic")
    )
    with pytest.raises(activation.ActiveBuildRefused, match="source_not_published"):
        activation.activate_build(conn, build_id=receipt.build_id, trust=inputs)
    runtime = inputs.model_construct(**{**inputs.__dict__, "scope": "runtime"})
    with pytest.raises(activation.ActiveBuildRefused, match="stale"):
        activation.read_active_build(conn, trust=runtime)
    assert (
        conn.execute(
            "SELECT count(*) FROM commune_indicator_build_activations"
        ).fetchone()[0]
        == 0
    )


def test_activation_rejects_candidate_pending_build(store, tmp_path):
    from app.commune import indicator_activation as activation
    from app.commune import indicator_results as results
    from app.commune.indicator_context import build_indicator_context

    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    pipeline = SimpleNamespace(
        dataset_name="dpe", production_table_name="diagnostics_dpe"
    )
    with Publication(dsn, pipeline) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            pending = build_indicator_context(
                work.connection, trust=trust, publication_work=work
            )
            receipt = results.build_and_store(
                work.connection,
                context=pending,
                request=ProjectionRequest(NOW, "synthetic"),
            )
            with pytest.raises(
                Exception, match=r"stale|source_not_published|publication_not_ready"
            ):
                activation.activate_build(
                    work.connection, build_id=receipt.build_id, trust=trust
                )
            assert (
                work.connection.execute(
                    "SELECT count(*) FROM commune_indicator_build_activations"
                ).fetchone()[0]
                == 0
            )
            publication.publish(lambda _: None)


def test_second_writer_waits_for_lock_and_commits_the_next_event(store, tmp_path):
    from app.commune.dependencies import PUBLICATION_LOCK_ID

    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    later = ProjectionRequest(NOW.replace(hour=NOW.hour + 1), "synthetic")
    writer_pids: Queue[int] = Queue(maxsize=1)

    def second_writer():
        with psycopg.connect(dsn) as owner:
            writer_pids.put(owner.info.backend_pid)
            owner.execute("SELECT pg_advisory_lock(%s)", (PUBLICATION_LOCK_ID,))
            return binding.rebuild_and_activate_indicator_results(
                owner, trust=trust, request=later
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with maintenance_connection(dsn) as first_owner:
            first = binding.rebuild_and_activate_indicator_results(
                first_owner,
                trust=trust,
                request=ProjectionRequest(NOW, "synthetic"),
            )
            with psycopg.connect(dsn, autocommit=True) as observer:
                # Prime statistics before the worker exists: a transaction-scoped
                # snapshot would then hide its later advisory-lock wait forever.
                observer.execute("SELECT count(*) FROM pg_stat_activity").fetchone()
                future = executor.submit(second_writer)
                writer_pid = writer_pids.get(timeout=5)
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline:
                    waiting = observer.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE pid=%s AND wait_event_type='Lock' "
                        "AND wait_event='advisory' "
                        "AND %s = ANY(pg_blocking_pids(pid)))",
                        (writer_pid, first_owner.info.backend_pid),
                    ).fetchone()[0]
                    if waiting:
                        break
                    time.sleep(0.05)
                assert waiting and not future.done()
                assert (
                    observer.execute(
                        "SELECT count(*) FROM commune_indicator_build_activations"
                    ).fetchone()[0]
                    == 0
                )
        second = future.result(timeout=10)
    assert second.build_id > first.build_id
    with maintenance_connection(dsn) as owner:
        assert owner.execute(
            "SELECT array_agg(build_id ORDER BY id) "
            "FROM commune_indicator_build_activations"
        ).fetchone()[0] == [first.build_id, second.build_id]

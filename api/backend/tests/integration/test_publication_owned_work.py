"""Owner-session publication transactions on a fresh wrapper-owned PostGIS DB."""

import importlib.util
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest

from app.commune.dependencies import PUBLICATION_LOCK_ID
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.publication import Publication
from app.etl.runner import PipelineRunner


@pytest.fixture
def target(isolated_pg_dsn):
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        conn.execute("CREATE EXTENSION postgis")
        conn.execute("CREATE TABLE owned_source(id integer NOT NULL)")
        conn.execute("INSERT INTO owned_source VALUES(1)")
        conn.execute(
            "CREATE TABLE dataset_metadata(dataset_name text PRIMARY KEY, "
            "version text); INSERT INTO dataset_metadata VALUES('owned_source','old')"
        )
        conn.execute("""CREATE TABLE matview_metadata(
            matview_name text PRIMARY KEY, last_refresh_at timestamptz,
            last_refresh_duration_seconds float, last_refresh_status text,
            last_error text);
            INSERT INTO matview_metadata(matview_name,last_refresh_status)
            VALUES('owned_view','old');
            CREATE MATERIALIZED VIEW owned_view AS SELECT id FROM owned_source;
            CREATE TABLE validation_runs(id bigserial PRIMARY KEY,dataset_name text,
            row_count_before bigint,row_count_after bigint,row_drop_pct float,
            null_rate_findings jsonb,passed boolean,blocked_swap boolean,notes text);
        """)
        path = (
            Path(__file__).parents[2]
            / "migrations/versions/094_publication_generations.py"
        )
        spec = importlib.util.spec_from_file_location("owned_work_094", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = SimpleNamespace(execute=conn.execute)
        migration.upgrade()
    return isolated_pg_dsn


def pipeline():
    return SimpleNamespace(
        dataset_name="owned_source",
        production_table_name="owned_source",
        source_period_label=None,
        source_updated_at=None,
        supports_department_scope=False,
        # Read by the runner since plan 02-11 (vintage label replacement).
        supports_millesime_pin=False,
        # Dropped by the runner right after publication since plan 04.1-04.
        published_old_tables=("owned_source_old",),
        swap=lambda: None,
        # Read by the runner after the swap since plan 04.1-13.
        swap_counters=lambda: {},
    )


def test_owner_transaction_commits_ddl_metadata_and_publication_together(target):
    with Publication(target, pipeline()) as publication:
        publication.observe_stock()
        owner = publication.connection
        with publication.required_work() as work:
            assert work.connection is owner and owner.autocommit is False
            assert work.assert_current(owner).generation == publication.generation
            owner.execute("CREATE TABLE owned_new(id integer)")
            publication.publish(
                lambda c: c.execute("UPDATE dataset_metadata SET version='new'")
            )
            with psycopg.connect(target) as observer:
                assert (
                    observer.execute("SELECT status FROM etl_publications").fetchone()[
                        0
                    ]
                    == "pending"
                )
                assert (
                    observer.execute("SELECT version FROM dataset_metadata").fetchone()[
                        0
                    ]
                    == "old"
                )
                assert (
                    observer.execute("SELECT to_regclass('owned_new')").fetchone()[0]
                    is None
                )
        assert owner.autocommit is True
        with pytest.raises(RuntimeError):
            work.assert_current(owner)
    with psycopg.connect(target) as observer:
        assert (
            observer.execute("SELECT status FROM etl_publications").fetchone()[0]
            == "published"
        )
        assert (
            observer.execute("SELECT version FROM dataset_metadata").fetchone()[0]
            == "new"
        )
        assert (
            observer.execute("SELECT to_regclass('owned_new')").fetchone()[0]
            == "owned_new"
        )


@pytest.mark.parametrize("failure", ["after_ddl", "metadata", "owner_lost"])
def test_owner_failure_rolls_back_work_and_preserves_durable_barrier(target, failure):
    error = RuntimeError("synthetic_after_ddl")
    with (
        pytest.raises((RuntimeError, psycopg.Error)),
        Publication(target, pipeline()) as publication,
    ):
        publication.observe_stock()
        owner = publication.connection
        try:
            with publication.required_work():
                owner.execute("CREATE TABLE must_rollback(id integer)")
                owner.execute(
                    "UPDATE matview_metadata SET last_refresh_status='success'"
                )
                if failure == "owner_lost":
                    with psycopg.connect(target, autocommit=True) as observer:
                        observer.execute(
                            "SELECT pg_terminate_backend(%s)",
                            (owner.info.backend_pid,),
                        )
                    owner.execute("SELECT 1")
                if failure == "after_ddl":
                    raise error

                def metadata(conn):
                    conn.execute("UPDATE dataset_metadata SET version='bad'")
                    raise error

                publication.publish(metadata)
        finally:
            if not owner.closed:
                assert owner.autocommit is True
    with psycopg.connect(target) as observer:
        assert (
            observer.execute("SELECT to_regclass('must_rollback')").fetchone()[0]
            is None
        )
        assert (
            observer.execute("SELECT version FROM dataset_metadata").fetchone()[0]
            == "old"
        )
        assert (
            observer.execute(
                "SELECT last_refresh_status FROM matview_metadata"
            ).fetchone()[0]
            == "old"
        )
        expected = "pending" if failure == "owner_lost" else "failed"
        assert (
            observer.execute("SELECT status FROM etl_publications").fetchone()[0]
            == expected
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "other_connection",
        "unlock",
        "shared",
        "oid",
        "generation",
        "pid",
        "session",
        "fake",
        "published",
    ],
)
def test_candidate_context_refuses_wrong_owner_or_stock(target, tamper):
    with pytest.raises(RuntimeError), Publication(target, pipeline()) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            owner = work.connection
            if tamper == "other_connection":
                with psycopg.connect(target) as other:
                    work.assert_current(other)
            elif tamper in {"unlock", "shared"}:
                owner.execute("SELECT pg_advisory_unlock(%s)", (PUBLICATION_LOCK_ID,))
                if tamper == "shared":
                    owner.execute(
                        "SELECT pg_advisory_lock_shared(%s)", (PUBLICATION_LOCK_ID,)
                    )
            elif tamper == "oid":
                owner.execute(
                    "ALTER TABLE owned_source RENAME TO retired_source; "
                    "CREATE TABLE owned_source(id integer)"
                )
            elif tamper == "generation":
                owner.execute(
                    "UPDATE etl_publications SET generation=gen_random_uuid()"
                )
            elif tamper == "pid":
                publication._owner_identity = (-1, *publication._owner_identity[1:])
            elif tamper == "session":
                publication._owner_identity = (
                    publication._owner_identity[0],
                    None,
                    publication._owner_identity[2],
                )
            elif tamper == "fake":
                from app.etl.publication import PublicationWork

                work = PublicationWork(publication)
            elif tamper == "published":
                publication.publish(lambda _: None)
            work.assert_current(owner)


def test_work_requires_observation_and_completion_and_standalone_publish_survives(
    target,
):
    with (
        pytest.raises(RuntimeError),
        Publication(target, pipeline()) as publication,
        publication.required_work(),
    ):
        pass
    with pytest.raises(RuntimeError), Publication(target, pipeline()) as publication:
        publication.observe_stock()
        with publication.required_work():
            pass  # Returning without publish must not commit candidate work.
    with Publication(target, pipeline()) as publication:
        publication.observe_stock()
        publication.publish(lambda _: None)
    with psycopg.connect(target) as conn:
        assert conn.execute(
            "SELECT status FROM etl_publications ORDER BY id"
        ).fetchall() == [("failed",), ("failed",), ("published",)]


@pytest.mark.parametrize(
    "argument,result_dataset", [("ban", "ban"), ("owned_source", "ban")]
)
@pytest.mark.parametrize("hook_type", [DependentMatviewHook, ValidationHook])
def test_hook_dataset_is_bound_to_its_actual_candidate_before_any_work(
    target, tmp_path, monkeypatch, argument, result_dataset, hook_type
):
    from app.config import settings
    from app.etl.registry import registry
    from app.etl.schemas import EtlResult

    monkeypatch.setattr(settings, "database_url", target)
    monkeypatch.setitem(
        registry._pipelines,
        argument,
        type("SyntheticMapped", (), {"production_table_name": "owned_source"}),
    )
    marker = Mock()
    monkeypatch.setattr(
        DependentMatviewHook, "_DEPENDENCIES", {argument: (("owned_view", marker),)}
    )
    with Publication(target, pipeline()) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            result = EtlResult(
                dataset_name=result_dataset,
                stages=[],
                total_rows_loaded=1,
                total_duration_seconds=0,
                success=True,
            )
            result._publication_work = work
            with pytest.raises(RuntimeError, match="dataset"):
                hook_type().on_run_complete(argument, result)
            marker.assert_not_called()
            assert (
                work.connection.execute(
                    "SELECT count(*) FROM validation_runs"
                ).fetchone()[0]
                == 0
            )
            publication.publish(lambda _: None)


@pytest.mark.parametrize("failure", [None, "ddl", "metadata", "failure_record"])
def test_real_runner_and_hook_share_owner_and_defer_failure_recording(
    target, monkeypatch, failure
):
    from app.config import settings
    from app.etl.registry import registry

    monkeypatch.setattr(settings, "database_url", target)
    monkeypatch.setitem(
        registry._pipelines,
        "owned_source",
        type("SyntheticPipeline", (), {"production_table_name": "owned_source"}),
    )
    events = []
    seen_results = []
    original_error = psycopg.OperationalError("synthetic_ddl_failure")

    def rebuild(conn):
        events.append(("ddl", conn.info.backend_pid))
        assert conn.autocommit is False
        # Validation ran on committed sources before any DDL.
        assert conn.execute("SELECT count(*) FROM validation_runs").fetchone()[0] == 1
        conn.execute(
            "DROP MATERIALIZED VIEW owned_view; "
            "CREATE MATERIALIZED VIEW owned_view AS SELECT id+1 AS id FROM owned_source"
        )
        conn.execute("CREATE TABLE rollback_marker(id integer)")
        if failure in {"ddl", "failure_record"}:
            raise original_error

    monkeypatch.setattr(
        DependentMatviewHook,
        "_DEPENDENCIES",
        {"owned_source": (("owned_view", rebuild),)},
    )
    original_complete = DependentMatviewHook.on_run_complete

    def complete(hook, dataset, result):
        seen_results.append(result)
        work = result._publication_work
        assert work.assert_current(work.connection).generation
        events.append(("owner", work.connection.info.backend_pid))
        assert "_publication_work" not in result.model_dump_json()
        return original_complete(hook, dataset, result)

    monkeypatch.setattr(DependentMatviewHook, "on_run_complete", complete)
    optional = Mock(required_on_complete=False)
    optional.on_run_complete.side_effect = lambda _, result: events.append(
        ("optional", result._publication_work)
    )
    runner = PipelineRunner([ValidationHook(), DependentMatviewHook(), optional])

    def metadata(*_, connection, **kwargs):
        events.append(("metadata", connection.info.backend_pid))
        connection.execute("UPDATE dataset_metadata SET version='new'")
        with psycopg.connect(target) as observer:
            assert (
                observer.execute(
                    "SELECT status FROM etl_publications ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                == "pending"
            )
            assert (
                observer.execute(
                    "SELECT last_refresh_status FROM matview_metadata"
                ).fetchone()[0]
                == "old"
            )
        if failure == "metadata":
            raise original_error

    monkeypatch.setattr(runner, "_update_dataset_metadata", metadata)
    monkeypatch.setattr(runner, "_update_etl_run", lambda *_: None)
    monkeypatch.setattr("app.etl.runner.cleanup_download_dir", lambda _: None)
    if failure == "failure_record":
        with psycopg.connect(target) as conn:
            conn.execute("""CREATE FUNCTION refuse_failed_trace() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN IF NEW.last_refresh_status='failed' THEN
                RAISE EXCEPTION 'synthetic_trace_failure'; END IF;
                RETURN NEW; END $$;
                CREATE TRIGGER reject_failed_trace BEFORE UPDATE ON matview_metadata
                FOR EACH ROW EXECUTE FUNCTION refuse_failed_trace();""")
    args = (
        pipeline(),
        target,
        [],
        1,
        1,
        time.perf_counter(),
        Mock(),
        1,
        logging.getLogger(__name__),
    )
    if failure:
        from app.etl.errors import transient_cause

        with pytest.raises(Exception) as caught:
            runner._publish(*args)
        assert transient_cause(caught.value) is original_error
    else:
        result = runner._publish(*args)
        assert result.success and events[-1] == ("optional", None)
    assert events[0][1] == events[1][1]
    assert all(result._publication_work is None for result in seen_results)
    with psycopg.connect(target) as conn:
        assert conn.execute("SELECT status FROM etl_publications").fetchone()[0] == (
            "failed" if failure else "published"
        )
        assert conn.execute("SELECT version FROM dataset_metadata").fetchone()[0] == (
            "old" if failure else "new"
        )
        assert conn.execute("SELECT id FROM owned_view").fetchone()[0] == (
            1 if failure else 2
        )
        expected_trace = (
            "failed" if failure == "ddl" else ("old" if failure else "success")
        )
        assert (
            conn.execute("SELECT last_refresh_status FROM matview_metadata").fetchone()[
                0
            ]
            == expected_trace
        )

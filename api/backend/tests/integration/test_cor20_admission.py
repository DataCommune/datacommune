"""Bounded writer admission against real readers; disposable wrapper only."""

import asyncio
import time
from unittest.mock import patch

import psycopg
from tenacity import stop_after_attempt, wait_none

from app.commune.dependencies import PUBLICATION_LOCK_ID
from app.config import settings
from app.etl.checkpoint import CheckpointManager
from app.etl.publication import (
    Publication,
    dataset_execution_lock,
    requires_fresh_start,
)
from app.etl.runner import PipelineRunner
from tests.etl.test_runner import _patch_all
from tests.integration.test_cor20_publication import (
    PublicationPipeline,
    create_chain,
)
from tests.integration.test_cor20_publication import published as _published_fixture

# Reuse the real isolated-PostGIS fixture in this independently collected file.
published = _published_fixture


async def test_short_reader_queues_writer_and_writer_publishes(published):
    dsn, _client = published
    reader = psycopg.connect(dsn)
    reader.execute("SELECT pg_advisory_xact_lock_shared(%s)", (PUBLICATION_LOCK_ID,))

    def writer():
        with Publication(dsn, PublicationPipeline(dsn)) as publication:
            publication.observe_stock()
            publication.publish(lambda _conn: None)

    task = asyncio.create_task(asyncio.to_thread(writer))
    try:
        queued = False
        with psycopg.connect(dsn, autocommit=True) as observer:
            until = time.monotonic() + 0.2
            while time.monotonic() < until and not task.done():
                queued = observer.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND classid=%s AND objid=%s AND NOT granted)",
                    (PUBLICATION_LOCK_ID >> 32, PUBLICATION_LOCK_ID & 0xFFFFFFFF),
                ).fetchone()[0]
                if queued:
                    break
                await asyncio.sleep(0.005)
            assert queued, "writer must enter PostgreSQL lock queue"
            # A later reader cannot keep overtaking the queued exclusive writer.
            assert not observer.execute(
                "SELECT pg_try_advisory_xact_lock_shared(%s)",
                (PUBLICATION_LOCK_ID,),
            ).fetchone()[0]
        reader.commit()
        await asyncio.wait_for(task, 1)
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT status FROM etl_publications").fetchone() == (
                "published",
            )
    finally:
        reader.close()
        if not task.done():
            await task
        elif task.exception() is not None:
            # Retrieve worker exceptions even when an earlier assertion failed.
            task.exception()


async def test_long_reader_timeout_retries_real_runner_and_preserves_staging(  # noqa: C901
    published,
    monkeypatch,
    tmp_path,
):
    from app.scheduler import jobs

    dsn, client = published
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "cache"))
    source = tmp_path / "source.csv"
    source.write_text("synthetic frozen source", encoding="utf-8")
    reader = psycopg.connect(dsn)
    reader.execute("SELECT pg_advisory_xact_lock_shared(%s)", (PUBLICATION_LOCK_ID,))
    stage_calls = []
    retries = []
    metadata_update = PipelineRunner._update_dataset_metadata

    class RetryingPipeline(PublicationPipeline):
        def download(self):
            return [source]

        def create_staging_table(self, _dsn):
            with psycopg.connect(dsn) as conn:
                conn.execute("CREATE TABLE revenus_staging (LIKE revenus)")

        def stage(self, _path):
            stage_calls.append(1)
            with psycopg.connect(dsn) as conn:
                conn.execute("INSERT INTO revenus_staging VALUES ('44999','new')")
            return 1

        def swap(self):
            with psycopg.connect(dsn) as conn:
                conn.execute("ALTER TABLE revenus RENAME TO revenus_old")
                conn.execute("ALTER TABLE revenus_staging RENAME TO revenus")

    class RequiredHook:
        required_on_complete = True

        def on_run_start(self, *_):
            pass

        def on_run_failure(self, *_):
            pass

        def on_run_complete(self, *_):
            with psycopg.connect(dsn) as conn:
                create_chain(conn)

    def execute():
        checkpoint = CheckpointManager("revenus", "revenus_staging")
        native_load, native_save, native_clear = (
            checkpoint.load,
            checkpoint.save,
            checkpoint.clear,
        )
        native_count = PipelineRunner._count_staging_rows
        native_run = PipelineRunner.run
        run_results = []

        def observed_run(self, *args, **kwargs):
            result = native_run(self, *args, **kwargs)
            run_results.append(result)
            return result

        with _patch_all(staging_exists=False, checkpoint_data=None) as mocks:
            mocks["staging_exists"].side_effect = [False, True]
            mocks["checkpoint_load"].side_effect = native_load
            mocks["checkpoint_save"].side_effect = native_save
            mocks["checkpoint_clear"].side_effect = native_clear
            mocks["stock"].side_effect = native_count

            def before_sleep(state):
                retries.append(state.outcome.exception())
                assert isinstance(retries[-1], psycopg.OperationalError)
                assert retries[-1].sqlstate == "55P03"
                assert 0.2 <= state.seconds_since_start < 1.0
                mocks["checkpoint_clear"].assert_not_called()
                mocks["cleanup_dir"].assert_not_called()
                saved = native_load()
                assert saved is not None and saved["version"] == 2
                assert saved["pending"] is None and saved["staging_rows"] == 1
                assert saved["loaded_files"] == [source.name]
                assert source.read_text(encoding="utf-8") == "synthetic frozen source"
                with psycopg.connect(dsn) as conn:
                    assert conn.execute("SELECT * FROM revenus_staging").fetchall() == [
                        ("44999", "new")
                    ]
                    assert conn.execute("SELECT version FROM revenus").fetchall() == [
                        ("old",)
                    ]
                    assert conn.execute(
                        "SELECT count(*) FROM etl_publications"
                    ).fetchone() == (0,)
                    assert conn.execute(
                        "SELECT row_count,last_updated_at::date::text "
                        "FROM dataset_metadata"
                    ).fetchone() == (1, "2020-01-01")
                reader.commit()

            with (
                patch.object(PipelineRunner, "run", observed_run),
                patch("app.etl.runner.Publication", Publication),
                patch("app.etl.runner.dataset_execution_lock", dataset_execution_lock),
                patch("app.etl.runner.requires_fresh_start", requires_fresh_start),
                patch.object(
                    PipelineRunner,
                    "_update_dataset_metadata",
                    staticmethod(metadata_update),
                ),
            ):
                jobs.run_scheduled_pipeline_with_retry.retry_with(
                    wait=wait_none(),
                    stop=stop_after_attempt(2),
                    before_sleep=before_sleep,
                )("revenus")
            assert [result.success for result in run_results] == [False, True]
            assert run_results[-1].total_rows_loaded == 1
            assert not checkpoint.exists()
            mocks["checkpoint_clear"].assert_called_once()
            mocks["cleanup_dir"].assert_called_once()

    monkeypatch.setitem(jobs.PIPELINE_MAP, "revenus", lambda: RetryingPipeline(dsn))
    monkeypatch.setattr(jobs, "build_default_hooks", lambda: [RequiredHook()])
    try:
        await asyncio.wait_for(asyncio.to_thread(execute), 3)
        assert len(retries) == 1 and stage_calls == [1]
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT status FROM etl_publications").fetchall() == [
                ("published",)
            ]
        response = await client.get("/v1/commune", params={"code_insee": "44999"})
        assert response.status_code == 200
        assert response.json()["donnees"][0]["nom_commune"] == "new"
    finally:
        reader.close()

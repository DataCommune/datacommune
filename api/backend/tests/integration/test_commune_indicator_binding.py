"""Owner-bound indicator orchestration on disposable PostGIS fixtures."""

import logging
import time
from datetime import timedelta
from unittest.mock import Mock

import psycopg
import pytest

from app.commune import indicator_binding as binding
from app.commune.indicator_context import build_indicator_context
from app.commune.indicator_results import ResultStoreRefused
from app.etl.publication import Publication, maintenance_connection
from app.etl.runner import PipelineRunner
from app.etl.schemas import EtlResult
from tests.integration.test_commune_indicator_projector import (
    NOW,
    REQUEST,
    prepare,
    register,
)
from tests.integration.test_commune_indicator_results import counts

pytest_plugins = ("tests.integration.test_commune_indicator_results",)


def _result(work) -> EtlResult:
    result = EtlResult(
        dataset_name="dpe",
        stages=[],
        total_rows_loaded=1,
        total_duration_seconds=0,
        success=True,
    )
    result._publication_work = work
    return result


def test_required_hook_builds_pending_then_publish_expires_it(store, tmp_path):
    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    hook = binding.CommuneIndicatorHook(trust=trust, request=REQUEST)

    with Publication(
        dsn,
        type(
            "Pipeline",
            (),
            {
                "dataset_name": "dpe",
                "production_table_name": "diagnostics_dpe",
            },
        )(),
    ) as publication:
        publication.observe_stock()
        with publication.required_work() as work:
            hook.on_run_complete("dpe", _result(work))
            receipt = hook.last_receipt
            assert receipt is not None
            assert all(
                row["indicateurs_dpe"]["etat"] == "not_qualified"
                for row in receipt.public_rows
            )
            publication.publish(lambda _: None)

    with psycopg.connect(dsn) as reader:
        from tests.integration import test_commune_indicator_storage as registry

        registry.lock(reader)
        fresh = build_indicator_context(
            reader,
            trust=trust.model_copy(update={"checked_at": NOW + timedelta(hours=1)}),
        )
        with pytest.raises(ResultStoreRefused, match="result_build_stale"):
            binding.read_bound_build(reader, build_id=receipt.build_id, context=fresh)


def test_maintenance_rebuild_uses_real_owner_without_new_generation(store, tmp_path):
    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    before = conn.execute("SELECT count(*) FROM etl_publications").fetchone()[0]
    conn.commit()
    conn.close()

    with maintenance_connection(dsn) as owner:
        receipt = binding.rebuild_indicator_results(owner, trust=trust, request=REQUEST)
        assert len(receipt.public_rows) == 460
        after = owner.execute("SELECT count(*) FROM etl_publications").fetchone()[0]
        assert after == before


@pytest.mark.parametrize("state", ["pending", "no_go"])
def test_later_non_qualified_decision_refuses_old_bound_read(store, tmp_path, state):
    conn, _ = store
    trust, _ = prepare(conn, tmp_path)
    receipt = binding.rebuild_indicator_results(conn, trust=trust, request=REQUEST)
    register(conn, tmp_path, trust, "dpe", state=state)
    fresh = build_indicator_context(conn, trust=trust)
    with pytest.raises(ResultStoreRefused, match="result_build_stale"):
        binding.read_bound_build(conn, build_id=receipt.build_id, context=fresh)


def test_hook_failure_rolls_back_results_and_publication(store, tmp_path, monkeypatch):
    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.commit()
    conn.close()
    hook = binding.CommuneIndicatorHook(trust=trust, request=REQUEST)
    monkeypatch.setattr(
        binding,
        "read_bound_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("read failure")),
    )
    pipeline = type(
        "Pipeline",
        (),
        {
            "dataset_name": "dpe",
            "production_table_name": "diagnostics_dpe",
            "source_period_label": None,
            "source_updated_at": None,
            "supports_department_scope": False,
            "swap": lambda self: None,
            # Read by the runner after the swap since plan 04.1-13.
            "swap_counters": lambda self: {},
        },
    )()
    runner = PipelineRunner([hook])
    monkeypatch.setattr(runner, "_require_publication_checks", lambda: None)
    monkeypatch.setattr(runner, "_update_dataset_metadata", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_update_etl_run", lambda *a, **k: None)
    monkeypatch.setattr("app.etl.runner.cleanup_download_dir", lambda _: None)
    args = (
        pipeline,
        dsn,
        [],
        1,
        1,
        time.perf_counter(),
        Mock(),
        1,
        logging.getLogger(__name__),
    )
    with pytest.raises(RuntimeError, match="Required validation/views failed"):
        runner._publish(*args)
    with psycopg.connect(dsn) as reader:
        assert counts(reader) == (0, 0)
        assert reader.execute(
            "SELECT status FROM etl_publications ORDER BY id DESC LIMIT 1"
        ).fetchone() == ("failed",)


def test_read_failure_rolls_back_only_binding_savepoint(store, tmp_path, monkeypatch):
    conn, dsn = store
    trust, _ = prepare(conn, tmp_path)
    conn.execute("CREATE TABLE caller_marker(value integer)")
    conn.execute("INSERT INTO caller_marker VALUES(1)")
    monkeypatch.setattr(
        binding,
        "read_bound_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("read failure")),
    )
    with pytest.raises(RuntimeError, match="read failure"):
        binding.rebuild_indicator_results(conn, trust=trust, request=REQUEST)
    assert counts(conn) == (0, 0)
    conn.commit()
    with psycopg.connect(dsn) as reader:
        assert counts(reader) == (0, 0)
        assert reader.execute("SELECT count(*) FROM caller_marker").fetchone() == (1,)


def test_reused_hook_clears_previous_receipt_before_failure(
    store, tmp_path, monkeypatch
):
    conn, _ = store
    trust, _ = prepare(conn, tmp_path)
    hook = binding.CommuneIndicatorHook(trust=trust, request=REQUEST)
    hook.last_receipt = binding.rebuild_indicator_results(
        conn, trust=trust, request=REQUEST
    )
    result = EtlResult(
        dataset_name="dpe",
        stages=[],
        total_rows_loaded=0,
        total_duration_seconds=0,
        success=True,
    )
    with pytest.raises(RuntimeError, match="requires publication owner work"):
        hook.on_run_complete("dpe", result)
    assert hook.last_receipt is None


def test_reused_hook_clears_receipt_on_new_run_and_early_failure(store, tmp_path):
    conn, _ = store
    trust, _ = prepare(conn, tmp_path)
    hook = binding.CommuneIndicatorHook(trust=trust, request=REQUEST)
    previous = binding.rebuild_indicator_results(conn, trust=trust, request=REQUEST)
    hook.last_receipt = previous
    hook.on_run_start("dpe")
    assert hook.last_receipt is None
    hook.last_receipt = previous
    hook.on_run_failure("dpe", "extract failed")
    assert hook.last_receipt is None

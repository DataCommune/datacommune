"""National read of the active indicator build on disposable PostgreSQL 17.

The build stays the qualified 44/85 one (its contract is plan 04.1-10's), but
the reader no longer depends on its size: a commune outside the build is
named, a requested commune is read alone, the build integrity is checked once
per fingerprint and any later tampering is refused (T-04.1-28, T-04.1-29).
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.commune import indicator_activation
from app.commune.indicator_reader import read_active_preproduction_build
from app.commune.indicator_results import ResultStoreRefused
from tests.integration import test_commune_indicator_storage as registry
from tests.integration.test_commune_indicator_http import build_preproduction
from tests.integration.test_commune_indicator_results import allow_corruption

pytest_plugins = ("tests.integration.test_commune_indicator_results",)

CELLS = "commune_indicator_result_cells"


class Observed:
    """Statements of the async engine that read the cells table, with rows."""

    def __init__(self, engine):
        self.reads: list[tuple[str, object, int]] = []
        event.listen(engine.sync_engine, "after_cursor_execute", self._record)

    def _record(self, _conn, cursor, statement, parameters, _context, _many):
        if CELLS in statement:
            self.reads.append((statement, parameters, cursor.rowcount))

    def clear(self):
        self.reads.clear()


def _engine(dsn):
    return create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))


async def _read(engine, policy, codes):
    async with AsyncSession(engine) as session:
        return await read_active_preproduction_build(
            session, policy=policy, environment="preproduction", codes=codes
        )


@pytest.mark.asyncio
async def test_outside_build_is_named_and_requested_cells_are_read_alone(
    store, tmp_path
):
    conn, dsn = store
    _, policy = build_preproduction(conn, tmp_path)
    engine = _engine(dsn)
    observed = Observed(engine)
    try:
        first = await _read(engine, policy, ["44001", "75056"])
        assert set(first.blocks) == {"44001"}
        assert first.hors_build == frozenset({"75056"})
        assert set(first.blocks["44001"]) == {
            "code_insee",
            "indicateurs_dvf",
            "indicateurs_dpe",
        }

        # Warm fingerprint: the request reads the two cells of 44001, nothing
        # else of the build, through statements bounded by the asked codes
        # of the build (75056 is known outside it, never looked up).
        observed.clear()
        again = await _read(engine, policy, ["44001", "75056"])
        assert again.blocks == first.blocks
        assert again.hors_build == frozenset({"75056"})
        assert observed.reads, "the requested cells must be read"
        assert sum(rows for _, _, rows in observed.reads) == 2
        for _statement, parameters, _rows in observed.reads:
            assert _codes(parameters) == ["44001"]

        observed.clear()
        nothing = await _read(engine, policy, ["75056"])
        assert nothing.blocks == {} and nothing.hors_build == frozenset({"75056"})
        assert sum(rows for _, _, rows in observed.reads) == 0
    finally:
        await engine.dispose()


def _codes(parameters) -> list[str]:
    """The code list bound to a cells statement, whatever the paramstyle."""
    values = parameters.values() if isinstance(parameters, dict) else parameters
    lists = [v for v in values if isinstance(v, list | tuple)]
    assert len(lists) == 1, parameters
    return list(lists[0])


@pytest.mark.parametrize("fault", ["cell", "missing_cell", "header", "manifest"])
@pytest.mark.asyncio
async def test_tampering_after_first_validation_is_refused(store, tmp_path, fault):
    conn, dsn = store
    receipt, policy = build_preproduction(conn, tmp_path)
    engine = _engine(dsn)
    try:
        assert set((await _read(engine, policy, ["44001"])).blocks) == {"44001"}
        allow_corruption(conn)
        if fault == "cell":
            conn.execute(
                "UPDATE commune_indicator_result_cells "
                'SET public_payload=public_payload || \'{"etat":"value"}\' '
                "WHERE build_id=%s AND dataset_name='dpe' AND code_insee='44001'",
                (receipt.build_id,),
            )
        elif fault == "missing_cell":
            conn.execute(
                "DELETE FROM commune_indicator_result_cells WHERE build_id=%s "
                "AND dataset_name='dpe' AND code_insee='44001'",
                (receipt.build_id,),
            )
        elif fault == "header":
            conn.execute(
                "UPDATE commune_indicator_result_builds "
                "SET header=header || '{\"commune_count\":461}' WHERE id=%s",
                (receipt.build_id,),
            )
        else:
            conn.execute(
                "ALTER TABLE commune_indicator_manifests DISABLE TRIGGER "
                "immutable_indicator_registry"
            )
            conn.execute(
                "UPDATE commune_indicator_manifests "
                "SET manifest=manifest || '{\"unexpected\":true}'::jsonb "
                "WHERE dataset_name='dpe' AND source_generation=%s",
                (policy.dpe.source_generation,),
            )
        conn.commit()
        with pytest.raises(ResultStoreRefused):
            await _read(engine, policy, ["44001"])
        # A refusal drops the fingerprint: the next request validates the
        # whole build again instead of serving the other communes from cache.
        if fault in {"cell", "missing_cell"}:
            with pytest.raises(ResultStoreRefused):
                await _read(engine, policy, ["44002"])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revoke_then_new_activation_revalidates_the_whole_build(store, tmp_path):
    conn, dsn = store
    receipt, policy = build_preproduction(conn, tmp_path)
    engine = _engine(dsn)
    observed = Observed(engine)
    try:
        await _read(engine, policy, ["44001"])
        registry.lock(conn)
        indicator_activation.revoke_active_build(conn)
        conn.commit()
        with pytest.raises(ResultStoreRefused, match="active_build_revoked"):
            await _read(engine, policy, ["44001", "75056"])

        registry.lock(conn)  # the owner transaction of the new activation
        indicator_activation.activate_build(
            conn, build_id=receipt.build_id, trust=policy.trust_inputs()
        )
        conn.commit()
        observed.clear()
        loaded = await _read(engine, policy, ["44001"])
        assert set(loaded.blocks) == {"44001"}
        # New activation event: the whole build is read and validated again
        # (920 cells in one aggregate row) before the requested cells.
        assert any(_codes_free(params) for _, params, _ in observed.reads)
    finally:
        await engine.dispose()


def _codes_free(parameters) -> bool:
    values = parameters.values() if isinstance(parameters, dict) else parameters
    return not any(isinstance(v, list | tuple) for v in values)

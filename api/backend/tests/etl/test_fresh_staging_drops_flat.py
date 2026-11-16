"""The flat staging table of a failed run never reaches the next publication.

Monuments run 59 (2026-10-29) was refused before swap() with 44 504 rows in
``_monuments_staging_flat``: the runner dropped the staging through an override
of drop_staging_table() that kept the flat table, the next fresh staging
(another override, of create_staging_table()) kept it too, and stage() creates
the flat table IF NOT EXISTS and appends with COPY. Run 62 published every
monument twice (88 974 rows for 44 488 references). The base contract of both
methods covers the flat table; every override must keep it, and swap() must
still read the flat rows of its own run.
"""

from contextlib import suppress
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class _StopSwapError(Exception):
    """Ends swap() at its first transfer, once the order is known."""


def _pipelines_with_flat_staging():
    from app.scope import InstanceScope
    from run_etl import ALL_PIPELINES

    for cls in ALL_PIPELINES:
        pipeline = cls()
        # Partitioned stagings read the hosted departements.
        pipeline.bind_scope(
            InstanceScope(frozenset(pipeline.scope_dataset_keys), ("44", "85"))
        )
        if pipeline.flat_staging_table_name:
            yield pytest.param(pipeline, id=pipeline.dataset_name)


PIPELINES = list(_pipelines_with_flat_staging())


def _record(call, *, stop_at_insert=False):
    executed: list[str] = []

    def record(query, *args, **kwargs):
        text = " ".join(
            (query if isinstance(query, str) else query.as_string()).split()
        )
        executed.append(text)
        if stop_at_insert and text.startswith("INSERT INTO"):
            raise _StopSwapError
        return MagicMock()

    connection = MagicMock()
    session = connection.__enter__.return_value
    session.cursor.return_value.__enter__.return_value.execute.side_effect = record
    session.execute.side_effect = record
    with (
        patch("psycopg.connect", return_value=connection),
        suppress(_StopSwapError),
    ):
        call()
    return executed


def _drops(executed, table):
    return [
        i
        for i, q in enumerate(executed)
        if q
        in (f"DROP TABLE IF EXISTS {table}", f"DROP TABLE IF EXISTS {table} CASCADE")
    ]


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_create_staging_table_drops_the_flat_staging_table(pipeline):
    executed = _record(lambda: pipeline.create_staging_table("postgresql://test"))
    flat = pipeline.flat_staging_table_name
    assert _drops(executed, flat), f"{pipeline.dataset_name}: {flat} kept"


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_drop_staging_table_drops_the_flat_staging_table(pipeline):
    executed = _record(lambda: pipeline.drop_staging_table("postgresql://test"))
    flat = pipeline.flat_staging_table_name
    assert _drops(executed, flat), f"{pipeline.dataset_name}: {flat} kept"


@pytest.mark.parametrize(
    "pipeline",
    [p for p in PIPELINES if p.values[0].dataset_name in ("monuments", "scolaire")],
)
def test_swap_reads_the_flat_rows_it_does_not_drop(pipeline):
    """monuments and scolaire rebuild their candidate inside swap()."""
    flat = pipeline.flat_staging_table_name
    with patch.object(type(pipeline), "get_sync_dsn", return_value="postgresql://t"):
        executed = _record(pipeline.swap, stop_at_insert=True)
    transfer = [
        i
        for i, q in enumerate(executed)
        if q.startswith("INSERT INTO") and f"FROM {flat}" in q
    ]
    assert transfer, executed
    assert not [i for i in _drops(executed, flat) if i < transfer[0]]

"""An index a migration adds to diagnostics_dpe must survive the ETL swap.

Why this file exists (2026-08-05). The DPE pipeline does not ALTER the
production table: it builds a staging table, indexes it, and renames it over
the live one. So the indexes that exist after a refresh are exactly those
``DpePipeline._create_staging_indexes`` rebuilds -- that method, not the
migration history, is the real definition of the table's indexes.

Nothing enforced the two agreed with each other. Migration 077 added an index
on the PRIMARY DPE attachment column and did not touch the pipeline; the next
refresh silently dropped it, and it was found missing in production months
later while ``alembic current`` still reported head. No error, no log, no
failing test -- a rename cannot fail for lack of an index.

This test closes that gap: every index a migration declares on
``diagnostics_dpe`` must have a counterpart in the pipeline's rebuild list,
matched on the INDEXED COLUMN rather than on the index name (the pipeline
deliberately keeps its ``_staging`` naming and does not rename after the swap).

An index can also be legitimately RETIRED -- migration 082 dropped
``idx_dpe_id_rnb`` when the Lot C2 path that read it was removed. So the
comparison is against the NET state of the migration history (created, minus
dropped by a LATER migration), not against every CREATE ever written. That state
is derived from the migrations themselves rather than from a hand-maintained
list: a parallel list of "indexes we no longer want" is the very defect this
file exists to prevent.

Only the body of ``upgrade()`` is read. Every migration's ``downgrade()``
undoes its own work, so scanning the whole file would read each CREATE as also
dropped and the detector would go permanently blind.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.etl_dpe.pipeline import DpePipeline

pytestmark = pytest.mark.unit

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# "CREATE INDEX [IF NOT EXISTS] <name> ON <table> [USING <method>] (<column>...)"
# Tolerates the line breaks of a Python string concatenation: the DDL is read
# from source, not from a live database.
_CREATE_INDEX = re.compile(
    r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+"
    r"ON\s+(\w+)\s+(?:USING\s+\w+\s+)?\(([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)

# "DROP INDEX [IF EXISTS] <name>" -- the name is all a DROP carries, hence the
# name -> column bookkeeping below.
_DROP_INDEX = re.compile(r"DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?(\w+)", re.IGNORECASE)


class _DdlRecordingCursor:
    """Collects the DDL a pipeline would execute, without a database."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(sql)


# Implicit string concatenation splices a DDL statement across source lines
# ("CREATE INDEX ... " "ON diagnostics_dpe (...)"), leaving the quote pair in
# the middle of the statement when the file is read as text. Stitch those back
# together before matching, otherwise every migration DDL looks unparseable.
_STRING_SPLICE = re.compile(r"""["']\s*["']""")


def _normalise(sql: str) -> str:
    """Collapse whitespace and re-splice concatenated string literals."""
    return " ".join(_STRING_SPLICE.sub(" ", sql).split())


def _indexed_columns(statements: list[str], table_prefix: str) -> set[str]:
    """The column of every CREATE INDEX in *statements* on a matching table."""
    columns: set[str] = set()
    for raw in statements:
        match = _CREATE_INDEX.search(_normalise(raw))
        if not match:
            continue
        _, table, column_list = match.groups()
        if not table.lower().startswith(table_prefix):
            continue
        # First column only: a composite index still covers equality on its
        # leading column, which is what every DPE access path filters on.
        columns.add(column_list.split(",")[0].strip().lower())
    return columns


def _pipeline_indexed_columns() -> set[str]:
    cursor = _DdlRecordingCursor()
    DpePipeline._create_staging_indexes(cursor)  # type: ignore[arg-type]
    return _indexed_columns(cursor.statements, "diagnostics_dpe")


def _upgrade_body(source: str) -> str:
    """The source between ``def upgrade`` and ``def downgrade``.

    Alembic always emits ``upgrade()`` before ``downgrade()``, so this split is
    enough to isolate the forward DDL -- and it also drops the module docstring,
    which is where migrations DISCUSS indexes in prose. A file with no
    ``downgrade`` keeps everything after ``def upgrade``.
    """
    if "def upgrade" not in source:
        return ""
    apres = source.split("def upgrade", 1)[1]
    return apres.split("def downgrade", 1)[0]


def _etat_net(sources: list[tuple[str, str]]) -> dict[str, str]:
    """Net indexed column -> declaring migration, from ordered *sources*.

    *sources* is ``(migration name, full file source)`` in application order.
    Pure so the DROP bookkeeping can be exercised on synthetic migrations --
    see :func:`test_a_later_drop_retires_the_column`.

    A later ``DROP INDEX`` retires the column an earlier ``CREATE INDEX``
    registered. Retired columns are then expected to be ABSENT from the
    pipeline: that is a removal done right, not an index lost to a swap.
    """
    declared: dict[str, str] = {}
    par_nom: dict[str, str] = {}  # index name -> column, to resolve DROPs
    for nom, source in sources:
        upgrade = _normalise(_upgrade_body(source))
        for match in _CREATE_INDEX.finditer(upgrade):
            name, table, column_list = match.groups()
            if table.lower() != "diagnostics_dpe":
                continue
            column = column_list.split(",")[0].strip().lower()
            par_nom[name.lower()] = column
            declared.setdefault(column, nom)
        for match in _DROP_INDEX.finditer(upgrade):
            column = par_nom.get(match.group(1).lower())
            # A DROP naming an index this scan never saw created (another
            # table's, or one predating the migration history) is not ours.
            if column is not None:
                declared.pop(column, None)
    return declared


def _migration_indexed_columns() -> dict[str, str]:
    """The net state of the real migration history, for diagnostics_dpe."""
    return _etat_net(
        [
            (path.name, path.read_text(encoding="utf-8"))
            for path in sorted(_MIGRATIONS_DIR.glob("*.py"))
        ]
    )


def test_pipeline_rebuilds_every_column_a_migration_indexes() -> None:
    """No migration index on diagnostics_dpe may be lost by the next swap."""
    from_migrations = _migration_indexed_columns()
    from_pipeline = _pipeline_indexed_columns()

    perdus = {
        column: origin
        for column, origin in from_migrations.items()
        if column not in from_pipeline
    }

    assert not perdus, (
        "These columns are indexed by a migration but NOT rebuilt by "
        "DpePipeline._create_staging_indexes, so the next DPE refresh will "
        "drop their index silently (alembic will still report head): "
        f"{perdus}. Add the index to _create_staging_indexes -- that list, "
        "not the migration history, decides what survives a swap."
    )


def test_the_detector_reads_real_ddl() -> None:
    """Guard the guard: both sides must be non-empty and actually parsed.

    Were the regex to stop matching -- a reformatting of the pipeline, a DDL
    written differently -- both sets would silently become empty and the test
    above would pass while checking nothing.
    """
    from_migrations = _migration_indexed_columns()
    from_pipeline = _pipeline_indexed_columns()

    assert len(from_pipeline) >= 6, (
        f"Only {len(from_pipeline)} indexed columns parsed from the pipeline; "
        "the DDL shape probably changed and the regex no longer matches."
    )
    assert from_migrations, (
        "No CREATE INDEX on diagnostics_dpe found in any migration; the "
        "detector is blind and would never fail."
    )
    # The spatial column is indexed by the pipeline under USING GIST -- proves
    # the optional USING clause is handled. Since migration 107 only the
    # geography expression is left; the column capture stops at its first
    # closing parenthesis, hence the key.
    assert "geography(position" in from_pipeline
    # The planar index on the column is retired by 107, on both sides.
    assert "position" not in from_pipeline
    assert "position" not in from_migrations


_CREATE_SOURCE = '''"""Docstring mentioning DROP INDEX idx_faux in prose."""


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_essai "
        "ON diagnostics_dpe (colonne_essai)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_essai")
'''

_DROP_SOURCE = '''"""Retire the index created above."""


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_essai")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_essai "
        "ON diagnostics_dpe (colonne_essai)"
    )
'''


def test_a_create_alone_registers_the_column() -> None:
    """Control: without a later DROP, the column IS required from the pipeline.

    Also the downgrade guard -- this source drops its own index in
    ``downgrade()``, and its docstring names a DROP in prose. Were either read,
    the column would vanish here and the whole detector would go blind while
    still passing.
    """
    assert _etat_net([("001_create.py", _CREATE_SOURCE)]) == {
        "colonne_essai": "001_create.py"
    }


def test_a_later_drop_retires_the_column() -> None:
    """The capability migration 082 relies on: a later DROP retires the column."""
    etat = _etat_net([("001_create.py", _CREATE_SOURCE), ("002_drop.py", _DROP_SOURCE)])
    assert "colonne_essai" not in etat, (
        "A migration that DROPs an index must retire its column from the "
        f"required set; got {etat}."
    )


def test_an_unknown_drop_changes_nothing() -> None:
    """A DROP naming an index no migration created here must be inert."""
    inconnu = """
def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dune_autre_table")
"""
    etat = _etat_net([("001_create.py", _CREATE_SOURCE), ("002_x.py", inconnu)])
    assert etat == {"colonne_essai": "001_create.py"}


def test_id_rnb_is_retired_not_lost() -> None:
    """Migration 082's removal must read as retired, not as a swap casualty.

    Pins the reason ``id_rnb`` is absent from ``_create_staging_indexes``: it was
    deliberately dropped with its only reader (the Lot C2 path), and NOT
    forgotten the way the 077 index was. If a future migration re-indexes the
    column, this test fails and whoever does it must also put the index back in
    the pipeline's rebuild list.
    """
    assert "id_rnb" not in _migration_indexed_columns()
    assert "id_rnb" not in _pipeline_indexed_columns()


def test_retry_resets_only_staging_uniqueness_before_reconciliation():
    class Cursor:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=None):
            self.statements.append((statement, params))
            return self

        def fetchall(self):
            return [("staging_pk",), ("staging_unique",)]

    cur = Cursor()
    DpePipeline._reset_staging_load(cur)
    assert cur.statements[0][1] == ("diagnostics_dpe_staging",)
    assert "contype IN ('p', 'u')" in cur.statements[0][0]
    assert [sql.as_string() for sql, _ in cur.statements[1:3]] == [
        'ALTER TABLE "diagnostics_dpe_staging" DROP CONSTRAINT "staging_pk"',
        'ALTER TABLE "diagnostics_dpe_staging" DROP CONSTRAINT "staging_unique"',
    ]
    assert cur.statements[-1][0] == "TRUNCATE diagnostics_dpe_staging"

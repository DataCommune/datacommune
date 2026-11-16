"""Invariant tests for staging index hygiene (DB-HYGIENE-01).

Production measurement that motivated this: 647 indexes named ``*_staging_*``
on the production database, 5 112 MB, 3 613 MB of which had never been scanned.
They accumulated because

  1. staging was created with ``LIKE prod INCLUDING ALL`` (which implies
     ``INCLUDING INDEXES``), so every production index was copied onto staging
     under an auto-generated name;
  2. ``_create_staging_indexes()`` then created the *same* indexes again under
     explicit names;
  3. ``ALTER TABLE ... RENAME`` does not cascade to indexes, so both sets landed
     on the production table still carrying "staging" in their names;
  4. the next run's ``LIKE ... INCLUDING ALL`` copied the now-doubled set.

These tests replay a full swap against an in-memory PostgreSQL catalog model and
assert the invariant: after a swap the production table carries no index whose
name contains "staging", and carries the expected index set exactly once.

The catalog fake only models the DDL the swap protocol actually emits. Any
statement it does not recognise is recorded in ``unhandled`` and asserted empty,
so a future change to the emitted SQL cannot silently void these tests.
"""

import re
from dataclasses import dataclass, replace
from unittest.mock import patch

import pytest

from app.etl.base import DEPARTEMENTS, BasePipeline
from app.etl.runner import PipelineRunner
from app.etl_antennes.pipeline import AntennesPipeline
from app.etl_ban.pipeline import BanPipeline
from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
from app.etl_dvf.pipeline import DvfPipeline
from app.etl_rnb_liens.pipeline import RnbLiensPipeline
from app.scope import floors


@pytest.fixture(autouse=True)
def _full_france_scope(bind_departements):
    # Departmental pipelines read their departements from the bound scope;
    # these catalog doubles model a full-France instance (102 partitions),
    # except the RNB links catalog, sized for 44+85.
    bind_departements(DEPARTEMENTS, BasePipeline)
    bind_departements(("44", "85"), RnbLiensPipeline)


# ---------------------------------------------------------------------------
# Minimal in-memory PostgreSQL catalog
# ---------------------------------------------------------------------------

_ROWCOUNT = 250_000


@dataclass(frozen=True)
class FakeIndex:
    """One row of pg_index, flattened."""

    name: str
    table: str
    unique: bool
    method: str
    columns: str
    constraint: str | None = None

    def definition(self) -> str:
        unique = "UNIQUE " if self.unique else ""
        return (
            f"CREATE {unique}INDEX {self.name} ON public.{self.table} "
            f"USING {self.method} ({self.columns})"
        )


# Reads of the DVF swap answered without modelling rows: the window of the
# DVF dates (C24) and the sequence moved past the source ordinals (WR-1).
_FIXED_ANSWERS: dict[str, list[tuple]] = {
    "select min(date_mutation), max(date_mutation)": [(None, None)],
    "select setval('mutations_staging_id_seq'": [(None,)],
    # BDNB swap (plan 04.7-19): rows per departement of the candidate, then
    # the duplicated relations and the diverging ffo groups of the flat table.
    "select code_departement, count(*) from bdnb_batiment_parcelle_staging": [
        ("44", 800_000),
        ("85", 600_000),
    ],
    "select (select count(*) from ( select 1 from _bdnb_batiment_parcelle": [(0, 0)],
}


class FakeCatalog:
    """Models just enough of PostgreSQL to replay an ETL swap."""

    def __init__(self) -> None:
        self.tables: set[str] = set()
        self.sequences: set[str] = set()
        # Row count returned by every ``SELECT count(*)``. Per-catalog because
        # each pipeline enforces its own GATE-01 floor inside swap().
        self.rowcount: int = _ROWCOUNT
        self.indexes: dict[str, FakeIndex] = {}
        # table -> {constraint name: constraint definition}
        self.constraints: dict[str, dict[str, str]] = {}
        # child partition -> partitioned parent table
        self.partitions: dict[str, str] = {}
        # child index -> the partitioned parent index that requires it
        self.index_parents: dict[str, str] = {}
        self.unhandled: list[str] = []

    # -- helpers ----------------------------------------------------------
    def add_table(self, name: str) -> None:
        self.tables.add(name)
        self.constraints.setdefault(name, {})

    def add_index(self, index: FakeIndex) -> None:
        self.indexes[index.name] = index

    def indexes_of(self, table: str) -> set[str]:
        return {i.name for i in self.indexes.values() if i.table == table}

    def partitions_of(self, table: str) -> set[str]:
        return {child for child, parent in self.partitions.items() if parent == table}

    def add_partition(self, child: str, parent: str) -> None:
        """Attach a partition and mirror the parent's partitioned indexes.

        ``CREATE TABLE child PARTITION OF parent`` makes PostgreSQL build one
        child index per index already carried by the parent, under an
        auto-generated name, attached to the parent index.
        """
        self.add_table(child)
        self.partitions[child] = parent
        for index_name in sorted(self.indexes_of(parent)):
            self._add_child_index(self.indexes[index_name], child)

    def _add_child_index(self, parent_index: FakeIndex, child_table: str) -> None:
        name = self._auto_index_name(child_table, parent_index.columns)
        self.add_index(
            replace(parent_index, name=name, table=child_table, constraint=None)
        )
        self.index_parents[name] = parent_index.name

    def add_constraint(self, table: str, kind: str, columns: str) -> None:
        cols = "_".join(c.strip() for c in columns.split(","))
        conname = f"{table}_pkey" if kind == "PRIMARY KEY" else f"{table}_{cols}_key"
        self.constraints.setdefault(table, {})[conname] = f"{kind} ({columns})"
        self.add_index(
            FakeIndex(
                name=conname,
                table=table,
                unique=True,
                method="btree",
                columns=columns,
                constraint=conname,
            )
        )

    def _auto_index_name(self, table: str, columns: str) -> str:
        cols = "_".join(c.strip() for c in columns.split(","))
        base = f"{table}_{cols}_idx"
        if base not in self.indexes:
            return base
        suffix = 1
        while f"{base}{suffix}" in self.indexes:
            suffix += 1
        return f"{base}{suffix}"

    def _drop_table(self, name: str) -> None:
        for child in sorted(self.partitions_of(name)):
            self._drop_table(child)
        self.tables.discard(name)
        self.constraints.pop(name, None)
        self.partitions.pop(name, None)
        for index_name in list(self.indexes):
            if self.indexes[index_name].table == name:
                del self.indexes[index_name]
                self.index_parents.pop(index_name, None)

    # -- statement dispatch -----------------------------------------------
    def execute(self, sql: str, params: object = None) -> list[tuple]:
        statement = " ".join(sql.split())

        rows = self._catalog_query(statement, params)
        if rows is not None:
            return rows

        if self._ddl(statement):
            return []

        self.unhandled.append(statement)
        return []

    def _catalog_query(self, statement: str, params: object) -> list[tuple] | None:
        if "relpersistence" in statement:
            return []  # every modelled candidate is logged (plan 04.1-04)
        if "pg_get_indexdef" in statement:
            assert isinstance(params, dict)
            table = params["table"]
            # The real query UNIONs the table with its partitions.
            scope = {table} | self.partitions_of(table)
            return [
                (
                    i.name,
                    i.table,
                    i.definition(),
                    i.constraint,
                    i.name in self.index_parents,
                )
                for i in sorted(self.indexes.values(), key=lambda x: (x.table, x.name))
                if i.table in scope
            ]
        if "x.indexrelid = rel.oid" in statement:
            assert isinstance(params, tuple)
            name = params[0]
            if name in self.indexes:
                return [(self.indexes[name].table,)]
            if name in self.tables or name in self.sequences:
                return [(None,)]
            return []
        if "inh.inhparent" in statement:
            assert isinstance(params, tuple)
            return [(name,) for name in sorted(self.partitions_of(params[0]))]
        if "pg_get_constraintdef" in statement:
            assert isinstance(params, tuple)
            table = params[0]
            return [(d,) for d in sorted(self.constraints.get(table, {}).values())]
        if statement.lower().startswith("select count(*)"):
            return [(self.rowcount,)]
        lowered = statement.lower()
        return next(
            (
                rows
                for prefix, rows in _FIXED_ANSWERS.items()
                if lowered.startswith(prefix)
            ),
            None,
        )

    def _ddl(self, statement: str) -> bool:
        for handler in (
            self._ddl_drop_table,
            self._ddl_sequences,
            self._ddl_create_like,
            self._ddl_create_partition,
            self._ddl_indexes,
            self._ddl_renames,
            self._ddl_constraints,
        ):
            if handler(statement):
                return True
        return bool(
            re.match(
                r"(SET |INSERT |ALTER TABLE \S+ (SET LOGGED|ALTER COLUMN)|"
                # LOCK TABLE: the ARCEP offer dictionary (plan 04.1-16), no shape.
                # COMMENT ON COLUMN: the DVF suffix mark (C44, review 04.1-19 WR-01).
                r"CREATE TABLE IF NOT EXISTS |COPY |DELETE |ANALYZE |LOCK TABLE |"
                r"COMMENT ON COLUMN )",
                statement,
                re.I,
            )
        )

    def _ddl_drop_table(self, statement: str) -> bool:
        m = re.match(r"DROP TABLE (?:IF EXISTS )?(\S+)", statement, re.I)
        if not m:
            return False
        self._drop_table(m.group(1))
        return True

    def _ddl_sequences(self, statement: str) -> bool:
        m = re.match(r"DROP SEQUENCE (?:IF EXISTS )?(\S+)", statement, re.I)
        if m:
            self.sequences.discard(m.group(1))
            return True
        m = re.match(r"CREATE SEQUENCE (\S+)", statement, re.I)
        if m:
            self.sequences.add(m.group(1))
            return True
        if re.match(r"ALTER SEQUENCE (?:IF EXISTS )?\S+ OWNED BY ", statement, re.I):
            return True  # ownership only, no catalog shape change
        m = re.match(
            r"ALTER SEQUENCE (?:IF EXISTS )?(\S+) RENAME TO (\S+)", statement, re.I
        )
        if m:
            if m.group(1) in self.sequences:
                self.sequences.discard(m.group(1))
                self.sequences.add(m.group(2))
            return True
        return False

    def _ddl_create_like(self, statement: str) -> bool:
        m = re.match(
            r"CREATE (?:UNLOGGED )?TABLE (\S+) \(LIKE (\S+) INCLUDING ALL"
            r"( EXCLUDING INDEXES)?\)",
            statement,
            re.I,
        )
        if not m:
            return False
        self._create_like(m.group(1), m.group(2), excluding_indexes=bool(m.group(3)))
        return True

    def _ddl_create_partition(self, statement: str) -> bool:
        m = re.match(
            r"CREATE (?:UNLOGGED )?TABLE (?:IF NOT EXISTS )?(\S+) PARTITION OF (\S+)",
            statement,
            re.I,
        )
        if not m:
            return False
        self.add_partition(m.group(1), m.group(2))
        return True

    def _ddl_indexes(self, statement: str) -> bool:
        m = re.match(
            r"CREATE (UNIQUE )?INDEX (\S+) ON (\S+) (?:USING (\S+) )?\((.+)\)",
            statement,
            re.I,
        )
        if m:
            index = FakeIndex(
                name=m.group(2),
                table=m.group(3),
                unique=bool(m.group(1)),
                method=(m.group(4) or "btree").lower(),
                columns=m.group(5),
            )
            self.add_index(index)
            # CREATE INDEX on a partitioned parent cascades to every partition.
            for child in sorted(self.partitions_of(index.table)):
                self._add_child_index(index, child)
            return True
        m = re.match(r"DROP INDEX (?:IF EXISTS )?(\S+)", statement, re.I)
        if m:
            index = self.indexes.get(m.group(1))
            if index is not None:
                if index.constraint is not None:
                    raise AssertionError(
                        f"cannot drop index {index.name} because constraint "
                        f"{index.constraint} requires it"
                    )
                parent = self.index_parents.get(index.name)
                if parent is not None:
                    raise AssertionError(
                        f"cannot drop index {index.name} because index "
                        f"{parent} requires it"
                    )
                # Dropping a partitioned index takes its children with it.
                for child, owner in list(self.index_parents.items()):
                    if owner == index.name:
                        del self.index_parents[child]
                        self.indexes.pop(child, None)
                del self.indexes[index.name]
            return True
        return False

    def _ddl_renames(self, statement: str) -> bool:
        m = re.match(
            r"ALTER INDEX (?:IF EXISTS )?(\S+) RENAME TO (\S+)", statement, re.I
        )
        if m:
            self._rename_index(m.group(1), m.group(2))
            return True
        m = re.match(
            r"ALTER TABLE (?:IF EXISTS )?(\S+) RENAME TO (\S+)", statement, re.I
        )
        if m:
            self._rename_table(m.group(1), m.group(2))
            return True
        return False

    def _ddl_constraints(self, statement: str) -> bool:
        m = re.match(
            r"ALTER TABLE (\S+) ADD (PRIMARY KEY|UNIQUE(?: NULLS NOT DISTINCT)?) "
            r"\((.+?)\)",
            statement,
            re.I,
        )
        if not m:
            return False
        self.add_constraint(m.group(1), m.group(2).upper(), m.group(3))
        return True

    def _create_like(self, name: str, source: str, *, excluding_indexes: bool) -> None:
        self.add_table(name)
        if excluding_indexes:
            return
        # INCLUDING INDEXES copies every source index AND the PK/UNIQUE
        # constraints backing them, under PostgreSQL auto-generated names.
        for index_name in sorted(self.indexes_of(source)):
            source_index = self.indexes[index_name]
            if source_index.constraint is not None:
                definition = self.constraints[source][source_index.constraint]
                kind = "PRIMARY KEY" if definition.startswith("PRIMARY") else "UNIQUE"
                self.add_constraint(name, kind, definition.split("(", 1)[1].rstrip(")"))
                continue
            self.add_index(
                replace(
                    source_index,
                    name=self._auto_index_name(name, source_index.columns),
                    table=name,
                )
            )

    def _rename_index(self, old: str, new: str) -> None:
        index = self.indexes.pop(old, None)
        if index is None:
            return
        assert new not in self.indexes, f"index name {new} already exists"
        constraint = index.constraint
        if constraint is not None:
            # PostgreSQL keeps the owning constraint's name in sync.
            definition = self.constraints[index.table].pop(constraint)
            self.constraints[index.table][new] = definition
            constraint = new
        self.indexes[new] = replace(index, name=new, constraint=constraint)
        parent = self.index_parents.pop(old, None)
        if parent is not None:
            self.index_parents[new] = parent
        for child, owner in list(self.index_parents.items()):
            if owner == old:
                self.index_parents[child] = new

    def _rename_table(self, old: str, new: str) -> None:
        if old not in self.tables:
            return
        assert new not in self.tables, f"table name {new} already exists"
        self.tables.discard(old)
        self.tables.add(new)
        self.constraints[new] = self.constraints.pop(old, {})
        for name, index in list(self.indexes.items()):
            if index.table == old:
                self.indexes[name] = replace(index, table=new)
        # RENAME does not detach partitions: the parent link follows the name.
        parent = self.partitions.pop(old, None)
        if parent is not None:
            self.partitions[new] = parent
        for child, owner in list(self.partitions.items()):
            if owner == old:
                self.partitions[child] = new


class FakeCursor:
    def __init__(self, catalog: FakeCatalog) -> None:
        self.catalog = catalog
        self._rows: list[tuple] = []
        self.statements: list[str] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> "FakeCursor":
        self.statements.append(" ".join(sql.split()))
        self._rows = self.catalog.execute(sql, params)
        return self

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, catalog: FakeCatalog) -> None:
        self.catalog = catalog
        self.cursor_obj = FakeCursor(catalog)
        self.commits = 0
        self.autocommit = False

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def execute(self, sql: str, params: object = None) -> FakeCursor:
        return self.cursor_obj.execute(sql, params)

    def commit(self) -> None:
        self.commits += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Production DDL for antennes — migration 033_antennes.py.
PRODUCTION_INDEXES = {
    "antennes_pkey",
    "idx_antennes_code_insee",
    "idx_antennes_generation",
    "idx_antennes_operateur",
    "idx_antennes_geom",
}


def _seeded_catalog() -> FakeCatalog:
    catalog = FakeCatalog()
    catalog.add_table("antennes")
    catalog.add_table("_antennes_staging_flat")
    catalog.sequences.add("antennes_id_seq")
    catalog.add_constraint("antennes", "PRIMARY KEY", "id")
    for name, method, columns in (
        ("idx_antennes_code_insee", "btree", "code_insee"),
        ("idx_antennes_generation", "btree", "generation"),
        ("idx_antennes_operateur", "btree", "operateur"),
        ("idx_antennes_geom", "gist", "geom"),
    ):
        catalog.add_index(FakeIndex(name, "antennes", False, method, columns))
    return catalog


def _run_swap(catalog: FakeCatalog, pipeline: BasePipeline | None = None) -> None:
    pipeline = pipeline or AntennesPipeline()
    with (
        patch("psycopg.connect", side_effect=lambda *a, **kw: FakeConnection(catalog)),
        # GATE-02 opens its own connection and reads information_schema, which
        # this catalog does not model; the drift gate is covered elsewhere.
        patch.object(
            PipelineRunner,
            "_check_schema_drift_conn",
            staticmethod(lambda *a, **kw: None),
        ),
    ):
        pipeline.swap()


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------


def test_swap_leaves_no_staging_named_index_on_production() -> None:
    """The invariant: production carries the canonical set, exactly once."""
    catalog = _seeded_catalog()

    _run_swap(catalog)

    production_indexes = catalog.indexes_of("antennes")
    staging_named = sorted(n for n in production_indexes if "staging" in n)
    assert staging_named == [], (
        f"production table still carries staging-named indexes: {staging_named}"
    )
    assert production_indexes == PRODUCTION_INDEXES
    assert catalog.unhandled == []


def test_repeated_swaps_do_not_accumulate_indexes() -> None:
    """Three consecutive runs must converge, not grow (the prod symptom)."""
    catalog = _seeded_catalog()

    counts = []
    for _ in range(3):
        _run_swap(catalog)
        counts.append(len(catalog.indexes_of("antennes")))
        assert catalog.indexes_of("antennes") == PRODUCTION_INDEXES

    assert counts == [len(PRODUCTION_INDEXES)] * 3


def test_swap_clears_legacy_staging_indexes_inherited_from_production() -> None:
    """Garbage already sitting on production must not survive one more swap."""
    catalog = _seeded_catalog()
    # Legacy shapes measured in prod: hand-written staging names plus the
    # auto-generated LIKE copies, all sitting on the production table.
    for name, method, columns in (
        ("idx_antennes_staging_insee", "btree", "code_insee"),
        ("antennes_staging_code_insee_idx", "btree", "code_insee"),
        ("antennes_staging_geom_idx2", "gist", "geom"),
    ):
        catalog.add_index(FakeIndex(name, "antennes", False, method, columns))

    _run_swap(catalog)

    assert catalog.indexes_of("antennes") == PRODUCTION_INDEXES


# ---------------------------------------------------------------------------
# Flat-staging pipelines that inherit swap_tables() (BAN, RNB liens)
# ---------------------------------------------------------------------------
#
# Unlike antennes these two build their prod-shaped staging table in the
# runner's staging phase, so the fixtures below seed it the way
# ``BasePipeline.create_staging_table`` leaves it: same shape as production,
# no index, no PK/UNIQUE (``LIKE ... EXCLUDING INDEXES``), plus its own id
# sequence. ``swap()`` then does the rest.

# Production DDL for adresses — migrations 001, 002 and 021.
ADRESSES_INDEXES = {
    "adresses_pkey",
    "idx_adresses_position",
    "idx_adresses_position_geog",
    "idx_adresses_ban_id",
    "idx_adresses_search_trgm",
    "idx_adresses_search_fts",
    "idx_adresses_code_postal",
    "idx_adresses_code_commune",
}

# Production DDL for rnb_liens — migration 079.
RNB_LIENS_INDEXES = {
    "rnb_liens_pkey",
    "rnb_liens_id_rnb_cle_ban_key",
    "idx_rnb_liens_cle_ban",
    "idx_rnb_liens_parcelles",
}


def _seed_staging(catalog: FakeCatalog, staging: str) -> None:
    """Recreate the post-``create_staging_table`` state: bare shell + sequence."""
    catalog.add_table(staging)
    catalog.sequences.add(f"{staging}_id_seq")


def _ban_catalog() -> FakeCatalog:
    catalog = FakeCatalog()
    # 26 M addresses in production — must clear min_staging_rows_final (20 M).
    catalog.rowcount = 26_000_000
    catalog.add_table("adresses")
    catalog.add_table("_ban_staging_flat")
    catalog.sequences.add("adresses_id_seq")
    catalog.add_constraint("adresses", "PRIMARY KEY", "id, code_departement")
    for name, method, columns in (
        ("idx_adresses_position", "gist", "position"),
        ("idx_adresses_position_geog", "gist", "geography(position)"),
        ("idx_adresses_ban_id", "btree", "ban_id"),
        ("idx_adresses_search_trgm", "gin", "search_text gin_trgm_ops"),
        ("idx_adresses_search_fts", "gin", "to_tsvector('french', search_text)"),
        ("idx_adresses_code_postal", "btree", "code_postal"),
        ("idx_adresses_code_commune", "btree", "code_commune"),
    ):
        catalog.add_index(FakeIndex(name, "adresses", False, method, columns))
    _seed_staging(catalog, "adresses_staging")
    return catalog


def _rnb_liens_catalog() -> FakeCatalog:
    catalog = FakeCatalog()
    # ~2 M pairs on 44+85 — must clear min_staging_rows_final (500 k).
    catalog.rowcount = 2_000_000
    catalog.add_table("rnb_liens")
    catalog.add_table("_rnb_liens_staging_flat")
    catalog.sequences.add("rnb_liens_id_seq")
    catalog.add_constraint("rnb_liens", "PRIMARY KEY", "id")
    catalog.add_constraint("rnb_liens", "UNIQUE", "id_rnb, cle_ban")
    for name, method, columns in (
        ("idx_rnb_liens_cle_ban", "btree", "cle_ban"),
        ("idx_rnb_liens_parcelles", "gin", "parcelles"),
    ):
        catalog.add_index(FakeIndex(name, "rnb_liens", False, method, columns))
    _seed_staging(catalog, "rnb_liens_staging")
    return catalog


# Production DDL for bdnb_batiment_parcelle — migration 113.
BDNB_INDEXES = {"bdnb_batiment_parcelle_pkey", "idx_bdnb_bp_parcelle_groupe"}


def _bdnb_catalog() -> FakeCatalog:
    catalog = FakeCatalog()
    # 1.4 M relations on 44+85 (the fixed answer): above 50 % of production.
    catalog.rowcount = 1_400_000
    catalog.add_table("bdnb_batiment_parcelle")
    catalog.add_table("_bdnb_batiment_parcelle_staging_flat")
    catalog.sequences.add("bdnb_batiment_parcelle_id_seq")
    catalog.add_constraint("bdnb_batiment_parcelle", "PRIMARY KEY", "id")
    catalog.add_index(
        FakeIndex(
            "idx_bdnb_bp_parcelle_groupe",
            "bdnb_batiment_parcelle",
            True,
            "btree",
            "parcelle_id, batiment_groupe_id",
        )
    )
    _seed_staging(catalog, "bdnb_batiment_parcelle_staging")
    return catalog


def test_bdnb_swaps_keep_the_production_index_set_and_converge(monkeypatch) -> None:
    """The read index of the construction theme survives every swap, once."""
    # The fake catalog counts no row per departement: the producer floors
    # (80 % of the national counts) are tested elsewhere, not here.
    monkeypatch.delitem(floors.PRODUCER_COUNTS, "bdnb_batiment_parcelle")
    catalog = _bdnb_catalog()

    for _ in range(2):
        _run_swap(catalog, BdnbBatimentParcellePipeline())
        assert catalog.indexes_of("bdnb_batiment_parcelle") == BDNB_INDEXES
        assert catalog.indexes["idx_bdnb_bp_parcelle_groupe"].unique
        assert "bdnb_batiment_parcelle_id_seq" in catalog.sequences
        assert catalog.unhandled == []
        _seed_staging(catalog, "bdnb_batiment_parcelle_staging")


def test_bdnb_refused_swap_keeps_the_counts_that_refused_it(monkeypatch) -> None:
    """D13: diverging ffo rows refuse the candidate before any rename, and
    the count is kept for the receipt (it was assigned after the guards)."""
    monkeypatch.delitem(floors.PRODUCER_COUNTS, "bdnb_batiment_parcelle")
    monkeypatch.setitem(
        _FIXED_ANSWERS,
        "select (select count(*) from ( select 1 from _bdnb_batiment_parcelle",
        [(1, 2)],
    )
    catalog = _bdnb_catalog()
    pipeline = BdnbBatimentParcellePipeline()

    with pytest.raises(RuntimeError, match="ffo_divergents"):
        _run_swap(catalog, pipeline)

    assert pipeline.swap_counters() == {
        "publiees_44": 800_000,
        "publiees_85": 600_000,
        "doublons_inter_fichiers": 1,
        "ffo_divergents": 2,
    }
    # Refused before phase B: production keeps its table and its index.
    assert catalog.indexes_of("bdnb_batiment_parcelle") == BDNB_INDEXES


def test_ban_swap_keeps_the_production_index_set() -> None:
    """adresses must still carry its 7 indexes + PK once the swap is done."""
    catalog = _ban_catalog()

    _run_swap(catalog, BanPipeline())

    assert catalog.indexes_of("adresses") == ADRESSES_INDEXES
    assert catalog.unhandled == []


def test_rnb_liens_swap_keeps_the_production_index_set() -> None:
    """rnb_liens must still carry its 2 indexes + PK/UNIQUE once swapped."""
    catalog = _rnb_liens_catalog()

    _run_swap(catalog, RnbLiensPipeline())

    assert catalog.indexes_of("rnb_liens") == RNB_LIENS_INDEXES
    assert catalog.unhandled == []


def test_repeated_ban_swaps_converge_on_the_production_index_set() -> None:
    """Two consecutive runs: no loss, no accumulation (both prod symptoms)."""
    catalog = _ban_catalog()

    for _ in range(2):
        _run_swap(catalog, BanPipeline())
        assert catalog.indexes_of("adresses") == ADRESSES_INDEXES
        _seed_staging(catalog, "adresses_staging")


def test_repeated_rnb_liens_swaps_converge_on_the_production_index_set() -> None:
    catalog = _rnb_liens_catalog()

    for _ in range(2):
        _run_swap(catalog, RnbLiensPipeline())
        assert catalog.indexes_of("rnb_liens") == RNB_LIENS_INDEXES
        _seed_staging(catalog, "rnb_liens_staging")


def test_flat_staging_swaps_rename_their_id_sequence() -> None:
    """The sequence rename is NOT covered by swap_tables() — it must stay."""
    ban = _ban_catalog()
    _run_swap(ban, BanPipeline())
    assert "adresses_id_seq" in ban.sequences
    assert "adresses_staging_id_seq" not in ban.sequences

    rnb = _rnb_liens_catalog()
    _run_swap(rnb, RnbLiensPipeline())
    assert "rnb_liens_id_seq" in rnb.sequences
    assert "rnb_liens_staging_id_seq" not in rnb.sequences


# ---------------------------------------------------------------------------
# DVF: a partitioned pipeline whose staging index set lost id_parcelle
# ---------------------------------------------------------------------------
#
# ``57-PERF-EXPLAIN.md`` § 6.3 measured the cost of the gap: without
# ``idx_mutations_id_parcelle`` the Fiche's nominal access path falls back to a
# sequential partition scan (19.8 ms vs 0.04-0.08 ms). Migration 072 declares
# the index, but ``_create_staging_indexes`` never rebuilt it on staging, so the
# atomic swap replaced an indexed table with an unindexed one on every run —
# the same "config != live state" failure migration 085 repaired for
# ``idx_equipements_geom_geog``.

# Production DDL for mutations — migrations 005, 021, 072 and 107.
MUTATIONS_INDEXES = {
    "mutations_pkey",
    "idx_mutations_code_commune",
    "idx_mutations_id_mutation",
    "idx_mutations_date_mutation",
    "idx_mutations_dept_date",
    "idx_mutations_id_parcelle",
}

# Departments with live data in production (57-CONVERGENCE.md).
DVF_LIVE_DEPARTEMENTS = ("44", "85")


def _seed_dvf_staging(catalog: FakeCatalog, pipeline: DvfPipeline) -> None:
    """Recreate the post-``create_staging_table`` state for a partitioned table.

    ``LIKE ... INCLUDING ALL EXCLUDING INDEXES`` plus one partition per scoped
    department plus DEFAULT: a bare shell, no index, no PK, own id sequence.
    """
    staging = pipeline.staging_table_name
    catalog.add_table(staging)
    catalog.add_table(pipeline.flat_staging_table_name)
    catalog.sequences.add(f"{staging}_id_seq")
    for dept in pipeline.partition_department_scope:
        catalog.add_partition(f"{staging}_{dept.lower()}", staging)
    catalog.add_partition(f"{staging}_default", staging)


def _dvf_catalog(pipeline: DvfPipeline) -> FakeCatalog:
    catalog = FakeCatalog()
    # 287 261 mutations on the two live partitions (57-CONVERGENCE.md).
    catalog.rowcount = 287_261
    catalog.add_table("mutations")
    catalog.add_constraint("mutations", "PRIMARY KEY", "id, code_departement")
    for name, method, columns in (
        ("idx_mutations_code_commune", "btree", "code_commune"),
        ("idx_mutations_id_mutation", "btree", "id_mutation"),
        ("idx_mutations_date_mutation", "btree", "date_mutation"),
        ("idx_mutations_dept_date", "btree", "code_departement, date_mutation"),
        ("idx_mutations_id_parcelle", "btree", "id_parcelle"),
    ):
        catalog.add_index(FakeIndex(name, "mutations", False, method, columns))
    for dept in DVF_LIVE_DEPARTEMENTS:
        catalog.add_partition(f"mutations_{dept.lower()}", "mutations")
    _seed_dvf_staging(catalog, pipeline)
    return catalog


def _run_dvf_swap(catalog: FakeCatalog, pipeline: DvfPipeline) -> None:
    """Replay ``DvfPipeline.swap()`` against the catalog fake.

    ``reconstruit_mutations_grain`` (D-04) runs real SQL against derived tables
    this catalog does not model; it is covered by its own tests and stubbed out
    here so the assertions stay on the index set.
    """
    with (
        patch("psycopg.connect", side_effect=lambda *a, **kw: FakeConnection(catalog)),
        patch(
            "app.etl_dvf.pipeline.reconstruit_mutations_grain",
            return_value={
                "mutations_reconstruites": catalog.rowcount,
                "liaisons_parcelles": catalog.rowcount,
                "valeur_divergente_count": 0,
            },
        ),
    ):
        pipeline.swap()


def test_dvf_staging_index_set_covers_id_parcelle() -> None:
    """The Fiche's nominal access path must be rebuilt on staging every run."""
    pipeline = DvfPipeline()
    catalog = _dvf_catalog(pipeline)
    cursor = FakeCursor(catalog)

    DvfPipeline._create_staging_indexes(cursor)

    staging = pipeline.staging_table_name
    indexed_columns = {
        catalog.indexes[name].columns for name in catalog.indexes_of(staging)
    }
    assert "id_parcelle" in indexed_columns, (
        f"{staging} carries no index on id_parcelle: {sorted(indexed_columns)}"
    )
    assert "idx_mutations_staging_id_parcelle" in catalog.indexes_of(staging)
    assert catalog.unhandled == []


def test_dvf_swap_keeps_the_canonical_id_parcelle_index() -> None:
    """After the swap, mutations carries migration 072's name, exactly once."""
    pipeline = DvfPipeline()
    catalog = _dvf_catalog(pipeline)

    _run_dvf_swap(catalog, pipeline)

    production_indexes = catalog.indexes_of("mutations")
    assert "idx_mutations_id_parcelle" in production_indexes, (
        "the swap dropped the index migration 072 declares: "
        f"{sorted(production_indexes)}"
    )
    staging_named = sorted(n for n in production_indexes if "staging" in n)
    assert staging_named == [], (
        f"production table still carries staging-named indexes: {staging_named}"
    )
    assert production_indexes == MUTATIONS_INDEXES
    assert catalog.unhandled == []


def test_repeated_dvf_swaps_converge_on_the_production_index_set() -> None:
    """Two consecutive runs: no loss, no duplicate suffixed homonym."""
    pipeline = DvfPipeline()
    catalog = _dvf_catalog(pipeline)

    for _ in range(2):
        _run_dvf_swap(catalog, pipeline)
        assert catalog.indexes_of("mutations") == MUTATIONS_INDEXES
        _seed_dvf_staging(catalog, pipeline)


# ---------------------------------------------------------------------------
# Unit tests on the reusable helpers
# ---------------------------------------------------------------------------


def test_staging_index_name_is_a_pure_suffix() -> None:
    pipeline = AntennesPipeline()
    assert (
        pipeline.staging_index_name("idx_antennes_geom") == "idx_antennes_geom_staging"
    )
    assert (
        pipeline.canonical_index_name("idx_antennes_geom_staging")
        == "idx_antennes_geom"
    )


def test_staging_index_name_rejects_oversized_identifiers() -> None:
    pipeline = AntennesPipeline()
    with pytest.raises(ValueError, match="exceeds 63 chars"):
        pipeline.staging_index_name("i" * 60)


def test_canonical_index_name_handles_constraint_and_legacy_forms() -> None:
    pipeline = AntennesPipeline()
    # PostgreSQL auto-named constraint index
    assert pipeline.canonical_index_name("antennes_staging_pkey") == "antennes_pkey"
    # Legacy hand-written staging name
    assert (
        pipeline.canonical_index_name("idx_antennes_staging_insee")
        == "idx_antennes_insee"
    )


def test_index_cleanup_is_wired_into_the_shared_swap_protocol() -> None:
    """Every pipeline inherits the cleanup because it lives in swap_tables()."""
    assert "DB-HYGIENE-01" in (BasePipeline.swap_tables.__doc__ or "")
    assert hasattr(BasePipeline, "rename_indexes_after_swap")

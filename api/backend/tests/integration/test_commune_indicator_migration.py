"""Real Alembic contract for immutable commune indicator persistence."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from psycopg import sql
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    ObjectNotInPrerequisiteState,
    UniqueViolation,
)
from psycopg.types.json import Jsonb

from app.config import settings

REVISION = "096_commune_indicator_build_activation"
PREVIOUS_REVISION = "095_commune_indicator_storage"
TABLES = (
    "commune_indicator_manifests",
    "commune_indicator_result_builds",
    "commune_indicator_result_cells",
    "commune_indicator_build_activations",
)
GENERATION = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def migration_dsn(pg_dsn: str) -> Iterator[str]:
    """Give each migration test its own wrapper-owned child database."""
    name = "contract_migration_" + uuid4().hex
    parts = urlsplit(pg_dsn)
    child = urlunsplit(parts._replace(path="/" + name))
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield child
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _upgrade(alembic_config, revision: str) -> None:
    command.upgrade(alembic_config, revision)


def _assert_rewrite_refused(conn, table: str) -> None:
    statements = (
        f"UPDATE {table} SET "
        + (
            "qualified_at=qualified_at"
            if table.endswith("manifests")
            else "recorded_at=recorded_at"
            if table.endswith("activations")
            else "inserted_at=inserted_at"
        )
        if not table.endswith("cells")
        else f"UPDATE {table} SET code_insee=code_insee",
        f"DELETE FROM {table}",
        f"TRUNCATE {table}" + (" CASCADE" if table.endswith("builds") else ""),
    )
    for statement in statements:
        with conn.transaction():
            with pytest.raises(ObjectNotInPrerequisiteState) as error:
                conn.execute(statement)
            assert error.value.sqlstate == "55000"


def test_revision_is_single_head_and_cycle_is_reversible(
    alembic_config, migration_dsn, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "database_url", migration_dsn)
    alembic_config.set_main_option("sqlalchemy.url", migration_dsn)
    script = ScriptDirectory.from_config(alembic_config)
    # 097_instance_scope now follows REVISION; the chain stays single and linear.
    heads = script.get_heads()
    assert len(heads) == 1
    chain = {revision.revision for revision in script.walk_revisions("base", heads[0])}
    assert REVISION in chain
    assert script.get_revision("097_instance_scope").down_revision == REVISION

    _upgrade(alembic_config, PREVIOUS_REVISION)
    with psycopg.connect(migration_dsn) as conn:
        assert (
            conn.execute(
                "SELECT to_regclass('commune_indicator_build_activations')"
            ).fetchone()[0]
            is None
        )
        assert all(
            conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] == table
            for table in TABLES[:-1]
        )

    _upgrade(alembic_config, REVISION)
    with psycopg.connect(migration_dsn) as conn:
        assert all(
            conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] == table
            for table in TABLES
        )

    command.downgrade(alembic_config, PREVIOUS_REVISION)
    with psycopg.connect(migration_dsn) as conn:
        assert (
            conn.execute(
                "SELECT to_regclass('commune_indicator_build_activations')"
            ).fetchone()[0]
            is None
        )
        assert all(
            conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] == table
            for table in TABLES[:-1]
        )
        assert conn.execute("SELECT to_regclass('etl_publications')").fetchone()[0]

    _upgrade(alembic_config, "head")
    _upgrade(alembic_config, "head")


def test_constraints_and_immutable_triggers_reject_invalid_writes(
    alembic_config, migration_dsn, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "database_url", migration_dsn)
    alembic_config.set_main_option("sqlalchemy.url", migration_dsn)
    _upgrade(alembic_config, "head")
    with psycopg.connect(migration_dsn) as conn:
        conn.execute(
            """INSERT INTO etl_publications
               (generation,dataset_name,table_name,status,relation_oids,row_count)
               VALUES (%s,'dpe','diagnostics_dpe','published','{}',0)""",
            (GENERATION,),
        )
        manifest = {
            "dataset_name": "dpe",
            "source_generation": str(GENERATION),
        }
        conn.execute(
            """INSERT INTO commune_indicator_manifests
               (dataset_name,source_generation,manifest,manifest_sha256,qualified_at)
               VALUES ('dpe',%s,%s,%s,NOW())""",
            (GENERATION, Jsonb(manifest), "a" * 64),
        )
        build_id = conn.execute(
            """INSERT INTO commune_indicator_result_builds(build_sha256,header)
               VALUES (%s,%s) RETURNING id""",
            ("b" * 64, Jsonb({"scope": "synthetic"})),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO commune_indicator_result_cells
               (build_id,dataset_name,code_insee,public_payload,private_binding)
               VALUES (%s,'dpe','44001',%s,%s)""",
            (build_id, Jsonb({}), Jsonb({})),
        )
        conn.commit()

        manifest_sql = """INSERT INTO commune_indicator_manifests
            (dataset_name,source_generation,manifest,manifest_sha256,qualified_at)
            VALUES (%s,%s,%s,%s,NOW())"""
        build_sql = """INSERT INTO commune_indicator_result_builds
            (build_sha256,header) VALUES (%s,%s)"""
        cell_sql = """INSERT INTO commune_indicator_result_cells
            (build_id,dataset_name,code_insee,public_payload,private_binding)
            VALUES (%s,%s,%s,%s,%s)"""
        missing_generation = UUID("22222222-2222-4222-8222-222222222222")
        missing_manifest = {
            "dataset_name": "dpe",
            "source_generation": str(missing_generation),
        }
        invalid = (
            (
                ForeignKeyViolation,
                manifest_sql,
                ("dpe", missing_generation, Jsonb(missing_manifest), "c" * 64),
            ),
            (CheckViolation, manifest_sql, ("dpe", GENERATION, Jsonb([]), "d" * 64)),
            (CheckViolation, manifest_sql, ("dpe", GENERATION, Jsonb(manifest), "bad")),
            (
                CheckViolation,
                manifest_sql,
                (
                    "other",
                    GENERATION,
                    Jsonb({**manifest, "dataset_name": "other"}),
                    "e" * 64,
                ),
            ),
            (
                CheckViolation,
                manifest_sql,
                (
                    "dpe",
                    GENERATION,
                    Jsonb({**manifest, "dataset_name": "dvf"}),
                    "f" * 64,
                ),
            ),
            (CheckViolation, build_sql, ("bad", Jsonb({}))),
            (CheckViolation, build_sql, ("c" * 64, Jsonb([]))),
            (UniqueViolation, build_sql, ("b" * 64, Jsonb({}))),
            (
                ForeignKeyViolation,
                cell_sql,
                (999999, "dpe", "44002", Jsonb({}), Jsonb({})),
            ),
            (
                CheckViolation,
                cell_sql,
                (build_id, "other", "44002", Jsonb({}), Jsonb({})),
            ),
            # Hosted commune codes only (migration 103): an overseas
            # collectivity, a code naming no departement, a shape error.
            (
                CheckViolation,
                cell_sql,
                (build_id, "dpe", "97501", Jsonb({}), Jsonb({})),
            ),
            (
                CheckViolation,
                cell_sql,
                (build_id, "dpe", "99001", Jsonb({}), Jsonb({})),
            ),
            (
                CheckViolation,
                cell_sql,
                (build_id, "dpe", "2C004", Jsonb({}), Jsonb({})),
            ),
            (
                CheckViolation,
                cell_sql,
                (build_id, "dpe", "44002", Jsonb([]), Jsonb({})),
            ),
            (
                CheckViolation,
                cell_sql,
                (build_id, "dpe", "44002", Jsonb({}), Jsonb([])),
            ),
            (
                UniqueViolation,
                cell_sql,
                (build_id, "dpe", "44001", Jsonb({}), Jsonb({})),
            ),
        )
        for exception, statement, params in invalid:
            with conn.transaction(), pytest.raises(exception):
                conn.execute(statement, params)
        # Any hostable departement, overseas departements and Corsica included.
        for code in ("01001", "75056", "97411", "2A004"):
            conn.execute(cell_sql, (build_id, "dvf", code, Jsonb({}), Jsonb({})))
        conn.commit()

        for table in TABLES:
            _assert_rewrite_refused(conn, table)

        activation_sql = """INSERT INTO commune_indicator_build_activations
            (event_type,build_id) VALUES (%s,%s)"""
        with conn.transaction(), pytest.raises(CheckViolation):
            conn.execute(activation_sql, ("activate", None))
        with conn.transaction(), pytest.raises(CheckViolation):
            conn.execute(activation_sql, ("revoke", build_id))
        with conn.transaction(), pytest.raises(CheckViolation):
            conn.execute(activation_sql, ("other", None))
        with conn.transaction(), pytest.raises(ForeignKeyViolation):
            conn.execute(activation_sql, ("activate", 999999))


def test_migration_103_widens_cells_and_never_downgrades_over_them(
    alembic_config, migration_dsn, monkeypatch
) -> None:
    """The 44/85 check of 095 comes back only while no other cell is stored."""
    monkeypatch.setattr(settings, "database_url", migration_dsn)
    alembic_config.set_main_option("sqlalchemy.url", migration_dsn)
    _upgrade(alembic_config, "head")
    cell_sql = """INSERT INTO commune_indicator_result_cells
        (build_id,dataset_name,code_insee,public_payload,private_binding)
        VALUES (%s,'dvf',%s,'{}','{}')"""
    with psycopg.connect(migration_dsn) as conn:
        build_id = conn.execute(
            """INSERT INTO commune_indicator_result_builds(build_sha256,header)
               VALUES (%s,'{}') RETURNING id""",
            ("a" * 64,),
        ).fetchone()[0]
        conn.execute(cell_sql, (build_id, "44001"))
    command.downgrade(alembic_config, "102_scoring_scope_national")
    with psycopg.connect(migration_dsn) as conn, pytest.raises(CheckViolation):
        conn.execute(cell_sql, (build_id, "75056"))
    _upgrade(alembic_config, "head")
    with psycopg.connect(migration_dsn) as conn:
        conn.execute(cell_sql, (build_id, "75056"))
    with pytest.raises(RuntimeError, match="outside 44/85"):
        command.downgrade(alembic_config, "102_scoring_scope_national")
    # The refused downgrade is rolled back as a whole: the base stays at head
    # (104 and 105 now follow 103).
    head = ScriptDirectory.from_config(alembic_config).get_heads()[0]
    with psycopg.connect(migration_dsn) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            head,
        )
        assert conn.execute(
            "SELECT count(*) FROM commune_indicator_result_cells"
        ).fetchone() == (2,)

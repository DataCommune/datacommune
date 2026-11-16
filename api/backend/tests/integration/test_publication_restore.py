"""R8: actual local pg_dump/pg_restore, rejected OIDs and validated republishing.

The source and every destination are child databases of the wrapper-owned
container. No remote source, app journal rewrite, or shared restore is allowed.
"""

import asyncio
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tenacity import stop_after_attempt, wait_none

from app.common.publication_readiness import guard_publication
from app.commune.dependencies import COMMUNE_EDGES, COMMUNE_SOURCES
from app.config import settings
from app.data_quality.profiles import PROFILES, DatasetProfile, NullSpec
from app.database import get_db
from app.etl.base import BasePipeline
from app.etl.checkpoint import CheckpointManager
from app.etl.download import get_download_dir
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.registry import registry
from app.etl.runner import PipelineRunner
from app.main import app
from app.middleware import RequestLoggingMiddleware
from app.scheduler import jobs
from tests.integration.test_cor20_publication import create_chain, create_fixture

pytestmark = pytest.mark.destructive

INDICATOR_TABLES = (
    "commune_indicator_manifests",
    "commune_indicator_result_builds",
    "commune_indicator_result_cells",
    "commune_indicator_build_activations",
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _install_indicator_storage(conn):
    for revision in (
        "095_commune_indicator_storage",
        "096_commune_indicator_build_activation",
    ):
        path = Path(__file__).parents[2] / f"migrations/versions/{revision}.py"
        spec = importlib.util.spec_from_file_location(f"restore_{revision}", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = SimpleNamespace(execute=conn.execute)
        migration.upgrade()


def _seed_indicator_storage(conn):
    generation = conn.execute(
        "SELECT generation FROM etl_publications WHERE status='published' ORDER BY id"
    ).fetchone()[0]
    manifest = {"dataset_name": "dpe", "source_generation": str(generation)}
    conn.execute(
        """INSERT INTO commune_indicator_manifests
           (dataset_name,source_generation,manifest,manifest_sha256,qualified_at)
           VALUES ('dpe',%s,%s,%s,'2026-09-09T12:00:00Z')""",
        (generation, Jsonb(manifest), "a" * 64),
    )
    build_id = conn.execute(
        """INSERT INTO commune_indicator_result_builds(build_sha256,header)
           VALUES (%s,%s) RETURNING id""",
        ("b" * 64, Jsonb({"scope": "synthetic", "version": 1})),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO commune_indicator_result_cells
           (build_id,dataset_name,code_insee,public_payload,private_binding)
           VALUES (%s,'dpe','44001',%s,%s)""",
        (build_id, Jsonb({"resultat": "synthetic"}), Jsonb({"pin": "private"})),
    )
    conn.execute(
        "INSERT INTO commune_indicator_build_activations(event_type,build_id) "
        "VALUES ('activate',%s)",
        (build_id,),
    )


def _indicator_snapshot(conn):
    ordering = {
        "commune_indicator_manifests": "id",
        "commune_indicator_result_builds": "id",
        "commune_indicator_result_cells": "build_id,dataset_name,code_insee",
        "commune_indicator_build_activations": "id",
    }
    rows = {
        table: conn.execute(
            sql.SQL("SELECT row_to_json(t) FROM {} t ORDER BY {}").format(
                sql.Identifier(table), sql.SQL(ordering[table])
            )
        ).fetchall()
        for table in INDICATOR_TABLES
    }
    constraints = conn.execute(
        """SELECT c.conrelid::regclass::text,c.conname,pg_get_constraintdef(c.oid)
           FROM pg_constraint c
           WHERE c.conrelid = ANY(%s::regclass[]) ORDER BY 1,2""",
        (list(INDICATOR_TABLES),),
    ).fetchall()
    triggers = conn.execute(
        """SELECT tgrelid::regclass::text,tgname,pg_get_triggerdef(oid)
           FROM pg_trigger WHERE NOT tgisinternal
           AND tgrelid = ANY(%s::regclass[]) ORDER BY 1,2""",
        (list(INDICATOR_TABLES),),
    ).fetchall()
    sequences = {}
    for table in INDICATOR_TABLES:
        if table == "commune_indicator_result_cells":
            continue
        sequence = conn.execute(
            "SELECT pg_get_serial_sequence(%s,'id')", (table,)
        ).fetchone()[0]
        sequences[table] = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM {}").format(
                sql.Identifier(*sequence.split("."))
            )
        ).fetchone()
    return {
        "rows": rows,
        "constraints": constraints,
        "triggers": triggers,
        "sequences": sequences,
    }


def _assert_indicator_protections(conn):
    snapshot = _indicator_snapshot(conn)
    assert len(snapshot["triggers"]) == 4
    assert all(snapshot["rows"].values())
    rewrite_cases = (
        "UPDATE commune_indicator_manifests SET id=id",
        "DELETE FROM commune_indicator_manifests",
        "TRUNCATE commune_indicator_manifests CASCADE",
        "UPDATE commune_indicator_result_builds SET inserted_at=inserted_at",
        "DELETE FROM commune_indicator_result_builds",
        "TRUNCATE commune_indicator_result_builds CASCADE",
        "UPDATE commune_indicator_result_cells SET public_payload=public_payload",
        "DELETE FROM commune_indicator_result_cells",
        "TRUNCATE commune_indicator_result_cells",
        "UPDATE commune_indicator_build_activations SET recorded_at=recorded_at",
        "DELETE FROM commune_indicator_build_activations",
        "TRUNCATE commune_indicator_build_activations",
    )
    for statement in rewrite_cases:
        with pytest.raises(psycopg.Error) as caught, conn.transaction():
            conn.execute(statement)
        assert caught.value.sqlstate == "55000"
    assert _indicator_snapshot(conn) == snapshot
    return snapshot


def _command(args, audit):
    started = time.monotonic()
    result = subprocess.run(args, capture_output=True, timeout=30, check=False)
    audit.append(
        {
            "argv": args,
            "exit": result.returncode,
            "seconds": round(time.monotonic() - started, 4),
            "stdout_sha256": _sha(result.stdout),
            "stderr_sha256": _sha(result.stderr),
        }
    )
    assert result.returncode == 0, (
        f"Local command failed: {args[0]} exit={result.returncode}"
    )
    return result.stdout


def _owned_container(dsn, audit):
    parts = urlsplit(dsn)
    assert parts.hostname in {"127.0.0.1", "localhost"}
    expected_project = os.environ.get("DC_TEST_PROJECT")
    assert expected_project, "Exact wrapper-owned project must be provided"
    ids = (
        _command(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.service=db",
                "--filter",
                f"label=com.docker.compose.project={expected_project}",
                "--format",
                "{{.ID}}",
            ],
            audit,
        )
        .decode()
        .split()
    )
    assert 0 < len(ids) < 16
    inspected = json.loads(_command(["docker", "inspect", *ids], audit))
    found = []
    for item in inspected:
        project = item["Config"]["Labels"].get("com.docker.compose.project", "")
        ports = item["NetworkSettings"]["Ports"].get("5432/tcp") or []
        if project == expected_project and any(
            p["HostIp"] in {"127.0.0.1", "localhost"}
            and int(p["HostPort"]) == parts.port
            for p in ports
        ):
            found.append(item["Id"])
    assert len(found) == 1, "DSN must identify one newly wrapper-owned container"
    return found[0]


@contextmanager
def _destination(parent_dsn):
    name = "contract_restore_" + uuid4().hex
    parts = urlsplit(parent_dsn)
    target = urlunsplit(parts._replace(path="/" + name))
    with psycopg.connect(parent_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield target
    finally:
        with psycopg.connect(parent_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _snapshot(dsn):
    with psycopg.connect(dsn) as conn:
        journal = conn.execute(
            "SELECT row_to_json(p) FROM etl_publications p ORDER BY id"
        ).fetchall()
        metadata = conn.execute(
            "SELECT row_to_json(m) FROM dataset_metadata m ORDER BY dataset_name"
        ).fetchall()
        source = conn.execute("SELECT * FROM revenus ORDER BY code_insee").fetchall()
        views = conn.execute(
            "SELECT * FROM v_commune_synthese ORDER BY code_insee"
        ).fetchall()
        spatial = conn.execute(
            "SELECT id,ST_SRID(geom),encode(ST_AsEWKB(geom),'hex') "
            "FROM restore_spatial_probe ORDER BY id"
        ).fetchall()
        oids = conn.execute(
            "SELECT to_regclass('revenus')::oid,to_regclass('v_commune_synthese')::oid"
        ).fetchone()
        runs = conn.execute("SELECT status FROM etl_runs ORDER BY id").fetchall()
        validations = conn.execute(
            "SELECT passed,row_count_after FROM validation_runs ORDER BY id"
        ).fetchall()
        sources = {}
        views_by_name = {}
        for table in sorted(COMMUNE_SOURCES.values()):
            sources[table] = conn.execute(
                sql.SQL("SELECT * FROM {} ORDER BY code_insee").format(
                    sql.Identifier(table)
                )
            ).fetchall()
        for view in sorted({edge[0] for edge in COMMUNE_EDGES}):
            views_by_name[view] = {
                "rows": conn.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY code_insee").format(
                        sql.Identifier(view)
                    )
                ).fetchall(),
                "definition": conn.execute(
                    "SELECT pg_get_viewdef(to_regclass(%s))", (view,)
                ).fetchone()[0],
            }
        indicators = _assert_indicator_protections(conn)
    content = {
        "sources": sources,
        "views_by_name": views_by_name,
        "source": source,
        "views": views,
        "spatial": spatial,
        "journal": journal,
        "metadata": metadata,
        "runs": runs,
        "validations": validations,
        "indicators": indicators,
    }
    return {
        **content,
        "oids": oids,
        "logical_sha256": _sha(
            json.dumps(content, sort_keys=True, default=str).encode()
        ),
    }


class RestorePipeline(BasePipeline):
    dataset_name = "revenus"
    production_table_name = "revenus"
    staging_table_name = "revenus_staging"
    expected_columns: ClassVar[list[str]] = ["code_insee", "version"]
    has_department_partitions = False
    staging_is_partitioned = False
    min_staging_rows = 1
    skip_runner_schema_drift_check = True

    def __init__(self, dsn, frozen, counters):
        self.dsn, self.frozen, self.counters = dsn, frozen, counters

    def get_sync_dsn(self):
        return self.dsn

    def download(self):
        self.counters["download"] += 1
        target = get_download_dir(self.dataset_name) / self.frozen.name
        shutil.copyfile(self.frozen, target)
        return [target]

    def validate_schema(self, path):
        with path.open(encoding="utf-8", newline="") as stream:
            assert next(csv.reader(stream)) == self.expected_columns

    def transform(self, path):
        return path

    def create_staging_table(self, dsn):
        self.counters["create_staging"] += 1
        with psycopg.connect(dsn) as conn:
            conn.execute("CREATE TABLE revenus_staging (LIKE revenus INCLUDING ALL)")

    def stage(self, path):
        self.counters["stage"] += 1
        with (
            psycopg.connect(self.dsn) as conn,
            path.open(encoding="utf-8") as stream,
            conn.cursor().copy(
                "COPY revenus_staging FROM STDIN WITH (FORMAT CSV, HEADER TRUE)"
            ) as copy,
        ):
            copy.write(stream.read())
        return 1

    def validate_data(self, dsn):
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "SELECT code_insee,version FROM revenus_staging"
            ).fetchall() == [("44999", "frozen-synthetic")]
        return []

    def swap(self):
        self.counters["swap"] += 1
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DROP TABLE IF EXISTS revenus_old CASCADE")
            conn.execute("ALTER TABLE revenus RENAME TO revenus_old")
            conn.execute("ALTER TABLE revenus_staging RENAME TO revenus")


async def test_dump_restore_rejects_old_oids_then_revalidates_via_real_runner(  # noqa: C901
    isolated_pg_dsn,
    client,
    monkeypatch,
    tmp_path,
):
    audit = []
    container = _owned_container(isolated_pg_dsn, audit)
    user = urlsplit(isolated_pg_dsn).username
    evidence_root = Path(os.environ.get("RESTORE_EVIDENCE_DIR", str(tmp_path)))
    evidence = evidence_root / ("restore_" + uuid4().hex)
    evidence.mkdir(parents=True)
    assert Path(__file__).resolve().parents[4] not in evidence.resolve().parents
    frozen = evidence / "source.csv"
    frozen.write_text("code_insee,version\n44999,frozen-synthetic\n", encoding="utf-8")
    original_source_hash = _sha(frozen.read_bytes())
    counters = dict.fromkeys(
        ("download", "create_staging", "stage", "swap", "validation", "rebuild"), 0
    )
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    monkeypatch.setattr(settings, "etl_min_disk_gb", 1)
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    monkeypatch.setitem(
        PROFILES,
        "revenus",
        DatasetProfile(
            dataset="revenus",
            table="revenus",
            nulls=NullSpec(required_cols=("code_insee", "version"), expected_cols=()),
        ),
    )
    monkeypatch.setitem(registry._pipelines, "revenus", RestorePipeline)
    # Adapt the physical mount probe to the actual disposable container.
    free_bytes = int(
        _command(
            [
                "docker",
                "exec",
                container,
                "df",
                "-B1",
                "--output=avail",
                "/var/lib/postgresql/data",
            ],
            audit,
        )
        .decode()
        .splitlines()[-1]
    )
    monkeypatch.setattr(
        PipelineRunner,
        "_gate03_free_bytes",
        staticmethod(
            lambda *_: (free_bytes, "wrapper-container:/var/lib/postgresql/data")
        ),
    )
    with psycopg.connect(isolated_pg_dsn) as conn:
        create_fixture(conn)
        _install_indicator_storage(conn)
        conn.execute("CREATE EXTENSION postgis")
        conn.execute(
            "CREATE TABLE restore_spatial_probe(id int PRIMARY KEY,"
            "geom geometry(Point,4326))"
        )
        conn.execute(
            "INSERT INTO restore_spatial_probe VALUES "
            "(1,ST_SetSRID(ST_Point(-1,47),4326))"
        )
        conn.execute(
            "CREATE TABLE etl_runs(id bigserial PRIMARY KEY,dataset_name text,"
            "status text,completed_at timestamptz,total_rows_loaded bigint,"
            "total_duration_seconds float,error_message text,stages jsonb,"
            # Columns added by migration 097 (instance scope journal).
            "scope_snapshot jsonb,source_files jsonb)"
        )

    class TracedValidation(ValidationHook):
        def on_run_complete(self, name, result):
            counters["validation"] += 1
            super().on_run_complete(name, result)

    class Rebuild(DependentMatviewHook):
        fail_next = False

        def on_run_complete(self, name, result):
            counters["rebuild"] += 1
            if self.fail_next:
                self.fail_next = False
                raise psycopg.OperationalError("synthetic rebuild interruption")
            with psycopg.connect(settings.database_url) as conn:
                create_chain(conn)

    hooks = [TracedValidation(), Rebuild()]
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    result = await asyncio.to_thread(
        PipelineRunner(hooks).run, RestorePipeline(isolated_pg_dsn, frozen, counters)
    )
    assert result.success
    with psycopg.connect(isolated_pg_dsn) as conn:
        _seed_indicator_storage(conn)
    before = _snapshot(isolated_pg_dsn)
    assert before["journal"][0][0]["status"] == "published"

    async def reader_state(dsn):
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://")
        )

        async def db():
            async with AsyncSession(engine) as session:
                yield session

        app.dependency_overrides[get_db] = db
        try:
            response = await client.get("/v1/commune", params={"code_insee": "44999"})
            async with AsyncSession(engine) as session:
                try:
                    await guard_publication(session, ["revenus"])
                    raw = 200
                except HTTPException as exc:
                    raw = exc.status_code
            return {"commune": response.status_code, "raw_guard": raw}
        finally:
            app.dependency_overrides.pop(get_db, None)
            await engine.dispose()

    assert await reader_state(isolated_pg_dsn) == {"commune": 200, "raw_guard": 200}
    dump_version = (
        _command(["docker", "exec", container, "pg_dump", "--version"], audit)
        .decode()
        .strip()
    )
    restore_version = (
        _command(["docker", "exec", container, "pg_restore", "--version"], audit)
        .decode()
        .strip()
    )
    archive = "/tmp/restore_" + uuid4().hex + ".dump"
    _command(
        [
            "docker",
            "exec",
            container,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "-U",
            user,
            "--file",
            archive,
            "--dbname",
            urlsplit(isolated_pg_dsn).path[1:],
        ],
        audit,
    )
    remote_hash_before = (
        _command(["docker", "exec", container, "sha256sum", archive], audit)
        .decode()
        .split()[0]
    )
    archive_local = evidence / "publication094.dump"
    _command(["docker", "cp", container + ":" + archive, str(archive_local)], audit)
    assert _sha(archive_local.read_bytes()) == remote_hash_before

    with ExitStack() as stack:
        allocations = 0
        restored = None
        for attempt in range(2):
            target = stack.enter_context(_destination(isolated_pg_dsn))
            if attempt:
                allocations = 32
                with psycopg.connect(target) as conn:
                    for index in range(allocations):
                        conn.execute(
                            sql.SQL("CREATE TABLE {} (id int)").format(
                                sql.Identifier(f"restore_oid_padding_{index}")
                            )
                        )
                        conn.execute(
                            sql.SQL("DROP TABLE {}").format(
                                sql.Identifier(f"restore_oid_padding_{index}")
                            )
                        )
            _command(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "-U",
                    user,
                    "--dbname",
                    urlsplit(target).path[1:],
                    archive,
                ],
                audit,
            )
            restored = _snapshot(target)
            assert restored["logical_sha256"] == before["logical_sha256"]
            if restored["oids"][0] != before["oids"][0]:
                break
        assert restored["oids"][0] != before["oids"][0], (
            "OID drift must be observed, never assumed"
        )
        assert restored["journal"] == before["journal"]
        with psycopg.connect(target) as conn:
            with conn.transaction(force_rollback=True):
                conn.execute(
                    "DROP TRIGGER immutable_results ON commune_indicator_result_cells"
                )
                with pytest.raises(AssertionError):
                    _assert_indicator_protections(conn)
            assert _assert_indicator_protections(conn) == before["indicators"]
        states = {"restored": await reader_state(target)}
        assert states["restored"] == {"commune": 503, "raw_guard": 503}
        monkeypatch.setattr(settings, "database_url", target)
        monkeypatch.setitem(
            jobs.PIPELINE_MAP,
            "revenus",
            lambda: RestorePipeline(target, frozen, counters),
        )
        monkeypatch.setattr(jobs, "build_default_hooks", lambda: hooks)
        hooks[1].fail_next = True
        retry_events = []
        loop = asyncio.get_running_loop()

        def before_retry(retry):
            assert isinstance(retry.outcome.exception(), psycopg.OperationalError)
            failed = _snapshot(target)
            assert failed["journal"][0] == before["journal"][0]
            assert failed["journal"][-1][0]["status"] == "failed"
            assert failed["metadata"] == before["metadata"]
            assert CheckpointManager("revenus", "revenus_staging").exists()
            state = asyncio.run_coroutine_threadsafe(reader_state(target), loop).result(
                3
            )
            assert state == {"commune": 503, "raw_guard": 503}
            states["failed_rebuild"] = state
            retry_events.append(
                {
                    "attempt": retry.attempt_number,
                    "exception": type(retry.outcome.exception()).__name__,
                }
            )

        await asyncio.to_thread(
            jobs.run_scheduled_pipeline_with_retry.retry_with(
                wait=wait_none(),
                stop=stop_after_attempt(2),
                before_sleep=before_retry,
            ),
            "revenus",
        )
        after = _snapshot(target)
        assert [row[0]["status"] for row in after["journal"]] == [
            "published",
            "failed",
            "published",
        ]
        assert after["journal"][0] == before["journal"][0]
        assert after["journal"][-1][0]["relation_oids"]["revenus"] == after["oids"][0]
        assert after["source"] == before["source"] and after["views"] == before["views"]
        assert after["spatial"] == before["spatial"]
        assert after["sources"] == before["sources"]
        assert after["views_by_name"] == before["views_by_name"]
        assert after["validations"] == [(True, 1)] * 3
        assert after["runs"] == [("success",), ("failed",), ("success",)]
        assert all(count == 3 for count in counters.values())
        assert not CheckpointManager("revenus", "revenus_staging").exists()
        states["recovered"] = await reader_state(target)
        assert states["recovered"] == {"commune": 200, "raw_guard": 200}
        remote_hash_after = (
            _command(["docker", "exec", container, "sha256sum", archive], audit)
            .decode()
            .split()[0]
        )
        assert (
            remote_hash_after == remote_hash_before == _sha(archive_local.read_bytes())
        )
        assert _sha(frozen.read_bytes()) == original_source_hash
        with psycopg.connect(target) as conn:
            prior_manifest_id = before["indicators"]["rows"][
                "commune_indicator_manifests"
            ][0][0]["id"]
            generation = conn.execute(
                "SELECT generation FROM etl_publications "
                "WHERE status='published' ORDER BY id LIMIT 1"
            ).fetchone()[0]
            new_manifest_id = conn.execute(
                """INSERT INTO commune_indicator_manifests
                   (dataset_name,source_generation,manifest,manifest_sha256,qualified_at)
                   VALUES ('dpe',%s,%s,%s,'2026-09-09T13:00:00Z') RETURNING id""",
                (
                    generation,
                    Jsonb(
                        {
                            "dataset_name": "dpe",
                            "source_generation": str(generation),
                        }
                    ),
                    "d" * 64,
                ),
            ).fetchone()[0]
            assert new_manifest_id > prior_manifest_id
            prior_id = before["indicators"]["rows"]["commune_indicator_result_builds"][
                0
            ][0]["id"]
            new_id = conn.execute(
                """INSERT INTO commune_indicator_result_builds(build_sha256,header)
                   VALUES (%s,%s) RETURNING id""",
                ("c" * 64, Jsonb({"scope": "synthetic", "version": 2})),
            ).fetchone()[0]
            assert new_id > prior_id
            prior_event_id = before["indicators"]["rows"][
                "commune_indicator_build_activations"
            ][0][0]["id"]
            new_event_id = conn.execute(
                "INSERT INTO commune_indicator_build_activations"
                "(event_type,build_id) VALUES ('activate',%s) RETURNING id",
                (new_id,),
            ).fetchone()[0]
            assert new_event_id > prior_event_id
        manifest = {
            "schema": "096_commune_indicator_build_activation",
            "fixture": "synthetic_only",
            "pg_dump": dump_version,
            "pg_restore": restore_version,
            "dump_sha256_before": remote_hash_before,
            "dump_sha256_after": remote_hash_after,
            "dump_bytes": archive_local.stat().st_size,
            "source_sha256": original_source_hash,
            "before": before,
            "restored": restored,
            "after": after,
            "reader_states": states,
            "retry_events": retry_events,
            "calls": counters,
            "oid_padding_allocations": allocations,
            "adapters": [
                "physical disk probe redirected to real container df",
                "fixture-specific rebuild of 13 branches",
                "synthetic source CSV replaces network download",
                "quota/logging test fixtures; publication guards stay active",
                "retry backoff=0, classifier and runner real",
            ],
            "commands": audit,
        }
        manifest_path = evidence / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print("RESTORE_MANIFEST=" + str(manifest_path))

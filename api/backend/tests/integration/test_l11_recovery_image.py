"""Recover actual migrated schema and frozen LOVAC through a local built image."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.config import settings
from app.etl.base import DEPARTEMENTS
from app.etl.download import get_download_dir
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_vacance.pipeline import VacancePipeline
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.test_publication_restore import _destination, _owned_container
from tests.oracles.lovac_full import FIELDS, expected_rows, frozen_bytes

pytestmark = pytest.mark.destructive
ROOT = Path(__file__).resolve().parents[4]
PROBE_DIR = ROOT / "docs/audit-stabilisation-2026-09-08/lot-11/recovery"


def evidence_directory(default_directory):
    """Create fresh external output, never overwrite checked-in historical proof."""
    value = os.environ.get("L11_RECOVERY_EVIDENCE_DIR")
    candidate = Path(value) if value else default_directory / "recovery-evidence"
    if not candidate.is_absolute():
        raise ValueError("Recovery evidence directory must be absolute")
    candidate = candidate.resolve()
    if candidate == ROOT or ROOT in candidate.parents or candidate in ROOT.parents:
        raise ValueError("Recovery evidence must remain outside the checkout")
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def assert_snapshot_equal(before, after, phase, out):
    if before["logical_sha256"] != after["logical_sha256"]:
        differences = {
            kind: {
                key: {"before": before[kind].get(key), "after": after[kind].get(key)}
                for key in before[kind].keys() | after[kind].keys()
                if before[kind].get(key) != after[kind].get(key)
            }
            for kind in ("tables", "objects")
        }
        (out / (phase + "-difference.json")).write_text(
            json.dumps(differences, indent=2, default=str) + "\n", "utf8"
        )
    assert before["logical_sha256"] == after["logical_sha256"]


def canonical_definition(conn, definition):
    """Normalize only PostgreSQL's proven varchar-array/text-array roundtrip."""
    pattern = (
        r"\(ARRAY\[((?:'[^']*'::character varying)"
        r"(?:, '[^']*'::character varying)*)\]\)::text\[\]"
    )

    def replace(match):
        values = match[1].split(", ")
        normalized = "ARRAY[" + ", ".join("(" + v + ")::text" for v in values) + "]"
        assert conn.execute(
            "SELECT (" + match[0] + ") IS NOT DISTINCT FROM (" + normalized + ")"
        ).fetchone()[0]
        return normalized

    return re.sub(pattern, replace, definition)


def snapshot(dsn):
    with psycopg.connect(dsn) as conn:
        conn.set_read_only(True)
        conn.execute("SET statement_timeout='10s'")
        names = conn.execute(
            "SELECT relname,relkind,relispopulated FROM pg_class "
            "WHERE relnamespace='public'::regnamespace "
            "AND relkind IN ('r','p','m') ORDER BY relname"
        ).fetchall()
        tables = {}
        for name, kind, populated in names:
            if kind == "m" and not populated:
                tables[name] = {"populated": False}
                continue
            count, signature = conn.execute(
                sql.SQL(
                    "SELECT count(*),md5(COALESCE(string_agg(row_to_json(t)::text,"
                    "E'\\n' ORDER BY row_to_json(t)::text),'')) FROM {} t"
                ).format(sql.Identifier(name))
            ).fetchone()
            tables[name] = {"rows": count, "logical_md5": signature}
        objects = {}
        for key, query in {
            "columns": "SELECT table_name,column_name,ordinal_position,data_type,"
            "udt_name,"
            "is_nullable,column_default FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY 1,3",
            "constraints": "SELECT conrelid::regclass::text,conname,"
            "pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2",
            "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY 1,2",
            "views": "SELECT matviewname,definition FROM pg_matviews "
            "WHERE schemaname='public' ORDER BY 1",
            "sequences": "SELECT sequencename,start_value,min_value,max_value,"
            "increment_by,cycle,last_value "
            "FROM pg_sequences WHERE schemaname='public' ORDER BY 1",
        }.items():
            objects[key] = conn.execute(query).fetchall()
            if key in {"constraints", "views"}:
                objects[key] = [
                    (*row[:-1], canonical_definition(conn, row[-1]))
                    for row in objects[key]
                ]
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        exists = conn.execute(
            "SELECT to_regclass('etl_publications') IS NOT NULL"
        ).fetchone()[0]
        journal = (
            conn.execute(
                "SELECT row_to_json(p) FROM etl_publications p ORDER BY id"
            ).fetchall()
            if exists
            else []
        )
        oids = conn.execute(
            "SELECT to_regclass('vacance')::oid,to_regclass('v_commune_synthese')::oid"
        ).fetchone()
    payload = {"tables": tables, "objects": objects, "schema": version}
    return {
        **payload,
        "logical_sha256": digest(payload),
        "journal": journal,
        "oids": oids,
    }


def call(args, audit, env=None):
    result = subprocess.run(args, capture_output=True, timeout=180, env=env)
    audit.append(
        {
            "argv": args,
            "exit": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        }
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]
    return result.stdout


def backup(container, dsn, suffix, audit):
    archive = "/tmp/l11_" + suffix + ".dump"
    call(
        [
            "docker",
            "exec",
            container,
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "-U",
            "contract_test",
            "-d",
            urlsplit(dsn).path[1:],
            "--file",
            archive,
        ],
        audit,
    )
    fingerprint = (
        call(["docker", "exec", container, "sha256sum", archive], audit)
        .decode()
        .split()[0]
    )
    return archive, fingerprint


def restore(container, dsn, archive, audit):
    call(
        [
            "docker",
            "exec",
            container,
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "-U",
            "contract_test",
            "-d",
            urlsplit(dsn).path[1:],
            archive,
        ],
        audit,
    )


@contextmanager
def image_reader(image_id, container, dsn, audit):  # noqa: C901
    inspected = json.loads(call(["docker", "inspect", container], audit))[0]
    network = next(iter(inspected["NetworkSettings"]["Networks"]))
    internal_host = inspected["Name"].lstrip("/")
    name = "dc-l11-reader-" + uuid4().hex[:10]
    # Pass the local fixture DSN by inherited environment, never into audit argv.
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://contract_test:contract_test@"
        + internal_host
        + ":5432"
        + urlsplit(dsn).path,
    }
    with TemporaryDirectory(prefix="dc-l11-reader-") as directory:
        cid_path = Path(directory) / "reader.cid"

        def attested_id():
            if not cid_path.exists():
                return None
            value = cid_path.read_text(encoding="ascii").strip()
            return value if re.fullmatch(r"[0-9a-f]{64}", value) else None

        failure = None
        reader_id = None
        try:
            call(
                [
                    "docker",
                    "run",
                    "-d",
                    "--cidfile",
                    str(cid_path),
                    "--name",
                    name,
                    "--network",
                    network,
                    "-p",
                    "127.0.0.1::8000",
                    "-e",
                    "DATABASE_URL",
                    "-e",
                    "DEBUG=true",
                    "--mount",
                    f"type=bind,source={PROBE_DIR},target=/probe,readonly",
                    image_id,
                    "uvicorn",
                    "--app-dir",
                    "/probe",
                    "probe:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                    "--no-access-log",
                ],
                audit,
                env,
            )
            reader_id = attested_id()
            assert reader_id is not None, (
                "Reader CID missing or invalid; cleanup is unverified"
            )
            port = call(["docker", "port", reader_id, "8000"], audit).decode().strip()
            assert port.startswith("127.0.0.1:")
            base = "http://" + port
            for _ in range(50):
                try:
                    if httpx.get(base + "/probe/live", timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            else:
                logs = subprocess.run(
                    ["docker", "logs", reader_id], capture_output=True, timeout=10
                )
                raise AssertionError((logs.stdout + logs.stderr).decode()[-2000:])
            active = (
                call(["docker", "inspect", "--format", "{{.Image}}", reader_id], audit)
                .decode()
                .strip()
            )
            assert active == image_id
            yield base
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                reader_id = reader_id or attested_id()
                if reader_id is not None:
                    call(["docker", "rm", "-f", reader_id], audit)
                elif failure is not None:
                    failure.add_note(
                        "No trustworthy CID was created; no container cleanup attempted"
                    )
            except BaseException as cleanup_error:
                if failure is not None:
                    raise BaseExceptionGroup(
                        "Reader operation and owned cleanup both failed",
                        [failure, cleanup_error],
                    ) from None
                raise


def check_http(base, expected, witnesses, status):
    evidence = {}
    for kind, code in witnesses.items():
        response = httpx.get(
            base + "/v1/vacance", params={"code_insee": code}, timeout=5
        )
        assert response.status_code == status, response.text
        if status == 200:
            row = response.json()["donnees"][0]
            for field, value in zip(FIELDS, expected[code], strict=True):
                assert row[field] == (
                    float(value)
                    if value is not None and field.startswith("taux_")
                    else value
                )
        else:
            assert "donnees" not in response.json()
        evidence[kind] = {"status": status, "body": response.json()}
    response = httpx.get(
        base + "/v1/commune", params={"code_insee": "44109"}, timeout=5
    )
    assert response.status_code == status, response.text
    evidence["commune"] = {"status": status, "body": response.json()}
    return evidence


async def test_full_schema_restore_and_image_recovery(
    isolated_pg_dsn, monkeypatch, tmp_path
):
    audit, phases = [], {}
    out = evidence_directory(tmp_path)
    container = _owned_container(isolated_pg_dsn, audit)
    image_id = (
        call(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                os.environ["L11_RECOVERY_IMAGE"],
            ],
            audit,
        )
        .decode()
        .strip()
    )
    contract = (
        call(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                '{{ index .Config.Labels "org.datacommune.publication-contract" }}',
                image_id,
            ],
            audit,
        )
        .decode()
        .strip()
    )
    assert contract == "1"
    image_sources = json.loads(
        call(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                image_id,
                "python",
                "-c",
                "import hashlib,json; from pathlib import Path; "
                "paths=[*Path('app').rglob('*.py'),*Path('migrations').rglob('*.py'),"
                "Path('pyproject.toml'),Path('uv.lock')]; "
                "print(json.dumps({p.as_posix():"
                "hashlib.sha256(p.read_bytes()).hexdigest() "
                "for p in paths},sort_keys=True))",
            ],
            audit,
        )
    )
    for path, expected_hash in image_sources.items():
        assert (
            hashlib.sha256((ROOT / "api/backend" / path).read_bytes()).hexdigest()
            == expected_hash
        )
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "api/backend/migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    command.upgrade(cfg, "091_dpe_source_lifecycle")
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute(
            "CREATE TABLE l11_spatial_probe(id bigserial PRIMARY KEY,"
            "geom geometry(Point,4326))"
        )
        conn.execute(
            "INSERT INTO l11_spatial_probe(geom) "
            "VALUES(ST_SetSRID(ST_Point(-1,47),4326))"
        )
    before091 = snapshot(isolated_pg_dsn)
    archive091, hash091 = backup(container, isolated_pg_dsn, "091", audit)
    command.upgrade(cfg, "head")
    with _destination(isolated_pg_dsn) as recovery091:
        restore(container, recovery091, archive091, audit)
        restored091 = snapshot(recovery091)
        assert_snapshot_equal(before091, restored091, "091", out)
        phases["restore091"] = {
            "schema": restored091["schema"],
            "logical_sha256": restored091["logical_sha256"],
            "tables": len(restored091["tables"]),
            "objects": {k: len(v) for k, v in restored091["objects"].items()},
        }

    raw, source_manifest = frozen_bytes()
    expected = expected_rows(raw)
    witnesses = {
        "nantes": "44109",
        "secret": next(k for k, v in expected.items() if v[2] is None),
        "zero": next(k for k, v in expected.items() if v[6] == 0),
    }
    with psycopg.connect(isolated_pg_dsn) as conn:
        for code in witnesses.values():
            conn.execute(
                "INSERT INTO demographie(code_insee,annee) VALUES(%s,2026)", (code,)
            )
        # The image reads the scope before /v1/vacance and /v1/commune
        # (DATA-03); restores carry these rows like any other table.
        install_instance_scope(conn, COMMUNE_SOURCES, DEPARTEMENTS)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    free = int(
        call(
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
        staticmethod(lambda *_: (free, "owned-container")),
    )

    async def download_transport(_self):
        target = get_download_dir("vacance") / "raw/lovac_communes.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return [target]

    monkeypatch.setattr(VacancePipeline, "_async_download", download_transport)

    class Rebuild(DependentMatviewHook):
        fail_next = False

        def on_run_complete(self, name, result):
            if self.fail_next:
                self.fail_next = False
                raise psycopg.OperationalError("L11 injected required rebuild failure")
            super().on_run_complete(name, result)

    rebuild = Rebuild()
    runner = PipelineRunner([ValidationHook(), rebuild])
    first = await asyncio.to_thread(runner.run, VacancePipeline())
    assert first.success, first.error
    with image_reader(image_id, container, isolated_pg_dsn, audit) as base:
        phases["published094"] = check_http(base, expected, witnesses, 200)
    before094 = snapshot(isolated_pg_dsn)
    assert before094["tables"]["vacance"]["rows"] == 34912
    archive094, hash094 = backup(container, isolated_pg_dsn, "094", audit)
    with _destination(isolated_pg_dsn) as recovered:
        restore(container, recovered, archive094, audit)
        restored094 = snapshot(recovered)
        assert_snapshot_equal(before094, restored094, "094", out)
        assert restored094["journal"] == before094["journal"]
        assert restored094["oids"][0] != before094["oids"][0]
        monkeypatch.setenv("DATABASE_URL", recovered)
        monkeypatch.setattr(settings, "database_url", recovered)
        with image_reader(image_id, container, recovered, audit) as base:
            phases["restored_stale_oids"] = check_http(base, expected, witnesses, 503)
            db_details = json.loads(call(["docker", "inspect", container], audit))[0]
            network = next(iter(db_details["NetworkSettings"]["Networks"]))
            writer_env = {
                **os.environ,
                "DATABASE_URL": "postgresql://contract_test:contract_test@"
                + db_details["Name"].lstrip("/")
                + ":5432"
                + urlsplit(recovered).path,
            }
            crashed = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "-e",
                    "DATABASE_URL",
                    "-e",
                    "DEBUG=true",
                    image_id,
                    "python",
                    "-c",
                    "import os; from app.etl.publication import Publication; "
                    "from app.etl_vacance.pipeline import VacancePipeline; "
                    "p=Publication(os.environ['DATABASE_URL'],VacancePipeline()); "
                    "p.__enter__(); p.observe_stock(); os._exit(17)",
                ],
                env=writer_env,
                capture_output=True,
                timeout=10,
            )
            assert crashed.returncode == 17
            audit.append(
                {
                    "operation": "image publication process crash",
                    "image_id": image_id,
                    "exit": crashed.returncode,
                }
            )
            after_crash = snapshot(recovered)
            assert after_crash["journal"][-1][0]["status"] == "pending"
            phases["process_crash_pending"] = check_http(base, expected, witnesses, 503)
            rebuild.fail_next = True
            failed = await asyncio.to_thread(runner.run, VacancePipeline())
            assert not failed.success
            after_failed = snapshot(recovered)
            assert after_failed["journal"][-1][0]["status"] == "failed"
            assert (
                after_failed["tables"]["dataset_metadata"]
                == before094["tables"]["dataset_metadata"]
            )
            phases["failed_required_rebuild"] = check_http(
                base, expected, witnesses, 503
            )
            success = await asyncio.to_thread(runner.run, VacancePipeline())
            assert success.success, success.error
            phases["recovered"] = check_http(base, expected, witnesses, 200)
        after = snapshot(recovered)
        assert after["journal"][: len(before094["journal"])] == before094["journal"]
        assert after["journal"][1] == after_crash["journal"][1]
        assert [row[0]["status"] for row in after["journal"]] == [
            "published",
            "pending",
            "failed",
            "published",
        ]
        assert after["tables"]["vacance"] == before094["tables"]["vacance"]
        with psycopg.connect(recovered) as conn:
            rows = conn.execute(
                "SELECT " + ",".join(FIELDS) + " FROM vacance ORDER BY code_commune"
            ).fetchall()
            assert {row[0]: row for row in rows} == expected
            assert (
                conn.execute(
                    "SELECT ST_AsText(geom) FROM l11_spatial_probe"
                ).fetchone()[0]
                == "POINT(-1 47)"
            )
            assert (
                conn.execute("SELECT nextval('l11_spatial_probe_id_seq')").fetchone()[0]
                == 2
            )
    report = {
        "image_id": image_id,
        "publication_contract": contract,
        "image_source_files": len(image_sources),
        "image_source_manifest_sha256": digest(image_sources),
        "source": source_manifest,
        "schema091": phases.pop("restore091"),
        "archive091_sha256": hash091,
        "archive094_sha256": hash094,
        "full_restore094_sha256": restored094["logical_sha256"],
        "tables094": len(restored094["tables"]),
        "objects094": {k: len(v) for k, v in restored094["objects"].items()},
        "oids_before": before094["oids"],
        "oids_restored": restored094["oids"],
        "oids_recovered": after["oids"],
        "journal_statuses": [row[0]["status"] for row in after["journal"]],
        "phases": phases,
        "commands": audit,
        "limits": [
            "local built production Dockerfile image; not deployed",
            "HTTP probe includes real routers/database/guards; "
            "auth/quota/main lifespan omitted",
            "full migration DDL; LOVAC frozen full source only; other datasets "
            "empty except synthetic demographics/spatial anchors",
            "pg_dump no-owner/no-privileges; roles/ACL/tablespaces/external "
            "settings not certified",
            "production backup/image/restore/RTO/RPO not certified",
        ],
    }
    (out / "verification.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", "utf8"
    )

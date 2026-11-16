"""Full official inputs through the real CLI, runner and hooks; disposable DB only."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from tests.integration.test_publication_restore import _command, _owned_container
from tests.oracles.lovac_full import FIELDS as LOVAC_FIELDS
from tests.oracles.lovac_full import expected_rows, frozen_bytes
from tests.oracles.loyers_full import FIELDS as RENT_FIELDS
from tests.oracles.loyers_full import expected, frozen_files

pytestmark = pytest.mark.destructive
BACKEND = Path(__file__).parents[2]

# Same process entry point as python -m run_etl; only HTTP denial and the physical
# Windows-host/Linux-PG disk observation are installed. No pipeline/hook substitution.
CLI_BOOTSTRAP = """
import os, runpy, sys
import httpx
from app.etl.runner import PipelineRunner
calls = []
def deny_http(*args, **kwargs):
    calls.append(True)
    raise AssertionError('offline CLI attempted HTTP')
httpx.AsyncClient.send = deny_http
httpx.Client.send = deny_http
if sys.platform == 'win32':
    PipelineRunner._gate03_free_bytes = staticmethod(
        lambda *_: (int(os.environ['FROZEN_TEST_PG_FREE']), 'owned-PG-filesystem')
    )
sys.argv = ['run_etl', *sys.argv[1:]]
runpy.run_module('run_etl', run_name='__main__')
assert not calls
"""


def source_manifest(folder, dataset):
    durable = folder / "durable"
    durable.mkdir()
    if dataset == "vacance":
        raw, _ = frozen_bytes()
        (durable / "lovac_communes.csv").write_bytes(raw)
        selected = [("communes", durable / "lovac_communes.csv")]
        oracle = list(expected_rows(raw).values())
        fields = LOVAC_FIELDS
        contract = "lovac-communes-v1:2026:2025"
        vintage = 2026
    else:
        frozen_files(durable)
        _, oracle, _ = expected(durable)
        selected = [
            (kind, durable / f"{kind}.csv")
            for kind in ("appartement", "maison", "1-2_pieces", "3+_pieces")
        ]
        fields = RENT_FIELDS
        contract = "loyers-prediction-metadata-v1:2025"
        vintage = 2025
    entries = [
        {
            "kind": kind,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for kind, path in selected
    ]
    payload = {
        "version": 1,
        "dataset": dataset,
        "vintage": vintage,
        "source_contract": contract,
        "files": entries,
    }
    path = durable / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload, oracle, fields


@pytest.mark.parametrize(
    "dataset,table,rows",
    [("vacance", "vacance", 34912), ("loyers", "indicateurs_loyers", 139600)],
)
def test_full_frozen_cli_without_network(
    isolated_pg_dsn, tmp_path, monkeypatch, full_instance_scope, dataset, table, rows
):
    manifest, payload, oracle, fields = source_manifest(tmp_path, dataset)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    command.upgrade(cfg, "head")
    # run_etl.py reads the persisted scope in its own process (D-21).
    full_instance_scope(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        # Public source keys, synthetic view spine; no demographic values invented.
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT code_insee FROM demographie WHERE annee=2025"
            ).fetchall()
        }
        with conn.cursor().copy(
            "COPY demographie(code_insee,annee) FROM STDIN"
        ) as copy:
            for code in sorted({row[0] for row in oracle} - existing):
                copy.write_row((code, 2025))
        old_oid = conn.execute("SELECT %s::regclass::oid", (table,)).fetchone()[0]
    audit = []
    container = _owned_container(isolated_pg_dsn, audit)
    free = int(
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
    temp = tmp_path / "etl"
    temp.mkdir()
    env = os.environ | {
        "DATABASE_URL": isolated_pg_dsn.replace(
            "postgresql://", "postgresql+asyncpg://"
        ),
        "ETL_TEMP_DIR": str(temp),
        "DEBUG": "true",
        "ALERT_URLS": "[]",
        "FROZEN_TEST_PG_FREE": str(free),
        "PYTHONPATH": str(BACKEND),
    }
    # A real incompatible checkpoint must not skip the qualified complete files.
    checkpoint = temp / f"datacommune_{dataset}" / "checkpoint.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text(
        json.dumps(
            {
                "dataset_name": dataset,
                "staging_table": table + "_staging",
                "loaded_files": [Path(item["path"]).name for item in payload["files"]],
                "scope_key": f"frozen-source-v1:{dataset}:" + "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    checkpoint_bytes = checkpoint.read_bytes()
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute(f"CREATE TABLE {table}_staging(stale_canary integer)")
        conn.execute(f"INSERT INTO {table}_staging VALUES (917)")
        stale_oid = conn.execute(
            "SELECT %s::regclass::oid", (table + "_staging",)
        ).fetchone()[0]
        run_count = conn.execute("SELECT count(*) FROM etl_runs").fetchone()[0]
    invalid = manifest.with_name("invalid-manifest.json")
    altered = json.loads(manifest.read_text(encoding="utf-8"))
    altered["files"][0]["sha256"] = "0" * 64
    invalid.write_text(json.dumps(altered), encoding="utf-8")
    refusal = subprocess.run(
        [
            sys.executable,
            "-c",
            CLI_BOOTSTRAP,
            dataset,
            "--source-manifest",
            str(invalid),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert refusal.returncode != 0
    assert b"Frozen source checksum mismatch" in refusal.stderr
    assert checkpoint.read_bytes() == checkpoint_bytes
    with psycopg.connect(isolated_pg_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM etl_runs").fetchone()[0] == run_count
        assert (
            conn.execute("SELECT %s::regclass::oid", (table,)).fetchone()[0] == old_oid
        )
        assert (
            conn.execute("SELECT %s::regclass::oid", (table + "_staging",)).fetchone()[
                0
            ]
            == stale_oid
        )
        assert conn.execute(f"SELECT stale_canary FROM {table}_staging").fetchall() == [
            (917,)
        ]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            CLI_BOOTSTRAP,
            dataset,
            "--source-manifest",
            str(manifest),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        timeout=180,
    )
    (tmp_path / "cli.stdout").write_bytes(completed.stdout)
    (tmp_path / "cli.stderr").write_bytes(completed.stderr)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-6000:]
    assert b"legacy or incompatible; rebuilding staging" in completed.stderr
    assert all(
        hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
        for item in payload["files"]
    )
    assert manifest.exists()
    assert not (temp / f"datacommune_{dataset}").exists()
    with psycopg.connect(isolated_pg_dsn) as conn:
        columns = ",".join(fields)
        conn.execute(
            f"CREATE TEMP TABLE expected_source AS SELECT {columns} "
            f"FROM {table} WITH NO DATA"
        )
        with conn.cursor().copy(f"COPY expected_source({columns}) FROM STDIN") as copy:
            for row in oracle:
                copy.write_row(row)
        exact = ",".join(field for field in fields if field != "r2_ajuste")
        differences = conn.execute(f"""SELECT count(*) FROM (
            (SELECT {exact} FROM {table} EXCEPT ALL SELECT {exact} FROM expected_source)
            UNION ALL (SELECT {exact} FROM expected_source
                       EXCEPT ALL SELECT {exact} FROM {table})
        ) d""").fetchone()[0]
        assert differences == 0
        if dataset == "loyers":
            assert (
                conn.execute("""SELECT count(*) FROM indicateurs_loyers a
                JOIN expected_source b USING(code_insee,type_habitat,annee)
                WHERE (a.r2_ajuste IS NULL)<>(b.r2_ajuste IS NULL)
                   OR abs(a.r2_ajuste-b.r2_ajuste)>0.000000000001""").fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM indicateurs_loyers "
                    "WHERE type_prediction='epci'"
                ).fetchone()[0]
                == 671
            )
        stock = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        journal = conn.execute(
            "SELECT status,row_count,relation_oids FROM etl_publications "
            "WHERE dataset_name=%s",
            (dataset,),
        ).fetchall()
        current_oid = conn.execute("SELECT %s::regclass::oid", (table,)).fetchone()[0]
        assert stock == rows
        assert current_oid != old_oid
        assert journal == [("published", rows, {table: current_oid})]
        assert (
            conn.execute(
                "SELECT count(*) FROM validation_runs WHERE dataset_name=%s AND passed",
                (dataset,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT last_refresh_status FROM matview_metadata "
                "WHERE matview_name='v_commune_synthese'"
            ).fetchone()[0]
            == "success"
        )
        assert conn.execute(
            """SELECT EXISTS(SELECT 1 FROM pg_depend d
            JOIN pg_rewrite r ON r.oid=d.objid
            WHERE d.classid='pg_rewrite'::regclass
              AND d.refclassid='pg_class'::regclass
              AND r.ev_class='v_commune_synthese'::regclass
              AND d.refobjid=%s::regclass)""",
            (table,),
        ).fetchone()[0]
        metadata = conn.execute(
            "SELECT row_count,source_period_label,source_updated_at,"
            "effective_departments FROM dataset_metadata WHERE dataset_name=%s",
            (dataset,),
        ).fetchone()
        expected_period = (
            "Logements vacants : 2026 via pp_vacant_26 ; "
            "parc privé : 2025 via ff_pp_total_25"
            if dataset == "vacance"
            else None
        )
        assert metadata == (rows, expected_period, None, None)
    assert not list((temp / "frozen_sources").iterdir())
    if output := os.environ.get("FROZEN_SOURCE_EVIDENCE"):
        destination = Path(output) / dataset
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "cli.stdout").write_bytes(completed.stdout)
        (destination / "cli.stderr").write_bytes(completed.stderr)
        (destination / "refusal.stderr").write_bytes(refusal.stderr)
        (destination / "result.json").write_text(
            json.dumps(
                {
                    "dataset": dataset,
                    "rows": rows,
                    "source_files": payload["files"],
                    "manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest(),
                    "column_differences": differences,
                    "journal": journal,
                    "before_oid": old_oid,
                    "current_oid": current_oid,
                    "metadata": metadata,
                    "cli_exit": completed.returncode,
                    "http_attempts": 0,
                    "disk_shim": sys.platform == "win32",
                    "durable_sources_retained": True,
                    "owned_snapshots_closed": True,
                    "bad_hash_refused_before_db_changes": True,
                    "incompatible_checkpoint_restarted": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

"""L09 full frozen LOVAC replay, real gates and publication, disposable DB only."""

import asyncio
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.common.publication_readiness import require_publication
from app.config import settings
from app.database import get_db
from app.etl.base import DEPARTEMENTS
from app.etl.download import get_download_dir
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_vacance.pipeline import VacancePipeline
from app.scope import load_scope_sync
from app.vacance.router import router
from scripts.table_digest import table_digest
from tests.integration.instance_scope_fixture import install_instance_scope
from tests.integration.test_publication_restore import _command, _owned_container
from tests.oracles.lovac_full import FIELDS, expected_rows, frozen_bytes

pytestmark = pytest.mark.destructive


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).parents[2] / "migrations")
    )
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


async def request(dsn, code):
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "3000",
            }
        },
    )

    async def db():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(require_publication)])
    app.dependency_overrides[get_db] = db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as client:
            response = await client.get("/v1/vacance", params={"code_insee": code})
            return response.status_code, response.json()
    finally:
        await engine.dispose()


def stock(dsn):
    with psycopg.connect(dsn) as conn:
        conn.set_read_only(True)
        rows = conn.execute(
            "SELECT " + ",".join(FIELDS) + " FROM vacance ORDER BY code_commune"
        ).fetchall()
        journal = conn.execute(
            "SELECT id,generation::text,status,row_count FROM etl_publications "
            "WHERE dataset_name='vacance' ORDER BY id"
        ).fetchall()
        metadata = conn.execute(
            "SELECT row_count,last_updated_at FROM dataset_metadata WHERE "
            "dataset_name='vacance'"
        ).fetchone()
        view_status = conn.execute(
            "SELECT last_refresh_status FROM matview_metadata WHERE "
            "matview_name='v_commune_synthese'"
        ).fetchone()
        validations = conn.execute(
            "SELECT count(*) FROM validation_runs WHERE dataset_name='vacance' "
            "AND passed"
        ).fetchone()[0]
        table_oid = conn.execute("SELECT 'vacance'::regclass::oid").fetchone()[0]
        duplicates = conn.execute(
            "SELECT count(*) FROM (SELECT code_commune,annee FROM vacance GROUP "
            "BY 1,2 HAVING count(*)>1) d"
        ).fetchone()[0]
    return dict(
        rows={row[0]: row for row in rows},
        table_oid=table_oid,
        journal=journal,
        metadata=metadata,
        view_status=view_status,
        validations=validations,
        duplicates=duplicates,
    )


async def test_full_source_replay_idempotence_and_failed_rebuild_recovery(  # noqa: C901
    migrated, monkeypatch, tmp_path
):
    raw, manifest = frozen_bytes()
    expected = expected_rows(raw)
    assert sum(row[6] == 0 for row in expected.values()) == 19
    assert VacancePipeline().min_staging_rows == 28000
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    # Keep all application data gates; read the physical PG disk in its container.
    audit = []
    container = _owned_container(migrated, audit)
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
    monkeypatch.setattr(
        PipelineRunner,
        "_gate03_free_bytes",
        staticmethod(lambda *_: (free, "wrapper-container:/var/lib/postgresql/data")),
    )
    calls = {"download": 0, "validation": 0, "rebuild": 0}
    duplicate = False
    downloads = []

    async def download_transport(_self):
        calls["download"] += 1
        target = get_download_dir("vacance") / "raw" / "lovac_communes.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = raw
        if duplicate:
            # Explicit derivative: repeat one complete source row, same vintage.
            payload = raw + raw.splitlines(keepends=True)[1]
        target.write_bytes(payload)
        downloads.append(
            {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "repeated_row": duplicate,
            }
        )
        assert hashlib.sha256(raw).hexdigest() == manifest["raw_sha256"]
        return [target]

    monkeypatch.setattr(VacancePipeline, "_async_download", download_transport)

    class TracedValidation(ValidationHook):
        def on_run_complete(self, name, result):
            calls["validation"] += 1
            super().on_run_complete(name, result)

    class TracedRebuild(DependentMatviewHook):
        fail_next = False

        def on_run_complete(self, name, result):
            calls["rebuild"] += 1
            if self.fail_next:
                self.fail_next = False
                raise psycopg.OperationalError(
                    "Injected local required rebuild failure"
                )
            super().on_run_complete(name, result)

    rebuild = TracedRebuild()
    runner = PipelineRunner([TracedValidation(), rebuild])
    secret = next(
        code for code, row in expected.items() if row[2] is None and row[3] is not None
    )
    zero = next(code for code, row in expected.items() if row[6] == 0)
    known = next(
        code for code, row in expected.items() if row[6] is not None and row[6] > 0
    )
    witnesses = {"secret": secret, "observed_zero": zero, "known": known}
    # Explicit old-stock counterexample, never an assertion about shared data.
    with psycopg.connect(migrated) as conn:
        conn.execute(
            "INSERT INTO "
            "vacance(code_commune,annee,nb_logements_prives,"
            "nb_logements_vacants,taux_vacance) VALUES(%s,2026,NULL,28,0)",
            (secret,),
        )
        for code in witnesses.values():
            conn.execute(
                "INSERT INTO demographie(code_insee,annee) VALUES(%s,2026)", (code,)
            )
        # /v1/vacance reads the scope first (DATA-03).
        install_instance_scope(conn, ["vacance"], DEPARTEMENTS)
    last_metadata = None
    phases = []
    responses = {}
    for phase in ("full", "identical_duplicate", "failed_rebuild", "fresh_recovery"):
        duplicate = phase == "identical_duplicate"
        rebuild.fail_next = phase == "failed_rebuild"
        result = await asyncio.to_thread(runner.run, VacancePipeline())
        current = stock(migrated)
        assert current["rows"] == expected
        assert {"transform:lovac_communes.csv", "stage:lovac_communes.csv"} <= {
            stage.stage_name for stage in result.stages
        }
        if phase == "failed_rebuild":
            assert not result.success
            assert current["journal"][-1][2] == "failed"
            assert current["metadata"] == last_metadata
            assert (await request(migrated, secret))[0] == 503
        else:
            assert result.success, result.error
            assert current["journal"][-1][2:] == ("published", 34912)
            assert current["metadata"][0] == 34912
            assert current["view_status"] == ("success",)
            assert current["duplicates"] == 0
            assert result.total_rows_loaded == (34913 if duplicate else 34912)
            last_metadata = current["metadata"]
            for kind, code in witnesses.items():
                status, body = await request(migrated, code)
                assert status == 200
                row = body["donnees"][0]
                assert tuple(row[key] for key in FIELDS[:6]) == expected[code][:6]
                assert [row[key] for key in FIELDS[6:]] == [
                    None if value is None else float(value)
                    for value in expected[code][6:]
                ]
                responses[kind] = body
            with psycopg.connect(migrated) as conn:
                observed = conn.execute(
                    "SELECT code_insee,annee_vacance,taux_vacance FROM "
                    "v_commune_synthese ORDER BY code_insee"
                ).fetchall()
                assert observed == sorted(
                    (code, 2026, expected[code][6]) for code in witnesses.values()
                )
        phases.append(
            {
                "phase": phase,
                "success": result.success,
                "staged": result.total_rows_loaded,
                "stock": len(current["rows"]),
                "table_oid": current["table_oid"],
                "journal": current["journal"][-1],
                "seconds": result.total_duration_seconds,
                "stages": [
                    {
                        "name": stage.stage_name,
                        "seconds": stage.duration_seconds,
                        "rows": stage.rows_affected,
                    }
                    for stage in result.stages
                ],
                "stock_sha256": hashlib.sha256(
                    json.dumps(sorted(current["rows"].values()), default=str).encode()
                ).hexdigest(),
            }
        )
    assert calls == {"download": 4, "validation": 4, "rebuild": 4}
    assert current["validations"] == 4
    assert len({phase["table_oid"] for phase in phases}) == 4
    assert len({entry[1] for entry in current["journal"]}) == 4
    if output := os.environ.get("LOVAC_REPLAY_EVIDENCE"):
        Path(output).write_text(
            json.dumps(
                {
                    "source": manifest,
                    "phases": phases,
                    "calls": calls,
                    "downloads": downloads,
                    "oracle_rows": len(expected),
                    "http": responses,
                    "secret_denominators": sum(
                        row[2] is None for row in expected.values()
                    ),
                    "observed_zero_rates": sum(
                        row[6] == 0 for row in expected.values()
                    ),
                    "pg_free_bytes_measured": free,
                    "commands": audit,
                    "scope": "Full frozen LOVAC only; canonical DDL, three "
                    "synthetic demographic anchors without geometry; no auth/quota "
                    "or other source import; recovery manually invoked "
                    "through real runner.",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


# -- Plan 02-17: partial delivery on the disposable harness ------------------
# Same frozen real LOVAC file, instance scope restricted to the two hosted
# departements, as on the work base. Nothing here touches the work base.

HOSTED = ("44", "85")
DIGEST_KEY = ("code_commune", "annee")
# Surrogate key drawn from a sequence: new at every publication.
DIGEST_EXCLUDE = ("id",)


@pytest.fixture()
def hosted_db(pg_dsn):
    """Own migrated child database per test: the published table holds only
    what this test imported, as on the work base (44 and 85 only)."""
    database = "contract_l09_hosted_" + uuid4().hex
    child = urlunsplit(urlsplit(pg_dsn)._replace(path="/" + database))
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        with psycopg.connect(child, connect_timeout=5) as conn:
            for extension in ("postgis", "pg_trgm", "unaccent"):
                conn.execute(
                    sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                        sql.Identifier(extension)
                    )
                )
        cfg = Config()
        cfg.set_main_option(
            "script_location", str(Path(__file__).parents[2] / "migrations")
        )
        with pytest.MonkeyPatch.context() as context:
            context.setenv("DATABASE_URL", child)
            context.setattr(settings, "database_url", child)
            cfg.set_main_option("sqlalchemy.url", child)
            command.upgrade(cfg, "head")
        yield child
    finally:
        # Only the exact UUID database created above is ever dropped.
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


def _scoped_runner(dsn, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    audit = []
    container = _owned_container(dsn, audit)
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
    monkeypatch.setattr(
        PipelineRunner,
        "_gate03_free_bytes",
        staticmethod(lambda *_: (free, "wrapper-container:/var/lib/postgresql/data")),
    )
    with psycopg.connect(dsn) as conn:
        install_instance_scope(conn, ["vacance"], HOSTED)
    # The scope the runner reads is the one stored in this database, not the
    # full test scope conftest substitutes for in-process runners.
    return PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=_stored_scope
    )


def _stored_scope(dsn):
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        return load_scope_sync(conn)


def _frozen_manifest(tmp_path, name, payload):
    """Reviewed vacance contract (source_manifest.py) over a durable copy."""
    folder = tmp_path / "frozen" / name
    folder.mkdir(parents=True)
    source = folder / "lovac_communes.csv"
    source.write_bytes(payload)
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "dataset": "vacance",
                "vintage": 2026,
                "source_contract": "lovac-communes-v1:2026:2025",
                "files": [
                    {
                        "kind": "communes",
                        "path": str(source),
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _import(runner, manifest):
    pipeline = VacancePipeline.from_manifest(manifest)
    try:
        return pipeline, runner.run(pipeline)
    finally:
        pipeline.close()


def _published(dsn):
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        rows, sha = table_digest(conn, "vacance", DIGEST_KEY, DIGEST_EXCLUDE)
        by_dept = dict(
            conn.execute(
                "SELECT code_departement, count(*) FROM vacance GROUP BY 1"
            ).fetchall()
        )
        published = conn.execute(
            "SELECT count(*) FROM etl_publications WHERE dataset_name='vacance' "
            "AND status='published'"
        ).fetchone()[0]
        metadata = conn.execute(
            "SELECT row_count,last_updated_at FROM dataset_metadata WHERE "
            "dataset_name='vacance'"
        ).fetchone()
        table_oid = conn.execute("SELECT 'vacance'::regclass::oid").fetchone()[0]
    return dict(
        digest=(rows, sha),
        by_dept=by_dept,
        published=published,
        metadata=metadata,
        table_oid=table_oid,
    )


def _only_44(raw, repeat=1):
    lines = raw.splitlines(keepends=True)
    # Rows are quoted: "44xxx";...
    fragment = [line for line in lines[1:] if line.startswith(b'"44')]
    return lines[0] + b"".join(fragment) * repeat, len(fragment)


def _full_publication(runner, tmp_path, dsn):
    raw, manifest = frozen_bytes()
    _, result = _import(runner, _frozen_manifest(tmp_path, "full", raw))
    assert result.success, result.error
    before = _published(dsn)
    assert before["by_dept"] == {"44": 207, "85": 253}
    return raw, manifest, before


def _evidence(key, payload):
    if output := os.environ.get("LOVAC_PARTIAL_EVIDENCE"):
        path = Path(output)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[key] = payload
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )


def test_44_only_fragment_is_refused_and_publication_unchanged(
    hosted_db, monkeypatch, tmp_path
):
    runner = _scoped_runner(hosted_db, monkeypatch, tmp_path)
    raw, manifest, before = _full_publication(runner, tmp_path, hosted_db)
    # The whole-file guard (04.1-REVIEW WR-01) refuses this fragment first:
    # off here, so the guard under test stays reached (see the next test).
    monkeypatch.setattr(VacancePipeline, "national_file_min_rows", None)

    fragment, rows_44 = _only_44(raw)
    assert rows_44 == 207
    pipeline, result = _import(
        runner, _frozen_manifest(tmp_path, "fragment-44", fragment)
    )

    # Expected before execution: the first guard of the swap, volume
    # regression at equal scope (207 vs 460 published), refuses; the floor
    # (367) would refuse too, never reached.
    assert not result.success
    assert result.error == (
        "Row count regression: scoped 207 vs 460 "
        "(drop 55.0% > threshold 20%) -- swap aborted"
    )
    assert pipeline.min_staging_rows == 367 > 207
    assert "validate_data" not in {stage.stage_name for stage in result.stages}
    after = _published(hosted_db)
    assert after == before
    _evidence(
        "fragment_44",
        {
            "source_sha256": manifest["raw_sha256"],
            "fragment_sha256": hashlib.sha256(fragment).hexdigest(),
            "fragment_rows": rows_44,
            "digest_before": before["digest"],
            "digest_after": after["digest"],
            "by_dept": after["by_dept"],
            "floor": pipeline.min_staging_rows,
            "error": result.error,
        },
    )


def test_a_44_only_fragment_of_the_national_file_is_refused_before_the_filter(
    hosted_db, monkeypatch, tmp_path
):
    """04.1-REVIEW WR-01: on a restricted scope a departement without a
    reference is a presence check; the truncation of the national file is
    caught on the whole file, against national_min_rows (28,000 for LOVAC)."""
    runner = _scoped_runner(hosted_db, monkeypatch, tmp_path)
    _raw, _manifest, before = _full_publication(runner, tmp_path, hosted_db)
    fragment, rows_44 = _only_44(_raw)
    _pipeline, result = _import(
        runner, _frozen_manifest(tmp_path, "fragment-44-whole", fragment)
    )
    assert not result.success
    assert result.error == (
        "Stage 'scope_filter' failed: National file volume: _vacance_staging_flat "
        f"held {rows_44} rows before the scope filter (minimum 28,000) "
        "-- swap aborted"
    )
    assert _published(hosted_db) == before


def test_coverage_refuses_a_44_only_staging_naming_85(hosted_db, monkeypatch, tmp_path):
    runner = _scoped_runner(hosted_db, monkeypatch, tmp_path)
    raw, manifest, before = _full_publication(runner, tmp_path, hosted_db)
    # Whole-file guard off: the coverage guard is the one under test.
    monkeypatch.setattr(VacancePipeline, "national_file_min_rows", None)

    # Controlled duplication of the 44 fragment: 414 rows pass the volume
    # regression (414 vs 460) and the floor (367) unchanged, 85 stays empty.
    doubled, rows_44 = _only_44(raw, repeat=2)
    pipeline, result = _import(
        runner, _frozen_manifest(tmp_path, "fragment-44-twice", doubled)
    )

    assert not result.success
    assert result.total_rows_loaded == 2 * rows_44 == 414
    assert pipeline.min_staging_rows == 367 <= result.total_rows_loaded
    assert result.error == (
        "Department coverage: missing scoped departments ['85'] in "
        "_vacance_staging_flat -- swap aborted"
    )
    after = _published(hosted_db)
    assert after == before
    _evidence(
        "coverage_85",
        {
            "source_sha256": manifest["raw_sha256"],
            "staging_sha256": hashlib.sha256(doubled).hexdigest(),
            "staging_rows": result.total_rows_loaded,
            "digest_before": before["digest"],
            "digest_after": after["digest"],
            "error": result.error,
        },
    )


# -- Plan 02-17: source correction, deterministic harness scenario -----------
# Two frozen entries of vacance under the same key (commune 44109): the real
# file, then the same file where the producer corrected pp_vacant_26. Stands
# for the source-correction proof when no real correction can be written
# before its reimport; never equivalent to a proof on real data.

CORRECTED = "44109"
SOURCE_BEFORE = b'"44109";"Nantes";"14854";"2399";"155375";'
SOURCE_AFTER = b'"44109";"Nantes";"14954";"2399";"155375";'


def _row(dsn, code):
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        cursor = conn.execute(
            "SELECT " + ",".join(FIELDS) + " FROM vacance WHERE code_commune=%s",
            (code,),
        )
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def test_source_correction_publishes_the_corrected_value_in_place(
    hosted_db, monkeypatch, tmp_path
):
    runner = _scoped_runner(hosted_db, monkeypatch, tmp_path)
    raw, _, before = _full_publication(runner, tmp_path, hosted_db)
    assert raw.count(SOURCE_BEFORE) == 1
    corrected = raw.replace(SOURCE_BEFORE, SOURCE_AFTER)
    assert len(corrected) == len(raw)

    (published_before,) = _row(hosted_db, CORRECTED)
    assert published_before["nb_logements_vacants"] == 14854

    _, result = _import(runner, _frozen_manifest(tmp_path, "corrected", corrected))
    assert result.success, result.error

    rows_after = _row(hosted_db, CORRECTED)
    # Corrected in place: one row under the same key, never deleted nor doubled.
    assert len(rows_after) == 1
    (published_after,) = rows_after
    assert published_after["nb_logements_vacants"] == 14954
    assert published_after["nb_vacants_moins_2ans"] == 14954 - 2399
    assert published_after["taux_vacance"] == Decimal("9.62")
    after = _published(hosted_db)
    assert after["by_dept"] == {"44": 207, "85": 253}
    assert after["digest"] != before["digest"]
    # Only the corrected key moved: every other published row is unchanged.
    with psycopg.connect(hosted_db) as conn:
        published = conn.execute(
            "SELECT " + ",".join(FIELDS) + " FROM vacance WHERE code_commune<>%s",
            (CORRECTED,),
        ).fetchall()
    unchanged = {
        code: row
        for code, row in expected_rows(raw).items()
        if code[:2] in HOSTED and code != CORRECTED
    }
    assert {row[0]: row for row in published} == unchanged
    _evidence(
        "correction",
        {
            "key": CORRECTED,
            "column": "nb_logements_vacants",
            "before": published_before["nb_logements_vacants"],
            "after": published_after["nb_logements_vacants"],
            "source_before_sha256": hashlib.sha256(raw).hexdigest(),
            "source_after_sha256": hashlib.sha256(corrected).hexdigest(),
            "digest_before": before["digest"],
            "digest_after": after["digest"],
        },
    )

"""Tracer 02-01: the size and fidelity extraction scripts against a migrated PostGIS.

The scripts will run once on the restored legacy production base (plan 02-03); an SQL
error or a non-deterministic output must surface here, on a disposable database
migrated to ``head``, not on the hypervisor. Both scripts are launched as real
subprocesses and receive the DSN through an environment variable only
(T-02-01-03).
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.config import settings
from scripts.extract_fidelity_reference import REFERENCE_TABLES

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).parents[2]
MEASURE = BACKEND / "scripts" / "measure_db_sizes.py"
EXTRACT = BACKEND / "scripts" / "extract_fidelity_reference.py"
DSN_ENV = "FIDELITY_TOOLS_TEST_DSN"
PANEL = "44109,44001,85047"

# Two 44 communes and one 85 commune; 44001 is a KI-D1 false zero.
MUTATION_ROWS = [
    ("2019-44-1", "2019-05-02", "RUE DU\nPORT"),
    ("2023-44-1", "2023-03-01", "RUE HAUTE"),
]

VACANCE_ROWS = [
    ("44109", "44", 1000, 52, 5.20, 2025),
    ("44001", "44", 0, 0, 0, 2025),
    ("85047", "85", 500, 15, 3.00, 2025),
]


@pytest.fixture(scope="module")
def migrated_dsn(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", isolated_pg_dsn)
        mp.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        command.upgrade(cfg, "head")
    with psycopg.connect(isolated_pg_dsn) as conn:
        for code, dept, prives, vacants, taux, annee in VACANCE_ROWS:
            conn.execute(
                "INSERT INTO vacance (code_commune, code_departement,"
                " nb_logements_prives, nb_logements_vacants, taux_vacance, annee)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (code, dept, prives, vacants, taux, annee),
            )
        # One pre-2021 44 sale whose street name spans two lines (a quoted
        # multi-line CSV field) and one recent sale outside the history cut.
        for id_mutation, day, voie in MUTATION_ROWS:
            conn.execute(
                "INSERT INTO mutations (code_departement, id_mutation, date_mutation,"
                " adresse_nom_voie, code_commune) VALUES ('44', %s, %s, %s, '44109')",
                (id_mutation, day, voie),
            )
    return isolated_pg_dsn


def run_script(script: Path, dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, DSN_ENV: dsn}
    env.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, str(script), "--dsn-env", DSN_ENV, *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert dsn not in result.stdout and dsn not in result.stderr
    return result


def extract_into(dsn: str, out_dir: Path) -> dict:
    run_script(
        EXTRACT,
        dsn,
        "--panel",
        PANEL,
        "--out-dir",
        str(out_dir),
        "--source-label",
        "harness-head",
        "--dvf-history-dir",
        str(out_dir.parent / f"{out_dir.name}-dvf"),
        "--dvf-history-before",
        "2021-01-01",
    )
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


def load(out_dir: Path, name: str) -> dict:
    return json.loads((out_dir / name).read_text(encoding="utf-8"))


def test_measure_reports_three_sizes_per_relation(migrated_dsn, tmp_path):
    out = tmp_path / "sizes.json"
    run_script(MEASURE, migrated_dsn, "--out", str(out), "--exact-counts")
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["alembic_version"]
    assert payload["pg_database_size"] > 0
    relations = {r["name"]: r for r in payload["relations"]}
    for rel in relations.values():
        for key in ("pg_total_relation_size", "pg_relation_size", "pg_indexes_size"):
            assert isinstance(rel[key], int) and rel[key] >= 0, (rel["name"], key)
    assert relations["adresses_44"]["departement"] == "44"
    assert relations["adresses_44"]["parent"] == "adresses"
    assert relations["adresses_2a"]["departement"] == "2A"
    assert relations["adresses_default"]["departement"] is None
    assert relations["vacance"]["exact_count"] == len(VACANCE_ROWS)
    assert payload["tables"]["adresses"]["partitions"] == 103


def test_extraction_is_complete_verifiable_and_deterministic(migrated_dsn, tmp_path):
    first = tmp_path / "run1"
    second = tmp_path / "run2"
    manifest = extract_into(migrated_dsn, first)
    manifest_bis = extract_into(migrated_dsn, second)

    level1 = load(first, "level1_counts.json")
    vacance = level1["tables"]["vacance"]
    assert vacance["counts"] == {"44": 2, "85": 1, "total": 3}
    assert vacance["method"] == "code_departement"
    assert level1["tables"]["adresses"]["method"] == "partition"
    assert level1["tables"]["arrets_transport"]["method"] == "absent"
    assert level1["tables"]["arrets_transport"]["motif"]
    assert level1["tables"]["arrets_transport"]["counts"]["44"] is None
    for table in REFERENCE_TABLES:
        entry = level1["tables"][table]
        assert entry["counts"] is not None or entry["status"] in {
            "absent",
            "non_peuplee",
        }

    zeros = load(first, "lovac_false_zeros.json")
    assert zeros["counts"] == {"44": 1, "85": 0, "total": 1}
    assert zeros["codes_44_85"] == [{"code_commune": "44001", "annee": 2025}]

    panel = load(first, "level2_panel.json")
    assert panel["panel"] == ["44001", "44109", "85047"]
    assert panel["status"] in {"ok", "non_peuplee", "absent"}

    millesimes = load(first, "millesimes.json")
    assert millesimes["dvf_years_by_departement"]["mutations"]["status"] == "ok"
    history_report = millesimes["dvf_history_extract"]["tables"]["mutations"]
    # One data row despite the embedded newline (CSV-aware count).
    assert history_report["rows"] == 1
    assert millesimes["dvf_years_by_departement"]["mutations"]["years"]["44"] == {
        "2019": 1,
        "2023": 1,
    }

    # Every SHA-256 in the manifest matches the file on disk.
    assert {f["name"] for f in manifest["files"]} == {
        "level1_counts.json",
        "level2_panel.json",
        "lovac_false_zeros.json",
        "millesimes.json",
    }
    for entry in manifest["files"]:
        data = (first / entry["name"]).read_bytes()
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    history_dir = tmp_path / "run1-dvf"
    assert len(manifest["dvf_history"]) == 3
    for entry in manifest["dvf_history"]:
        data = (history_dir / entry["name"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()

    # Two extractions differ only by extracted_at.
    for name in (
        "level1_counts.json",
        "level2_panel.json",
        "lovac_false_zeros.json",
        "millesimes.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    manifest.pop("extracted_at")
    manifest_bis.pop("extracted_at")
    assert manifest == manifest_bis


def test_missing_table_and_unpopulated_matview_are_reasons(migrated_dsn, tmp_path):
    # Runs last: it degrades the module-owned database on purpose.
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE gares CASCADE")
        conn.execute("REFRESH MATERIALIZED VIEW scoring_renov_target WITH NO DATA")

    out = tmp_path / "degraded"
    extract_into(migrated_dsn, out)
    level1 = load(out, "level1_counts.json")
    assert level1["tables"]["gares"] == {
        "status": "absent",
        "method": None,
        "column": None,
        "counts": None,
    }
    assert level1["tables"]["scoring_renov_target"]["status"] == "non_peuplee"

    sizes = tmp_path / "sizes.json"
    run_script(MEASURE, migrated_dsn, "--out", str(sizes), "--exact-counts")
    relations = {
        r["name"]: r for r in json.loads(sizes.read_text("utf-8"))["relations"]
    }
    assert relations["scoring_renov_target"]["count_error"] == "non_peuplee"
    assert relations["scoring_renov_target"]["exact_count"] is None


def test_unreadable_relation_is_a_reason_not_an_exception(migrated_dsn, tmp_path):
    # A role that may read everything except vacance: the extraction must go on
    # and record the permission error instead of aborting.
    role = "fidelity_ro_" + uuid4().hex[:12]
    parts = urlsplit(migrated_dsn)
    database = parts.path.lstrip("/")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fidelity'").format(
                sql.Identifier(role)
            )
        )
        for statement in (
            "GRANT CONNECT ON DATABASE {db} TO {r}",
            "GRANT USAGE ON SCHEMA public TO {r}",
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {r}",
            "REVOKE SELECT ON vacance FROM {r}",
        ):
            conn.execute(
                sql.SQL(statement).format(
                    db=sql.Identifier(database), r=sql.Identifier(role)
                )
            )
    host = parts.netloc.split("@", 1)[-1]
    restricted = urlunsplit(parts._replace(netloc=f"{role}:fidelity@{host}"))
    try:
        out = tmp_path / "restricted"
        extract_into(restricted, out)
        level1 = load(out, "level1_counts.json")
        assert level1["tables"]["vacance"]["status"] == "illisible"
        assert level1["tables"]["vacance"]["motif"].startswith("InsufficientPrivilege")
        assert level1["tables"]["vacance"]["counts"] is None
        assert level1["tables"]["adresses"]["status"] == "ok"
        zeros = load(out, "lovac_false_zeros.json")
        assert zeros["status"] == "illisible"
        assert zeros["counts"] is None
    finally:
        with psycopg.connect(migrated_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

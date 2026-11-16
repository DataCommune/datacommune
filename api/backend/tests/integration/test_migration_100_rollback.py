"""Rollback of ``100_dpe_source_columns`` proven on a disposable PostGIS base.

R06-09/10 sequence (04-09 task 3, contract of 04-20), final review API WR-01:

1. the previous application (``f5c7606``, the SHA the work base serves, before
   04-15) migrates an empty base to its head ``099_geo_arrondissements``;
2. the checkout that knows 100 upgrades; an injected failure first (a held lock,
   ``lock_timeout``) leaves revision and schema unchanged and the previous
   application serving, then the upgrade is resumed;
3. the previous application runs on the new schema (boot gate, DPE read);
4. the downgrade runs from the checkout that knows 100, again after an injected
   failure that keeps C1 to C3 and their values; the downgrade drops them;
5. the previous application runs on 099 again; the schema is the one of step 1.

Each checkout runs its own ``alembic`` and its own ASGI application in a child
process (``git archive`` of the previous SHA, the current interpreter): the
alembic commands that the work base deploy script (private) runs, not the script
itself. The deploy-time revision check (``alembic current`` before
``dc stop api``) is proven on both checkouts: the previous one refuses a base
at 100. The script, its single checkout whose HEAD tags the api image, and the
README rollback procedure are proven on doubles by
the private tests of that script. Never ``alembic stamp``.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_migration_100_rollback.py
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import psycopg
import pytest

from app.common.migration_check import _evaluate_revisions, _load_script_directory
from tests.integration.instance_scope_fixture import install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parents[1]
# The SHA served by the work base before 04-20 (03-RECETTE.md, DEPLOYED
# f5c7606 alembic=099_geo_arrondissements): the rollback target.
PREVIOUS_SHA = "f5c76064da36c0862878518a0526b53732e272a4"
PREVIOUS_HEAD = "099_geo_arrondissements"
# The revision under test; the checkout knows later ones (101 since 04.1-07),
# so the upgrades of this sequence stop at it explicitly.
NEW_HEAD = "100_dpe_source_columns"
COLUMNS_C1_C3 = {
    "version_dpe",
    "date_derniere_modification_dpe",
    "date_reception_dpe",
    "date_visite_diagnostiqueur",
    "conso_5_usages_par_m2_ep",
    "emission_ges_5_usages_par_m2",
    "numero_dpe_immeuble_associe",
    "numero_immatriculation_copropriete",
}
NUMERO = "2485E0000100R"
POINT = (-1.4, 46.6)
# Lock wait of an injected failure: the migration waits behind a held lock.
LOCK_TIMEOUT = "-c lock_timeout=1500"

# Child process of the previous application: its boot gate (lifespan) and a
# DPE read through the real routing, the quota dependency only overridden.
_PROBE = r"""
import json, logging, sys
import app, app.main
from fastapi.testclient import TestClient
from app.auth.dependencies import check_quota_hybrid

class Keep(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines = []
    def emit(self, record):
        self.lines.append(record.getMessage())

keep = Keep()
logging.getLogger("app.common.migration_check").addHandler(keep)
app.main.app.dependency_overrides[check_quota_hybrid] = lambda: None
lon, lat = float(sys.argv[1]), float(sys.argv[2])
try:
    with TestClient(app.main.app) as client:
        logging.getLogger("app.common.migration_check").addHandler(keep)
        reponse = client.get(
            "/v1/dpe", params={"lat": lat, "lon": lon, "rayon_m": 100}
        )
        corps = reponse.json()
        sortie = {
            "boot": "ok",
            "status": reponse.status_code,
            "numeros": [d.get("numero_dpe") for d in corps.get("donnees", [])],
        }
except Exception as exc:
    sortie = {"boot": "refused", "error": f"{type(exc).__name__}: {exc}"}
sortie["app_file"] = app.__file__
sortie["warnings"] = keep.lines
print("PROBE " + json.dumps(sortie))
"""


@pytest.fixture(scope="module")
def previous_tree(tmp_path_factory) -> Path:
    """``api/backend`` of the previous SHA, extracted by ``git archive``."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", PREVIOUS_SHA, "api/backend"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        # Never a skip: CI must fetch the history (fetch-depth: 0).
        pytest.fail(
            f"previous SHA {PREVIOUS_SHA} unavailable: "
            f"{archive.stderr.decode(errors='replace').strip()}"
        )
    racine = tmp_path_factory.mktemp("previous")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(racine, filter="data")
    return racine / "api" / "backend"


def _env(dsn: str, tree: Path, pgoptions: str | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PGOPTIONS"}
    }
    env.update(
        DATABASE_URL=dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
        PYTHONPATH=str(tree),
        DEBUG="true",
    )
    if pgoptions:
        env["PGOPTIONS"] = pgoptions
    return env


def _alembic(
    dsn: str, tree: Path, *args: str, pgoptions: str | None = None
) -> subprocess.CompletedProcess[str]:
    """``alembic <args>`` run from *tree*, as the image of that checkout runs it."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=tree,
        env=_env(dsn, tree, pgoptions),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _ok(resultat: subprocess.CompletedProcess[str]) -> str:
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    return resultat.stdout + resultat.stderr


def _probe(dsn: str, tree: Path) -> dict:
    """Boot the application of *tree* on the base and read one DPE."""
    resultat = subprocess.run(
        [sys.executable, "-c", _PROBE, str(POINT[0]), str(POINT[1])],
        cwd=tree,
        env=_env(dsn, tree),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    lignes = [x for x in resultat.stdout.splitlines() if x.startswith("PROBE ")]
    assert lignes, resultat.stdout + resultat.stderr
    sortie = json.loads(lignes[-1].removeprefix("PROBE "))
    assert Path(sortie["app_file"]).is_relative_to(tree), sortie["app_file"]
    return sortie


def _revision(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _schema(dsn: str) -> dict[str, list]:
    """Columns, indexes and constraints of the public schema (catalog only)."""
    with psycopg.connect(dsn) as conn:
        colonnes = conn.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable,
                   coalesce(column_default, '')
            FROM information_schema.columns WHERE table_schema = 'public'
            ORDER BY 1, 2
            """
        ).fetchall()
        index = conn.execute(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' ORDER BY 1, 2"
        ).fetchall()
        contraintes = conn.execute(
            """
            SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'public' ORDER BY 1, 2
            """
        ).fetchall()
    return {"colonnes": colonnes, "index": index, "contraintes": contraintes}


def _dpe_columns(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'diagnostics_dpe'"
            )
        }


def _seed(dsn: str) -> None:
    """One present document at the point, and the scope the read needs."""
    install_scope_at(dsn, ("dpe",), ("44", "85"))
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO diagnostics_dpe (id, numero_dpe, identifiant_ban, "
            "code_departement, code_insee, etiquette_dpe, date_etablissement_dpe, "
            "date_fin_validite_dpe, lifecycle_facts, position) VALUES "
            "(1000001, %s, '85191_0001_00001', '85', '85191', 'C', "
            "DATE '2024-02-03', DATE '2034-02-03', "
            '\'{"replacement_state": "no_successor_known", '
            '"graph_issues": [], "date_issues": []}\'::jsonb, '
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (NUMERO, *POINT),
        )


class _HeldLock:
    """A transaction holding a lock on ``diagnostics_dpe``, as an import read."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def __enter__(self):
        self.conn = psycopg.connect(self.dsn)
        self.conn.execute("LOCK TABLE diagnostics_dpe IN ACCESS SHARE MODE")
        return self

    def __exit__(self, *exc):
        self.conn.rollback()
        self.conn.close()


def _served(sortie: dict, *, warned: bool) -> None:
    """The previous application booted and served the document."""
    assert sortie["boot"] == "ok", sortie
    assert sortie["status"] == 200 and sortie["numeros"] == [NUMERO], sortie
    tolere = [w for w in sortie["warnings"] if "TOLEREE" in w]
    assert bool(tolere) is warned, sortie["warnings"]


def test_rollback_of_migration_100_is_proven_old_code_on_both_schemas(
    isolated_pg_dsn, previous_tree
):
    dsn = isolated_pg_dsn

    # 1. Previous checkout at its head.
    assert NEW_HEAD not in _ok(_alembic(dsn, previous_tree, "heads"))
    _ok(_alembic(dsn, previous_tree, "upgrade", "head"))
    assert _revision(dsn) == PREVIOUS_HEAD
    _seed(dsn)
    schema_099 = _schema(dsn)
    assert not COLUMNS_C1_C3 & _dpe_columns(dsn)
    _served(_probe(dsn, previous_tree), warned=False)
    # Deploy check, both checkouts know 099.
    _ok(_alembic(dsn, previous_tree, "current"))
    _ok(_alembic(dsn, BACKEND, "current"))

    # 2. Upgrade with an injected failure: nothing changes, the previous
    # container restarted on this schema still serves (deploy restart).
    with _HeldLock(dsn):
        echec = _alembic(dsn, BACKEND, "upgrade", NEW_HEAD, pgoptions=LOCK_TIMEOUT)
    assert echec.returncode != 0, echec.stdout + echec.stderr
    assert "lock timeout" in echec.stderr.lower(), echec.stderr
    assert _revision(dsn) == PREVIOUS_HEAD
    assert _schema(dsn) == schema_099
    _served(_probe(dsn, previous_tree), warned=False)
    _ok(_alembic(dsn, BACKEND, "upgrade", NEW_HEAD))
    assert _revision(dsn) == NEW_HEAD
    schema_100 = _schema(dsn)
    assert _dpe_columns(dsn) >= COLUMNS_C1_C3
    ajoutees = {
        (table, colonne)
        for table, colonne, *_ in set(schema_100["colonnes"])
        - set(schema_099["colonnes"])
    }
    assert ajoutees == {("diagnostics_dpe", name) for name in COLUMNS_C1_C3}
    assert schema_100["index"] == schema_099["index"]
    assert schema_100["contraintes"] == schema_099["contraintes"]

    # 3. Previous application on the new schema: boots with the tolerated
    # « base ahead » warning, serves the document.
    _served(_probe(dsn, previous_tree), warned=True)
    # Deploy check: the previous checkout refuses a base at 100 (exit != 0,
    # before `dc stop api`); the checkout that knows 100 accepts it.
    refus = _alembic(dsn, previous_tree, "current")
    assert refus.returncode != 0, refus.stdout + refus.stderr
    assert NEW_HEAD in refus.stdout + refus.stderr
    assert NEW_HEAD in _ok(_alembic(dsn, BACKEND, "current"))
    # The new application refuses a base behind it: the API is stopped before
    # the downgrade (README procedure).
    with pytest.raises(RuntimeError, match="BLOQUANTE"):
        _evaluate_revisions({PREVIOUS_HEAD}, _load_script_directory())

    # 4. Downgrade from the checkout that knows 100, after an injected failure
    # that keeps C1 to C3 and their values.
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE diagnostics_dpe SET conso_5_usages_par_m2_ep = 226.10, "
            "numero_dpe_immeuble_associe = '2485E0000099Z' WHERE numero_dpe = %s",
            (NUMERO,),
        )
    with _HeldLock(dsn):
        echec = _alembic(
            dsn, BACKEND, "downgrade", PREVIOUS_HEAD, pgoptions=LOCK_TIMEOUT
        )
    assert echec.returncode != 0, echec.stdout + echec.stderr
    assert "lock timeout" in echec.stderr.lower(), echec.stderr
    assert _revision(dsn) == NEW_HEAD
    assert _schema(dsn) == schema_100
    with psycopg.connect(dsn) as conn:
        gardees = conn.execute(
            "SELECT conso_5_usages_par_m2_ep::text, numero_dpe_immeuble_associe "
            "FROM diagnostics_dpe WHERE numero_dpe = %s",
            (NUMERO,),
        ).fetchone()
    assert gardees == ("226.10", "2485E0000099Z")
    _ok(_alembic(dsn, BACKEND, "downgrade", PREVIOUS_HEAD))
    assert _revision(dsn) == PREVIOUS_HEAD

    # 5. Previous application on 099: the schema of step 1, C1 to C3 and their
    # values gone (a later upgrade brings the columns back empty).
    assert _schema(dsn) == schema_099
    assert not COLUMNS_C1_C3 & _dpe_columns(dsn)
    _ok(_alembic(dsn, previous_tree, "current"))
    _served(_probe(dsn, previous_tree), warned=False)
    _ok(_alembic(dsn, BACKEND, "upgrade", "head"))
    with psycopg.connect(dsn) as conn:
        perdues = conn.execute(
            "SELECT conso_5_usages_par_m2_ep, numero_dpe_immeuble_associe "
            "FROM diagnostics_dpe WHERE numero_dpe = %s",
            (NUMERO,),
        ).fetchone()
    assert perdues == (None, None)

"""Plan 04.1-24: migration 107 drops exactly the indexes plan 04.1-23 retired.

The expected set comes from ``tests/fixtures/index_removal_107.json`` (drawn
from the registry ``04.1-INDEX-EXPLAIN.md`` and ``catalogue-index-23.json``),
never from the migration source, so a migration dropping one index too many,
or one too few, fails here.

Cases: (a) one linear chain, 107 after 106; (b) upgrade from 106 drops the
fixture set and nothing else, every other index unchanged; (c) downgrade
rebuilds 106, partitioned trees complete (valid parent, one valid attached
child per partition); (d) the tree check itself reports a parent created
alone from the catalogue definition (``ON ONLY``) as incomplete; (e) a real
DVF publication at 107 through the runner recreates no retired index, and the
downgrade after it rebuilds complete trees on the published partitions.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_index_removal.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.etl_dvf.pipeline import DvfPipeline
from tests.integration.test_departmental_scope import FakeDvfSource, run_dvf

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (BACKEND / "tests" / "fixtures" / "index_removal_107.json").read_text(
        encoding="utf-8"
    )
)
RETIRES = FIXTURE["retires"]
GARDES = FIXTURE["gardes"]
PARTITIONNES = [entry for entry in RETIRES if entry["partitionne"]]
HEAD = "107_drop_unused_indexes"
PRECEDENTE = "106_eligibilite_fixe_par_immeuble"
TABLES = (
    "mutations",
    "mutations_grain",
    "mutation_parcelles",
    "diagnostics_dpe",
    "foncier_pm",
)
DVF_TABLES = ("mutations", "mutations_grain", "mutation_parcelles")
PUBLIES = {"44", "85"}
# Kept indexes next to the retired DVF ones (registry of plan 04.1-23): the
# unique (id_mutation, id_parcelle) serves the reads by id_mutation.
KEPT_DVF = {
    "idx_mutations_id_mutation": "mutations",
    "idx_mutations_grain_position_geog": "mutations_grain",
    "idx_mutation_parcelles_id_parcelle": "mutation_parcelles",
    "mutation_parcelles_id_mutation_id_parcelle_key": "mutation_parcelles",
}
# Pre-existing drift, unrelated to migration 107 and recorded in the 04.1-24
# SUMMARY (never masked): the DVF reconstruction restores the constraints of
# mutations_grain and mutation_parcelles on staging under staging names and
# never renames them back; catalogue-index-23.json shows the same names on
# the work base. Pinned exactly, so a fix or a new drift fails case (e).
NOMS_APRES_RECONSTRUCTION = {
    "mutations_grain_pkey": "mutations_grain_staging_pkey",
    "mutation_parcelles_pkey": "mutation_parcelles_staging_pkey",
    "mutation_parcelles_id_mutation_id_parcelle_key": (
        "mutation_parcelles_staging_id_mutation_id_parcelle_key"
    ),
}

# "CREATE [UNIQUE] INDEX <name> ON [ONLY] <table> " -- the part a partition
# child differs by from its parent; what follows ("USING ...") is the shape.
_TETE = re.compile(r"^CREATE (UNIQUE )?INDEX \S+ ON (?:ONLY )?\S+ ")

_PARENTS_SQL = """
    SELECT i.relname, t.relname, pg_get_indexdef(i.oid), x.indisvalid, i.oid
    FROM pg_index x
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_class t ON t.oid = x.indrelid
    WHERE t.relnamespace = 'public'::regnamespace AND t.relname = ANY(%s)
"""
_ENFANTS_SQL = """
    SELECT t.relname, i.relname, pg_get_indexdef(i.oid), x.indisvalid
    FROM pg_inherits h
    JOIN pg_class i ON i.oid = h.inhrelid
    JOIN pg_index x ON x.indexrelid = i.oid
    JOIN pg_class t ON t.oid = x.indrelid
    WHERE h.inhparent = %s
"""
_PARTITIONS_SQL = """
    SELECT c.relname FROM pg_inherits h JOIN pg_class c ON c.oid = h.inhrelid
    WHERE h.inhparent = to_regclass(%s)
"""
_INDEX_DE_TABLE_SQL = """
    SELECT i.relname, pg_get_indexdef(i.oid)
    FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
    WHERE x.indrelid = to_regclass(%s)
"""
_INDEX_SQL = """
    SELECT x.indisvalid, pg_get_indexdef(i.oid), i.oid
    FROM pg_class i JOIN pg_index x ON x.indexrelid = i.oid
    WHERE i.oid = to_regclass(%s)
"""


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def _forme(definition: str) -> str:
    """The shape of an index, the fixture's rule: from " USING " on."""
    return "USING " + definition.split(" USING ", 1)[1]


def _normalise(definition: str) -> str:
    return _TETE.sub(
        lambda m: f"CREATE {m[1] or ''}INDEX <index> ON <partition> ", definition
    )


def _partitions(conn: psycopg.Connection, table: str) -> set[str]:
    rows = conn.execute(_PARTITIONS_SQL, (f"public.{table}",)).fetchall()
    return {row[0] for row in rows}


def _etat(conn: psycopg.Connection) -> dict[str, dict]:
    """Parent-level indexes of TABLES with, for partitioned ones, their tree.

    Children are keyed by partition, their definition normalised (index and
    partition names replaced) so a rebuilt tree compares equal whatever names
    PostgreSQL gave the children.
    """
    etat: dict[str, dict] = {}
    for nom, table, definition, valide, oid in conn.execute(
        _PARENTS_SQL, (list(TABLES),)
    ).fetchall():
        enfants: dict[str, list[tuple[str, bool]]] = {}
        for partition, _nom, d, v in conn.execute(_ENFANTS_SQL, (oid,)).fetchall():
            enfants.setdefault(partition, []).append((_normalise(d), v))
        etat[nom] = {
            "table": table,
            "definition": definition,
            "valide": valide,
            "enfants": {p: sorted(e) for p, e in enfants.items()},
        }
    return etat


def _noms(etat: dict[str, dict], table: str) -> set[str]:
    return {nom for nom, entree in etat.items() if entree["table"] == table}


def _arbre_incomplet(conn: psycopg.Connection, table: str, index: str) -> list[str]:
    """Defects of the partition tree of *index*; empty means complete.

    Complete: the parent is valid and every partition of *table* (read by
    ``pg_inherits`` on the table) carries exactly one attached index, valid,
    of the parent's shape.
    """
    row = conn.execute(_INDEX_SQL, (f"public.{index}",)).fetchone()
    if row is None:
        return [f"{index}: absent"]
    valide, definition, oid = row
    defauts = [] if valide else [f"{index}: invalid parent"]
    enfants: dict[str, list[tuple[str, bool]]] = {}
    for partition, _nom, d, v in conn.execute(_ENFANTS_SQL, (oid,)).fetchall():
        enfants.setdefault(partition, []).append((d, v))
    partitions = _partitions(conn, table)
    if not partitions:
        defauts.append(f"{table}: no partition")
    for partition in sorted(partitions):
        attaches = enfants.get(partition, [])
        if len(attaches) != 1:
            defauts.append(f"{partition}: {len(attaches)} attached indexes")
        for d, v in attaches:
            if not v:
                defauts.append(f"{partition}: invalid child")
            if _forme(d) != _forme(definition):
                defauts.append(f"{partition}: child shape {_forme(d)}")
    return defauts


def _formes(conn: psycopg.Connection, table: str) -> list[tuple[str, str, str]]:
    """(relation, index, shape) of every index on *table* and its partitions."""
    relations = [table, *sorted(_partitions(conn, table))]
    return [
        (relation, nom, _forme(definition))
        for relation in relations
        for nom, definition in conn.execute(
            _INDEX_DE_TABLE_SQL, (f"public.{relation}",)
        ).fetchall()
    ]


def _restes(conn: psycopg.Connection, entree: dict) -> list[tuple[str, str, str]]:
    """Indexes on the table of *entree* (or its partitions) carrying its name
    or its shape."""
    return [
        f
        for f in _formes(conn, entree["table"])
        if f[1] == entree["index"] or f[2] == entree["forme"]
    ]


@pytest.fixture(scope="module")
def chaine(isolated_pg_dsn):
    """106 -> 107 -> 106 -> 107 on a fresh chain, a state taken at each step."""
    dsn = isolated_pg_dsn
    cfg = _config(dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        command.upgrade(cfg, PRECEDENTE)
        with psycopg.connect(dsn) as conn:
            s106 = _etat(conn)
        command.upgrade(cfg, "head")
        with psycopg.connect(dsn) as conn:
            s107 = _etat(conn)
            restes107 = {e["index"]: _restes(conn, e) for e in RETIRES}
        command.downgrade(cfg, PRECEDENTE)
        with psycopg.connect(dsn) as conn:
            retour = _etat(conn)
            arbres = {
                e["index"]: _arbre_incomplet(conn, e["table"], e["index"])
                for e in PARTITIONNES
            }
        command.upgrade(cfg, "head")
        with psycopg.connect(dsn) as conn:
            remontee = _etat(conn)
    return {
        "dsn": dsn,
        "s106": s106,
        "s107": s107,
        "restes107": restes107,
        "retour": retour,
        "arbres": arbres,
        "remontee": remontee,
    }


def test_a_107_is_in_the_single_chain_and_follows_106():
    script = ScriptDirectory.from_config(_config("postgresql://unused"))
    # 108_dvf_grain_valeur_fonciere (plan 04.4-07) now follows HEAD.
    heads = script.get_heads()
    assert len(heads) == 1
    chain = {revision.revision for revision in script.walk_revisions("base", heads[0])}
    assert HEAD in chain
    assert script.get_revision("108_dvf_grain_valeur_fonciere").down_revision == HEAD
    assert script.get_revision(HEAD).down_revision == PRECEDENTE


def test_b_upgrade_drops_exactly_the_fixture_set(chaine):
    s106, s107 = chaine["s106"], chaine["s107"]
    assert set(s106) - set(s107) == {e["index"] for e in RETIRES}
    assert set(s107) - set(s106) == set()
    # Every remaining index keeps its definition, validity and children.
    changes = sorted(nom for nom in s107 if s107[nom] != s106[nom])
    assert changes == []
    for garde in GARDES:
        assert s107[garde["index"]]["table"] == garde["table"]
        assert s107[garde["index"]]["valide"]


@pytest.mark.parametrize("entree", RETIRES, ids=lambda e: e["index"])
def test_b_no_relation_keeps_the_retired_index_at_107(chaine, entree):
    # Neither the parent nor any partition child survives, by name or shape.
    assert chaine["restes107"][entree["index"]] == []


def test_c_downgrade_restores_106_and_upgrade_107(chaine):
    assert chaine["retour"] == chaine["s106"]
    assert chaine["remontee"] == chaine["s107"]


@pytest.mark.parametrize("entree", RETIRES, ids=lambda e: e["index"])
def test_c_downgrade_recreates_the_ct102_definition(chaine, entree):
    recree = chaine["retour"][entree["index"]]
    assert recree["table"] == entree["table"]
    assert recree["definition"] == entree["definition_ct102"]
    assert recree["valide"]
    assert bool(recree["enfants"]) == entree["partitionne"]


@pytest.mark.parametrize("entree", PARTITIONNES, ids=lambda e: e["index"])
def test_c_downgrade_rebuilds_the_whole_tree(chaine, entree):
    assert chaine["arbres"][entree["index"]] == []
    # A fresh chain: 102 departements plus default (migration 005).
    assert len(chaine["retour"][entree["index"]]["enfants"]) == 103


@pytest.mark.parametrize("entree", PARTITIONNES, ids=lambda e: e["index"])
def test_d_the_tree_check_reports_a_parent_alone_as_incomplete(chaine, entree):
    """Replaying the catalogue definition (``ON ONLY``) creates the parent
    alone: the check must call that tree incomplete, or case (c) proves
    nothing about a rollback that only restores names."""
    with psycopg.connect(chaine["dsn"]) as conn:
        assert _arbre_incomplet(conn, entree["table"], entree["index"]) == [
            f"{entree['index']}: absent"
        ]
        conn.execute(entree["definition_ct102"])
        defauts = _arbre_incomplet(conn, entree["table"], entree["index"])
        partitions = _partitions(conn, entree["table"])
        conn.rollback()
    assert f"{entree['index']}: invalid parent" in defauts
    assert {f"{p}: 0 attached indexes" for p in partitions} <= set(defauts)
    assert len(partitions) == 103


def test_e_dvf_publication_at_107_recreates_no_retired_index(
    chaine, monkeypatch, tmp_path, instance_scope
):
    dsn = chaine["dsn"]
    cfg = _config(dsn)
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "database_url", dsn)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    # Two mutations per (year, departement): floors are the unit suite's.
    monkeypatch.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))
    dvf = [e for e in RETIRES if e["table"] in DVF_TABLES]
    assert dvf, "the fixture names no DVF index: case (e) would check nothing"
    tables = sorted({e["table"] for e in dvf})

    instance_scope(dsn, ["dvf"], sorted(PUBLIES))
    result = run_dvf(monkeypatch, FakeDvfSource())
    assert result.success, result.error

    with psycopg.connect(dsn) as conn:
        apres = _etat(conn)
        restes = {e["index"]: _restes(conn, e) for e in dvf}
        assert _partitions(conn, "mutations") == {
            f"mutations_{d}" for d in (*PUBLIES, "default")
        }
    assert restes == {e["index"]: [] for e in dvf}
    for table in tables:
        attendus = {
            NOMS_APRES_RECONSTRUCTION.get(nom, nom)
            for nom in _noms(chaine["s107"], table)
        }
        assert _noms(apres, table) == attendus, table
    # The kept neighbours of the retired indexes are rebuilt by the publication.
    for garde, table in KEPT_DVF.items():
        if table in tables:
            nom = NOMS_APRES_RECONSTRUCTION.get(garde, garde)
            assert nom in _noms(apres, table), nom

    command.downgrade(cfg, PRECEDENTE)
    with psycopg.connect(dsn) as conn:
        for entree in dvf:
            assert entree["index"] in _noms(_etat(conn), entree["table"])
            if entree["partitionne"]:
                assert _arbre_incomplet(conn, entree["table"], entree["index"]) == []
                assert _partitions(conn, entree["table"]) == {
                    f"{entree['table']}_{d}" for d in (*PUBLIES, "default")
                }
    command.upgrade(cfg, "head")
    with psycopg.connect(dsn) as conn:
        assert {e["index"]: _restes(conn, e) for e in dvf} == {
            e["index"]: [] for e in dvf
        }

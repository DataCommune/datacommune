#!/usr/bin/env python3
"""Indexes the planner uses for the served queries, read-only (plans 04.1-18, 23).

Reads ``explain_statements.json`` (the served SQL rendered from the cited
module, kept equal by ``tests/test_explain_statements.py``, with public
parameters), runs ``EXPLAIN (FORMAT JSON)`` for each one (planning only,
nothing executed) with its parameters, with each of its ``variantes``
(merged on ``params``) and as a generic plan (``EXPLAIN (GENERIC_PLAN)``,
the plan a prepared statement may get), and writes, per query, the indexes
and node types of every plan. An index absent from every plan of the list
is one of the five conditions of a removal (``04.1-INDEX-EXPLAIN.md``),
never a proof alone; a plan that cannot be built is reported (``erreur``),
never read as an absence.

Where the SQL of an entry comes from (``source``):

- ``litteral`` (default): the literal passed to ``text()`` inside
  ``symbole`` of ``fichier`` (AST), ``rang``-th of them;
- ``attribut``: ``str()`` of the module object ``symbole`` (a ``text(f"…")``
  or a string composed at import);
- ``appel``: ``symbole`` is a function of the module, called with the named
  arguments of ``appel`` and a recording session (``execute`` keeps the
  statement and the parameter keys, and returns an empty result); the
  ``rang``-th statement is kept. A SQLAlchemy Core statement is compiled with
  the PostgreSQL dialect (the entry says ``"compilation": "postgresql"``); a
  psycopg statement (``%s`` / ``%(name)s``, ``"binds": "psycopg"``) gets
  ``:p1``… / ``:name`` binds;
- ``pg_matviews``: the served definition of the materialized view
  ``matview``, read from ``pg_matviews`` on the base itself.

Launch on the work base, read-only::

    wb-tool.sh --read-only python - --liste \\
        api/backend/scripts/explain_statements.json --out - \\
        < api/backend/scripts/explain_statements.py

Regenerate the ``sql`` fields from the code (workstation only)::

    DEBUG=true python -m scripts.explain_statements regenerer   # in api/backend

The connection is opened by ``_open_read_only_connection`` (asserts
``transaction_read_only = on``); only ``EXPLAIN`` without ``ANALYZE``,
``SHOW server_version``, the Alembic revision and ``pg_matviews`` are read.
A failed plan rolls the read-only transaction back before the next one.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LISTE_DEFAUT = Path(__file__).resolve().with_name("explain_statements.json")
BACKEND = Path(__file__).resolve().parent.parent
# SQLAlchemy ``:name`` binds, never the ``::type`` casts.
_BIND_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
# psycopg placeholders, ``%%`` first so an escaped percent is never a bind.
_PSYCOPG_RE = re.compile(r"%%|%\((\w+)\)s|%s")
_MATVIEW_SQL = (
    "SELECT definition FROM pg_matviews "
    "WHERE schemaname = 'public' AND matviewname = %(matview)s"
)


def en_psycopg(sql: str) -> str:
    """``:name`` binds as psycopg ``%(name)s`` placeholders (``%`` escaped)."""
    return _BIND_RE.sub(r"%(\1)s", sql.replace("%", "%%"))


def binds(sql: str) -> list[str]:
    """``:name`` binds in order of first appearance."""
    return list(dict.fromkeys(_BIND_RE.findall(sql)))


def en_generique(sql: str, types: Mapping[str, str] | None = None) -> str:
    """``:name`` binds as ``$n`` (first appearance), ``::type`` added from *types*."""
    ordre = binds(sql)
    types = types or {}

    def _sub(m: re.Match[str]) -> str:
        nom = m.group(1)
        cast = f"::{types[nom]}" if nom in types else ""
        return f"${ordre.index(nom) + 1}{cast}"

    return _BIND_RE.sub(_sub, sql)


def depuis_psycopg(sql: str) -> str:
    """psycopg placeholders as ``:name`` binds (``%s`` → ``:p1``, ``:p2``…)."""
    rang = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal rang
        if m.group(0) == "%%":
            return "%"
        if m.group(1):
            return f":{m.group(1)}"
        rang += 1
        return f":p{rang}"

    return _PSYCOPG_RE.sub(_sub, sql)


def noeuds(plan: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield plan
    for enfant in plan.get("Plans", ()):
        yield from noeuds(enfant)


def index_du_plan(explain: Any) -> dict[str, list[str]]:
    """Index names and node types of an ``EXPLAIN (FORMAT JSON)`` result."""
    racine = explain[0]["Plan"] if isinstance(explain, list) else explain["Plan"]
    tous = list(noeuds(racine))
    return {
        "index": sorted({n["Index Name"] for n in tous if "Index Name" in n}),
        "noeuds": sorted({n["Node Type"] for n in tous}),
    }


# --------------------------------------------------------------------------
# Rendering an entry from the code (workstation, tests, ``regenerer``)
# --------------------------------------------------------------------------


def litteraux(fichier: str, symbole: str) -> list[str]:
    """SQL literals passed to ``text()`` inside *symbole* of *fichier*."""
    tree = ast.parse((BACKEND / fichier).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            nom = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            nom = node.targets[0].id
        else:
            continue
        if nom == symbole:
            return [
                appel.args[0].value
                for appel in ast.walk(node)
                if isinstance(appel, ast.Call)
                and getattr(appel.func, "id", None) == "text"
                and appel.args
                and isinstance(appel.args[0], ast.Constant)
            ]
    raise LookupError(f"{symbole} absent de {fichier}")


def _module(fichier: str) -> Any:
    return importlib.import_module(fichier.removesuffix(".py").replace("/", "."))


def objet(module: Any, expression: str) -> Any:
    """Module object named by *expression*: a name, attributes and literal keys.

    ``_REQUETES[(False, True)][1]`` reads the statement a module composed at
    import; nothing but names, attributes and literal subscripts is evaluated.
    """

    def _eval(node: ast.expr) -> Any:
        if isinstance(node, ast.Name):
            return getattr(module, node.id)
        if isinstance(node, ast.Attribute):
            return getattr(_eval(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            return _eval(node.value)[ast.literal_eval(node.slice)]
        raise ValueError(f"unsupported expression: {expression}")

    return _eval(ast.parse(expression, mode="eval").body)


class _Ligne(dict):
    """A neutral row: any column reads as 0 (``int()``, ``bool()`` hold)."""

    def __missing__(self, cle: str) -> int:
        return 0

    def __getattr__(self, cle: str) -> Any:
        return self[cle]

    @property
    def _mapping(self) -> _Ligne:
        return self


class _Resultat:
    """Result of a recorded statement: the rows given to the double, else none."""

    def __init__(self, lignes: list[Mapping[str, Any]]) -> None:
        self._lignes = [_Ligne(ligne) for ligne in lignes]

    def __iter__(self) -> Iterator[_Ligne]:
        return iter(self._lignes)

    def mappings(self) -> _Resultat:
        return self

    def all(self) -> list[_Ligne]:
        return list(self._lignes)

    fetchall = all

    def fetchone(self) -> _Ligne | None:
        return self._lignes[0] if self._lignes else None

    first = fetchone

    def one(self) -> _Ligne:
        return self._lignes[0] if self._lignes else _Ligne()

    def one_or_none(self) -> _Ligne | None:
        return self.fetchone()

    def scalar(self) -> Any:
        ligne = self.fetchone()
        return next(iter(ligne.values()), None) if ligne else None

    scalar_one_or_none = scalar

    def scalars(self) -> _Resultat:
        return self


def _rendu(stmt: Any, params: Any) -> dict[str, Any]:
    """SQL with ``:name`` binds and the parameter keys of one recorded call."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.sql.elements import TextClause

    if isinstance(stmt, str):
        sql = depuis_psycopg(stmt)
        if isinstance(params, Mapping):
            cles = sorted(params)
        else:
            cles = [f"p{i + 1}" for i in range(len(params or ()))]
        return {"sql": sql, "cles": cles, "binds": "psycopg", "compilation": None}
    if isinstance(stmt, TextClause):
        cles = sorted(params) if isinstance(params, Mapping) else []
        return {"sql": stmt.text, "cles": cles, "binds": None, "compilation": None}
    compile_ = stmt.compile(dialect=postgresql.dialect())
    return {
        "sql": depuis_psycopg(str(compile_)),
        "cles": sorted(compile_.params),
        "binds": None,
        "compilation": "postgresql",
    }


class Enregistreur:
    """Records every statement; *lignes* maps a call rank to its result rows."""

    def __init__(self, lignes: Mapping[int, list[Mapping[str, Any]]] | None = None):
        self.appels: list[dict[str, Any]] = []
        self._lignes = dict(lignes or {})

    def _noter(self, stmt: Any, params: Any = None) -> _Resultat:
        self.appels.append(_rendu(stmt, params))
        return _Resultat(self._lignes.get(len(self.appels) - 1, []))


class _Bloc:
    """``begin_nested()`` / ``transaction()`` / ``cursor()`` of a double."""

    def __init__(self, double: Enregistreur) -> None:
        self._double = double

    def __enter__(self) -> Enregistreur:
        return self._double

    def __exit__(self, *_: Any) -> None:
        return None

    async def __aenter__(self) -> Enregistreur:
        return self._double

    async def __aexit__(self, *_: Any) -> None:
        return None


class SessionEnregistreuse(Enregistreur):
    """Double of an ``AsyncSession``."""

    async def execute(self, stmt: Any, params: Any = None, **_: Any) -> _Resultat:
        return self._noter(stmt, params)

    async def scalar(self, stmt: Any, params: Any = None, **_: Any) -> Any:
        return self._noter(stmt, params).scalar()

    def begin_nested(self) -> _Bloc:
        return _Bloc(self)


class ConnexionEnregistreuse(Enregistreur):
    """Double of a synchronous connection (psycopg or SQLAlchemy) and its cursor."""

    def execute(self, stmt: Any, params: Any = None, **_: Any) -> _Resultat:
        return self._noter(stmt, params)

    def cursor(self, *_: Any, **__: Any) -> _Bloc:
        return _Bloc(self)

    def transaction(self) -> _Bloc:
        return _Bloc(self)

    def rollback(self) -> None:
        return None


def appels(
    requete: Mapping[str, Any],
    lignes: Mapping[int, list[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Every statement *symbole* sends when called with ``appel``.

    An argument ``{"attribut": "<expression>"}`` is the module object it
    names (a profile, a statement template). A function that fails on the
    empty result AFTER sending its statements keeps them: what was sent is
    the served SQL; a failure before any statement is raised.
    """
    module = _module(requete["fichier"])
    fonction = objet(module, requete["symbole"])
    kwargs = {
        cle: objet(module, valeur["attribut"])
        if isinstance(valeur, Mapping) and set(valeur) == {"attribut"}
        else valeur
        for cle, valeur in requete.get("appel", {}).items()
    }
    asynchrone = inspect.iscoroutinefunction(fonction)
    double = (
        SessionEnregistreuse(lignes) if asynchrone else ConnexionEnregistreuse(lignes)
    )
    try:
        if asynchrone:
            asyncio.run(fonction(double, **kwargs))
        else:
            fonction(double, **kwargs)
    # The served code may fail on an empty result once its statements are sent.
    except Exception:
        if not double.appels:
            raise
    return double.appels


def rendre(
    requete: Mapping[str, Any],
    lignes: Mapping[int, list[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The served SQL of an entry (``sql``) and, for a call, its parameter keys."""
    source = requete.get("source", "litteral")
    if source == "litteral":
        sql = litteraux(requete["fichier"], requete["symbole"])[requete["rang"]]
        return {"sql": sql, "cles": None, "binds": None, "compilation": None}
    if source == "attribut":
        lu = objet(_module(requete["fichier"]), requete["symbole"])
        sql = lu.text if hasattr(lu, "text") else str(lu)
        return {"sql": sql, "cles": None, "binds": None, "compilation": None}
    if source == "appel":
        return appels(requete, lignes)[requete["rang"]]
    raise ValueError(f"{requete['nom']}: source {source!r} is not rendered")


def regenerer(chemin: Path = LISTE_DEFAUT) -> int:
    """Rewrite the ``sql`` field of every entry from the code."""
    liste = json.loads(chemin.read_text(encoding="utf-8"))
    changes = 0
    for requete in liste["requetes"]:
        if requete.get("source") == "pg_matviews":
            continue
        sql = rendre(requete)["sql"]
        if requete.get("sql") != sql:
            requete["sql"] = sql
            changes += 1
    chemin.write_text(
        json.dumps(liste, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{changes} entry(ies) regenerated in {chemin}")
    return 0


# --------------------------------------------------------------------------
# EXPLAIN on the base (read-only)
# --------------------------------------------------------------------------


def _premiere_valeur(ligne: Any) -> Any:
    return ligne[0] if not isinstance(ligne, Mapping) else next(iter(ligne.values()))


def _erreur(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _plan(conn: Any, sql: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Indexes and nodes of one plan, or the error (transaction rolled back)."""
    try:
        if params is None:
            ligne = conn.execute(sql).fetchone()
        else:
            ligne = conn.execute(sql, params).fetchone()
        return index_du_plan(_premiere_valeur(ligne))
    except psycopg.Error as exc:  # reported, never read as an absence
        # The read-only transaction is in a failed state: roll it back so the
        # next plan runs in a fresh one (``read_only`` stays on the connection).
        conn.rollback()
        return {"erreur": _erreur(exc)}


def _definition(conn: Any, matview: str) -> str | None:
    ligne = conn.execute(_MATVIEW_SQL, {"matview": matview}).fetchone()
    if ligne is None:
        return None
    return str(_premiere_valeur(ligne)).strip().rstrip(";")


def expliquer_une(conn: Any, requete: Mapping[str, Any]) -> dict[str, Any]:
    sortie: dict[str, Any] = {"nom": requete["nom"], "tables": requete["tables"]}
    if requete.get("source") == "pg_matviews":
        sortie["matview"] = requete["matview"]
        try:
            sql = _definition(conn, requete["matview"])
        except psycopg.Error as exc:
            conn.rollback()
            sortie.update(index=[], noeuds=[], erreur=_erreur(exc))
            return sortie
        if sql is None:
            sortie.update(index=[], noeuds=[], erreur="matview absente de la base")
            return sortie
    else:
        sql = requete["sql"]
    noms = binds(sql)
    plans: list[dict[str, Any]] = []
    if noms:
        ps = en_psycopg(sql)
        base = _plan(conn, "EXPLAIN (FORMAT JSON) " + ps, requete["params"])
        variantes = []
        for variante in requete.get("variantes", ()):
            params = {**requete["params"], **variante}
            params.pop("id", None)
            plan = _plan(conn, "EXPLAIN (FORMAT JSON) " + ps, params)
            variantes.append({"id": variante["id"], **plan})
        generique = _plan(
            conn,
            "EXPLAIN (GENERIC_PLAN, FORMAT JSON) "
            + en_generique(sql, requete.get("types")),
            None,
        )
        plans = [base, *variantes, generique]
        sortie["base"] = base
        if variantes:
            sortie["variantes"] = variantes
        sortie["generique"] = generique
    else:
        base = _plan(conn, "EXPLAIN (FORMAT JSON) " + sql, None)
        plans = [base]
        sortie["base"] = base
    sortie["index"] = sorted({i for p in plans for i in p.get("index", ())})
    sortie["noeuds"] = sorted({n for p in plans for n in p.get("noeuds", ())})
    erreurs = [p["erreur"] for p in plans if "erreur" in p]
    if "erreur" in base:
        sortie["erreur"] = base["erreur"]
    elif erreurs:
        sortie["erreurs"] = len(erreurs)
    return sortie


_PARENTS_SQL = (
    "SELECT c.relname AS index, "
    "COALESCE(r.relname, c.relname) AS parent "
    "FROM pg_class c LEFT JOIN pg_class r ON r.oid = pg_partition_root(c.oid) "
    "WHERE c.relkind IN ('i', 'I') "
    "AND c.relnamespace = 'public'::regnamespace "
    "AND c.relname = ANY(%(noms)s)"
)


def parents(conn: Any, noms: list[str]) -> dict[str, str]:
    """Partition index -> index of the partitioned table (itself otherwise).

    Plans name the index of the partition they read
    (``mutations_44_position_idx``), the register names the parent
    (``idx_mutations_position``): an absence is judged on parents only.
    """
    if not noms:
        return {}
    lignes = conn.execute(_PARENTS_SQL, {"noms": sorted(noms)}).fetchall()
    trouves = {}
    for ligne in lignes:
        if isinstance(ligne, Mapping):
            trouves[ligne["index"]] = ligne["parent"]
        else:
            trouves[ligne[0]] = ligne[1]
    # A name the catalogue does not know stays as is, visibly unresolved.
    return {nom: trouves.get(nom, nom) for nom in noms}


def _avec_parents(plan: dict[str, Any], carte: Mapping[str, str]) -> None:
    if "index" in plan:
        plan["index_parents"] = sorted({carte.get(i, i) for i in plan["index"]})


def expliquer(conn: Any, liste: Mapping[str, Any]) -> list[dict[str, Any]]:
    sorties = [expliquer_une(conn, requete) for requete in liste["requetes"]]
    carte = parents(conn, sorted({i for s in sorties for i in s["index"]}))
    for sortie in sorties:
        _avec_parents(sortie, carte)
        for cle in ("base", "generique"):
            if cle in sortie:
                _avec_parents(sortie[cle], carte)
        for variante in sortie.get("variantes", ()):
            _avec_parents(variante, carte)
    return sorties


def _sha_script() -> str | None:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return os.getenv("SCRIPT_SHA256")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "commande", nargs="?", default="expliquer", choices=["expliquer", "regenerer"]
    )
    parser.add_argument("--liste", default=str(LISTE_DEFAUT))
    parser.add_argument("--out", default="-")
    args = parser.parse_args(argv)
    if args.commande == "regenerer":
        return regenerer(Path(args.liste))

    from scripts.verify_golden_cases import (
        GoldenSecurityError,
        _open_read_only_connection,
    )

    brut = Path(args.liste).read_bytes()
    liste = json.loads(brut.decode("utf-8"))
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise GoldenSecurityError("DATABASE_URL is missing")
    conn = _open_read_only_connection(
        dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        version = _premiere_valeur(conn.execute("SHOW server_version").fetchone())
        requetes = expliquer(conn, liste)
        alembic = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    rapport = {
        "script_sha256": _sha_script(),
        "liste_sha256": hashlib.sha256(brut).hexdigest(),
        "sha_servi": os.getenv("WORKBASE_SHA"),
        "server_version": version,
        "alembic": _premiere_valeur(alembic) if alembic else None,
        "requetes": requetes,
    }
    texte = json.dumps(rapport, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out == "-":
        print(texte)
    else:
        Path(args.out).write_text(texte + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The EXPLAIN list stays the served SQL (plans 04.1-18, 23), no database."""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg
import pytest

from scripts import explain_statements as ex

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
LISTE = json.loads(ex.LISTE_DEFAUT.read_text(encoding="utf-8"))
RENDUES = [r for r in LISTE["requetes"] if r.get("source") != "pg_matviews"]
VUES = [r for r in LISTE["requetes"] if r.get("source") == "pg_matviews"]
REGENERER = "regenerate it: DEBUG=true python -m scripts.explain_statements regenerer"


def _norme(sql: str) -> str:
    return " ".join(sql.split())


@pytest.mark.parametrize("requete", RENDUES, ids=lambda r: r["nom"])
def test_each_listed_statement_is_the_served_sql(requete) -> None:
    rendu = ex.rendre(requete)
    assert _norme(requete["sql"]) == _norme(rendu["sql"]), (
        f"{requete['nom']}: the copy is not the served SQL, {REGENERER}"
    )
    if rendu["cles"] is not None:
        # The served call supplies every bind the list gives a public value
        # to (it may pass more keys: SQLAlchemy and psycopg ignore them).
        assert set(requete["params"]) <= set(rendu["cles"]), requete["nom"]
        assert set(ex.binds(rendu["sql"])) <= set(rendu["cles"]), requete["nom"]
    assert requete.get("compilation") == rendu["compilation"], requete["nom"]
    assert requete.get("binds") == rendu["binds"], requete["nom"]


def test_names_are_unique_and_sources_known() -> None:
    noms = [r["nom"] for r in LISTE["requetes"]]
    assert len(noms) == len(set(noms))
    for requete in LISTE["requetes"]:
        assert requete.get("source", "litteral") in {
            "litteral",
            "attribut",
            "appel",
            "pg_matviews",
        }, requete["nom"]


def test_every_bind_has_a_public_parameter() -> None:
    for requete in RENDUES:
        binds = set(ex._BIND_RE.findall(requete["sql"]))
        assert binds == set(requete["params"]), requete["nom"]
        assert set(requete.get("types", {})) <= binds, requete["nom"]
        ids = [v["id"] for v in requete.get("variantes", ())]
        assert len(ids) == len(set(ids)), requete["nom"]
        for variante in requete.get("variantes", ()):
            # A variant changes values, never the statement.
            assert set(variante) - {"id"} <= binds, (requete["nom"], variante["id"])


@pytest.mark.parametrize("requete", VUES, ids=lambda r: r["nom"])
def test_each_listed_view_is_a_served_materialized_view(requete) -> None:
    motif = re.compile(
        rf"CREATE MATERIALIZED VIEW (IF NOT EXISTS )?{requete['matview']}\b"
    )
    sources = (p.read_text(encoding="utf-8") for p in (BACKEND / "app").rglob("*.py"))
    assert any(motif.search(s) for s in sources), requete["matview"]
    assert "sql" not in requete and "params" not in requete


def test_dvf_par_point_is_rendered_with_its_variants() -> None:
    entree = next(r for r in LISTE["requetes"] if r["nom"] == "dvf_par_point")
    assert entree["source"] == "appel"
    assert "ORDER BY mg.position::geography" in entree["sql"]
    assert len(entree["variantes"]) >= 8


def test_recording_session_keeps_each_statement_and_its_keys() -> None:
    requete = {
        "fichier": "app/dvf/queries.py",
        "symbole": "dvf_par_point",
        "appel": {"lat": 47.0, "lon": -1.0, "annee_min": 2020},
    }
    (appel,) = ex.appels(requete)
    assert "EXTRACT(YEAR FROM mg.date_mutation) >= :annee_min" in appel["sql"]
    assert appel["cles"] == ["annee_min", "lat", "limit", "lon", "offset", "rayon_m"]


def test_psycopg_placeholders_become_binds() -> None:
    sql = "SELECT 1 FROM t WHERE a = %s AND b LIKE '%%x' AND c = %(c)s AND d = %s"
    assert ex.depuis_psycopg(sql) == (
        "SELECT 1 FROM t WHERE a = :p1 AND b LIKE '%x' AND c = :c AND d = :p2"
    )


def test_binds_become_placeholders_and_casts_stay() -> None:
    sql = "SELECT x::geography FROM t WHERE a = :a AND b LIKE '%' || :b"
    assert ex.en_psycopg(sql) == (
        "SELECT x::geography FROM t WHERE a = %(a)s AND b LIKE '%%' || %(b)s"
    )


def test_generic_plan_numbers_binds_by_first_appearance() -> None:
    sql = "SELECT x::geography FROM t WHERE b = :b AND a = :a AND c = :b"
    assert ex.en_generique(sql, {"a": "text"}) == (
        "SELECT x::geography FROM t WHERE b = $1 AND a = $2::text AND c = $1"
    )


def test_indexes_and_nodes_are_read_from_every_level_of_the_plan() -> None:
    plan = [
        {
            "Plan": {
                "Node Type": "Limit",
                "Plans": [
                    {
                        "Node Type": "Bitmap Heap Scan",
                        "Plans": [
                            {
                                "Node Type": "Bitmap Index Scan",
                                "Index Name": "idx_parcelles_geometrie",
                            }
                        ],
                    },
                    {"Node Type": "Index Scan", "Index Name": "idx_b"},
                ],
            }
        }
    ]
    assert ex.index_du_plan(plan) == {
        "index": ["idx_b", "idx_parcelles_geometrie"],
        "noeuds": ["Bitmap Heap Scan", "Bitmap Index Scan", "Index Scan", "Limit"],
    }


# --------------------------------------------------------------------------
# Simulated connection: variants, generic plan, errors, partition parents
# --------------------------------------------------------------------------


class _Resultat:
    def __init__(self, ligne=None, lignes=()):
        self._ligne, self._lignes = ligne, list(lignes)

    def fetchone(self):
        return self._ligne

    def fetchall(self):
        return self._lignes


def _plan_json(index: str, noeud: str = "Index Scan") -> list[dict]:
    return [{"Plan": {"Node Type": noeud, "Index Name": index}}]


class _Connexion:
    """Plans by parameter value; a failed statement poisons the transaction."""

    def __init__(self, generique_ko: bool = False, vues: dict | None = None):
        self.envoyes: list[str] = []
        self.generique_ko = generique_ko
        self.vues = vues or {}
        self.en_echec = False
        self.rollbacks = 0

    def rollback(self):
        self.en_echec = False
        self.rollbacks += 1

    def execute(self, sql, params=None):
        self.envoyes.append(sql)
        if self.en_echec:
            raise psycopg.errors.InFailedSqlTransaction(
                "current transaction is aborted"
            )
        if sql.startswith("SELECT definition FROM pg_matviews"):
            return _Resultat(
                (self.vues[params["matview"]],)
                if params["matview"] in self.vues
                else None
            )
        if sql.startswith("SELECT c.relname AS index"):
            return _Resultat(
                lignes=[
                    (
                        n,
                        "idx_mutations_grain_position"
                        if n.startswith("mutations_grain_")
                        else n,
                    )
                    for n in params["noms"]
                ]
            )
        if sql.startswith("EXPLAIN (GENERIC_PLAN"):
            if self.generique_ko:
                self.en_echec = True
                raise psycopg.errors.UndefinedFunction(
                    "could not determine data type of parameter $3"
                )
            assert "$1" in sql and "%(" not in sql
            return _Resultat((_plan_json("idx_generique"),))
        assert sql.startswith("EXPLAIN (FORMAT JSON) ")
        if params is None:
            return _Resultat((_plan_json("idx_vue", "Seq Scan"),))
        if params.get("rayon_m", 0) >= 5000:
            return _Resultat((_plan_json("mutations_grain_44_position_idx"),))
        return _Resultat((_plan_json("idx_geog"),))


_ENTREE = {
    "nom": "point",
    "tables": ["mutations_grain"],
    "sql": "SELECT 1 FROM mutations_grain WHERE ST_DWithin(p, :lon, :rayon_m)",
    "params": {"lon": -1.5, "rayon_m": 500.0},
    "variantes": [{"id": "large", "rayon_m": 5000.0}, {"id": "petit", "rayon_m": 50.0}],
}


def test_every_variant_and_the_generic_plan_are_explained_and_merged() -> None:
    conn = _Connexion()
    (sortie,) = ex.expliquer(conn, {"requetes": [_ENTREE]})
    assert [v["id"] for v in sortie["variantes"]] == ["large", "petit"]
    assert sortie["variantes"][0]["index"] == ["mutations_grain_44_position_idx"]
    assert sortie["generique"]["index"] == ["idx_generique"]
    assert sortie["index"] == [
        "idx_generique",
        "idx_geog",
        "mutations_grain_44_position_idx",
    ]
    # Only EXPLAIN (no ANALYZE) and the catalogue read are sent.
    assert all(
        s.startswith(("EXPLAIN (FORMAT JSON) ", "EXPLAIN (GENERIC_PLAN, FORMAT JSON) "))
        or s.startswith("SELECT c.relname AS index")
        for s in conn.envoyes
    )
    assert not any("ANALYZE" in s for s in conn.envoyes)


def test_partition_indexes_are_judged_by_their_parent() -> None:
    (sortie,) = ex.expliquer(_Connexion(), {"requetes": [_ENTREE]})
    assert "idx_mutations_grain_position" in sortie["index_parents"]
    assert sortie["variantes"][0]["index_parents"] == ["idx_mutations_grain_position"]
    assert sortie["base"]["index_parents"] == ["idx_geog"]


def test_a_failed_generic_plan_is_reported_and_the_next_entry_still_plans() -> None:
    conn = _Connexion(generique_ko=True)
    suivante = {**_ENTREE, "nom": "suivante", "variantes": []}
    premiere, seconde = ex.expliquer(conn, {"requetes": [_ENTREE, suivante]})
    assert premiere["generique"]["erreur"].startswith("UndefinedFunction: ")
    assert "index" not in premiere["generique"]
    assert premiere["erreurs"] == 1 and "erreur" not in premiere
    # The aborted transaction was rolled back: the next plans are real.
    assert seconde["base"]["index"] == ["idx_geog"]
    assert conn.rollbacks == 2


def test_a_matview_is_explained_from_its_served_definition_or_reported() -> None:
    conn = _Connexion(vues={"v_commune_dvf": " SELECT code FROM mutations_grain;"})
    presente, absente = ex.expliquer(
        conn,
        {
            "requetes": [
                {
                    "nom": "v1",
                    "source": "pg_matviews",
                    "matview": "v_commune_dvf",
                    "tables": ["mutations_grain"],
                },
                {
                    "nom": "v2",
                    "source": "pg_matviews",
                    "matview": "v_absente",
                    "tables": ["mutations"],
                },
            ]
        },
    )
    assert presente["index"] == ["idx_vue"] and "erreur" not in presente
    assert "EXPLAIN (FORMAT JSON) SELECT code FROM mutations_grain" in conn.envoyes
    assert absente["erreur"] == "matview absente de la base"
    assert absente["index"] == []

"""Every listed served query plans on a head schema (plans 04.1-18, 23).

``scripts/explain_statements.py`` sends ``EXPLAIN (FORMAT JSON)`` of the
served SQL copied in ``explain_statements.json``: on a base migrated to the
head, each one must plan with its public parameters, and the indexes of the
plan are read. Planner choices on this tiny base say nothing of the work base:
the decisions of ``04.1-INDEX-EXPLAIN.md`` come from the real run.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_explain_statements_sql.py
"""

import json
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts import explain_statements as ex

pytestmark = pytest.mark.destructive
BACKEND = Path(__file__).resolve().parents[2]


def _partitions_lues(plan: dict, table: str) -> set[str]:
    """Partitions of *table* whose index a plan reads, from the index names."""
    return {
        nom.split("_")[len(table.split("_"))]
        for nom in plan["index"]
        if nom.startswith(f"{table}_")
    }


def test_every_listed_statement_plans_and_reads_its_indexes(
    isolated_pg_dsn, monkeypatch
):
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    command.upgrade(cfg, "head")
    liste = json.loads(ex.LISTE_DEFAUT.read_text(encoding="utf-8"))

    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.read_only = True
        # Force index paths on the empty base: the extraction is exercised.
        conn.execute("SET enable_seqscan = off")
        resultats = ex.expliquer(conn, liste)

    assert [r["nom"] for r in resultats] == [r["nom"] for r in liste["requetes"]]
    lus = {r["nom"]: r["index"] for r in resultats}
    # Parcels are partitioned: each partition index names the parent's column.
    assert any("geometrie" in nom for nom in lus["parcelle_par_point"]), lus
    # Partition indexes are reported under their parent (pg_partition_root).
    parents = {r["nom"]: r["index_parents"] for r in resultats}
    assert "idx_parcelles_geometrie" in parents["parcelle_par_point"], parents
    # 04.10-02, parcels of a map tile: the spatial index of ``parcelles``, the
    # link index of the sales count, the spatial index of the referential.
    assert "idx_parcelles_geometrie" in parents["carte_parcelles_de_la_tuile"], parents
    assert "idx_mutation_parcelles_id_parcelle" in lus["carte_ventes_des_parcelles"], (
        lus
    )
    assert "idx_geo_dept_geom" in lus["carte_departements_de_la_tuile"], lus
    # 04.10-04, territory of a base map tile: the widened envelope is still an
    # index condition on the referential, in the plan with its parameters and
    # in the generic one a prepared statement may get.
    par_nom = {r["nom"]: r for r in resultats}
    fond = par_nom["carte_departements_du_fond"]
    assert "idx_geo_dept_geom" in fond["base"]["index"], fond
    assert "idx_geo_dept_geom" in fond["generique"]["index"], fond
    # 04.10-05, sales of a map parcel. The asked parcel is read by the
    # identifier index of ``parcelles``, never by the box of the bounds, in the
    # one partition of its departement. The parcels of the sales of a page are
    # read by the index of the unique constraint of the link table, which
    # leads with ``id_mutation``: the index on that column alone was dropped
    # by migration 107. Each one in the plan with its parameters and in the
    # generic one.
    appartenance = par_nom["carte_parcelle_dans_l_emprise"]
    liens = par_nom["carte_parcelles_des_ventes"]
    groupe = par_nom["carte_parcelles_du_groupe"]
    for plan in (appartenance["base"], appartenance["generique"]):
        assert plan["index_parents"] == ["idx_parcelles_id_parcelle"], plan
    assert _partitions_lues(appartenance["base"], "parcelles") == {"85"}, appartenance
    for plan in (liens["base"], liens["generique"]):
        assert plan["index"] == ["mutation_parcelles_id_mutation_id_parcelle_key"], plan
    # The parcels of a group: the identifier index too, in the generic plan
    # and with two departements. With one departement this base, which holds
    # no row and no statistic, rates a list of one departement above a list of
    # two identifiers and reads the primary key of the one partition left; the
    # same statement on a base with statistics reads the identifier index
    # (``test_carte_ventes_contract.py``). Never the index of the geometry.
    for plan in (groupe["generique"], *groupe["variantes"]):
        assert plan["index_parents"] == ["idx_parcelles_id_parcelle"], plan
    assert set(groupe["index_parents"]) <= {
        "idx_parcelles_id_parcelle",
        "parcelles_pkey",
    }, groupe
    # The bound list of departements prunes at planning: one departement, the
    # indexes of one partition; two, of two; never those of every partition.
    for nom, table in (
        ("carte_parcelles_de_la_tuile", "parcelles"),
        ("carte_ventes_des_parcelles", "mutations_grain"),
        ("carte_parcelles_du_groupe", "parcelles"),
    ):
        tous = _partitions_lues(par_nom[nom]["generique"], table)
        un = _partitions_lues(par_nom[nom]["base"], table)
        (deux,) = (_partitions_lues(v, table) for v in par_nom[nom]["variantes"])
        assert len(un) == 1 and len(deux) == 2 and un < deux < tous, (nom, un, deux)
    vues = {r["nom"] for r in liste["requetes"] if r.get("source") == "pg_matviews"}
    for resultat in resultats:
        if resultat["nom"] in vues:
            # A view absent from this base is reported, never a crash.
            assert resultat["noeuds"] or resultat["erreur"], resultat
            continue
        # Every served statement plans with its parameters and variants.
        assert "erreur" not in resultat, resultat
        assert resultat["noeuds"], resultat["nom"]
        for variante in resultat.get("variantes", ()):
            assert "erreur" not in variante, (resultat["nom"], variante)
        assert "generique" in resultat or not resultat.get("variantes"), resultat

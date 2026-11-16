"""Departement pages against PostGIS (plan 04.7-10, decisions 1 to 4).

The fixture follows the PLAN: 44 and 85 hosted and imported, 29 hosted but not
imported, 22 not hosted, 75 with 75056, 75101 and 75112; sales of Nantes and
Saint-Herblain, one straddling both, one straddling 75101 and 75112, one at
75105 (absent from geo_arrondissements), one at an orphan code, one out of the
window; DPE documents of known and unknown class,
one without commune code, one at an orphan code and a tombstone; census rows
of 75056 and of its arrondissements, two vintages in 85; the import journal
with ``effective_departments`` without 29 and a dataset without a departement
perimeter (fallback on the ``scope_snapshot`` of its last success).

The view is proven both ways it exists: ``migration`` (the frozen statements of
migration 112) and ``reconstruction`` (the application DDL, as every commune
source publication rebuilds it); each departement figure is checked against
the commune pages (``v_commune_dvf``) or an oracle per commune.

    pwsh -NoProfile -Command "& ./scripts/test-postgis.ps1 -Paths
        tests/integration/test_departement_synthese.py; exit $LASTEXITCODE"
"""

from __future__ import annotations

import importlib.util
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.exceptions import register_exception_handlers
from app.common.geo import expand_code_insee
from app.commune.matview_ddl import (
    commune_view_drift,
    departement_statements,
    recreate_commune_synthese,
)
from app.database import get_db
from app.zone.router import router as zone_router
from tests.integration.instance_scope_fixture import (
    HOSTABLE_KEYS,
    install_instance_scope,
)

# ``migrated`` (and the ``migrated_dsn`` it needs) are fixtures: imported for
# pytest, not called.
from tests.integration.test_commune_coverage_contract import (  # noqa: F401
    _engine,
    _migrate,
    migrated,
    migrated_dsn,
)

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

_SQUARE = "ST_Multi(ST_SetSRID(ST_MakeEnvelope(2.25, 48.80, 2.40, 48.85), 4326))"
POSITION = "ST_SetSRID(ST_MakePoint(2.35, 48.83), 4326)"
# The latest sale of the fixture: the window is 2024-06-30 -> 2025-06-30.
SALE_DAY = date(2025, 6, 30)
OLD_DAY = date(2024, 6, 1)
HOSTED = ("29", "44", "75", "85")

DEPARTEMENTS = (
    ("22", "Côtes-d'Armor"),
    ("29", "Finistère"),
    ("2A", "Corse-du-Sud"),
    ("44", "Loire-Atlantique"),
    ("75", "Paris"),
    ("85", "Vendée"),
)
COMMUNES = (
    ("44109", "Nantes", "44", 320000),
    ("44162", "Saint-Herblain", "44", 46000),
    ("85191", "La Roche-sur-Yon", "85", 55000),
    ("85047", "Challans", "85", 21000),
    ("29019", "Brest", "29", 140000),
    ("22278", "Saint-Brieuc", "22", 44000),
    ("75056", "Paris", "75", 2100000),
    ("2A004", "Ajaccio", "2A", 70000),
)
ARRONDISSEMENTS = (
    ("75101", "Paris 1er Arrondissement", 16000),
    ("75112", "Paris 12e Arrondissement", 140000),
)
# Price per m2 of each sale (valeur_fonciere / 50 m2).
NANTES_FLATS = (3000, 3100, 3200, 3300, 3400, 3500)
HERBLAIN_FLATS = (2800, 2900)
# 2800 2900 3000 3100 3200 3300 3400 3500: the median of the departement.
MEDIAN_44_FLATS = 3150
DPE_DAYS = (date(2021, 7, 1), date(2026, 9, 30))


def _geo(conn) -> None:
    conn.execute("TRUNCATE geo_arrondissements")
    conn.execute("DELETE FROM geo_communes")
    conn.execute("DELETE FROM geo_departements")
    for code, nom in DEPARTEMENTS:
        conn.execute(
            "INSERT INTO geo_departements (code, nom, geometry) "
            f"VALUES (%s, %s, {_SQUARE})",
            (code, nom),
        )
    for code, nom, dept, population in COMMUNES:
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, population, "
            f"geometry) VALUES (%s, %s, %s, %s, {_SQUARE})",
            (code, nom, dept, population),
        )
    for code, nom, population in ARRONDISSEMENTS:
        conn.execute(
            "INSERT INTO geo_arrondissements (code, nom, departement_code, "
            f"commune_code, population, geometry) VALUES "
            f"(%s, %s, '75', '75056', %s, {_SQUARE})",
            (code, nom, population),
        )


def _sale(conn, ident, code, per_m2, *, kind="appartement", day=SALE_DAY, lines=()):
    """One mutation of the grain (50 m2), and its lines when it spans communes."""
    dept = code[:2]
    conn.execute(
        f"""INSERT INTO mutations_grain (
            code_departement, id_mutation, date_mutation, classe_mutation,
            nb_lignes, nb_dispositions, nb_communes, nb_parcelles,
            mono_local, est_vente, valeur_fonciere, surface_bati_totale,
            code_commune, position)
        VALUES (%s, %s, %s, %s, 1, 1, %s, 1, true, true, %s, 50, %s, {POSITION})""",
        (
            dept,
            ident,
            day,
            f"residentiel_{kind}",
            max(1, len(lines)),
            per_m2 * 50,
            code,
        ),
    )
    for line in lines:
        conn.execute(
            "INSERT INTO mutations (code_departement, id_mutation, date_mutation, "
            "code_commune) VALUES (%s, %s, %s, %s)",
            (dept, ident, day, line),
        )


def _sales(conn) -> None:
    for index, per_m2 in enumerate(NANTES_FLATS):
        _sale(conn, f"NANTES-A{index}", "44109", per_m2)
    for index in range(4):
        _sale(conn, f"NANTES-M{index}", "44109", 2500, kind="maison")
    for index, per_m2 in enumerate(HERBLAIN_FLATS):
        _sale(conn, f"HERBLAIN-A{index}", "44162", per_m2)
    # On two communes of 44: retained by neither page, hence not by 44.
    _sale(conn, "NANTES-HERBLAIN", "44109", 9000, lines=("44109", "44162"))
    # A code absent from the referential: in no departement.
    _sale(conn, "ORPHELIN", "44999", 9000)
    # Before the window: out of every statistic.
    _sale(conn, "ANCIENNE", "44109", 9000, day=OLD_DAY)
    for index in range(5):
        _sale(conn, f"PARIS12-{index}", "75112", 8000)
    for index in range(2):
        _sale(conn, f"PARIS1-{index}", "75101", 12000)
    # On two arrondissements of Paris: counted once by Paris and by the 75.
    _sale(conn, "PARIS-1-12", "75101", 9000, lines=("75101", "75112"))
    # Reaching another commune: excluded as everywhere.
    _sale(conn, "PARIS-AUTRE", "75112", 20000, lines=("75112", "92050"))
    # At an arrondissement geo_arrondissements does not hold (the referential
    # loads them per perimeter): Paris's page counts it, so does the 75.
    _sale(conn, "PARIS5", "75105", 10000)
    for index in range(5):
        _sale(conn, f"ROCHE-A{index}", "85191", 2000)
    _sale(conn, "ROCHE-M", "85191", 1800, kind="maison")
    _sale(conn, "CHALLANS-M", "85047", 1900, kind="maison")
    # Rows of departements the API does not serve as figures (29 not
    # imported, 22 not hosted): the state wins over the table.
    _sale(conn, "BREST-A", "29019", 2500)
    _sale(conn, "BRIEUC-A", "22278", 2000)


def _document(conn, numero, code, etiquette, *, day=DPE_DAYS[1]):
    conn.execute(
        "INSERT INTO diagnostics_dpe (numero_dpe, code_departement, code_insee, "
        "etiquette_dpe, date_etablissement_dpe) VALUES (%s, %s, %s, %s, %s)",
        (numero, (code or "44")[:2], code, etiquette, day),
    )


# A tombstone, retired by a complete source scope, its description purged (the
# shape the schema accepts, as tests/integration/test_adresse_contract.py).
_RETIRED_OBSERVATIONS = json.dumps(
    [
        {
            "source_slug": "dpe03existant",
            "department": "44",
            "source_version": "a" * 64,
            "observed_at": "2026-09-25T20:30:00Z",
            "last_seen_at": "2026-09-20T20:30:00Z",
            "state": "absent_from_complete_scope",
            "import_id": "import-synthetique",
        }
    ]
)
_NO_SUCCESSOR = json.dumps(
    {"replacement_state": "no_successor_known", "graph_issues": [], "date_issues": []}
)


def _tombstone(conn, numero, code) -> None:
    conn.execute(
        "INSERT INTO diagnostics_dpe (numero_dpe, code_departement, code_insee, "
        "source_state, source_snapshot_at, source_observations, is_tombstone, "
        "lifecycle_facts) VALUES (%s, %s, %s, 'absent_from_complete_scope', "
        "'2026-09-25T20:30:00Z', %s::jsonb, true, %s::jsonb)",
        (numero, code[:2], code, _RETIRED_OBSERVATIONS, _NO_SUCCESSOR),
    )


def _documents(conn) -> None:
    for index, etiquette in enumerate(("A", "B", "F", "G", "G", None)):
        day = DPE_DAYS[0] if index == 0 else DPE_DAYS[1]
        _document(conn, f"NANTES-{index}", "44109", etiquette, day=day)
    # Out of the documentary population: a tombstone (the migrated schema
    # admits no explicitly_deactivated state; the shared fragment excludes it).
    _tombstone(conn, "NANTES-TOMBE", "44109")
    _document(conn, "HERBLAIN-C", "44162", "C")
    _document(conn, "HERBLAIN-F", "44162", "F")
    _document(conn, "ORPHELIN", "44999", "G")
    _document(conn, "SANS-COMMUNE", None, "G")
    _document(conn, "PARIS", "75056", "A")
    _document(conn, "PARIS12-1", "75112", "B")
    _document(conn, "PARIS12-2", "75112", "B")
    _document(conn, "PARIS1", "75101", "G")
    # At an arrondissement absent from geo_arrondissements, as the sale PARIS5.
    _document(conn, "PARIS5", "75105", "C")
    _document(conn, "ROCHE", "85191", "D")


def _demographie(conn) -> None:
    conn.execute(
        """INSERT INTO demographie
            (code_insee, nom_commune, code_departement, population, annee)
        VALUES ('44109', 'Nantes', '44', 320000, 2022),
               ('44109', 'Nantes', '44', 300000, 2019),
               ('44162', 'Saint-Herblain', '44', 46000, 2022),
               ('44999', 'Orpheline', '44', 1000, 2022),
               ('75056', 'Paris', '75', 2100000, 2022),
               ('75101', 'Paris 1er Arrondissement', '75', 16000, 2022),
               ('75112', 'Paris 12e Arrondissement', '75', 140000, 2022),
               ('85191', 'La Roche-sur-Yon', '85', 55000, 2022),
               ('85047', 'Challans', '85', 21000, 2021),
               ('29019', 'Brest', '29', 140000, 2022)"""
    )


def _run(conn, dataset, status, completed, departements=None) -> None:
    snapshot = (
        None
        if departements is None
        else json.dumps({"datasets": [dataset], "departements": departements})
    )
    conn.execute(
        "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at, "
        "scope_snapshot) VALUES (%s, %s, %s, %s, %s::jsonb)",
        (dataset, status, completed, completed, snapshot),
    )


# dpe is published (dataset_metadata, with its perimeter) at 23:58 in Paris on
# 30 October; its run is marked success after the cleanup, at 00:05 on the
# 31st (CET). The perimeter is dated by its own publication (review Codex n. 3).
DPE_PUBLISHED = datetime(2026, 10, 30, 22, 58, tzinfo=UTC)
DPE_RUN_SUCCESS = datetime(2026, 10, 30, 23, 5, tzinfo=UTC)


def _journal(conn) -> None:
    """Imports: dvf on 44/75/85, dpe on 44/75, demographie by its snapshot."""
    conn.execute("TRUNCATE etl_runs")
    conn.execute("DELETE FROM dataset_metadata")
    for dataset, effective, published in (
        ("dvf", ["44", "75", "85"], datetime(2026, 10, 20, 9, 58, tzinfo=UTC)),
        ("dpe", ["44", "75"], DPE_PUBLISHED),
        ("demographie", None, datetime(2026, 10, 25, 7, 58, tzinfo=UTC)),
    ):
        conn.execute(
            "INSERT INTO dataset_metadata (dataset_name, status, last_updated_at, "
            "effective_departments) VALUES (%s, 'disponible', %s, %s)",
            (dataset, published, effective),
        )
    _run(conn, "dvf", "success", datetime(2026, 10, 20, 10, 0, tzinfo=UTC))
    _run(conn, "dpe", "success", DPE_RUN_SUCCESS)
    # demographie names no departement perimeter: its last success's snapshot
    # is read -- not an older success nor a later failure covering the 29.
    _run(
        conn,
        "demographie",
        "success",
        datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        ["29", "44", "75", "85"],
    )
    _run(
        conn,
        "demographie",
        "success",
        datetime(2026, 10, 25, 8, 0, tzinfo=UTC),
        ["44", "75", "85"],
    )
    _run(
        conn,
        "demographie",
        "failed",
        datetime(2026, 10, 28, 8, 0, tzinfo=UTC),
        ["29", "44", "75", "85"],
    )


def _fixture(conn) -> None:
    install_instance_scope(conn, sorted(HOSTABLE_KEYS), HOSTED)
    conn.execute(
        "TRUNCATE mutations, mutations_grain, diagnostics_dpe, demographie, "
        "matview_metadata"
    )
    _geo(conn)
    _sales(conn)
    _documents(conn)
    _demographie(conn)
    _journal(conn)


def _migration_112():
    path = BACKEND / "migrations" / "versions" / "112_departement_synthese.py"
    spec = importlib.util.spec_from_file_location("migration_112", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(conn, construction: str) -> None:
    if construction == "reconstruction":
        recreate_commune_synthese(conn)
    else:
        # The module database keeps the views the last case built: put back the
        # definitions of migration 112 (its frozen statements).
        for statement in _migration_112().STATEMENTS:
            conn.execute(statement)


def _rows(conn) -> dict[str, dict]:
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT code_departement, to_jsonb(v) FROM v_departement_synthese v"
        )
    }


@pytest.fixture(params=["migration", "reconstruction"])
def built(request, migrated):  # noqa: F811
    conn, dsn, _ = migrated
    _fixture(conn)
    _build(conn, request.param)
    return conn, dsn, _rows(conn)


@pytest.fixture
def served(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    _fixture(conn)
    recreate_commune_synthese(conn)
    return conn, dsn


@asynccontextmanager
async def _api(dsn):
    engine = _engine(dsn, None)
    sessions = async_sessionmaker(engine)
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(zone_router)

    async def database():
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(application), base_url="http://fixture"
        ) as client:
            yield client
    finally:
        await engine.dispose()


def _communes_of(conn, departement) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT code FROM geo_communes WHERE departement_code = %s ORDER BY code",
            (departement,),
        )
    ]


# ------------------------------------------------- sums and masking (D1) --


@pytest.mark.parametrize("departement", ["44", "75", "85"])
def test_the_sales_of_a_departement_are_the_sum_of_its_commune_pages(
    built, departement
):
    conn, _, rows = built
    pages = conn.execute(
        """SELECT sum(nb_mutations_12m_maison), sum(nb_mutations_12m_appartement)
        FROM v_commune_dvf WHERE code_insee = ANY(%s)""",
        (_communes_of(conn, departement),),
    ).fetchone()
    row = rows[departement]
    assert (row["nb_mutations_12m_maison"], row["nb_mutations_12m_appartement"]) == (
        pages[0],
        pages[1],
    )


def test_44_keeps_the_sales_of_one_commune_in_the_window(built):
    _, _, rows = built
    # Nantes 6 + Saint-Herblain 2; neither the straddling sale, the orphan code
    # nor the sale before the window.
    assert rows["44"]["nb_mutations_12m_appartement"] == 8
    assert rows["44"]["prix_m2_median_appartement"] == MEDIAN_44_FLATS
    assert (rows["44"]["fenetre_debut"], rows["44"]["fenetre_fin"]) == (
        "2024-06-30",
        "2025-06-30",
    )


def test_the_sale_on_two_arrondissements_counts_once(built):
    conn, _, rows = built
    # 5 at 75112, 2 at 75101, the straddling one and the one at 75105 (absent
    # from geo_arrondissements); never the arrondissement rows added to the
    # commune.
    assert rows["75"]["nb_mutations_12m_appartement"] == 9
    (paris,) = conn.execute(
        "SELECT nb_mutations_12m_appartement FROM v_commune_dvf "
        "WHERE code_insee = '75056'"
    ).fetchone()
    assert paris == 9


def test_orphan_codes_belong_to_no_departement(built):
    conn, _, rows = built
    # The orphan sale has its own row in the commune view, and none here.
    assert conn.execute(
        "SELECT nb_mutations_12m FROM v_commune_dvf WHERE code_insee = '44999'"
    ).fetchone() == (1,)
    total = sum(row["dpe_total"] or 0 for row in rows.values())
    # 8 retained documents in 44, 5 in Paris, 1 in 85: neither the orphan nor
    # the one without a commune code, nor the tombstone.
    assert total == 14
    assert set(rows) == {"22", "29", "44", "75", "85"}


def test_houses_of_44_under_the_threshold_keep_their_count(built):
    _, _, rows = built
    assert rows["44"]["nb_mutations_12m_maison"] == 4
    assert rows["44"]["prix_m2_median_maison"] is None


@pytest.mark.parametrize("departement", ["44", "75"])
def test_the_dpe_of_a_departement_is_the_sum_of_a_commune_oracle(built, departement):
    conn, _, rows = built
    oracle = {"total": 0, **dict.fromkeys("ABCDEFG", 0)}
    for commune in _communes_of(conn, departement):
        for etiquette, count in conn.execute(
            """SELECT etiquette_dpe, count(*) FROM diagnostics_dpe
            WHERE code_insee = ANY(%s) AND NOT is_tombstone
              AND source_state NOT IN (
                  'absent_from_complete_scope', 'explicitly_deactivated')
            GROUP BY etiquette_dpe""",
            (expand_code_insee(commune),),
        ):
            oracle["total"] += count
            if etiquette in oracle:
                oracle[etiquette] += count
    row = rows[departement]
    assert row["dpe_total"] == oracle["total"]
    for classe in "ABCDEFG":
        assert row[f"dpe_{classe.lower()}"] == oracle[classe], classe


def test_the_classes_add_up_to_the_known_classes(built):
    _, _, rows = built
    for code, row in rows.items():
        if row["dpe_total"] is None:
            continue
        classes = sum(row[f"dpe_{c}"] for c in "abcdefg")
        assert classes == row["dpe_classes_connues"], code
        assert classes + row["dpe_inconnue"] == row["dpe_total"], code
    assert rows["44"]["dpe_inconnue"] == 1
    assert float(rows["44"]["dpe_part_fg_pct"]) == 57.1  # 4 F or G of 7 known
    assert (rows["44"]["dpe_date_min"], rows["44"]["dpe_date_max"]) == (
        DPE_DAYS[0].isoformat(),
        DPE_DAYS[1].isoformat(),
    )


def test_the_population_of_75_counts_paris_once(built):
    _, _, rows = built
    assert rows["75"]["population"] == 2100000
    assert rows["75"]["population_communes"] == 1
    # The latest vintage of each commune, never an older row nor an orphan.
    assert rows["44"]["population"] == 366000
    assert rows["44"]["annee_population"] == 2022


def test_the_year_of_85_is_null_when_its_vintages_differ(built):
    _, _, rows = built
    assert rows["85"]["population"] == 76000
    assert rows["85"]["annee_population"] is None


def test_the_view_has_one_row_per_departement(built):
    conn, _, _ = built
    assert (
        conn.execute(
            "SELECT code_departement FROM v_departement_synthese "
            "GROUP BY code_departement HAVING count(*) > 1"
        ).fetchall()
        == []
    )


def test_rebuild_duration_of_the_chain_is_measured(served, capsys):
    """T-09: the narrow view's share of the chain rebuild (fixture scale).

    A measurement written to the SUMMARY, not a gate: the chain includes the
    view, and fixture timings say nothing of the national rebuild.
    """
    conn, _ = served
    started = time.perf_counter()
    recreate_commune_synthese(conn)
    chain = time.perf_counter() - started
    started = time.perf_counter()
    for statement in departement_statements():
        conn.execute(statement)
    view = time.perf_counter() - started
    with capsys.disabled():
        print(f"\nREBUILD chain={chain:.3f}s departement_view={view:.3f}s (fixture)")


# ---------------------------------------------------- served states (D2) --


async def test_44_serves_its_key_figures(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/44")

    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    assert (body["code"], body["nom"], body["region"]) == (
        "44",
        "Loire-Atlantique",
        "Pays de la Loire",
    )
    assert body["indexable"] is True
    assert body["derniere_importation"] == "2026-10-30"
    assert body["communes"] == {"nombre": 2, "arrondissements": 0}
    assert body["population"] == {
        "total": 366000,
        "communes_comptees": 2,
        "annee": 2022,
        "etat": None,
    }
    prix = body["prix"]
    assert prix["fenetre"] == {"debut": "2024-06-30", "fin": "2025-06-30"}
    assert prix["appartement"] == {
        "mediane_m2": MEDIAN_44_FLATS,
        "effectif": 8,
        "etat": None,
    }
    assert prix["maison"]["mediane_m2"] is None
    assert prix["maison"]["effectif"] == 4
    assert prix["maison"]["etat"]["etat"] == "insufficient_sample"
    dpe = body["dpe"]
    assert dpe["classes"] == {"A": 1, "B": 1, "C": 1, "D": 0, "E": 0, "F": 2, "G": 2}
    assert (dpe["inconnue"], dpe["total"], dpe["classes_connues"]) == (1, 8, 7)
    assert dpe["part_fg_pct"] == 57.1
    assert dpe["etat"] is None and dpe["etat_part_fg"] is None
    assert [c["code_insee"] for c in body["communes_principales"]] == [
        "44109",
        "44162",
    ]
    sources = {s["nom"] for s in response.json()["meta"]["source"]}
    assert len(sources) == 3


async def test_22_is_out_of_coverage_never_zero(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/22")

    assert response.status_code == 200, response.text
    body = response.json()["donnees"]
    out = {"etat": "out_of_coverage", "motif": "territoire_hors_perimetre"}
    assert body["population"]["etat"] == out
    assert body["prix"]["maison"] == {"mediane_m2": None, "effectif": None, "etat": out}
    assert body["prix"]["appartement"]["etat"] == out
    assert body["prix"]["fenetre"] is None
    assert body["dpe"]["etat"] == out and body["dpe"]["total"] is None
    assert (body["indexable"], body["derniere_importation"]) == (False, None)
    # The referential is served whatever the scope.
    assert body["communes"]["nombre"] == 1
    assert [c["code_insee"] for c in body["communes_principales"]] == ["22278"]


async def test_29_hosted_but_not_imported_is_not_reported(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/29")
        listing = await api.get("/v1/departements")

    body = response.json()["donnees"]
    # The view has rows of 29; nothing imported for 29 is served as a figure.
    for etat in (
        body["population"]["etat"],
        body["prix"]["appartement"]["etat"],
        body["prix"]["maison"]["etat"],
        body["dpe"]["etat"],
    ):
        assert etat["etat"] == "not_reported"
    assert body["prix"]["appartement"]["effectif"] is None
    assert (body["indexable"], body["derniere_importation"]) == (False, None)
    # Review Codex n. 1: the census of 29 is not read for its principal
    # communes either; Brest keeps the referential population, year unknown.
    assert [
        (c["code_insee"], c["population_source"], c["population_annee"])
        for c in body["communes_principales"]
    ] == [("29019", "geo_communes", None)]
    row = next(r for r in listing.json()["donnees"] if r["code"] == "29")
    assert (row["indexable"], row["derniere_importation"]) == (False, None)
    assert row["prix"]["etat"] == "heberge"


async def test_the_date_of_85_ignores_the_dataset_not_imported_there(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/85")

    body = response.json()["donnees"]
    # dpe (30 October) is not imported for 85; demographie (25 October) is,
    # through the scope_snapshot of its last success.
    assert body["derniere_importation"] == "2026-10-25"
    assert body["dpe"]["etat"]["etat"] == "not_reported"
    assert body["population"]["annee"] is None
    assert body["prix"]["maison"]["etat"]["etat"] == "insufficient_sample"
    assert body["prix"]["maison"]["effectif"] == 2


async def test_paris_lists_its_arrondissements_as_principal_communes(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/75")

    body = response.json()["donnees"]
    assert body["communes"] == {"nombre": 1, "arrondissements": 2}
    assert [
        (c["code_insee"], c["type_zone"], c["commune_parent"])
        for c in body["communes_principales"]
    ] == [("75112", "arrondissement", "75056"), ("75101", "arrondissement", "75056")]
    assert body["population"]["total"] == 2100000
    # The sale at 75105 counts though the referential does not list 75105.
    assert body["prix"]["appartement"]["effectif"] == 9


async def test_codes_are_validated_then_looked_up(served):
    _, dsn = served
    async with _api(dsn) as api:
        unknown = await api.get("/v1/departements/99")
        malformed = await api.get("/v1/departements/4A")
        corsica = await api.get("/v1/departements/2a")
        fiches_unknown = await api.get("/v1/departements/99/fiches")

    assert unknown.status_code == 404
    assert fiches_unknown.status_code == 404
    assert malformed.status_code == 422
    assert corsica.status_code == 200, corsica.text
    body = corsica.json()["donnees"]
    assert body["code"] == "2A"
    # 2A is outside the hosted departements: out of coverage, never 0.
    assert body["prix"]["appartement"]["etat"]["etat"] == "out_of_coverage"


async def test_a_stale_edge_is_a_503(served):
    conn, dsn = served
    # A rename swap: demographie is a new relation, the views still read the
    # retired one.
    conn.execute("ALTER TABLE demographie RENAME TO demographie_retiree")
    conn.execute("CREATE TABLE demographie AS SELECT * FROM demographie_retiree")
    try:
        async with _api(dsn) as api:
            response = await api.get("/v1/departements/44")
            fiches = await api.get("/v1/departements/44/fiches")
    finally:
        conn.execute("DROP TABLE demographie")
        conn.execute("ALTER TABLE demographie_retiree RENAME TO demographie")
    assert response.status_code == 503
    assert "donnees" not in response.json()
    # The sitemap list reads no view: the referential stays served.
    assert fiches.status_code == 200


async def test_a_failed_chain_rebuild_is_a_503(served):
    conn, dsn = served
    conn.execute(
        "INSERT INTO matview_metadata (matview_name, last_refresh_status) "
        "VALUES ('v_commune_synthese', 'failed')"
    )
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/44")
        out = await api.get("/v1/departements/22")

    assert response.status_code == 503
    # A departement whose three sources are out of coverage reads nothing.
    assert out.status_code == 200


# ------------------------------------------------ pages and dates (D3, D4) --


async def test_the_pages_of_paris_are_the_commune_and_its_arrondissements(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/75/fiches")

    body = response.json()["donnees"]
    assert body["fiches"] == [
        {"code_insee": "75056", "nom": "Paris", "type_zone": "commune"},
        {
            "code_insee": "75101",
            "nom": "Paris 1er Arrondissement",
            "type_zone": "arrondissement",
        },
        {
            "code_insee": "75112",
            "nom": "Paris 12e Arrondissement",
            "type_zone": "arrondissement",
        },
    ]
    assert body["derniere_importation"] == "2026-10-30"


async def test_the_pages_of_44_are_its_referential(served):
    conn, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements/44/fiches")
        fiches_85 = await api.get("/v1/departements/85/fiches")
        fiches_29 = await api.get("/v1/departements/29/fiches")

    body = response.json()["donnees"]
    assert [f["code_insee"] for f in body["fiches"]] == _communes_of(conn, "44")
    assert body["derniere_importation"] == "2026-10-30"
    assert fiches_85.json()["donnees"]["derniere_importation"] == "2026-10-25"
    assert fiches_29.json()["donnees"]["derniere_importation"] is None


async def test_the_list_says_which_departements_are_indexable(served):
    _, dsn = served
    async with _api(dsn) as api:
        response = await api.get("/v1/departements")

    rows = {row["code"]: row for row in response.json()["donnees"]}
    assert [row["code"] for row in response.json()["donnees"]] == sorted(rows)
    assert {code for code, row in rows.items() if row["indexable"]} == {
        "44",
        "75",
        "85",
    }
    assert rows["75"]["region"] == "Île-de-France"
    assert rows["44"]["derniere_importation"] == "2026-10-30"
    assert rows["85"]["derniere_importation"] == "2026-10-25"
    assert rows["22"]["prix"] == {
        "etat": "hors_couverture",
        "motif": "territoire_hors_perimetre",
        "departements": [],
    }
    assert rows["22"]["derniere_importation"] is None


# --------------------------------------------------- migration 112 itself --


def test_migration_112_roundtrip_and_no_drift(migrated_dsn):  # noqa: F811
    from alembic import command

    def present(conn) -> bool:
        return (
            conn.execute(
                "SELECT to_regclass('v_departement_synthese') IS NOT NULL"
            ).fetchone()[0]
            is True
        )

    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert present(conn)
        assert commune_view_drift(conn) == []

    _migrate(migrated_dsn, command.downgrade, "111_plm_commune_dvf")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert not present(conn)
        # Only the departement view went, and the yearly series migration 115
        # added after it: the chain of 111 is untouched.
        assert commune_view_drift(conn) == [
            "v_departement_synthese",
            "v_commune_dvf_annuel",
        ]

    _migrate(migrated_dsn, command.upgrade, "head")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert present(conn)
        assert commune_view_drift(conn) == []

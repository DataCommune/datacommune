"""Paris, Lyon, Marseille against PostGIS (plan 04.7-09, decisions 3 to 6).

DVF and DPE are coded at the arrondissement: the commune 75056 has no price of
its own, and ``/v1/commune?code_insee=75056`` used to expand to 21 rows. The
fixture below follows the PLAN: 75056 in ``geo_communes``, 75112 and 75101 in
``geo_arrondissements``, sales at 75112, at 75101 and one on both, demographie
for 75112 and the aggregate 75056 (none for 75101), DPE coded 75056 and 75112,
a witness commune outside PLM, and a scope where the 75 is not hosted.

The view is proven both ways it exists: ``migration`` (the definitions
``alembic upgrade head`` installs, migration 111 included) and
``reconstruction`` (the application DDL, as every commune-source publication
rebuilds it).

Written without a PostGIS at hand: to be played on the harness.

    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths
        tests/integration/test_plm_arrondissements.py
"""

from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.exceptions import register_exception_handlers
from app.common.publication_readiness import require_publication
from app.commune.matview_ddl import commune_view_drift
from app.commune.router import router as commune_router
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
    documents,
    migrated,
    migrated_dsn,
)

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]

# A square of 0.05 degrees: the fixture needs a geometry, not a map.
_SQUARE = "ST_Multi(ST_SetSRID(ST_MakeEnvelope({x}, 48.80, {x2}, 48.85), 4326))"
POSITION = "ST_SetSRID(ST_MakePoint(2.35, 48.83), 4326)"
SALE_DAY = "2025-06-01"
# prix_m2 of the sales (valeur_fonciere / 50 m2): easy medians to read.
PER_M2_75112, PER_M2_75101, PER_M2_STRADDLE, PER_M2_OTHER = 8000, 12000, 9000, 20000
NANTES_PER_M2 = 3000


def _geo(conn, hosted_75: bool = True) -> None:
    """Referential of the fixture: Paris and its two arrondissements, Nantes."""
    conn.execute("TRUNCATE geo_arrondissements")
    conn.execute("DELETE FROM geo_communes WHERE code IN ('75056', '44109')")
    for code, nom, dept, population in (
        ("75056", "Paris", "75", 2100000),
        ("44109", "Nantes", "44", 320000),
    ):
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, population, "
            f"geometry) VALUES (%s, %s, %s, %s, {_SQUARE.format(x=2.25, x2=2.40)})",
            (code, nom, dept, population),
        )
    if hosted_75:
        for code, nom, population, x in (
            ("75112", "Paris 12e Arrondissement", 140000, 2.30),
            ("75101", "Paris 1er Arrondissement", 16000, 2.25),
        ):
            conn.execute(
                "INSERT INTO geo_arrondissements (code, nom, departement_code, "
                "commune_code, population, geometry) VALUES "
                f"(%s, %s, '75', '75056', %s, {_SQUARE.format(x=x, x2=x + 0.05)})",
                (code, nom, population),
            )


def _sale(conn, ident, code, per_m2, nb_communes=1, lines=None) -> None:
    """One mutation of the grain (50 m2 flat), and its lines when given."""
    dept = code[:2]
    conn.execute(
        f"""INSERT INTO mutations_grain (
            code_departement, id_mutation, date_mutation, classe_mutation,
            nb_lignes, nb_dispositions, nb_communes, nb_parcelles,
            mono_local, est_vente, valeur_fonciere, surface_bati_totale,
            code_commune, position)
        VALUES (%s, %s, %s, 'residentiel_appartement', 1, 1, %s, 1, true, true,
                %s, 50, %s, {POSITION})""",
        (dept, ident, SALE_DAY, nb_communes, per_m2 * 50, code),
    )
    for line_code in lines or ():
        conn.execute(
            "INSERT INTO mutations (code_departement, id_mutation, date_mutation, "
            "code_commune) VALUES (%s, %s, %s, %s)",
            (dept, ident, SALE_DAY, line_code),
        )


def _paris(conn) -> None:
    """Sales and documents of the PLAN fixture (the window ends 2025-06-01)."""
    conn.execute("TRUNCATE mutations, mutations_grain, diagnostics_dpe, demographie")
    for index in range(5):
        _sale(conn, f"A-{index}", "75112", PER_M2_75112)
        _sale(conn, f"B-{index}", "75101", PER_M2_75101)
        _sale(conn, f"N-{index}", "44109", NANTES_PER_M2)
    # One sale on two arrondissements of Paris: one grain row (the representative
    # code is the lowest), counted once by the commune, by none of the two parts.
    _sale(conn, "STRADDLE", "75101", PER_M2_STRADDLE, 2, ["75101", "75112"])
    # One sale reaching another commune: excluded from Paris as everywhere.
    _sale(conn, "OTHER", "75112", PER_M2_OTHER, 2, ["75112", "75900"])
    conn.execute(
        """INSERT INTO demographie
            (code_insee, nom_commune, code_departement, population, annee)
        VALUES ('75056', 'Paris', '75', 2100000, 2022),
               ('75112', 'Paris 12e Arrondissement', '75', 140000, 2022),
               ('44109', 'Nantes', '44', 320000, 2022)"""
    )
    # DPE: one document coded at the commune, two at the arrondissement.
    for numero, code, etiquette in (
        ("DPE-COMMUNE", "75056", "A"),
        ("DPE-12E-1", "75112", "B"),
        ("DPE-12E-2", "75112", "B"),
    ):
        conn.execute(
            "INSERT INTO diagnostics_dpe (numero_dpe, code_departement, code_insee, "
            "etiquette_dpe) VALUES (%s, '75', %s, %s)",
            (numero, code, etiquette),
        )


def _migration_111():
    """Migration 111 read from its file (migrations are never imported as app code)."""
    path = BACKEND / "migrations" / "versions" / "111_plm_commune_dvf.py"
    spec = importlib.util.spec_from_file_location("migration_111", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["migration", "reconstruction"])
def paris(request, migrated):  # noqa: F811
    """The fixture, the scope hosting 44 and 75, and the view built either way."""
    conn, dsn, _ = migrated
    install_instance_scope(conn, sorted(HOSTABLE_KEYS), ("44", "75"))
    _geo(conn)
    _paris(conn)
    if request.param == "migration":
        # The module database keeps the views the last case built: put back the
        # definitions of migration 111 (its frozen statements), then refresh.
        for statement in _migration_111().STATEMENTS:
            conn.execute(statement)
    synthese = documents(conn, request.param)
    return conn, dsn, synthese


@asynccontextmanager
async def _api(dsn):
    engine = _engine(dsn, None)
    sessions = async_sessionmaker(engine)
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(commune_router)
    application.include_router(zone_router)

    async def database():
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db] = database
    application.dependency_overrides[require_publication] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(application), base_url="http://fixture"
        ) as client:
            yield client
    finally:
        await engine.dispose()


# --------------------------------------------------------- /v1/commune (D3) --


async def test_commune_of_paris_is_one_row_at_75056(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get("/v1/commune", params={"code_insee": "75056"})

    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]
    assert row["code_insee"] == "75056"
    assert (row["type_zone"], row["commune_parent"]) == ("commune", None)
    assert row["population"] == 2100000


async def test_an_arrondissement_is_its_own_row_with_its_commune_parent(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get("/v1/commune", params={"code_insee": "75112"})

    (row,) = response.json()["donnees"]
    assert row["code_insee"] == "75112"
    assert (row["type_zone"], row["commune_parent"]) == ("arrondissement", "75056")


async def test_the_gps_path_still_resolves_the_arrondissement(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        # Inside the square of 75112 (2.30 to 2.35, 48.80 to 48.85).
        response = await api.get("/v1/commune", params={"lat": 48.83, "lon": 2.32})

    assert [r["code_insee"] for r in response.json()["donnees"]] == ["75112"]


# ---------------------------------------------------- prices of Paris (D4) --


def test_paris_has_the_prices_of_all_its_arrondissements_each_sale_once(paris):
    _, _, synthese = paris
    commune = synthese["75056"]

    # 5 sales at 75112, 5 at 75101, and the sale on both counted once; the one
    # reaching another commune is out. 8000 x5, 9000, 12000 x5: median 9000.
    assert commune["nb_mutations_12m"] == 11
    assert commune["nb_mutations_12m_appartement"] == 11
    assert commune["prix_m2_median_appartement"] == PER_M2_STRADDLE
    assert commune["prix_m2_median"] == PER_M2_STRADDLE
    assert commune["nb_mutations_12m_maison"] == 0
    assert commune["prix_m2_median_maison"] is None


def test_the_arrondissements_keep_their_own_prices_without_the_straddling_sale(paris):
    _, _, synthese = paris
    # The sales on several arrondissements belong to none of the parts.
    assert synthese["75112"]["nb_mutations_12m"] == 5
    assert synthese["75112"]["prix_m2_median"] == PER_M2_75112


def test_a_commune_outside_plm_is_unchanged(paris):
    _, _, synthese = paris
    assert synthese["44109"]["nb_mutations_12m"] == 5
    assert synthese["44109"]["prix_m2_median"] == NANTES_PER_M2


# ------------------------------------------------- DPE of Paris (D4) --


def _refresh_dpe(conn) -> dict:
    """Refresh the DPE view and the synthese, whichever way they were built."""
    conn.execute("REFRESH MATERIALIZED VIEW v_commune_dpe")
    conn.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT code_insee, to_jsonb(v) FROM v_commune_synthese v"
        )
    }


def test_paris_reads_the_documents_of_the_commune_and_of_its_arrondissements(paris):
    conn, _, synthese = paris
    # Fixture: A coded 75056, B and B coded 75112 -> the median class of the three
    # is B, not the A of the lone commune-coded document.
    assert synthese["75056"]["dpe_median"] == "B"
    assert synthese["75056"]["dpe_passoires_pct"] == 0
    # The arrondissement keeps its own two documents.
    assert synthese["75112"]["dpe_median"] == "B"

    # Documents elsewhere in Paris are counted once each: 2 G at 75101 and 1 F
    # at 75056 give 2 + 1 of 6 in F or G; each document sits in one row only.
    for numero, code, etiquette in (
        ("DPE-1ER-1", "75101", "G"),
        ("DPE-1ER-2", "75101", "G"),
        ("DPE-COMMUNE-F", "75056", "F"),
    ):
        conn.execute(
            "INSERT INTO diagnostics_dpe (numero_dpe, code_departement, code_insee, "
            "etiquette_dpe) VALUES (%s, '75', %s, %s)",
            (numero, code, etiquette),
        )
    synthese = _refresh_dpe(conn)
    assert float(synthese["75056"]["dpe_passoires_pct"]) == 50.0
    assert float(synthese["75112"]["dpe_passoires_pct"]) == 0.0
    # Read the intermediate view itself for the arrondissement 75101.
    (passoires_1er,) = conn.execute(
        "SELECT dpe_passoires_pct FROM v_commune_dpe WHERE code_insee = '75101'"
    ).fetchone()
    assert float(passoires_1er) == 100.0


def test_a_dpe_commune_outside_plm_is_unchanged(paris):
    conn, _, _ = paris
    conn.execute(
        "INSERT INTO diagnostics_dpe (numero_dpe, code_departement, code_insee, "
        "etiquette_dpe) VALUES ('DPE-NANTES', '44', '44109', 'F')"
    )
    synthese = _refresh_dpe(conn)
    assert float(synthese["44109"]["dpe_passoires_pct"]) == 100.0
    assert synthese["44109"]["dpe_median"] == "F"


async def test_the_commune_endpoint_of_paris_carries_the_three_documents(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get("/v1/commune", params={"code_insee": "75056"})

    (row,) = response.json()["donnees"]
    assert row["dpe_median"] == "B"


def test_the_dpe_view_has_one_row_per_code(paris):
    conn, _, _ = paris
    duplicated = conn.execute(
        "SELECT code_insee FROM v_commune_dpe GROUP BY code_insee HAVING count(*) > 1"
    ).fetchall()
    assert duplicated == []


def test_the_view_has_one_row_per_code(paris):
    conn, _, _ = paris
    duplicated = conn.execute(
        "SELECT code_insee FROM v_commune_dvf GROUP BY code_insee HAVING count(*) > 1"
    ).fetchall()
    assert duplicated == []


async def test_the_directory_gives_paris_a_price(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get(
            "/v1/communes/annuaire", params={"nom": "paris", "per_page": 100}
        )

    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]["results"]
    assert row["code_insee"] == "75056"
    assert row["prix"]["appartement"]["mediane_m2"] == PER_M2_STRADDLE
    assert row["prix"]["appartement"]["effectif"] == 11


async def test_zone_stats_of_paris_read_its_arrondissements(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get(
            "/v1/zone/stats", params={"type": "commune", "code": "75056"}
        )

    assert response.status_code == 200, response.text
    stats = response.json()["donnees"]
    assert stats["nb_transactions"] == 11
    assert stats["prix_median_m2"] == PER_M2_STRADDLE
    # Documents coded at the commune plus those coded at the arrondissements.
    assert stats["dpe_distribution"]["A"] == 1
    assert stats["dpe_distribution"]["B"] == 2


async def test_zone_resolve_lists_every_mutation_of_paris_once(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        dvf = await api.get(
            "/v1/zone/resolve",
            params={"type": "commune", "code": "75056", "datasets": "dvf"},
        )
        dpe = await api.get(
            "/v1/zone/resolve",
            params={"type": "commune", "code": "75056", "datasets": "dpe"},
        )

    # History keeps the multi-commune sales: 5 + 5 + STRADDLE + OTHER.
    assert dvf.json()["donnees"]["total_count"] == 12
    ids = [r["id_mutation"] for r in dvf.json()["donnees"]["results"]]
    assert len(ids) == len(set(ids))
    assert dpe.json()["donnees"]["total_count"] == 3


# --------------------------------------------------------- referential (D5) --


async def _annuaire(api, **params) -> dict:
    response = await api.get(
        "/v1/communes/annuaire", params={"per_page": 100, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()["donnees"]


async def test_totals_and_pages_are_unchanged_without_the_option(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        default = await _annuaire(api)
        explicit = await _annuaire(api, inclure_arrondissements="false")
        first = await _annuaire(api, per_page=1, sort_by="nom", order="asc")

    assert default["total_count"] == explicit["total_count"] == 2
    assert [r["code_insee"] for r in default["results"]] == ["75056", "44109"]
    assert default == explicit
    assert {r["type_zone"] for r in default["results"]} == {"commune"}
    assert {r["commune_parent"] for r in default["results"]} == {None}
    assert (first["total_count"], first["has_next"]) == (2, True)


async def test_the_option_adds_the_arrondissements_and_keeps_the_commune_once(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        annuaire = await _annuaire(api, inclure_arrondissements="true")
        listing = await api.get(
            "/v1/communes",
            params={"inclure_arrondissements": "true", "departement": "75"},
        )
        by_code = await api.get(
            "/v1/communes",
            params={"inclure_arrondissements": "true", "code_insee": "75112"},
        )
        without = await api.get("/v1/communes", params={"code_insee": "75112"})

    rows = {r["code_insee"]: r for r in annuaire["results"]}
    assert set(rows) == {"75056", "75112", "75101", "44109"}
    assert annuaire["total_count"] == 4
    assert (rows["75112"]["type_zone"], rows["75112"]["commune_parent"]) == (
        "arrondissement",
        "75056",
    )
    # The name is the one of geo_arrondissements, never composed.
    assert rows["75112"]["nom"] == "Paris 12e Arrondissement"
    assert (rows["75056"]["type_zone"], rows["75056"]["commune_parent"]) == (
        "commune",
        None,
    )
    # The commune keeps its own population: it is not the sum of its parts.
    assert rows["75056"]["population"] == 2100000
    assert rows["75112"]["prix"]["appartement"]["mediane_m2"] == PER_M2_75112
    assert listing.json()["donnees"]["total_count"] == 3
    (one,) = by_code.json()["donnees"]["results"]
    assert one["code_insee"] == "75112"
    assert without.json()["donnees"]["results"] == []


async def test_search_paris_has_no_duplicate(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        plain = await api.get("/v1/communes/search", params={"q": "Paris"})
        with_option = await api.get(
            "/v1/communes/search",
            params={"q": "Paris", "inclure_arrondissements": "true", "limit": 50},
        )

    assert [r["code_insee"] for r in plain.json()["donnees"]] == ["75056"]
    codes = [r["code_insee"] for r in with_option.json()["donnees"]]
    assert sorted(codes) == ["75056", "75101", "75112"]
    assert len(codes) == len(set(codes))


async def test_an_arrondissement_outside_the_hosted_perimeter_is_not_listed(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    # Only the 44 is hosted, and geo_arrondissements is loaded for the hosted
    # departements only: it holds no 75 row, the commune stays listed.
    install_instance_scope(conn, sorted(HOSTABLE_KEYS), ("44",))
    _geo(conn, hosted_75=False)
    _paris(conn)
    documents(conn, "migration")
    async with _api(dsn) as api:
        everything = await _annuaire(
            api, couverture="toutes", inclure_arrondissements="true"
        )
        served = await _annuaire(api, inclure_arrondissements="true")

    assert [r["code_insee"] for r in everything["results"]] == ["75056", "44109"]
    assert [r["code_insee"] for r in served["results"]] == ["44109"]


async def test_outlines_left_after_a_scope_change_are_not_listed(migrated):  # noqa: F811
    # Review Codex 04.7-09: the 75 was loaded, then removed from the scope
    # without purging its outlines; with every coverage, only the commune.
    conn, dsn, _ = migrated
    install_instance_scope(conn, sorted(HOSTABLE_KEYS), ("44",))
    _geo(conn, hosted_75=True)
    _paris(conn)
    documents(conn, "migration")
    async with _api(dsn) as api:
        everything = await _annuaire(
            api, couverture="toutes", inclure_arrondissements="true"
        )

    assert [r["code_insee"] for r in everything["results"]] == ["75056", "44109"]


async def test_the_served_departements_filter_the_arrondissements_too(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    install_instance_scope(conn, sorted(HOSTABLE_KEYS), ("44",))
    _geo(conn, hosted_75=True)  # rows loaded for the 75, which is not hosted
    _paris(conn)
    documents(conn, "migration")
    async with _api(dsn) as api:
        served = await _annuaire(api, inclure_arrondissements="true")

    assert [r["code_insee"] for r in served["results"]] == ["44109"]


# ------------------------------------------------------- comparator (D6) --


async def test_a_commune_cannot_be_compared_to_its_arrondissement(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get(
            "/v1/communes/comparaison", params={"codes": "75056,75112"}
        )

    assert response.status_code == 422
    assert "arrondissement" in response.text


async def test_two_arrondissements_are_compared_with_their_names(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get(
            "/v1/communes/comparaison", params={"codes": "75112,75101"}
        )

    assert response.status_code == 200, response.text
    communes = response.json()["donnees"]["communes"]
    assert [(c["code_insee"], c["nom"]) for c in communes] == [
        ("75112", "Paris 12e Arrondissement"),
        ("75101", "Paris 1er Arrondissement"),
    ]
    assert communes[0]["synthese"]["commune_parent"] == "75056"
    assert communes[0]["synthese"]["type_zone"] == "arrondissement"


async def test_paris_and_nantes_are_compared_with_a_price_for_paris(paris):
    _, dsn, _ = paris
    async with _api(dsn) as api:
        response = await api.get(
            "/v1/communes/comparaison", params={"codes": "75056,44109"}
        )

    assert response.status_code == 200, response.text
    critere = next(
        c
        for c in response.json()["donnees"]["criteres"]
        if c["cle"] == "prix_m2_median_appartement"
    )
    assert critere["cellules"][0]["valeur"] == PER_M2_STRADDLE


# --------------------------------------------------- migration 111 itself --


def test_migration_111_roundtrip_and_no_drift(migrated_dsn):  # noqa: F811
    from alembic import command

    def definition(conn) -> str:
        return conn.execute(
            "SELECT definition FROM pg_matviews WHERE matviewname = 'v_commune_dvf'"
        ).fetchone()[0]

    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert "commune_code" in definition(conn)
        assert "v_commune_dvf" not in commune_view_drift(conn)

    _migrate(migrated_dsn, command.downgrade, "110_logement_thrs_mandat")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert "commune_code" not in definition(conn)

    _migrate(migrated_dsn, command.upgrade, "head")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert "commune_code" in definition(conn)
        assert "v_commune_dvf" not in commune_view_drift(conn)


def test_migration_110_downgrade_to_109_recreates_the_chain_and_returns_to_head(
    migrated_dsn,  # noqa: F811
):
    from alembic import command

    dropped = {
        "fiscalite_locale": ("majoration_thrs", "taux_majoration_thrs"),
        "copropriete": ("mandat_en_cours", "date_fin_dernier_mandat"),
    }

    def columns(conn, table) -> set[str]:
        return {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (table,),
            )
        }

    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        for table, names in dropped.items():
            assert set(names) <= columns(conn, table)

    _migrate(migrated_dsn, command.downgrade, "109_permis_dossier_destination_key")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        for table, names in dropped.items():
            assert not set(names) & columns(conn, table)
        # The chain was dropped and recreated around the column removal.
        conn.execute("REFRESH MATERIALIZED VIEW v_commune_synthese")
        assert conn.execute(
            "SELECT count(*) FROM pg_matviews WHERE matviewname = 'v_commune_synthese'"
        ).fetchone() == (1,)

    _migrate(migrated_dsn, command.upgrade, "head")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        for table, names in dropped.items():
            assert set(names) <= columns(conn, table)
        assert commune_view_drift(conn) == []

"""DVF 57/67/68/976 and overseas collectivities out of coverage (04.1-06).

ROADMAP 4.1 criterion 4, D-122, D-123. On a disposable migrated PostGIS with
the scope {44, 67, 976} written by the production path (``replace_scope``):

- the routes that serve DVF for an address or a commune of 67 or 976 answer
  « hors couverture », reason ``source_sans_couverture`` (the producer
  publishes neither Alsace-Moselle nor Mayotte): never a zero, never a panne;
- ``/v1/resolve`` on a point whose commune is an overseas collectivity
  declares every requested key out of coverage, reason
  ``territoire_hors_perimetre``, without querying it;
- the DVF import of that scope requests 44 only: no download is attempted
  for 67 or 976, no error, ``effective_departments`` = [44].

The scope dependency is never overridden; only the session is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.exceptions import register_exception_handlers
from app.commune.matview_ddl import recreate_commune_synthese
from app.commune.router import router as commune_router
from app.commune.schemas import COMMUNE_FIELD_SOURCES
from app.config import settings
from app.database import get_db
from app.dvf.router import router as dvf_router
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_dvf.config import YEARS
from app.etl_dvf.pipeline import DvfPipeline
from app.resolve import multi
from app.resolve import router as resolve_module
from tests.integration.instance_scope_fixture import HOSTABLE_KEYS, install_scope_at
from tests.integration.test_departmental_scope import FakeDvfSource, db_scope_loader

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
SCOPE = ("44", "67", "976")

# (commune, name, departement, postcode, street, (lat, lon)); 975 and 986 are
# collectivities (D-123): the BAN row of a COM lands in the default partition.
COMMUNES = {
    "nantes": ("44109", "Nantes", "44", "44000", "rue du test", (47.2184, -1.5536)),
    "strasbourg": (
        "67482",
        "Strasbourg",
        "67",
        "67000",
        "rue d alsace",
        (48.5734, 7.7521),
    ),
    "mamoudzou": (
        "97611",
        "Mamoudzou",
        "976",
        "97600",
        "rue de mayotte",
        (-12.7806, 45.2279),
    ),
    "saint_pierre": (
        "97502",
        "Saint-Pierre",
        "975",
        "97500",
        "rue de miquelon",
        (46.7811, -56.1764),
    ),
    "mata_utu": (
        "98613",
        "Uvea",
        "986",
        "98600",
        "rue de wallis",
        (-13.2825, -176.1736),
    ),
}
# Tables of the commune synthesis, emptied before the referential rows.
_COMMUNE_TABLES = (
    "demographie",
    "revenus",
    "zonage_abc",
    "vacance",
    "fiscalite_locale",
    "indicateurs_loyers",
    "mutations_grain",
    "diagnostics_dpe",
    "equipements",
    "carte_scolaire",
    "monuments_historiques",
    "foncier_pm",
    "eligibilite_fixe",
)
DVF_COLUMNS = sorted(c for c, s in COMMUNE_FIELD_SOURCES.items() if s == "dvf")


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute(f"TRUNCATE {', '.join(_COMMUNE_TABLES)}")
        conn.execute("DELETE FROM etl_publications")
        for index, (code, nom, dept, cp, voie, (lat, lon)) in enumerate(
            COMMUNES.values(), start=1
        ):
            conn.execute(
                "INSERT INTO adresses (id, code_departement, ban_id, numero, "
                "nom_voie, code_postal, code_commune, nom_commune, position) "
                "VALUES (%s, %s, %s, '1', %s, %s, %s, %s, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (
                    93_000_000 + index,
                    dept,
                    f"{code}_0001_00001",
                    voie,
                    cp,
                    code,
                    nom,
                    lon,
                    lat,
                ),
            )
            if dept in SCOPE:
                # Referential and synthesis rows for the hosted territories.
                conn.execute(
                    "INSERT INTO geo_communes (code, nom, departement_code, "
                    "population, geometry) VALUES (%s, %s, %s, 1000, ST_Multi("
                    "ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.05)))",
                    (code, nom, dept, lon, lat),
                )
                conn.execute(
                    "INSERT INTO demographie (code_insee, nom_commune, "
                    "code_departement, population, annee) "
                    "VALUES (%s, %s, %s, 1000, 2021)",
                    (code, nom, dept),
                )
        recreate_commune_synthese(conn)
    install_scope_at(isolated_pg_dsn, HOSTABLE_KEYS, SCOPE)
    return isolated_pg_dsn


@pytest.fixture
def upstream_calls(monkeypatch):
    calls: list[str] = []

    async def fake_http(name, lat, lon, ban_id=None):
        calls.append(name)
        return None

    monkeypatch.setattr(multi, "_dispatch_http", fake_http)
    return calls


@pytest.fixture
def statements() -> list[str]:
    return []


@pytest.fixture
async def api(migrated, monkeypatch, statements) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    for router in (dvf_router, commune_router, resolve_module.router):
        application.include_router(router)
    application.dependency_overrides[get_db] = db
    # Dataset tasks of /v1/resolve open their own sessions.
    monkeypatch.setattr(resolve_module, "async_session", sessions)
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c
    finally:
        await engine.dispose()


def _reads(statements: list[str], table: str) -> list[str]:
    return [sql for sql in statements if table in sql]


# ---------------------------------------------------------- DVF, by address --


@pytest.mark.parametrize("commune", ["strasbourg", "mamoudzou"])
async def test_dvf_by_address_in_67_and_976_is_source_without_coverage(
    api, statements, commune
):
    code, nom, _dept, cp, voie, _ = COMMUNES[commune]
    statements.clear()

    response = await api.get("/v1/dvf/adresse", params={"q": f"1 {voie} {cp} {nom}"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recherche"]["etat"] == "point_retenu"
    assert body["recherche"]["code_commune"] == code
    assert body["donnees"] == []  # not a zero sale: the reason below says why
    hosting = body["meta"]["hebergement"]["dvf"]
    assert (hosting["etat"], hosting["motif"]) == (
        "hors_couverture",
        "source_sans_couverture",
    )
    assert _reads(statements, "mutations_grain") == []


async def test_dvf_by_address_in_44_is_served(api, statements):
    code, nom, _dept, cp, voie, _ = COMMUNES["nantes"]
    statements.clear()

    response = await api.get("/v1/dvf/adresse", params={"q": f"1 {voie} {cp} {nom}"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recherche"]["code_commune"] == code
    assert "hebergement" not in (body.get("meta") or {})
    assert _reads(statements, "mutations_grain") != []  # positive control


# ----------------------------------------------------------- commune fiche --


async def test_commune_fiche_in_67_serves_dvf_out_of_coverage(api):
    code = COMMUNES["strasbourg"][0]

    response = await api.get("/v1/commune", params={"code_insee": code})

    assert response.status_code == 200, response.text
    (row,) = response.json()["donnees"]
    assert row["code_insee"] == code
    assert row["population"] == 1000  # the commune itself is served
    dvf = row["couverture"]["dvf"]
    assert (dvf["etat"], dvf["motif"]) == ("hors_couverture", "source_sans_couverture")
    assert DVF_COLUMNS
    for column in DVF_COLUMNS:
        if column in row:
            assert row[column] is None, column  # never 0
    assert row["couverture"]["demographie"]["etat"] == "heberge"


async def test_commune_fiche_in_976_is_out_of_coverage_without_census(api):
    # The INSEE census files publish no commune of Mayotte (measured on
    # 2026-10-30): demographie, structural for the fiche, is out of coverage
    # there, so the whole fiche is, with the source's reason, never a zero.
    code = COMMUNES["mamoudzou"][0]

    response = await api.get("/v1/commune", params={"code_insee": code})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    commune = body["meta"]["hebergement"]["commune"]
    assert (commune["etat"], commune["motif"]) == (
        "hors_couverture",
        "source_sans_couverture",
    )


# ----------------------------------------------------------------- resolve --


@pytest.mark.parametrize("commune", ["strasbourg", "mamoudzou"])
async def test_resolve_dvf_key_in_67_and_976(api, upstream_calls, commune):
    _code, _nom, _dept, _cp, _voie, (lat, lon) = COMMUNES[commune]

    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "dvf"},
    )

    assert response.status_code == 200, response.text
    state = response.json()["donnees"][0]["dataset_states"]["dvf"]
    assert state["absence_reason"] == "out_of_coverage"
    assert state["motif"] == "source_sans_couverture"


@pytest.mark.parametrize("commune", ["saint_pierre", "mata_utu"])
async def test_resolve_on_a_collectivity_is_outside_the_perimeter(
    api, upstream_calls, statements, commune
):
    code, _nom, _dept, _cp, _voie, (lat, lon) = COMMUNES[commune]
    statements.clear()

    response = await api.get(
        "/v1/resolve",
        params={"q": f"{lat},{lon}", "type": "gps", "datasets": "dvf,entreprises"},
    )

    assert response.status_code == 200, response.text
    result = response.json()["donnees"][0]
    assert result["code_insee"] == code
    for name in ("dvf", "entreprises"):
        state = result["dataset_states"][name]
        assert state["absence_reason"] == "out_of_coverage", name
        assert state["motif"] == "territoire_hors_perimetre", name
    # Declared before any query or upstream call (D-24).
    assert upstream_calls == []
    assert _reads(statements, "mutations_grain") == []


# ------------------------------------------- DROM polygon (D-122, real SQL) --


def test_drom_polygon_from_communes_feeds_the_bounds_gate_and_point_rule(migrated):
    """The referential import's SQL on PostGIS: union of the 976 communes,
    then the BD TOPO bounds gate and the departement of a point see 976."""
    import logging

    from app.etl.geo_gate import check_bounds_gate
    from app.scope.model import POINT_DEPARTEMENT_SQL
    from scripts import import_geo_boundaries as geo

    _code, _nom, _dept, _cp, _voie, (lat, lon) = COMMUNES["mamoudzou"]
    listing = geo.Response(
        "departements-liste.json", b"", [{"properties": {"code": "976", "nom": "D"}}]
    )
    point = f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)"
    with psycopg.connect(migrated) as conn:
        geo.insert_drom_polygons(conn, listing, ["976", "971"])
        # 976 has communes: built; 971 has none: no row, refused by the check.
        assert geo.check_drom_polygons(conn, ["976"]) == ["976"]
        with pytest.raises(geo.BoundaryImportError, match="971"):
            geo.check_drom_polygons(conn, ["976", "971"])
        of_point = POINT_DEPARTEMENT_SQL.format(point=point)
        kind, departement = conn.execute(
            f"SELECT GeometryType(geometry), {of_point} "
            "FROM geo_departements WHERE code = '976'"
        ).fetchone()
        conn.commit()
        assert (kind, departement) == ("MULTIPOLYGON", "976")
    # check_bounds_gate opens its own connection: the temp table is not seen,
    # so a real (permanent) table is used, dropped right after.
    with psycopg.connect(migrated) as conn:
        conn.execute(
            "CREATE TABLE drom_gate_probe AS SELECT ST_Buffer("
            f"{point}, 0.001) AS geometrie"
        )
        conn.commit()
    try:
        verdict = check_bounds_gate(
            migrated,
            "drom_gate_probe",
            "geometrie",
            "batiments",
            threshold_pct=5.0,
            log=logging.getLogger(__name__),
        )
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute("DROP TABLE drom_gate_probe")
            conn.execute("DELETE FROM geo_departements WHERE code = '976'")
    assert verdict is None  # a Mayotte building is inside France + DROM


# -------------------------------------------- import (last: writes mutations) --


def test_dvf_import_never_requests_67_or_976(migrated, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    # Floors are the unit suite's: here only the download list is under test.
    monkeypatch.setattr(DvfPipeline, "min_staging_rows", property(lambda _: 1))
    source = FakeDvfSource()
    monkeypatch.setattr("app.etl_dvf.pipeline.verified_download_gz", source)

    result = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=db_scope_loader
    ).run(DvfPipeline())

    assert result.success, result.error
    assert source.departements == {"44"}
    assert len(source.requested) == len(YEARS)
    with psycopg.connect(migrated) as conn:
        partitions = conn.execute(
            "SELECT DISTINCT code_departement FROM mutations ORDER BY 1"
        ).fetchall()
        (effective,) = conn.execute(
            "SELECT effective_departments FROM dataset_metadata "
            "WHERE dataset_name = 'dvf'"
        ).fetchone()
    assert partitions == [("44",)]
    assert list(effective) == ["44"]

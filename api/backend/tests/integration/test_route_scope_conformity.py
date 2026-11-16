"""DATA-03 / D-23: every /v1 route serving a source reads the persisted scope.

``/v1/resolve`` and ``/v1/datasets`` (02-07) are not the only way to a source:
each public route serving a key of the V1 list, or the ``commune`` synthesis,
declares ``hosted_datasets`` and reads nothing when all its keys are out of
coverage. The expected key set is the V1 list (``PERIMETRE`` of
``scripts/compare_fidelity.py``, which that script checks against the
``v1-keys`` markers of ``02-DATASETS.md``), never derived from the routes found.

The scope is written by the production path (``replace_scope``) into a
migrated disposable database and read by the real scope dependency of the
API (``app.scope.dependencies``): no override of it reaches this module.
"""

import ast
import inspect
import re
import textwrap
from collections.abc import AsyncIterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cadastre.router import router as cadastre_router
from app.common.exceptions import register_exception_handlers
from app.common.source_catalog import DATASET_CATALOG
from app.common.sources import get_meta_source
from app.config import settings
from app.database import get_db
from app.dpe.inventory_service import DpeInventoryServiceResult
from app.dpe.router import router as dpe_router
from app.dpe.schemas import DpeInventoryDistribution, DpeInventoryPage
from app.dvf.router import router as dvf_router
from app.foncier_pm.router import router as foncier_pm_router
from app.logements import router as logements_module
from app.main import app as main_app
from app.sirene import router as sirene_module
from app.transport import router as transport_module
from app.vacance.router import router as vacance_router
from app.zone.router import router as zone_router
from scripts.compare_fidelity import PERIMETRE
from tests.integration.instance_scope_fixture import HOSTABLE_KEYS, install_scope_at

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
_ATTRIBUTION = {"get_meta_source", "get_meta_sources"}

# /v1 routes serving no key of the V1 list, one reason per route (no prefix).
EXCLUDED_ROUTES: dict[str, str] = {
    "/v1/health": "santé du service, aucune source lue",
    "/v1/health/live": "santé du service, aucune source lue",
    "/v1/health/version": "version du service, aucune source lue",
    "/v1/communes": (
        "référentiel geo_communes ; ban et demographie lus pour les seuls "
        "départements hébergés (H2, H6), aucune méta de source"
    ),
    "/v1/departements": (
        "référentiel geo_departements ; hébergement de DVF et jour d'import lus "
        "dans le périmètre et le journal d'import (04.7-10), aucune table de "
        "source lue, aucune méta de source"
    ),
    "/v1/departements/{code}/fiches": (
        "référentiel national geo_communes, et geo_arrondissements là où la BAN "
        "est hébergée (04.7-10) ; jour d'import lu dans le journal, aucune table "
        "de source lue"
    ),
    "/v1/communes/voisines": (
        "référentiel national geo_communes et geo_arrondissements (liens "
        "internes des fiches, audit SEO 2026-11-14), aucune table de source lue"
    ),
    "/v1/scoring/renov": "SurfaceIndisponible (503), aucune lecture de source",
    "/v1/scoring/renov-target": "SurfaceIndisponible (503), aucune lecture de source",
    "/v1/encadrement-loyers": (
        "SurfaceIndisponible (503) ; encadrement_loyers hors V1 (quarantaine)"
    ),
    "/v1/signalements": (
        "dépôt d'un signalement d'erreur (écriture dans signalements), "
        "aucune source lue"
    ),
    "/v1/validation/reports": "rapports ETL réservés à l'administrateur",
    "/v1/admin/users": "administration",
    "/v1/admin/users/bulk": "administration",
    "/v1/admin/users/{user_id}": "administration",
    "/v1/admin/users/{user_id}/grant-plan": "administration",
    "/v1/admin/users/{user_id}/admin": "administration",
    "/v1/admin/users/{user_id}/reset-quota": "administration",
    "/v1/admin/users/{user_id}/revoke-keys": "administration",
    "/v1/admin/users/{user_id}/resend-verification": "administration",
    "/v1/admin/audit": "administration",
    "/v1/admin/requests": "administration",
    "/v1/admin/requests/export.csv": "administration",
    "/v1/admin/api-keys": "administration",
    "/v1/admin/api-keys/{key_id}/revoke": "administration",
    "/v1/admin/api-keys/{key_id}/quota": "administration",
    "/v1/admin/subscriptions": "administration (facturation)",
    "/v1/admin/subscriptions/{sub_id}/sync": "administration (facturation)",
    "/v1/admin/overview": "administration",
    "/v1/admin/users/{user_id}/heatmap": "administration",
    "/v1/admin/system": "administration (état système)",
    "/v1/admin/etl": "administration (journal ETL)",
    "/v1/admin/etl/{pipeline_name}": "administration (journal ETL)",
    "/v1/admin/etl/{pipeline_name}/trigger": (
        "administration ; garde 409 sur le périmètre en base (02-08 tâche 1)"
    ),
}
# Routes serving every key through their own scope reading, delivered by 02-07.
SCOPED_BY_02_07: dict[str, str] = {
    "/v1/resolve": "état par clé demandée (_declared_out_of_coverage)",
    "/v1/datasets": "exception explicite : chaque entrée garde hebergement.etat",
}
# Catalogue keys served after the V1 list was closed (02-03), one plan each:
# upstream proxies without import, out of coverage until the published scope
# poses them. The V1 list stays the one compared with the old base.
ADDED_AFTER_V1: dict[str, str] = {
    "altimetrie": "04.7-04 : altitude IGN RGE ALTI du rapport d'adresse",
    "geologie": "04.7-04 : lithologie BRGM au millionième du rapport d'adresse",
    "axes_transport": "04.7-05 : grands axes routiers et voies ferrées (BD TOPO)",
    "peb": "04.7-05 : plans d'exposition au bruit des aérodromes (DGAC)",
    "delinquance": "04.7-06 : délinquance enregistrée, commune et département (SSMSI)",
    "eau_potable": "04.7-06 : contrôle sanitaire de l'eau du robinet (Hub'Eau)",
    "annuaire_education": "04.7-06 : écoles, IPS et valeur ajoutée (Éducation)",
    "qualite_air": "04.7-07 : indice ATMO du jour, commune ou intercommunalité",
    "filosofi_carreaux": "04.7-07 : niveau de vie du carreau de 200 m (Filosofi)",
    "iris": "04.7-07 : IRIS du point et sa composition (recensement, Melodi)",
    "zone_tendue": "04.7-08 : commune en zone tendue (taxe sur les logements vacants)",
    "encadrement_communes": "04.7-08 : commune à encadrement des loyers (ELAN)",
}


def v1_keys() -> frozenset[str]:
    keys = frozenset(PERIMETRE)
    assert keys <= set(DATASET_CATALOG), sorted(keys - set(DATASET_CATALOG))
    return keys


def _declared_keys(dependant) -> frozenset[str]:
    keys: set[str] = set()
    for dependency in dependant.dependencies:
        keys.update(getattr(dependency.call, "served_keys", ()))
        keys.update(_declared_keys(dependency))
    return frozenset(keys)


def _reads_scope(dependant) -> bool:
    """The route depends on the persisted-scope reader of ``app.scope``."""
    return any(
        getattr(dependency.call, "__module__", None) == "app.scope.dependencies"
        or _reads_scope(dependency)
        for dependency in dependant.dependencies
    )


def attributed_keys(endpoint) -> frozenset[str]:
    """Catalogue keys a handler attributes by ``get_meta_source(s)`` literals."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            in _ATTRIBUTION
        ):
            continue
        for arg in ast.walk(ast.Module(body=[ast.Expr(a) for a in node.args])):
            if isinstance(arg, ast.Constant) and arg.value in DATASET_CATALOG:
                keys.add(arg.value)
    return frozenset(keys)


@dataclass(frozen=True)
class Inventory:
    declared: dict[str, frozenset[str]]
    # Route neither declaring hosted_datasets nor excluded -> keys it attributes.
    missing: dict[str, frozenset[str]]


def inventory(application: FastAPI) -> Inventory:
    declared: dict[str, frozenset[str]] = {}
    missing: dict[str, frozenset[str]] = {}
    for route in application.routes:
        if not (isinstance(route, APIRoute) and route.path.startswith("/v1")):
            continue
        keys = _declared_keys(route.dependant)
        if keys:
            declared[route.path] = declared.get(route.path, frozenset()) | keys
        elif route.path not in EXCLUDED_ROUTES | SCOPED_BY_02_07:
            missing[route.path] = attributed_keys(route.endpoint)
    return Inventory(declared, missing)


# --- 5.a / 5.b : inventory ------------------------------------------------


def test_every_v1_route_serving_a_source_declares_its_keys(capsys):
    found = inventory(main_app)
    declared_union = frozenset().union(*found.declared.values())
    assert not set(ADDED_AFTER_V1) & v1_keys()
    assert set(ADDED_AFTER_V1) <= set(DATASET_CATALOG)
    expected = v1_keys() | {"commune"} | set(ADDED_AFTER_V1)
    with capsys.disabled():
        print(f"\nINVENTORY declared={sorted(declared_union)}")
        print(f"INVENTORY expected={sorted(expected)}")
        print(f"INVENTORY missing={found.missing}")

    assert found.missing == {}
    assert declared_union == expected
    assert found.declared["/v1/zone/resolve"] == {"dvf", "dpe"}
    assert found.declared["/v1/zone/stats"] == {"dvf", "dpe", "commune"}
    # 04.7-10: the departement page serves its three sources, each qualified
    # for the departement named by the path.
    assert found.declared["/v1/departements/{code}"] == {"dvf", "dpe", "demographie"}
    # Address search and identity (04-02): hosting per departement, the
    # identity also serves the parcel relation.
    assert found.declared["/v1/adresses/recherche"] == {"ban"}
    # 04-03: the address page also serves its landmarks; the DPE list serves
    # the BAN key and the declared documents. 04-06: and the themes of the
    # place (buildings, RNB, condominium, planning, broadband, transports).
    assert found.declared["/v1/adresse/{ban_id}"] == {
        "ban",
        "cadastre",
        "dpe",
        "dvf",
        "georisques",
        "commune",
        "batiments",
        "rnb",
        "copropriete",
        "urbanisme",
        "connectivite",
        "transports",
        "gares",
    }
    # 04-07: each compared address serves the keys of its own page.
    assert (
        found.declared["/v1/adresses/comparaison"]
        == found.declared["/v1/adresse/{ban_id}"]
    )
    # 04-19: the D-110 finding also counts the buildings of the parcel.
    assert found.declared["/v1/adresse/{ban_id}/dpe"] == {
        "ban",
        "dpe",
        "cadastre",
        "copropriete",
        "batiments",
    }
    # 04.7-04: the address report is the only route serving the added keys.
    for path, keys in found.declared.items():
        served = keys & set(ADDED_AFTER_V1)
        assert served == (
            set(ADDED_AFTER_V1) if path == "/v1/adresse/{ban_id}/rapport" else set()
        ), path

    routes = {r.path: r for r in main_app.routes if isinstance(r, APIRoute)}
    for path in found.declared:
        # A key attributed by the handler is a key the route declares.
        assert attributed_keys(routes[path].endpoint) <= found.declared[path], path
    for path in EXCLUDED_ROUTES:
        assert path in routes, f"stale exclusion {path}"
        assert attributed_keys(routes[path].endpoint) == frozenset(), path
    for path in SCOPED_BY_02_07:
        assert _reads_scope(routes[path].dependant), path
    assert not {"/v1/zone/resolve", "/v1/zone/stats"} & set(EXCLUDED_ROUTES)


def test_inventory_fails_on_a_route_serving_a_key_without_the_guard():
    control = APIRouter(prefix="/v1")

    @control.get("/controle-vacance")
    async def controle_vacance() -> dict:
        return {"donnees": [], "meta": get_meta_source("vacance")}

    application = FastAPI()
    application.include_router(vacance_router)
    application.include_router(control)

    found = inventory(application)

    assert found.missing == {"/v1/controle-vacance": frozenset({"vacance"})}
    assert found.declared == {"/v1/vacance": frozenset({"vacance"})}


# --- 5.c to 5.f : behaviour on a migrated database --------------------------

NANTES = (47.2184, -1.5536)
# C52 (B2): two communes of departements outside a 44/85 scope (75), or
# hosted without DVF coverage (57), in the commune referential only.
PARIS = (48.8566, 2.3522)
METZ = (49.1193, 6.1757)
DVF_IDS = tuple(f"MUT-44109-{index}" for index in range(5))


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    lat, lon = NANTES
    with psycopg.connect(isolated_pg_dsn) as conn:
        # Commune referential plus one BAN address carrying its postal code.
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, population, "
            "geometry) VALUES ('44109', 'Nantes', '44', 320732, "
            "ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.05)))",
            (lon, lat),
        )
        for code, nom, departement, (c_lat, c_lon) in (
            ("75056", "Paris", "75", PARIS),
            ("57463", "Metz", "57", METZ),
        ):
            conn.execute(
                "INSERT INTO geo_communes (code, nom, departement_code, population, "
                "geometry) VALUES (%s, %s, %s, 1, "
                "ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0.05)))",
                (code, nom, departement, c_lon, c_lat),
            )
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie, "
            "code_postal, code_commune, nom_commune, position) VALUES "
            "(92000001, '44', '44109_0001_00001', '1', 'rue du test', '44000', "
            "'44109', 'Nantes', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat),
        )
        # Two identified rows served for Paris (75056 expands to arrondissements).
        conn.execute(
            "INSERT INTO vacance (code_commune, nom_commune, code_departement, "
            "nb_logements_prives, nb_logements_vacants, taux_vacance, annee) "
            "VALUES ('75101', 'Paris 1er', '75', 1000, 80, 8.0, 2024), "
            "('75102', 'Paris 2e', '75', 2000, 120, 6.0, 2024)"
        )
        conn.execute(
            "INSERT INTO arrets_transport (stop_id, stop_name, position) VALUES "
            "('A', 'ARRET-A', ST_SetSRID(ST_MakePoint(%s, %s), 4326)), "
            "('B', 'ARRET-B', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat, lon + 0.001, lat),
        )
        conn.execute(
            "INSERT INTO gares (nom_gare, code_insee, position) VALUES "
            "('GARE-A', '44109', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat + 0.001),
        )
        # Geocoded (phase 04.4, D-03: a NULL position is a statistic only,
        # never a row of the zone list), about 2 km north of the address
        # point so no address radius ever reaches them.
        for index, id_mutation in enumerate(DVF_IDS):
            conn.execute(
                "INSERT INTO mutations_grain (code_departement, id_mutation, "
                "date_mutation, nature_mutation, valeur_fonciere, classe_mutation, "
                "nb_locaux_maison, nb_lignes, nb_dispositions, nb_communes, "
                "nb_parcelles, surface_bati_totale, mono_local, est_vente, "
                "code_commune, nom_commune, position) VALUES ('44', %s, "
                "'2024-06-01', 'Vente', %s, 'residentiel_maison', 1, 1, 1, 1, 1, "
                "100, true, true, '44109', 'Nantes', "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (id_mutation, 200_000 + 10_000 * index, lon, lat + 0.02),
            )
        conn.execute(
            "INSERT INTO diagnostics_dpe (numero_dpe, code_insee, "
            "code_departement, code_postal, date_etablissement_dpe, "
            "etiquette_dpe) VALUES "
            "('DPE-44109-1', '44109', '44', '44000', '2024-01-01', 'C'), "
            "('DPE-44109-2', '44109', '44', '44000', '2024-01-02', 'D')"
        )
    return isolated_pg_dsn


@dataclass
class Api:
    client: httpx.AsyncClient
    statements: list[str]
    sessions: async_sessionmaker

    def reads(self, table: str) -> list[str]:
        return [sql for sql in self.statements if table in sql]


@pytest.fixture
async def api(migrated) -> AsyncIterator[Api]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    for router in (
        vacance_router,
        sirene_module.router,
        transport_module.router,
        zone_router,
        dvf_router,
        dpe_router,
        cadastre_router,
        logements_module.router,
        foncier_pm_router,
    ):
        application.include_router(router)
    application.dependency_overrides[get_db] = db
    # The scope dependency itself is never overridden: only the session is.
    assert set(application.dependency_overrides) == {get_db}
    assert not any(
        getattr(call, "__module__", None) == "app.scope.dependencies"
        for call in application.dependency_overrides
    )
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements, sessions)
    finally:
        await engine.dispose()


def _scope(dsn, *, without=(), departements=("44", "85")):
    install_scope_at(dsn, sorted(set(HOSTABLE_KEYS) - set(without)), departements)


async def test_disabled_vacance_serves_no_row_after_import(api, migrated):
    _scope(migrated, without={"vacance"}, departements=("44", "75", "85"))
    response = await api.client.get("/v1/vacance", params={"code_insee": "75056"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["vacance"]["motif"] == "dataset_non_heberge"
    assert api.reads("FROM vacance") == []

    _scope(migrated, departements=("44", "75", "85"))
    response = await api.client.get("/v1/vacance", params={"code_insee": "75056"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["code_commune"] for row in body["donnees"]} == {"75101", "75102"}
    assert len(body["donnees"]) == 2
    assert "hebergement" not in body["meta"]


@pytest.mark.parametrize(
    ("code_insee", "departement"), [("33063", "33"), ("2A004", "2A"), ("2a004", "2A")]
)
async def test_code_insee_outside_the_territory_reads_no_row(
    api, migrated, code_insee, departement
):
    _scope(migrated)  # whole catalogue on 44/85
    response = await api.client.get("/v1/vacance", params={"code_insee": code_insee})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["vacance"]["motif"] == (
        "territoire_hors_perimetre"
    ), departement
    assert api.reads("FROM vacance") == []


@pytest.mark.parametrize("code_insee", ["20004", "99999"])
async def test_code_insee_naming_no_departement_is_no_proof(api, migrated, code_insee):
    # A degenerate prefix (20, 99) names no departement: no « hors couverture »
    # proof, the table is read as before and nothing is declared.
    _scope(migrated)
    response = await api.client.get("/v1/vacance", params={"code_insee": code_insee})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert "hebergement" not in (body.get("meta") or {})
    assert api.reads("FROM vacance") != []


@pytest.fixture
def sirene_calls(monkeypatch):
    calls = []

    async def fake(lat, lon, rayon_km, activite, page=1, per_page=10):
        calls.append((lat, lon))
        return {"results": [{"siret": "12345678900011", "nom_complet": "ETAB-A"}]}

    monkeypatch.setattr(sirene_module, "entreprises_a_proximite", fake)
    return calls


async def test_disabled_sirene_proxy_is_never_called(api, migrated, sirene_calls):
    lat, lon = NANTES
    params = {"lat": lat, "lon": lon}
    _scope(migrated, without={"sirene"})
    response = await api.client.get("/v1/entreprises", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["sirene"] == {
        "etat": "hors_couverture",
        "motif": "dataset_non_heberge",
        "departements": [],
    }
    assert len(sirene_calls) == 0

    _scope(migrated)
    response = await api.client.get("/v1/entreprises", params=params)

    assert response.status_code == 200, response.text
    assert len(sirene_calls) == 1
    assert {row["siret"] for row in response.json()["donnees"]} == {"12345678900011"}


@pytest.fixture
def transport_calls(monkeypatch):
    calls = []
    original = transport_module.transports_a_proximite

    async def counting(*args, **kwargs):
        calls.append(args[1:3])
        return await original(*args, **kwargs)

    monkeypatch.setattr(transport_module, "transports_a_proximite", counting)
    return calls


async def test_transports_with_both_sources_disabled_reads_no_table(
    api, migrated, transport_calls
):
    # Partial case: test_transports_serves_only_the_hosted_source below.
    lat, lon = NANTES
    params = {"lat": lat, "lon": lon, "rayon_m": 500}
    _scope(migrated, without={"transports", "gares"})
    response = await api.client.get("/v1/transports", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert {
        key: value["motif"] for key, value in body["meta"]["hebergement"].items()
    } == {"transports": "dataset_non_heberge", "gares": "dataset_non_heberge"}
    assert len(transport_calls) == 0
    assert api.reads("arrets_transport") == []

    _scope(migrated)
    response = await api.client.get("/v1/transports", params=params)

    assert response.status_code == 200, response.text
    assert len(transport_calls) == 1
    stop_names = {row["stop_name"] for row in response.json()["donnees"]}
    assert stop_names == {"ARRET-A", "ARRET-B", "GARE-A"}


@pytest.mark.parametrize(
    ("without", "expected"),
    [
        ({"gares"}, {"ARRET-A", "ARRET-B"}),
        ({"transports"}, {"GARE-A"}),
    ],
    ids=["transports-only", "gares-only"],
)
async def test_transports_serves_only_the_hosted_source(
    api, migrated, transport_calls, without, expected
):
    lat, lon = NANTES
    _scope(migrated, without=without)
    response = await api.client.get(
        "/v1/transports", params={"lat": lat, "lon": lon, "rayon_m": 500}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    stop_names = [row["stop_name"] for row in body["donnees"]]
    assert set(stop_names) == expected
    assert len(stop_names) == len(expected)
    (key,) = without
    assert body["meta"]["hebergement"][key]["motif"] == "dataset_non_heberge"
    table = "gares" if key == "gares" else "arrets_transport"
    assert not [sql for sql in api.reads("FROM " + table)]
    assert len(transport_calls) == 1


async def test_commune_search_serves_code_postal_only_while_ban_is_hosted(
    api, migrated
):
    params = {"q": "Nant"}
    _scope(migrated)
    response = await api.client.get("/v1/communes/search", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(c["code_insee"], c["code_postal"]) for c in body["donnees"]] == [
        ("44109", "44000")
    ]
    assert body.get("meta") is None
    assert api.reads("adresses") != []

    api.statements.clear()
    _scope(migrated, without={"ban"})
    response = await api.client.get("/v1/communes/search", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    # The commune referential stays; the BAN postal code is neither read nor served.
    assert [(c["code_insee"], c["code_postal"]) for c in body["donnees"]] == [
        ("44109", None)
    ]
    assert body["meta"]["hebergement"]["ban"]["motif"] == "dataset_non_heberge"
    assert api.reads("adresses") == []


async def test_zone_routes_follow_the_scope_and_the_derived_departement(api, migrated):
    _scope(migrated, without={"dpe"})
    commune = {"type": "commune", "code": "44109"}

    response = await api.client.get(
        "/v1/zone/resolve", params={**commune, "datasets": "dvf"}
    )
    assert response.status_code == 200, response.text
    dvf = response.json()["donnees"]
    assert dvf["total_count"] == len(DVF_IDS)
    assert {row["id_mutation"] for row in dvf["results"]} == set(DVF_IDS)

    api.statements.clear()
    response = await api.client.get(
        "/v1/zone/resolve", params={**commune, "datasets": "dpe"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"]["results"] == []
    assert body["donnees"]["total_count"] == 0
    assert body["donnees"]["has_next"] is False
    assert body["meta"]["hebergement"]["dpe"]["motif"] == "dataset_non_heberge"
    assert api.reads("diagnostics_dpe") == []

    response = await api.client.get("/v1/zone/stats", params=commune)
    assert response.status_code == 200, response.text
    body = response.json()
    stats = body["donnees"]
    assert stats["nb_transactions"] == len(DVF_IDS)
    assert stats["prix_median_m2"] == 2200.0
    assert "dpe_distribution" in stats
    assert stats["dpe_distribution"] is None  # null, never 0 (SEM-01)
    assert body["meta"]["hebergement"]["dpe"]["motif"] == "dataset_non_heberge"
    assert body["meta"]["hebergement"]["dvf"]["etat"] == "heberge"
    # The hosted synthesis (population, taux_vacance) checks its view's
    # dependencies by relation name; no statement reads the DPE table itself.
    assert api.reads("FROM diagnostics_dpe") == []

    # Whole catalogue hosted on 44/85: 33 is named directly (type=departement)
    # or derived from a commune code (33063), and is out of the territory.
    _scope(migrated)
    for params in (
        {"type": "departement", "code": "33"},
        {"type": "commune", "code": "33063"},
    ):
        api.statements.clear()
        response = await api.client.get("/v1/zone/stats", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["donnees"].values()) == {None}, body["donnees"]
        assert {
            key: value["motif"] for key, value in body["meta"]["hebergement"].items()
        } == {
            "dvf": "territoire_hors_perimetre",
            "dpe": "territoire_hors_perimetre",
            "commune": "territoire_hors_perimetre",
        }
        response = await api.client.get(
            "/v1/zone/resolve", params={**params, "datasets": "dvf"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["donnees"]["total_count"] == 0
        for table in ("mutations_grain", "diagnostics_dpe", "v_commune_synthese"):
            assert api.reads(table) == [], table


# -- WR-03 of 02-08 (decision 2026-09-22): parcelles, adresses and rnb_liens
# are sources; an unhosted one is never read. ------------------------------


@pytest.mark.parametrize("without", [(), ("cadastre",)], ids=["all", "no-cadastre"])
async def test_gps_location_comes_from_the_commune_referential(api, migrated, without):
    with psycopg.connect(migrated) as conn:
        conn.execute(
            "INSERT INTO vacance (code_commune, nom_commune, code_departement, "
            "nb_logements_prives, nb_logements_vacants, taux_vacance, annee) "
            "VALUES ('44109', 'Nantes', '44', 1000, 70, 7.0, 2024) "
            "ON CONFLICT DO NOTHING"
        )
    _scope(migrated, without=set(without))
    lat, lon = NANTES
    api.statements.clear()
    response = await api.client.get("/v1/vacance", params={"lat": lat, "lon": lon})

    assert response.status_code == 200, response.text
    assert [row["code_commune"] for row in response.json()["donnees"]] == ["44109"]
    assert api.reads("geo_communes") != []
    # The fixture holds no parcel: the former parcelles lookup answered 404.
    assert api.reads("parcelles") == []


async def test_gps_point_outside_every_commune_is_404(api, migrated):
    _scope(migrated)
    response = await api.client.get("/v1/vacance", params={"lat": 0.0, "lon": 0.0})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("ban_hosted", [True, False], ids=["ban", "no-ban"])
async def test_dvf_by_address_reads_the_ban_only_while_hosted(
    api, migrated, ban_hosted
):
    _scope(migrated, without=set() if ban_hosted else {"ban"})
    api.statements.clear()
    response = await api.client.get(
        "/v1/dvf/adresse", params={"q": "1 rue du test 44000 Nantes"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    if ban_hosted:
        assert api.reads("adresses") != []
        assert "hebergement" not in (body.get("meta") or {})
    else:
        assert body["donnees"] == []
        assert body["meta"]["hebergement"]["ban"]["motif"] == "dataset_non_heberge"
        assert api.reads("adresses") == []
        assert api.reads("mutations") == []


# -- C52 (B2): the generic routes qualify the departement they are asked about.
# The departement comes from the commune referential (point), the INSEE prefix
# (parcel reference) or the retained address; never from a dataset table. -----

_POINT_ROUTES = [
    ("/v1/dvf", "dvf", "mutations_grain"),
    ("/v1/dpe", "dpe", "diagnostics_dpe"),
    ("/v1/parcelle", "cadastre", "parcelles"),
]


@pytest.mark.parametrize(("path", "key", "table"), _POINT_ROUTES)
async def test_a_point_outside_the_territory_is_out_of_coverage_without_read(
    api, migrated, path, key, table
):
    _scope(migrated)  # whole catalogue on 44/85
    lat, lon = PARIS
    api.statements.clear()

    response = await api.client.get(path, params={"lat": lat, "lon": lon})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"][key]["motif"] == "territoire_hors_perimetre"
    assert api.reads("geo_communes") != []
    assert api.reads(table) == []


@pytest.mark.parametrize(("path", "key", "table"), _POINT_ROUTES)
@pytest.mark.parametrize("point", [NANTES, (0.0, 0.0)], ids=["nantes", "no-commune"])
async def test_a_point_inside_or_without_proof_keeps_the_former_read(
    api, migrated, path, key, table, point
):
    # Nantes is hosted; (0, 0) lies in no commune: no proof of « hors
    # couverture » (02-07 rule), the table is read as before.
    _scope(migrated)
    lat, lon = point
    api.statements.clear()

    response = await api.client.get(path, params={"lat": lat, "lon": lon})

    assert response.status_code == 200, response.text
    assert "hebergement" not in (response.json().get("meta") or {})
    assert api.reads(table) != []


async def test_dvf_at_a_point_of_a_departement_without_dvf_coverage(api, migrated):
    _scope(migrated, departements=("44", "57"))
    lat, lon = METZ
    api.statements.clear()

    response = await api.client.get("/v1/dvf", params={"lat": lat, "lon": lon})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["dvf"]["motif"] == "source_sans_couverture"
    assert api.reads("mutations_grain") == []


@pytest.mark.parametrize(
    ("ref", "hors"), [("75101000AB0001", True), ("44109000HN0129", False)]
)
async def test_parcel_reference_is_qualified_by_its_insee_prefix(
    api, migrated, ref, hors
):
    _scope(migrated)
    api.statements.clear()

    response = await api.client.get(f"/v1/parcelle/{ref}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    if hors:
        assert body["meta"]["hebergement"]["cadastre"]["motif"] == (
            "territoire_hors_perimetre"
        )
        assert api.reads("parcelles") == []
    else:
        assert "hebergement" not in (body.get("meta") or {})
        assert api.reads("parcelles") != []


async def test_dvf_by_address_retained_in_the_territory_serves_its_search(
    api, migrated
):
    _scope(migrated)
    api.statements.clear()

    response = await api.client.get(
        "/v1/dvf/adresse", params={"q": "1 rue du test 44000 Nantes"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recherche"]["etat"] == "point_retenu"
    assert body["recherche"]["ban_id"] == "44109_0001_00001"
    assert body["recherche"]["rayon_m"] == 50.0
    assert "hebergement" not in (body.get("meta") or {})
    assert api.reads("mutations_grain") != []


@pytest.mark.parametrize(
    ("q", "hors"),
    [
        ("99 avenue introuvable 75001 Paris", True),
        ("99 boulevard introuvable 44300 Nantes", False),
    ],
    ids=["postal-75", "postal-44"],
)
async def test_dvf_by_address_not_found_is_qualified_by_its_postal_code(
    api, migrated, q, hors
):
    _scope(migrated)
    api.statements.clear()

    response = await api.client.get("/v1/dvf/adresse", params={"q": q})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["recherche"]["etat"] == "adresse_non_trouvee"
    # The BAN read is never skipped because of a postal prefix.
    assert api.reads("adresses") != []
    assert api.reads("mutations_grain") == []
    if hors:
        assert {
            key: value["motif"] for key, value in body["meta"]["hebergement"].items()
        } == {"dvf": "territoire_hors_perimetre", "ban": "territoire_hors_perimetre"}
    else:
        assert "hebergement" not in (body.get("meta") or {})


async def test_dvf_by_address_retained_out_of_coverage_is_never_a_zero_sale(
    api, migrated
):
    # C55 (fix 6, WR-04): 44/57 instance, the BAN hosts 57 but DVF does not
    # cover it; the retained address of Metz is qualified by its own
    # departement: the reason, never an empty list read as zero sale.
    _scope(migrated, departements=("44", "57"))
    lat, lon = METZ
    with psycopg.connect(migrated) as conn:
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie, "
            "code_postal, code_commune, nom_commune, position) VALUES "
            "(92000057, '57', '57463_0001_00001', '1', 'rue de la moselle', "
            "'57000', '57463', 'Metz', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (lon, lat),
        )
    api.statements.clear()
    try:
        response = await api.client.get(
            "/v1/dvf/adresse", params={"q": "1 rue de la moselle 57000 Metz"}
        )
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute("DELETE FROM adresses WHERE id = 92000057")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recherche"]["etat"] == "point_retenu"
    assert body["recherche"]["ban_id"] == "57463_0001_00001"
    assert body["donnees"] == []
    assert body["meta"]["hebergement"]["dvf"]["motif"] == "source_sans_couverture"
    assert api.reads("mutations_grain") == []


@contextmanager
def mutations_au_point(dsn, lignes):
    """Mutations positioned at the BAN point of the Nantes address (C55).

    Inserted by the test that reads them and deleted after it, so that the
    counts of the other tests (``DVF_IDS``) never change. Each line is
    ``(id_mutation, nature_mutation, est_vente, hors_stats_vefa)``; all share
    one date and one price.
    """
    lat, lon = NANTES
    with psycopg.connect(dsn) as conn:
        for id_mutation, nature, est_vente, vefa in lignes:
            conn.execute(
                "INSERT INTO mutations_grain (code_departement, id_mutation, "
                "date_mutation, nature_mutation, valeur_fonciere, classe_mutation, "
                "nb_locaux_maison, nb_lignes, nb_dispositions, nb_communes, "
                "nb_parcelles, surface_bati_totale, mono_local, est_vente, "
                "hors_stats_vefa, code_commune, nom_commune, position) VALUES "
                "('44', %s, '2024-06-01', %s, 300000, 'residentiel_maison', 1, 1, "
                "1, 1, 1, 100, true, %s, %s, '44109', 'Nantes', "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (id_mutation, nature, est_vente, vefa, lon, lat),
            )
    try:
        yield
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "DELETE FROM mutations_grain WHERE id_mutation = ANY(%s)",
                ([ligne[0] for ligne in lignes],),
            )


async def test_dvf_by_address_serves_sales_and_vefa(api, migrated):
    # C56 (D-12): a VEFA is a sale, served and flagged ``hors_stats_vefa``;
    # an exchange positioned at the retained point is not a sale (``est_vente``).
    _scope(migrated)
    lignes = (
        ("MUT-ADR-VENTE", "Vente", True, False),
        ("MUT-ADR-ECHANGE", "Echange", False, False),
        ("MUT-ADR-VEFA", "Vente en l'état futur d'achèvement", False, True),
    )

    with mutations_au_point(migrated, lignes):
        response = await api.client.get(
            "/v1/dvf/adresse", params={"q": "1 rue du test 44000 Nantes"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recherche"]["etat"] == "point_retenu"
    flags = {row["id_mutation"]: row["hors_stats_vefa"] for row in body["donnees"]}
    assert flags == {"MUT-ADR-VENTE": False, "MUT-ADR-VEFA": True}


async def test_dvf_by_address_pages_are_ordered_by_a_unique_key(api, migrated):
    # C55 (fix 3): two sales of the same date and price; the mutation key
    # breaks the tie, so the pages never repeat nor skip one. Inserted out of
    # key order: without the tie-break the physical order would show.
    _scope(migrated)
    lignes = (
        ("MUT-ADR-B", "Vente", True, False),
        ("MUT-ADR-A", "Vente", True, False),
    )
    params = {"q": "1 rue du test 44000 Nantes", "limite": 1}

    with mutations_au_point(migrated, lignes):
        premiere = await api.client.get("/v1/dvf/adresse", params=params)
        curseur = premiere.json()["pagination"]["curseur_suivant"]
        seconde = await api.client.get(
            "/v1/dvf/adresse", params={**params, "curseur": curseur}
        )

    assert premiere.status_code == 200 and seconde.status_code == 200
    pages = [
        [row["id_mutation"] for row in reponse.json()["donnees"]]
        for reponse in (premiere, seconde)
    ]
    assert pages == [["MUT-ADR-A"], ["MUT-ADR-B"]]


async def test_dvf_by_address_serves_one_row_per_mutation(api, migrated):
    # D-128 (plan 04.1-15, replaces IN-04 of C58): two rows of ONE
    # id_mutation are one sale, served once, as the address page lists
    # (``DISTINCT ON (mg.id_mutation)`` keeping the lowest row key). Inserted
    # with the higher key first, so that the physical order contradicts it.
    _scope(migrated)
    lat, lon = NANTES
    with psycopg.connect(migrated) as conn:
        for id_ligne, surface in ((990000002, 102), (990000001, 101)):
            conn.execute(
                "INSERT INTO mutations_grain (id, code_departement, id_mutation, "
                "date_mutation, nature_mutation, valeur_fonciere, classe_mutation, "
                "nb_locaux_maison, nb_lignes, nb_dispositions, nb_communes, "
                "nb_parcelles, surface_bati_totale, mono_local, est_vente, "
                "hors_stats_vefa, code_commune, nom_commune, position) VALUES "
                "(%s, '44', 'MUT-ADR-DOUBLE', '2024-06-01', 'Vente', 300000, "
                "'residentiel_maison', 1, 1, 1, 1, 1, %s, true, true, false, "
                "'44109', 'Nantes', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (id_ligne, surface, lon, lat),
            )
    params = {"q": "1 rue du test 44000 Nantes", "limite": 1}
    try:
        premiere = await api.client.get("/v1/dvf/adresse", params=params)
        toutes = await api.client.get(
            "/v1/dvf/adresse", params={"q": params["q"], "limite": 10}
        )
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute(
                "DELETE FROM mutations_grain WHERE id_mutation = 'MUT-ADR-DOUBLE'"
            )

    assert premiere.status_code == 200 and toutes.status_code == 200
    # One sale: page 1 carries it (its lowest row) and no next page.
    assert [row["surface_reelle_bati"] for row in premiere.json()["donnees"]] == [101]
    assert premiere.json()["pagination"]["curseur_suivant"] is None
    assert [row["id_mutation"] for row in toutes.json()["donnees"]] == [
        "MUT-ADR-DOUBLE"
    ]


async def test_dvf_by_address_pages_turn_around_one_point_of_tied_addresses(
    api, migrated
):
    # D-128 (plan 04.1-15, C58): each page geocodes again with ``limit=1``.
    # A second BAN address tied on every criterion (same text, lower
    # ban_id, inserted after the first one) 300 m north: every page turns
    # around the point of the lowest ban_id, never the physical first row.
    _scope(migrated)
    lat, lon = NANTES
    loin = (lat + 0.003, lon)
    with psycopg.connect(migrated) as conn:
        conn.execute(
            "INSERT INTO adresses (id, code_departement, ban_id, numero, nom_voie, "
            "code_postal, code_commune, nom_commune, position) VALUES "
            "(92000002, '44', '44109_0000_00001', '1', 'rue du test', '44000', "
            "'44109', 'Nantes', ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (loin[1], loin[0]),
        )
        for id_mutation, (p_lat, p_lon), valeur in (
            ("MUT-PT-ORIGINE", NANTES, 300000),
            ("MUT-PT-LOIN-1", loin, 310000),
            ("MUT-PT-LOIN-2", loin, 290000),
        ):
            conn.execute(
                "INSERT INTO mutations_grain (code_departement, id_mutation, "
                "date_mutation, nature_mutation, valeur_fonciere, classe_mutation, "
                "nb_locaux_maison, nb_lignes, nb_dispositions, nb_communes, "
                "nb_parcelles, surface_bati_totale, mono_local, est_vente, "
                "hors_stats_vefa, code_commune, nom_commune, position) VALUES "
                "('44', %s, '2024-06-01', 'Vente', %s, 'residentiel_maison', 1, 1, "
                "1, 1, 1, 100, true, true, false, '44109', 'Nantes', "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (id_mutation, valeur, p_lon, p_lat),
            )
    params = {"q": "1 rue du test 44000 Nantes", "limite": 1}
    try:
        premiere = await api.client.get("/v1/dvf/adresse", params=params)
        curseur = premiere.json()["pagination"]["curseur_suivant"]
        seconde = await api.client.get(
            "/v1/dvf/adresse", params={**params, "curseur": curseur}
        )
    finally:
        with psycopg.connect(migrated) as conn:
            conn.execute("DELETE FROM adresses WHERE id = 92000002")
            conn.execute(
                "DELETE FROM mutations_grain WHERE id_mutation LIKE 'MUT-PT-%'"
            )

    assert premiere.status_code == 200 and seconde.status_code == 200
    reponses = [reponse.json() for reponse in (premiere, seconde)]
    assert [r["recherche"]["ban_id"] for r in reponses] == ["44109_0000_00001"] * 2
    pages = [[row["id_mutation"] for row in r["donnees"]] for r in reponses]
    assert pages == [["MUT-PT-LOIN-1"], ["MUT-PT-LOIN-2"]]


def _absent_inventory():
    return DpeInventoryServiceResult(
        page=DpeInventoryPage(
            state="absent",
            context_ref="opaque-context",
            total=0,
            distribution=DpeInventoryDistribution(),
            page_size=20,
            has_more=False,
            read_state="complete",
        ),
        apartment_document_count=0,
    )


@pytest.mark.parametrize("rnb_hosted", [True, False], ids=["rnb", "no-rnb"])
async def test_logements_reads_rnb_links_only_while_rnb_is_hosted(
    api, migrated, monkeypatch, rnb_hosted
):
    async def inventory(_ban_id):
        return _absent_inventory()

    # The exact-address inventory is not under test; the RNB widening then
    # runs on this database, its statements captured.
    monkeypatch.setattr(logements_module, "_charge_inventaire", inventory)
    monkeypatch.setattr(logements_module, "async_session", api.sessions)
    _scope(migrated, without=set() if rnb_hosted else {"rnb"})
    api.statements.clear()
    response = await api.client.get(
        "/v1/bien/logements",
        params={"id_parcelle": "44109000HN0129", "ban_id": "44109_0001_00001"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    if rnb_hosted:
        assert api.reads("rnb_liens") != []
    else:
        assert api.reads("rnb_liens") == []
        assert body["meta"]["hebergement"]["rnb"]["motif"] == "dataset_non_heberge"
    # The other sources of the route are read in both cases.
    assert api.reads("copropriete") != []


@pytest.mark.parametrize("out", ["dpe", "dvf", "copropriete"])
async def test_logements_refuses_before_any_read_once_one_source_is_out(
    api, migrated, monkeypatch, out
):
    # Decision of 2026-09-26 (orchestrator, delegated by Ianis): one of dpe,
    # dvf, copropriete out of coverage refuses the whole route (409) before any
    # read, instead of serving the unhosted DPE inventory (WINDOWS 37/45).
    monkeypatch.setattr(logements_module, "async_session", api.sessions)
    _scope(migrated, without={out})
    api.statements.clear()
    response = await api.client.get(
        "/v1/bien/logements",
        params={"id_parcelle": "44109000HN0129", "ban_id": "44109_0001_00001"},
    )

    assert response.status_code == 409, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"] == f"Hors couverture — {out}: dataset_non_heberge"
    for table in (
        "diagnostics_dpe",
        "adresses",
        "rnb_liens",
        "copropriete",
        "mutation",
    ):
        assert api.reads(table) == [], table


@pytest.mark.parametrize(
    "cadastre_hosted", [True, False], ids=["cadastre", "no-cadastre"]
)
async def test_foncier_pm_radius_comes_from_the_commune_referential(
    api, migrated, cadastre_hosted
):
    # WR-04 (2nd review): the radius communes come from geo_communes, never
    # from the cadastre parcels, hosted or not.
    with psycopg.connect(migrated) as conn:
        conn.execute("DELETE FROM foncier_pm WHERE code_commune = '44109'")
        conn.execute(
            "INSERT INTO foncier_pm (code_departement, code_commune, code_parcelle, "
            "numero_siren, denomination) VALUES "
            "('44', '44109', '44109000HN0129', '123456789', 'SCI TEST')"
        )
    _scope(migrated, without=set() if cadastre_hosted else {"cadastre"})
    lat, lon = NANTES
    api.statements.clear()
    response = await api.client.get(
        "/v1/foncier-pm", params={"lat": lat, "lon": lon, "rayon_m": 500}
    )

    assert response.status_code == 200, response.text
    assert [row["numero_siren"] for row in response.json()["donnees"]] == ["123456789"]
    assert api.reads("geo_communes") != []
    assert [sql for sql in api.statements if re.search(r"\bparcelles\b", sql)] == []

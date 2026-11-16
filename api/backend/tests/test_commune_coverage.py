"""/v1/commune never serves 0 for an out-of-coverage source (KI-D4, SEM-01).

One projection, ``apply_commune_coverage``, masks the synthesis for its three
consumers: ``/v1/commune``, the ``commune`` dispatch of ``/v1/resolve`` and
``/v1/zone/stats``. The database-backed zero/absence scenarios live in
``tests/integration/test_commune_coverage_contract.py``.
"""

import ast
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.common import geo
from app.common.publication_readiness import require_publication
from app.common.source_catalog import DATASET_CATALOG
from app.commune import router as commune_router
from app.commune.schemas import (
    COMMUNE_FIELD_SOURCES,
    COMMUNE_SOURCE_KEYS,
    NON_SEMANTIC_COMMUNE_COLUMNS,
    ResultatCommuneSynthese,
    apply_commune_coverage,
    commune_coverage,
)
from app.database import get_db
from app.datasets.provenance import ProvenanceFacts
from app.resolve import multi
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from app.zone import router as zone_router

pytestmark = pytest.mark.unit

APP_DIR = Path(__file__).resolve().parents[1] / "app"
WITHOUT_EQUIPEMENTS = InstanceScope(
    frozenset({*COMMUNE_SOURCE_KEYS, "dvf", "dpe"} - {"equipements"}),
    ("44", "85"),
)
WITH_EQUIPEMENTS = InstanceScope(
    frozenset({*COMMUNE_SOURCE_KEYS, "dvf", "dpe"}), ("44", "85")
)

# A synthesis row as the view returns it: equipements measured at 0.
ROW = {
    "code_insee": "44109",
    "nom_commune": "Nantes",
    "code_departement": "44",
    "code_region": "52",
    "population": 320732,
    "annee_demographie": 2021,
    "revenu_median": 22480.0,
    "taux_vacance": 7.8,
    "annee_vacance": 2024,
    "nb_equipements": 0,
    "annee_equipements": 2022,
    "nb_monuments": 3,
}


def test_every_column_has_a_source_or_is_declared_non_semantic():
    columns = set(ResultatCommuneSynthese.model_fields) - {"couverture"}
    assert columns - set(COMMUNE_FIELD_SOURCES) - NON_SEMANTIC_COMMUNE_COLUMNS == set()
    # Identity columns come with the row, never from a masked source.
    identity = {"code_insee", "nom_commune", "code_departement", "code_region"}
    assert identity <= NON_SEMANTIC_COMMUNE_COLUMNS
    assert not set(COMMUNE_FIELD_SOURCES) & identity
    assert set(COMMUNE_FIELD_SOURCES.values()) <= set(COMMUNE_SOURCE_KEYS)
    assert set(COMMUNE_SOURCE_KEYS) <= set(DATASET_CATALOG)


def test_a_non_hosted_source_is_null_with_its_reason_never_zero():
    served = apply_commune_coverage(ROW, commune_coverage(WITHOUT_EQUIPEMENTS, "44109"))
    assert ROW["nb_equipements"] == 0  # without masking, 0 would be served
    assert served["nb_equipements"] is None
    assert served["annee_equipements"] is None
    assert served["couverture"]["equipements"].motif == "dataset_non_heberge"
    # Other sources untouched.
    assert served["population"] == 320732
    assert served["nb_monuments"] == 3


def test_a_hosted_source_keeps_its_measured_zero():
    served = apply_commune_coverage(ROW, commune_coverage(WITH_EQUIPEMENTS, "44109"))
    assert served["nb_equipements"] == 0
    assert served["couverture"]["equipements"].etat == "heberge"


def test_a_commune_outside_the_departements_is_masked_with_the_territory():
    served = apply_commune_coverage(
        {**ROW, "code_insee": "33063"}, commune_coverage(WITH_EQUIPEMENTS, "33063")
    )
    # demographie itself is out of the territory: no row to serve.
    assert served is None


def test_without_the_structural_source_no_row_is_served():
    scope = InstanceScope(
        frozenset(set(COMMUNE_SOURCE_KEYS) - {"demographie"}), ("44", "85")
    )
    assert apply_commune_coverage(ROW, commune_coverage(scope, "44109")) is None


# -- the three consumers serve the same projection ---------------------------


def _app(scope):
    app = FastAPI()
    app.include_router(commune_router.router)
    app.include_router(zone_router.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_instance_scope] = lambda: scope
    app.dependency_overrides[require_publication] = lambda: None
    return app


@pytest.fixture
def synthesis(monkeypatch):
    """The same view row for every consumer; records the hosted sources."""
    calls = []

    async def rows(_db, codes, sources):
        calls.append(sorted(sources))
        return [dict(ROW)] if "44109" in codes else []

    async def no_facts(_db, _names):
        return {}

    async def empty(*_args, **_kwargs):
        return {}

    for module in (commune_router, zone_router, multi):
        monkeypatch.setattr(module, "commune_synthese_par_codes", rows)
    monkeypatch.setattr(commune_router, "get_provenance_facts", no_facts)
    # The yearly price series is another read of the route (migration 115).
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(zone_router, "zone_stats_dvf", empty)
    monkeypatch.setattr(zone_router, "zone_stats_dpe", empty)
    return calls


async def _three_payloads(scope):
    transport = httpx.ASGITransport(app=_app(scope))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        commune = await client.get("/v1/commune", params={"code_insee": "44109"})
        zone = await client.get(
            "/v1/zone/stats", params={"type": "commune", "code": "44109"}
        )
    assert commune.status_code == 200, commune.text
    assert zone.status_code == 200, zone.text
    resolved, _ = await multi._dispatch_db(
        "commune", object(), 47.2, -1.55, "44109", scope=scope
    )
    return commune.json()["donnees"][0], resolved, zone.json()["donnees"]


@pytest.mark.parametrize("scope", [WITHOUT_EQUIPEMENTS, WITH_EQUIPEMENTS])
async def test_router_resolver_and_zone_stats_serve_the_same_values(synthesis, scope):
    commune, resolved, zone = await _three_payloads(scope)
    for column in set(ROW):
        assert commune[column] == resolved[column], column
    assert {
        source: hosting.model_dump(mode="json")
        for source, hosting in resolved["couverture"].items()
    } == commune["couverture"]
    assert (zone["population"], zone["taux_vacance"]) == (
        commune["population"],
        commune["taux_vacance"],
    )
    expected = None if scope is WITHOUT_EQUIPEMENTS else 0
    assert commune["nb_equipements"] == resolved["nb_equipements"] == expected
    # The availability barrier only reads the hosted sources, on each path.
    hosted = sorted(
        s for s in COMMUNE_SOURCE_KEYS if s != "equipements" or scope.hosts(s)
    )
    # router, zone/stats, then the resolver dispatch
    assert synthesis == [hosted, hosted, hosted]


@pytest.mark.parametrize(
    ("point_commune", "code_insee"), [("44109", "44109"), ("33063", "33063")]
)
async def test_gps_and_code_insee_give_the_same_answer(
    synthesis, monkeypatch, point_commune, code_insee
):
    # WR-01: the commune of a point comes from the referential; a point in
    # Bordeaux on a 44/85 scope is out of the territory, as its code_insee.
    async def referential(_session, _lat, _lon):
        return point_commune

    monkeypatch.setattr(geo, "commune_at_point", referential)
    transport = httpx.ASGITransport(app=_app(WITH_EQUIPEMENTS))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        by_point = await client.get("/v1/commune", params={"lat": 45.0, "lon": -0.5})
        by_code = await client.get("/v1/commune", params={"code_insee": code_insee})
    assert by_point.status_code == by_code.status_code == 200
    assert by_point.json()["donnees"] == by_code.json()["donnees"]
    point_meta, code_meta = by_point.json()["meta"], by_code.json()["meta"]
    assert point_meta.get("hebergement") == code_meta.get("hebergement")
    if point_commune == "33063":
        assert by_point.json()["donnees"] == []
        assert point_meta["hebergement"]["commune"]["motif"] == (
            "territoire_hors_perimetre"
        )
        # Neither request read the view.
        assert synthesis == []
    else:
        assert by_point.json()["donnees"][0]["code_insee"] == "44109"


async def test_zone_stats_masks_a_non_hosted_vacance(synthesis):
    scope = InstanceScope(
        frozenset({*COMMUNE_SOURCE_KEYS, "dvf", "dpe"} - {"vacance"}), ("44", "85")
    )
    commune, resolved, zone = await _three_payloads(scope)
    assert ROW["taux_vacance"] == 7.8
    assert zone["taux_vacance"] is None
    assert commune["taux_vacance"] is None and resolved["taux_vacance"] is None
    assert zone["population"] == 320732


async def test_resolver_dispatch_refuses_to_serve_without_scope(synthesis):
    with pytest.raises(RuntimeError, match="instance scope"):
        await multi._dispatch_db("commune", object(), 47.2, -1.55, "44109")


@pytest.mark.parametrize(
    ("code_insee", "expected"),
    [
        ("44109", [["transports", "gares"]]),
        ("33063", []),
        (None, [["transports", "gares"]]),
    ],
)
async def test_resolver_transports_follow_the_commune_departement(
    monkeypatch, code_insee, expected
):
    # IN-04 (resolver dispatch next to the commune one): the departement of
    # the commune decides the hosted branches; outside it nothing is read.
    calls = []

    async def query(_db, _lat, _lon, *, limit, sources):
        calls.append(list(sources))
        return [{"stop_name": "ARRET-A"}]

    monkeypatch.setattr(multi, "transports_a_proximite", query)
    data, _ = await multi._dispatch_db(
        "transports",
        object(),
        47.2,
        -1.55,
        code_insee,
        scope=InstanceScope(frozenset({"transports", "gares"}), ("44", "85")),
    )
    assert calls == expected
    assert (data is None) == (expected == [])


# -- structured provenance on /v1/commune ------------------------------------


async def test_commune_meta_carries_a_period_per_source(monkeypatch, synthesis):
    source_date = datetime(2026, 3, 1, tzinfo=UTC)

    async def facts(_db, names):
        return {
            "revenus": ProvenanceFacts(source_period_label="2021"),
            "dpe": ProvenanceFacts(source_updated_at=source_date),
            # An import date alone never makes a period.
            "vacance": ProvenanceFacts(last_success_at=source_date),
        }

    monkeypatch.setattr(commune_router, "get_provenance_facts", facts)
    transport = httpx.ASGITransport(app=_app(WITH_EQUIPEMENTS))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        body = (await client.get("/v1/commune", params={"code_insee": "44109"})).json()
    sources = {entry["dataset"]: entry for entry in body["meta"]["source"]}
    assert set(sources) == set(COMMUNE_SOURCE_KEYS)
    for entry in sources.values():
        assert entry["periode"]["statut"] in {"connue", "glissante", "inconnue"}
        assert entry["nom"] and entry["licence"]
    assert sources["revenus"]["periode"] == {
        "statut": "connue",
        "label": "2021",
        "debut": None,
        "fin": None,
    }
    assert sources["dpe"]["periode"]["label"] == "2026-03-01"
    assert sources["vacance"]["periode"]["statut"] == "inconnue"
    assert sources["vacance"]["periode"]["label"] is None
    assert sources["vacance"]["derniere_importation_reussie"] is not None
    assert sources["monuments"]["periode"]["statut"] == "inconnue"


# -- no fourth path without the projection -----------------------------------


def _imported_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.name for alias in node.names}
    return names


def _called_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                names.add(function.id)
            elif isinstance(function, ast.Attribute):
                names.add(function.attr)
    return names


def missing_projection(modules: dict[str, str]) -> list[str]:
    """Modules calling the synthesis query without calling the projection.

    A call, not an import (IN-07): importing the projection without using it
    masks no value.
    """
    missing = []
    for name, source in sorted(modules.items()):
        called = _called_names(ast.parse(source))
        if (
            "commune_synthese_par_codes" in called
            and "apply_commune_coverage" not in called
        ):
            missing.append(name)
    return missing


def _app_modules() -> dict[str, str]:
    return {
        str(path.relative_to(APP_DIR.parent)): path.read_text(encoding="utf-8")
        for path in APP_DIR.rglob("*.py")
    }


def test_every_consumer_of_the_synthesis_imports_the_projection():
    modules = _app_modules()
    consumers = [
        name
        for name, source in modules.items()
        if "commune_synthese_par_codes" in _imported_names(ast.parse(source))
    ]
    assert sorted(consumers) == sorted(
        [
            # 04-03: landmark 4 of the address page, the commune median.
            str(Path("app/adresse/service.py")),
            str(Path("app/commune/router.py")),
            str(Path("app/resolve/multi.py")),
            str(Path("app/zone/router.py")),
        ]
    )
    assert missing_projection(modules) == []


def test_a_consumer_without_the_projection_is_detected():
    control = str(Path("app/controle/router.py"))
    imports_only = str(Path("app/controle/imports_only.py"))
    modules = {
        **_app_modules(),
        control: (
            "from app.commune.queries import commune_synthese_par_codes\n"
            "async def serve(db):\n"
            "    return await commune_synthese_par_codes(db, ['44109'], [])\n"
        ),
        # Importing the projection is not applying it.
        imports_only: (
            "from app.commune.queries import commune_synthese_par_codes\n"
            "from app.commune.schemas import apply_commune_coverage\n"
            "async def serve(db):\n"
            "    return await commune_synthese_par_codes(db, ['44109'], ['x'])\n"
        ),
    }
    assert missing_projection(modules) == sorted([control, imports_only])

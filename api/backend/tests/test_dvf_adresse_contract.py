"""C52 (B1): ``/v1/dvf/adresse`` is a proximity search around ONE retained point.

The address is geocoded on the instance BAN and its first result is retained;
the route lists the DVF sales positioned within 50 m of that point. It never
describes the history of an address nor the identity of a building. Without a
retained address the empty list is not a zero sale; a geocoder outage is a
503, never an empty list (SEM-01). The geocoder and the DVF reader are
replaced; the whole catalogue is hosted (``client`` fixture).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from app.ban.queries import GeocodageIndisponibleError
from app.database import get_db
from app.dvf import router as dvf_router
from app.main import app

pytestmark = pytest.mark.unit

SAISIE = "1 rue confidentielle 44000 Nantes"
ADRESSE = {
    "ban_id": "44109_0001_00001",
    "numero": "1",
    "rep": None,
    "nom_voie": "Rue Confidentielle",
    "code_postal": "44000",
    "code_commune": "44109",
    "nom_commune": "Nantes",
    "longitude": -1.5536,
    "latitude": 47.2184,
    "score": 0.9,
}


async def _mock_db():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    yield session


@pytest.fixture()
def lecteurs(client, monkeypatch):
    """The geocoder and the DVF reader of the route, replaced."""
    app.dependency_overrides[get_db] = _mock_db
    geocoder = AsyncMock(return_value=[ADRESSE])
    dvf_par_adresse = AsyncMock(return_value=[])
    monkeypatch.setattr(dvf_router, "geocoder", geocoder)
    monkeypatch.setattr(dvf_router, "dvf_par_adresse", dvf_par_adresse)
    return geocoder, dvf_par_adresse


@pytest.mark.asyncio
async def test_the_retained_point_and_the_radius_are_served(client, lecteurs):
    geocoder, dvf_par_adresse = lecteurs

    response = await client.get("/v1/dvf/adresse", params={"q": SAISIE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recherche"] == {
        "etat": "point_retenu",
        "ban_id": "44109_0001_00001",
        "code_commune": "44109",
        "latitude": 47.2184,
        "longitude": -1.5536,
        "rayon_m": 50.0,
    }
    # The first result only, an outage raised rather than an empty list.
    assert geocoder.await_args.kwargs == {"limit": 1, "erreurs": "lever"}
    kwargs = dvf_par_adresse.await_args.kwargs
    assert (kwargs["lat"], kwargs["lon"], kwargs["rayon_m"]) == (
        47.2184,
        -1.5536,
        50.0,
    )
    assert "hebergement" not in (body.get("meta") or {})


@pytest.mark.asyncio
async def test_no_retained_address_is_not_a_zero_sale_and_reads_no_sale(
    client, lecteurs
):
    geocoder, dvf_par_adresse = lecteurs
    geocoder.return_value = []

    response = await client.get("/v1/dvf/adresse", params={"q": SAISIE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["donnees"] == []
    assert body["recherche"] == {
        "etat": "adresse_non_trouvee",
        "ban_id": None,
        "code_commune": None,
        "latitude": None,
        "longitude": None,
        "rayon_m": 50.0,
    }
    dvf_par_adresse.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_geocoder_outage_is_a_503_never_an_empty_list(client, lecteurs):
    geocoder, dvf_par_adresse = lecteurs
    geocoder.side_effect = GeocodageIndisponibleError()

    response = await client.get("/v1/dvf/adresse", params={"q": SAISIE})

    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["code"] == "geocodage_indisponible"
    assert body["status"] == 503
    # The problem never quotes the input.
    assert "confidentielle" not in response.text.lower()
    dvf_par_adresse.assert_not_awaited()


class _SessionEnPanne:
    """A session whose every statement fails with *error* (real geocoder)."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        raise self.error


def _db_error(sqlstate: str):
    class DriverError(Exception):
        pass

    original = DriverError("synthetic driver failure")
    original.sqlstate = sqlstate
    return DBAPIError("SELECT synthetic", {}, original)


@pytest.mark.asyncio
async def test_an_expected_database_failure_of_the_geocoder_is_the_503(client):
    async def db():
        yield _SessionEnPanne(_db_error("57014"))

    app.dependency_overrides[get_db] = db

    response = await client.get("/v1/dvf/adresse", params={"q": SAISIE})

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "geocodage_indisponible"


@pytest.mark.asyncio
async def test_another_error_of_the_geocoder_is_never_the_geocoding_503(client):
    # C55 (fix 4): a Python error keeps the normal 500 path of the
    # application, never presented as a temporary geocoding outage.
    async def db():
        yield _SessionEnPanne(RuntimeError("synthetic bug"))

    app.dependency_overrides[get_db] = db

    response = await client.get("/v1/dvf/adresse", params={"q": SAISIE})

    assert response.status_code == 500, response.text
    assert "geocodage_indisponible" not in response.text


def test_the_public_contract_names_a_proximity_search_never_a_history():
    schema = app.openapi()
    operation = schema["paths"]["/v1/dvf/adresse"]["get"]
    summary = operation["summary"].lower()
    description = operation["description"].lower()

    assert "proximité" in summary
    # The radius is read from the constant the route uses (IN-03).
    rayon = f"{dvf_router.RAYON_ADRESSE_M:g} m"
    for attendu in ("proximité", "premier résultat", rayon, "point retenu"):
        assert attendu in description, attendu
    for promesse in (
        "historique dvf",
        "historique complet",
        "immeuble",
        "toutes les transactions",
        "numero, voie et code postal",
    ):
        assert promesse not in summary and promesse not in description, promesse
    assert "503" in operation["responses"]
    reference = operation["responses"]["200"]["content"]["application/json"]
    nom = reference["schema"]["$ref"].rsplit("/", 1)[-1]
    proprietes = schema["components"]["schemas"][nom]["properties"]
    assert "recherche" in proprietes


def test_the_public_contract_names_the_natures_listed_and_excluded():
    # C56 and IN-02 (C58): the natures served are exactly those of the
    # predicate est_vente OR hors_stats_vefa (app.etl_dvf.qualification);
    # « Vente terrain à bâtir » is not a sale there.
    from app.etl_dvf.qualification import NATURE_VEFA, NATURE_VENTE

    description = app.openapi()["paths"]["/v1/dvf/adresse"]["get"]["description"]

    for nature in (NATURE_VENTE, NATURE_VEFA):
        assert f"« {nature} »" in description, nature
    assert "« Vente terrain à bâtir »" in description
    assert "exclu" in description

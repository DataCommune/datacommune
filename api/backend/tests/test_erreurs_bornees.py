"""C59 I2: the generic 422 and 500 bodies never reflect the real path.

The 422 and 500 problem bodies name the route template in ``instance``, never
the real path (a BAN key, an address typed as a key), as the business problems
of the address routes; a 500 of an address route is never stored by a cache.
The 500 log itself is the subject of the ``public-launch`` branch (bounded
``log_unhandled_exception``), not of this lot.
"""

from unittest.mock import MagicMock

import pytest

from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit

SECRET = "1 rue confidentielle nantes"
CLE = "44109_7777_00042"


@pytest.fixture
def db_factice():
    async def db():
        yield MagicMock()

    app.dependency_overrides[get_db] = db


def _defaut_chaine(*args, **kwargs):
    """A defect whose message and chained cause both quote the input."""
    try:
        raise ValueError(f"cause quoting {SECRET} and {CLE}")
    except ValueError as cause:
        raise RuntimeError(f"defect quoting {SECRET} and {CLE}") from cause


@pytest.mark.asyncio
async def test_a_500_of_an_address_route_names_the_template_and_is_never_stored(
    client, monkeypatch, db_factice
):
    from app.adresse import router as adresse_router

    monkeypatch.setattr(adresse_router, "lire_fiche_adresse", _defaut_chaine)

    response = await client.get(f"/v1/adresse/{CLE}")

    assert response.status_code == 500, response.text
    assert response.json()["instance"] == "/v1/adresse/{ban_id}"
    assert CLE not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_a_500_elsewhere_keeps_its_headers(client, monkeypatch, db_factice):
    from app.dvf import router as dvf_router

    monkeypatch.setattr(dvf_router, "geocoder", _defaut_chaine)

    response = await client.get("/v1/dvf/adresse", params={"q": SECRET})

    assert response.status_code == 500
    assert response.json()["instance"] == "/v1/dvf/adresse"
    assert "cache-control" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chemin", "valeur"),
    [
        # An address typed where a key is expected (review sol-F, I2).
        ("/v1/adresse/{}", "1 rue confidentielle nantes"),
        # A key one character too long for the pattern.
        ("/v1/adresse/{}/ventes", f"{CLE}9"),
    ],
    ids=["address-as-key", "invalid-key"],
)
async def test_a_422_never_reflects_the_value_in_its_body(client, chemin, valeur):
    response = await client.get(chemin.format(valeur))

    assert response.status_code == 422, response.text
    corps = response.text.lower()
    assert valeur.lower() not in corps
    assert "confidentielle" not in corps
    assert response.json()["instance"] == chemin.format("{ban_id}")
    assert response.headers["cache-control"] == "no-store"

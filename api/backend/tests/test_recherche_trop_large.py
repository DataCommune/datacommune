"""R-08: a search too broad for its budget is not an outage (2026-11-10).

Without a commune detected nor a full-text result, the geocoder ends on
Strategy D, a trigram read over every hosted departement under its own
budget (``BUDGET_STRATEGIE_D_MS``). On the national base an input such as
« equeurdreville » or « rue victr hugo » runs out of that budget at every
try: the cancellation is the same each time, and « Relancer » reruns the
same read. It is neither an outage (I-4: an absence is never read as an
outage, nor the reverse) nor an absence (the read was stopped before it
could tell): ``/v1/adresses/recherche`` answers 200 with
``etat = "trop_large"``.

A real outage keeps its 503: a cancellation that comes before the budget
has run out is not the budget's doing, and any other availability failure
of Strategy D stays an outage whatever its timing.

Only the session is replaced (no database): Strategy D is the statement
carrying ``search_text % unaccent``; every other read answers nothing.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from app.ban import queries as ban_queries
from app.ban.queries import (
    GeocodageIndisponibleError,
    RechercheTropLargeError,
    geocoder,
)
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit

# A street with a typo and no commune: no full-text match, no commune
# detected, not a commune name: the search reaches Strategy D.
FAUTE_DE_FRAPPE = "rue victr hugo"
STRATEGIE_D = "search_text % unaccent"
# Short budget so the test waits a few hundredths of a second, not two.
BUDGET_MS = 20


def _asyncpg(nom: str) -> DBAPIError:
    """The asyncpg error *nom*, as SQLAlchemy hands it over."""
    import asyncpg.exceptions

    cls = getattr(asyncpg.exceptions, nom)
    original = cls("canceling statement")
    return DBAPIError("SELECT 1", {}, original)


class _Session:
    """Strategy D fails with *erreur* after *attente* seconds; the rest is empty."""

    def __init__(self, erreur: Exception, attente: float) -> None:
        self.erreur = erreur
        self.attente = attente

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        if STRATEGIE_D in str(statement):
            await asyncio.sleep(self.attente)
            raise self.erreur
        result = MagicMock()
        result.first.return_value = None
        result.__iter__.return_value = iter([])
        result.scalar_one.return_value = "15s"
        return result


@pytest.fixture()
def budget_court(monkeypatch):
    monkeypatch.setattr(ban_queries, "BUDGET_STRATEGIE_D_MS", BUDGET_MS)


def _session(app_session) -> None:
    async def db():
        yield app_session

    app.dependency_overrides[get_db] = db


# --- The geocoder -----------------------------------------------------------


async def test_strategy_d_past_its_budget_raises_the_too_broad_error(budget_court):
    session = _Session(_asyncpg("QueryCanceledError"), BUDGET_MS / 1000 * 2)

    with pytest.raises(RechercheTropLargeError):
        await geocoder(session, FAUTE_DE_FRAPPE, 8, erreurs="lever")


async def test_strategy_d_past_its_budget_stays_silent_under_taire(budget_court):
    session = _Session(_asyncpg("QueryCanceledError"), BUDGET_MS / 1000 * 2)

    assert await geocoder(session, FAUTE_DE_FRAPPE, 8) == []


async def test_a_cancellation_before_the_budget_is_an_outage():
    # Default budget (2 s), cancelled at once: not the budget's doing.
    session = _Session(_asyncpg("QueryCanceledError"), 0)

    with pytest.raises(GeocodageIndisponibleError) as erreur:
        await geocoder(session, FAUTE_DE_FRAPPE, 8, erreurs="lever")
    assert not isinstance(erreur.value, RechercheTropLargeError)


async def test_another_outage_of_strategy_d_after_the_budget_is_an_outage(
    budget_court,
):
    session = _Session(_asyncpg("ConnectionDoesNotExistError"), BUDGET_MS / 1000 * 2)

    with pytest.raises(GeocodageIndisponibleError) as erreur:
        await geocoder(session, FAUTE_DE_FRAPPE, 8, erreurs="lever")
    assert not isinstance(erreur.value, RechercheTropLargeError)


# --- The route ----------------------------------------------------------------


async def test_the_search_too_broad_answers_200_typed(client, budget_court):
    _session(_Session(_asyncpg("QueryCanceledError"), BUDGET_MS / 1000 * 2))

    response = await client.get(
        "/v1/adresses/recherche", params={"q": FAUTE_DE_FRAPPE, "limite": 8}
    )

    assert response.status_code == 200, response.text
    donnees = response.json()["donnees"]
    assert donnees["etat"] == "trop_large"
    assert donnees["resultats"] == []
    # The read was stopped: nothing is said of the number typed either.
    assert donnees["correspondance_numero"] is None
    assert response.headers["cache-control"] == "no-store"


async def test_a_search_that_completes_is_aboutie(client):
    # Strategy D answers nothing at once: an absence, not a broad search.
    class _Vide(_Session):
        async def execute(self, statement, params=None):
            result = MagicMock()
            result.first.return_value = None
            result.__iter__.return_value = iter([])
            result.scalar_one.return_value = "15s"
            return result

    _session(_Vide(None, 0))

    response = await client.get("/v1/adresses/recherche", params={"q": FAUTE_DE_FRAPPE})

    assert response.status_code == 200, response.text
    assert response.json()["donnees"]["etat"] == "aboutie"
    assert response.json()["donnees"]["resultats"] == []


async def test_a_real_outage_of_strategy_d_stays_a_503(client):
    # Témoin (I-4): cancelled before the budget, the search is down.
    _session(_Session(_asyncpg("QueryCanceledError"), 0))

    response = await client.get("/v1/adresses/recherche", params={"q": FAUTE_DE_FRAPPE})

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "geocodage_indisponible"

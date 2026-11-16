"""A read slower than the deadline of its page is not thrown away (R-07).

Observed 2026-11-10 on an address never opened: the Géorisques risk report
answered after the cut of the page, twice in a row, each view starting a read
it abandoned; six themes stayed « indisponible » until the producer happened
to answer fast. The deadline bounds the wait of the page, never the read: the
read ends in the background, within its own bound, and the next view finds
its answer without calling the producer again. An outage is still never kept,
and a producer that never answers still never holds a page.
"""

import asyncio
import time

import httpx
import pytest
import respx

from app.adresse import lieu
from app.common.exceptions import UpstreamUnavailableError
from app.rapport import partage
from app.rapport.partage import RapportRisques
from tests.test_rapport_risques import _contexte_amont, _donnees_rapport
from tests.test_rapport_themes import POINT

pytestmark = pytest.mark.unit

ECHEANCE_S = 0.3
REPONSE_S = 0.6  # the producer answers after the deadline of the page
MARGE_S = 0.25


@pytest.fixture(autouse=True)
def _echeance_courte(monkeypatch):
    partage.vider_caches()
    monkeypatch.setattr(partage, "DEADLINE_S", ECHEANCE_S)
    yield
    partage.vider_caches()


class _Producteur:
    """A risk report of Géorisques that answers after *delai_s*, or fails."""

    def __init__(self, delai_s: float, en_panne: bool = False) -> None:
        self.delai_s, self.en_panne, self.appels = delai_s, en_panne, 0

    async def __call__(self, _chemin: str, _params: dict) -> dict:
        self.appels += 1
        await asyncio.sleep(self.delai_s)
        if self.en_panne:
            raise UpstreamUnavailableError("Georisques", "HTTP 500")
        return _donnees_rapport(POINT.lon, POINT.lat)


async def _vue() -> tuple[object, float]:
    """One view of the address: its own request, its own memo."""
    debut = time.perf_counter()
    lu = await partage.rapport_risques(_contexte_amont(POINT))
    return lu, time.perf_counter() - debut


async def test_a_read_slower_than_the_deadline_serves_the_next_view(monkeypatch):
    producteur = _Producteur(REPONSE_S)
    monkeypatch.setattr(partage, "get_georisques", producteur)
    premiere, duree = await _vue()
    # The page is never held beyond its deadline, and says the outage.
    assert isinstance(premiere, lieu.PanneAmont)
    assert duree < ECHEANCE_S + MARGE_S, f"{duree:.2f} s"
    await asyncio.sleep(REPONSE_S)  # the read ends in the background
    seconde, duree = await _vue()
    assert isinstance(seconde, RapportRisques)
    assert duree < 0.1, f"{duree:.2f} s"
    assert producteur.appels == 1


async def test_two_views_of_a_cold_address_share_one_read(monkeypatch):
    producteur = _Producteur(0.1)
    monkeypatch.setattr(partage, "get_georisques", producteur)
    (une, _), (autre, _) = await asyncio.gather(_vue(), _vue())
    assert isinstance(une, RapportRisques)
    assert isinstance(autre, RapportRisques)
    assert producteur.appels == 1


async def test_an_outage_that_ends_in_the_background_is_never_kept(monkeypatch):
    en_panne = _Producteur(REPONSE_S, en_panne=True)
    monkeypatch.setattr(partage, "get_georisques", en_panne)
    premiere, _ = await _vue()
    assert isinstance(premiere, lieu.PanneAmont)
    await asyncio.sleep(REPONSE_S)  # the read fails in the background
    retabli = _Producteur(0.0)
    monkeypatch.setattr(partage, "get_georisques", retabli)
    seconde, _ = await _vue()
    assert isinstance(seconde, RapportRisques)
    assert (en_panne.appels, retabli.appels) == (1, 1)


async def test_a_producer_that_never_answers_is_dropped_at_the_background_bound(
    monkeypatch,
):
    monkeypatch.setattr(partage, "DELAI_FOND_S", 2 * ECHEANCE_S)
    cache = partage.CacheAmont()
    lancees, abandonnees = 0, 0

    async def muet():
        nonlocal lancees, abandonnees
        lancees += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            abandonnees += 1
            raise

    for attendues in (1, 2):
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(ECHEANCE_S):
                await cache.obtenir("k", muet)
        # The page gave up; the read is still bounded, and leaves nothing.
        assert (lancees, abandonnees) == (attendues, attendues - 1)
        await asyncio.sleep(ECHEANCE_S + MARGE_S)
        assert (lancees, abandonnees) == (attendues, attendues)


def test_the_deadline_ends_the_wait_before_the_bounds_of_the_read():
    # A report answering between the former read bound (4 s) and the deadline
    # (5 s) is served on the first view; past the deadline the read goes on,
    # under bounds of its own.
    assert lieu.AMONT_DEADLINE_S < partage.DELAI_FOND_S
    assert partage._TIMEOUT_GEORISQUES.read == partage.DELAI_FOND_S
    assert partage._TIMEOUT_GEORISQUES.connect <= lieu.AMONT_DEADLINE_S


# --- A refused connection of the Géoplateforme is tried once more ---------------
#
# Observed 2026-11-10: a first view opens seven connections to data.geopf.fr at
# once; the sixth and seventh were refused in 0.6 s (three cold views out of
# three from the workstation, one out of three in production), and « Revenus »
# was served with a partial outage that the next view did not have.


async def test_a_refused_connection_is_tried_once_more():
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(partage.WFS_URL).mock(
            side_effect=[
                httpx.ConnectError("refusée"),
                httpx.Response(200, json={"features": []}),
            ]
        )
        reponse = await partage._get_terrain(partage.WFS_URL, {})
    assert reponse.json() == {"features": []}
    assert route.call_count == 2


async def test_two_refused_connections_are_an_outage_after_one_more_try():
    with respx.mock() as mock:
        route = mock.get(partage.WFS_URL).mock(
            side_effect=httpx.ConnectError("refusée")
        )
        with pytest.raises(UpstreamUnavailableError):
            await partage._get_terrain(partage.WFS_URL, {})
    assert route.call_count == 2


async def test_a_connection_that_times_out_is_not_tried_again():
    # Only a refusal, which comes at once, is tried again: a second wait of the
    # connect bound would leave nothing of the deadline of the page.
    with respx.mock() as mock:
        route = mock.get(partage.WFS_URL).mock(
            side_effect=httpx.ConnectTimeout("lente")
        )
        with pytest.raises(UpstreamUnavailableError):
            await partage._get_terrain(partage.WFS_URL, {})
    assert route.call_count == 1

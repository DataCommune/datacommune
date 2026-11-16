"""Upstream readers of the address report stay bounded, without any network.

``lire_amonts`` has no deadline of its own: the page stays up only because
every reader carries one. A reader without it would hold the report until the
server loader gives up (``DELAI_API_SSR_MS``, 10 s) and turn the whole page into
an outage, where one theme should have been. The upstreams below never answer
and ignore the httpx timeouts, as a connection that trickles bytes does, so
only the ``asyncio`` deadlines of the readers can end the read.
"""

import asyncio
import time

import httpx
import pytest

from app.adresse import lieu, risques
from app.georisques import client as georisques
from app.rapport import partage, service
from app.rapport.registre import THEMES
from app.rapport.socle import ContexteAmont, Memo
from app.scope.dependencies import RouteHosting
from tests.test_rapport_themes import AS_OF, HEBERGE, POINT, _contexte

pytestmark = pytest.mark.unit

DELAI_CHARGEUR_S = 10.0  # DELAI_API_SSR_MS, frontend/src/lib/server/api-route-fetch.ts
DELAI_TEST_S = 0.5
# Below two deadlines: two reads in a row, each with its own deadline, fail too.
LIMITE_TEST_S = 1.9 * DELAI_TEST_S
FILET_S = 5.0


class _ToutHeberge(dict):
    """Every source hosted, so that every reader calls its upstream."""

    def __missing__(self, _cle: str):
        return HEBERGE


class _ClientMuet:
    """An httpx client whose upstream never answers."""

    appels = 0

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def get(self, *_args, **_kwargs):
        type(self).appels += 1
        await asyncio.Event().wait()


@pytest.fixture
def amonts_muets(monkeypatch):
    partage.vider_caches()
    _ClientMuet.appels = 0
    monkeypatch.setattr(httpx, "AsyncClient", _ClientMuet)
    monkeypatch.setattr(partage, "DEADLINE_S", DELAI_TEST_S)
    monkeypatch.setattr(lieu, "AMONT_DEADLINE_S", DELAI_TEST_S)
    monkeypatch.setattr(risques, "RGA_DEADLINE_S", DELAI_TEST_S)
    monkeypatch.setattr(georisques, "RGA_DEADLINE_S", DELAI_TEST_S)
    yield
    partage.vider_caches()


def test_les_delais_reels_laissent_de_la_marge_au_chargeur():
    assert lieu.AMONT_DEADLINE_S < DELAI_CHARGEUR_S
    assert georisques.RGA_DEADLINE_S < DELAI_CHARGEUR_S


async def test_aucun_lecteur_amont_ne_depasse_son_delai(amonts_muets):
    definitions = [d for d in THEMES if d.amont is not None]
    contexte = ContexteAmont(
        identite=_contexte().identite,
        point=POINT,
        hosting=RouteHosting(_ToutHeberge()),
        as_of=AS_OF,
        memo=Memo(),
    )

    debut = time.perf_counter()
    async with asyncio.timeout(FILET_S):  # a failure, never a hanging suite
        amonts = await service.lire_amonts(definitions, contexte)
    duree = time.perf_counter() - debut

    assert set(amonts) == {d.id for d in definitions}
    assert _ClientMuet.appels >= 1
    assert duree < LIMITE_TEST_S, f"{duree:.2f} s"

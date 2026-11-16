"""Every upstream client of the address report reuses one TLS context.

httpx builds a new TLS context for each ``AsyncClient()`` by default: it
loads the whole CA bundle, synchronously, on the event loop. The upstream
readers of a report start about thirty clients at once, so the loop stayed
blocked for their sum (18 ms each on python:3.12-slim, 240 ms on the Windows
workstation, measured 2026-11-13) and the deadlines of the other reads ran
out meanwhile. One shared context costs that once.
"""

import asyncio
from typing import ClassVar

import httpx
import pytest

from app.common.http import CONTEXTE_TLS
from app.rapport import partage, service
from app.rapport.registre import THEMES
from app.rapport.socle import ContexteAmont, Memo
from app.scope.dependencies import RouteHosting
from tests.test_rapport_amonts_bornes import _ToutHeberge
from tests.test_rapport_themes import AS_OF, POINT, _contexte

pytestmark = pytest.mark.unit


class _ClientTemoin:
    """An httpx client that records how it was built and answers nothing."""

    verifs: ClassVar[list[object]] = []

    def __init__(self, *_args, **kwargs) -> None:
        type(self).verifs.append(kwargs.get("verify", True))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def get(self, *_args, **_kwargs):
        raise httpx.ConnectError("no network in unit tests")

    def stream(self, *_args, **_kwargs):
        raise httpx.ConnectError("no network in unit tests")


async def test_les_lecteurs_amont_partagent_un_contexte_tls(monkeypatch):
    partage.vider_caches()
    _ClientTemoin.verifs = []
    monkeypatch.setattr(httpx, "AsyncClient", _ClientTemoin)
    contexte = ContexteAmont(
        identite=_contexte().identite,
        point=POINT,
        hosting=RouteHosting(_ToutHeberge()),
        as_of=AS_OF,
        memo=Memo(),
    )
    try:
        async with asyncio.timeout(10):
            await service.lire_amonts([d for d in THEMES if d.amont], contexte)
    finally:
        partage.vider_caches()

    assert len(_ClientTemoin.verifs) >= 10
    propres = [v for v in _ClientTemoin.verifs if v is not CONTEXTE_TLS]
    assert not propres, f"{len(propres)} clients build their own TLS context"

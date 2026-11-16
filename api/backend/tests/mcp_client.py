"""The official MCP client against the real application, in process (D-09).

The client is the ``mcp`` package (dev group only); the server is ``app.main.app``
with its middlewares. Only the database is replaced, by the tests that use this.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.common.publication_readiness import require_publication
from app.config import settings
from app.database import get_db
from app.main import app
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE

VISITEUR = ("198.51.100.40", 40000)
URL_MCP = "http://test/mcp"


@asynccontextmanager
async def client_mcp(
    visiteur: tuple[str, int] = VISITEUR,
    headers: dict[str, str] | None = None,
    event_hooks: dict | None = None,
) -> AsyncIterator[ClientSession]:
    """An initialized MCP session talking to ``/mcp`` of the application."""
    transport = httpx.ASGITransport(app=app, client=visiteur)
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=headers,
            event_hooks=event_hooks,
        ) as http,
        streamable_http_client(URL_MCP, http_client=http) as (lire, ecrire, _),
        ClientSession(lire, ecrire) as session,
    ):
        await session.initialize()
        yield session


@contextmanager
def application_ouverte(monkeypatch) -> Iterator[None]:
    """MCP open, whole catalogue hosted, no database: reads are doubles."""
    monkeypatch.setattr(settings, "mcp_enabled", True)

    async def pas_de_session():
        yield object()

    app.dependency_overrides[get_db] = pas_de_session
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    app.dependency_overrides[require_publication] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.clear()

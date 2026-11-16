"""In-process channel from an MCP tool call to the existing /v1 routes (D-03).

A tool never reads data itself: it replays the /v1 route through the whole
application (middlewares, coverage, publication guard, deadlines, RFC 9457
errors), exactly as an HTTP client would. The subrequest is marked in the ASGI
scope state, which no network request can set: the authentication dependency
then reads it without a key, on the visitor's per-IP buckets, and the global
ceiling does not count it a second time (the POST /mcp already counted).

What crosses from the MCP request: the peer address and the two forwarded IP
headers, so ``_get_client_ip`` resolves the same visitor. Never a cookie, never
an API key: an MCP call does not act as a session.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

CANAL_MCP = "canal_mcp"
# Forwarded IP headers read by app.middleware._get_client_ip, copied as received.
_EN_TETES_IP = ("cf-connecting-ip", "x-forwarded-for")
# A /v1 address page may wait on upstream sources (Géorisques, IGN): its own
# deadlines answer before this one.
_DELAI_SECONDES = 60.0


def est_appel_mcp(request: Request) -> bool:
    """True for a subrequest of the MCP channel, never for a network request."""
    return bool(request.scope.get("state", {}).get(CANAL_MCP))


class _MarqueMcp:
    """ASGI wrapper: marks the scope and keeps the visitor's peer address."""

    def __init__(self, app: ASGIApp, client: tuple[str, int] | None) -> None:
        self.app = app
        self.client = client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        etat = {**scope.get("state", {}), CANAL_MCP: True}
        scope = {**scope, "state": etat, "client": self.client}
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class ReponseV1:
    """Status, decoded JSON body and Retry-After of a replayed /v1 route."""

    statut: int
    corps: Any
    retry_after: str | None = None


async def lire_v1(
    request: Request, chemin: str, params: dict[str, str] | None = None
) -> ReponseV1:
    """GET *chemin* on this application as the visitor of *request*."""
    client = (request.client.host, request.client.port) if request.client else None
    en_tetes = {
        nom: request.headers[nom] for nom in _EN_TETES_IP if nom in request.headers
    }
    en_tetes["accept"] = "application/json"
    transport = httpx.ASGITransport(app=_MarqueMcp(request.app, client))
    # In-process only: no network transport, no proxy read from the environment,
    # hence no TLS context (each one blocked the loop ~142 ms on Windows, see the
    # project post-mortems). Built per call: it costs about 0.02 ms (2026-11-15).
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://mcp.interne",
        timeout=_DELAI_SECONDES,
        trust_env=False,
    ) as http:
        reponse = await http.get(chemin, params=params, headers=en_tetes)
    try:
        corps = reponse.json()
    except ValueError:
        corps = None
    return ReponseV1(reponse.status_code, corps, reponse.headers.get("retry-after"))

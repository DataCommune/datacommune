"""Gate, transport and ceilings of the MCP server (plan 04.12-01, D-02 to D-04).

Raw HTTP for what a client would never send (closed gate, foreign Origin,
GET, malformed JSON-RPC); the official MCP client for the rest. The real
limiter and the real authentication dependency are played; no database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app import rate_limit
from app.commune import router as commune_router
from app.config import settings
from app.main import app
from app.mcp.protocole import VERSIONS
from tests.mcp_client import application_ouverte, client_mcp

pytestmark = pytest.mark.unit

OUTILS_ATTENDUS = {
    "chercher_adresse",
    "lire_adresse",
    "comparer_adresses",
    "lire_commune",
    "comparer_communes",
    "lister_jeux_de_donnees",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "essai", "version": "0"},
    },
}


@pytest.fixture
def ouverte(monkeypatch):
    with application_ouverte(monkeypatch):
        yield


@pytest.fixture
def commune_double(monkeypatch, ouverte):
    async def lignes(_db, codes, _sources):
        return [{"code_insee": code, "population": 1000} for code in codes]

    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", lignes)
    doubles = {
        "synthese_autre_maille": [],
        "fenetre_mediane_commune": None,
        "tendances_prix_par_codes": {},
        "get_provenance_facts": {},
    }
    for nom, retour in doubles.items():
        monkeypatch.setattr(commune_router, nom, AsyncMock(return_value=retour))


async def _post(corps, headers=None, client=("198.51.100.50", 40000)):
    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.post("/mcp", json=corps, headers=headers or {})


# --- Gate, Origin, transport --------------------------------------------------


async def test_closed_by_default_answers_404_and_reads_nothing():
    assert settings.mcp_enabled is False
    reponse = await _post(INITIALIZE)
    assert reponse.status_code == 404


async def test_a_foreign_origin_is_refused(ouverte):
    reponse = await _post(INITIALIZE, {"Origin": "https://exemple.invalid"})
    assert reponse.status_code == 403
    # The site itself, or no Origin at all (an agent on a server), passes.
    reponse = await _post(INITIALIZE, {"Origin": "https://datacommune.fr"})
    assert reponse.status_code == 200


async def test_no_server_stream_get_answers_405(ouverte):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        reponse = await http.get("/mcp")
    assert reponse.status_code == 405
    assert reponse.headers["allow"] == "POST"


async def test_version_negotiation(ouverte):
    reponse = await _post(INITIALIZE)
    resultat = reponse.json()["result"]
    assert resultat["protocolVersion"] == "2025-11-25"
    assert resultat["capabilities"] == {"tools": {"listChanged": False}}
    ancienne = {**INITIALIZE, "params": {**INITIALIZE["params"]}}
    ancienne["params"]["protocolVersion"] = "2025-03-26"
    assert (await _post(ancienne)).json()["result"]["protocolVersion"] == "2025-03-26"
    inconnue = {**INITIALIZE, "params": {**INITIALIZE["params"]}}
    inconnue["params"]["protocolVersion"] = "1999-01-01"
    assert (await _post(inconnue)).json()["result"]["protocolVersion"] == VERSIONS[0]
    refusee = await _post(INITIALIZE, {"MCP-Protocol-Version": "1999-01-01"})
    assert refusee.status_code == 400


async def test_json_rpc_errors_and_notifications(ouverte):
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert (await _post(notification)).status_code == 202
    inconnue = await _post({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert inconnue.json()["error"]["code"] == -32601
    outil = await _post(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "x"}}
    )
    assert outil.json()["error"]["code"] == -32602
    assert (await _post({"id": 4})).status_code == 400


async def test_the_official_client_lists_six_french_read_only_tools(ouverte):
    async with client_mcp() as session:
        outils = (await session.list_tools()).tools
        await session.send_ping()

    assert {outil.name for outil in outils} == OUTILS_ATTENDUS
    for outil in outils:
        assert outil.annotations.readOnlyHint is True
        assert outil.outputSchema["type"] == "object"
        assert outil.description


async def test_invalid_arguments_are_a_tool_error_the_agent_can_read(ouverte):
    async with client_mcp() as session:
        resultat = await session.call_tool("lire_commune", {"code_insee": "abc"})
    assert resultat.isError is True
    assert "code_insee" in resultat.content[0].text


# --- Channel to /v1 and ceilings ----------------------------------------------


async def test_a_network_request_without_key_keeps_its_401(commune_double):
    # MCP open, keyless access closed: only the in-process channel reads keyless.
    assert settings.keyless_access_enabled is False
    transport = httpx.ASGITransport(app=app, client=("198.51.100.51", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        reponse = await http.get(
            "/v1/commune",
            params={"code_insee": "44109"},
            headers={"x-canal-mcp": "1", "cookie": "canal_mcp=1"},
        )
    assert reponse.status_code == 401


async def test_a_tool_call_spends_the_visitor_buckets_once(commune_double):
    postes: list[str] = []

    async def compter(requete: httpx.Request) -> None:
        postes.append(requete.method)

    visiteur = ("198.51.100.52", 40000)
    async with client_mcp(visiteur, event_hooks={"request": [compter]}) as session:
        resultat = await session.call_tool("lire_commune", {"code_insee": "44109"})

    assert resultat.isError is False, resultat.content
    # Every POST of the client (initialize, initialized, tools/call and the
    # tools/list the client sends to read the output schema) counts once in the
    # global ceiling; the /v1 replay does not count there a second time, but
    # once in the visitor's buckets.
    assert len(rate_limit._buckets["ip:198.51.100.52"]) == postes.count("POST") == 4
    assert len(rate_limit._buckets["anon:ip:198.51.100.52"]) == 1
    assert len(rate_limit._buckets["anon:ip-day:198.51.100.52"]) == 1


async def test_the_visitor_ceiling_is_a_tool_error_with_its_delay(
    monkeypatch, commune_double
):
    monkeypatch.setattr(settings, "anon_rate_limit_per_ip_minute", 2)
    async with client_mcp(("198.51.100.53", 40000)) as session:
        for _ in range(2):
            ok = await session.call_tool("lire_commune", {"code_insee": "44109"})
            assert ok.isError is False
        refus = await session.call_tool("lire_commune", {"code_insee": "44109"})

    assert refus.isError is True
    assert "429" in refus.content[0].text
    assert "Réessayer dans" in refus.content[0].text


async def test_the_forwarded_visitor_is_the_one_counted(monkeypatch, commune_double):
    # Behind the trusted proxy, the visitor is CF-Connecting-IP, for the /v1
    # replay as for the POST itself.
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    entetes = {"CF-Connecting-IP": "203.0.113.7"}
    async with client_mcp(("198.51.100.2", 40000), headers=entetes) as session:
        await session.call_tool("lire_commune", {"code_insee": "44109"})

    assert len(rate_limit._buckets["anon:ip:203.0.113.7"]) == 1
    assert "anon:ip:198.51.100.2" not in rate_limit._buckets


async def test_the_v1_replay_never_builds_a_network_transport(
    monkeypatch, commune_double
):
    # A network client builds a TLS context, which blocked the loop ~142 ms per
    # client on Windows (project post-mortem); a proxy of the environment would
    # build one too. The replay is in process: none may be built, even then.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    def interdit(*_args, **_kwargs):
        raise AssertionError("network transport built by the MCP channel")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", interdit)
    async with client_mcp(("198.51.100.54", 40000)) as session:
        resultat = await session.call_tool("lire_commune", {"code_insee": "44109"})

    assert resultat.isError is False, resultat.content

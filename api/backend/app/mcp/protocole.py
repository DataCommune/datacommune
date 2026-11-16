"""MCP over stateless HTTP at ``/mcp`` (D-02): JSON-RPC, JSON responses only.

``POST /mcp`` carries one JSON-RPC message: a request gets its response as
``application/json``, a notification or a client response gets ``202``. No
session is created and no server stream is offered: ``GET`` and ``DELETE``
answer 405. Closed by default (``MCP_ENABLED``): 404 before anything is read.

Written here rather than taken from the MCP SDK: the server side needs a
handful of methods, and the SDK would bring some fifteen runtime packages
into the image. The official client (dev group) proves the interoperability.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.config import settings
from app.mcp.contrat import REGLE_DE_LECTURE
from app.mcp.outils import OUTILS, OutilError

logger = logging.getLogger(__name__)

# Newest first; the first is answered to a client asking for an unknown one.
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
_SANS_CACHE = {"Cache-Control": "no-store"}

INSTRUCTIONS = (
    "DataCommune rassemble des données publiques ouvertes à l'adresse et à la "
    "commune (ventes DVF, DPE de l'ADEME, Géorisques, Base Adresse Nationale, "
    "INSEE…). Pour une adresse : chercher_adresse, puis lire_adresse avec la clé "
    "BAN rendue. Pour une commune : lire_commune avec son code INSEE. "
    + REGLE_DE_LECTURE
    + " Le code est ouvert (AGPL-3.0-or-later) et auto-hébergeable."
)

mcp_router = APIRouter()


def _probleme(statut: int, titre: str) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": titre, "status": statut},
        status_code=statut,
        media_type="application/problem+json",
        headers=_SANS_CACHE,
    )


def _reponse(identifiant: Any, resultat: dict) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": identifiant, "result": resultat},
        headers=_SANS_CACHE,
    )


def _erreur(identifiant: Any, code: int, message: str, statut: int = 200) -> Response:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": identifiant,
            "error": {"code": code, "message": message},
        },
        status_code=statut,
        headers=_SANS_CACHE,
    )


def _origine_refusee(request: Request) -> bool:
    """An Origin header, when sent, must be a known one (DNS rebinding guard)."""
    origine = request.headers.get("origin")
    return origine is not None and origine not in (
        *settings.cors_origins,
        *settings.mcp_origines,
    )


def _resultat_d_erreur(texte: str) -> dict:
    return {"content": [{"type": "text", "text": texte}], "isError": True}


async def _appeler_outil(request: Request, params: dict) -> dict:
    """``tools/call``: validated arguments, /v1 replayed, structured result."""
    outil = OUTILS[params["name"]]
    try:
        entree = outil.entree.model_validate(params.get("arguments") or {})
    except ValidationError as erreur:
        champs = ", ".join(
            ".".join(str(p) for p in e["loc"]) or "arguments" for e in erreur.errors()
        )
        return _resultat_d_erreur(f"Arguments invalides : {champs}.")
    try:
        sortie = await outil.executer(request, entree)
    except OutilError as erreur:
        return _resultat_d_erreur(str(erreur))
    structure = sortie.model_dump(mode="json", exclude_none=True)
    texte = json.dumps(structure, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": texte}],
        "structuredContent": structure,
        "isError": False,
    }


async def _traiter(request: Request, identifiant: Any, methode: str, params: Any):
    if not isinstance(params, dict):
        return _erreur(identifiant, -32602, "Paramètres invalides")
    if methode == "initialize":
        demandee = params.get("protocolVersion")
        return _reponse(
            identifiant,
            {
                "protocolVersion": demandee if demandee in VERSIONS else VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "datacommune",
                    "title": "DataCommune",
                    "version": request.app.version,
                },
                "instructions": INSTRUCTIONS,
            },
        )
    if methode == "ping":
        return _reponse(identifiant, {})
    if methode == "tools/list":
        return _reponse(
            identifiant, {"tools": [outil.definition() for outil in OUTILS.values()]}
        )
    if methode == "tools/call":
        if params.get("name") not in OUTILS:
            return _erreur(identifiant, -32602, "Outil inconnu")
        return _reponse(identifiant, await _appeler_outil(request, params))
    return _erreur(identifiant, -32601, "Méthode inconnue")


@mcp_router.post("/mcp", include_in_schema=False)
async def recevoir(request: Request) -> Response:
    """One JSON-RPC message per POST, answered in JSON or by 202."""
    if not settings.mcp_enabled:
        return _probleme(404, "Not Found")
    if _origine_refusee(request):
        return _probleme(403, "Origine refusée")
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version not in VERSIONS:
        return _erreur(None, -32600, "Version du protocole non prise en charge", 400)
    try:
        message = json.loads(await request.body())
    except ValueError:
        return _erreur(None, -32700, "JSON illisible", 400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _erreur(None, -32600, "Message JSON-RPC 2.0 attendu", 400)
    if "method" not in message or "id" not in message:
        # A notification, or a response to a request this server never sends.
        return Response(status_code=202, headers=_SANS_CACHE)
    identifiant = message["id"]
    try:
        return await _traiter(
            request, identifiant, message["method"], message.get("params") or {}
        )
    # An agent gets a JSON-RPC error; the log keeps the trace.
    except Exception:
        logger.exception("mcp_erreur_interne", extra={"methode": message["method"]})
        return _erreur(identifiant, -32603, "Erreur interne")


@mcp_router.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
async def refuser(request: Request) -> Response:
    """No server stream and no session: 405 (404 while closed)."""
    if not settings.mcp_enabled:
        return _probleme(404, "Not Found")
    reponse = _probleme(405, "Méthode non prise en charge")
    reponse.headers["Allow"] = "POST"
    return reponse

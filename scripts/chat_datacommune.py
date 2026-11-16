"""Chat interactif DataCommune — Claude interroge l'API live.

Usage:
    pip install anthropic httpx
    set ANTHROPIC_API_KEY=sk-ant-...
    set DC_API_KEY=dc_live_...
    python scripts/chat_datacommune.py

Pose des questions en francais, Claude appelle les bons endpoints.
Tape 'quit' pour sortir.
"""

import json
import os
import sys

import anthropic
import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE = "https://api.datacommune.fr/v1"
API_KEY = os.environ.get("DC_API_KEY")
if not API_KEY:
    sys.exit("ERREUR: variable d'environnement DC_API_KEY manquante. Voir ~/.secrets/api-keys.env")
HEADERS = {"X-API-Key": API_KEY}
MODEL = "claude-sonnet-4-6-20250514"

# ---------------------------------------------------------------------------
# Tools — each maps to a GET endpoint on the API
# ---------------------------------------------------------------------------

ENDPOINT_MAP = {
    "parcelle": "/parcelle",
    "dpe": "/dpe",
    "dvf": "/dvf",
    "commune": "/commune",
    "revenus": "/revenus",
    "loyers": "/loyers",
    "encadrement_loyers": "/encadrement_loyers",
    "transports": "/transports",
    "monuments": "/monuments",
    "demographie": "/demographie",
    "fiscalite": "/fiscalite",
    "equipements": "/equipements",
    "georisques": "/georisques",
    "resolve": "/resolve",
}

TOOLS = [
    {
        "name": "query_api",
        "description": (
            "Appelle un endpoint de l'API DataCommune (donnees immobilieres et territoriales francaises). "
            "Endpoints disponibles:\n"
            "- parcelle: recherche cadastrale (params: lat, lon OU ref_cadastrale)\n"
            "- dpe: diagnostics energetiques (params: lat, lon, rayon_m?, etiquette_dpe?)\n"
            "- dvf: transactions immobilieres (params: lat, lon, rayon_m?, annee_min?, type_local?)\n"
            "- commune: profil synthese d'une commune (params: codes_insee)\n"
            "- revenus: revenus et pauvrete Filosofi (params: codes_insee)\n"
            "- loyers: loyers medians par type (params: codes_insee)\n"
            "- encadrement_loyers: loyers de reference encadres (params: lat, lon)\n"
            "- transports: arrets de transport a proximite (params: lat, lon, rayon_m?)\n"
            "- monuments: monuments historiques (params: lat, lon, rayon_m?)\n"
            "- demographie: statistiques demographiques (params: codes_insee)\n"
            "- fiscalite: fiscalite locale (params: codes_insee)\n"
            "- equipements: equipements publics a proximite (params: lat, lon, rayon_m?)\n"
            "- georisques: risques naturels/industriels (params: lat, lon)\n"
            "- resolve: enrichissement multi-dataset par adresse (params: adresse OU lat+lon)\n\n"
            "Les params lat/lon sont des coordonnees GPS WGS-84. "
            "codes_insee est une liste de codes INSEE a 5 chiffres (ex: 75056 pour Paris, 44109 pour Nantes). "
            "Tu peux appeler cet outil plusieurs fois pour combiner des donnees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "enum": list(ENDPOINT_MAP.keys()),
                    "description": "Nom de l'endpoint a appeler.",
                },
                "params": {
                    "type": "object",
                    "description": "Parametres query string a envoyer.",
                    "additionalProperties": True,
                },
            },
            "required": ["endpoint", "params"],
        },
    },
    {
        "name": "geocode",
        "description": (
            "Geocode une adresse francaise en coordonnees GPS via l'API BAN (base adresse nationale). "
            "Utiliser quand l'utilisateur donne une adresse textuelle et qu'il faut des coordonnees "
            "pour les autres outils."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "adresse": {
                    "type": "string",
                    "description": "Adresse a geocoder (ex: '14 rue de Rivoli, Paris').",
                },
            },
            "required": ["adresse"],
        },
    },
]

SYSTEM = """Tu es un assistant expert en donnees immobilieres et territoriales francaises.
Tu utilises l'API DataCommune pour repondre aux questions des utilisateurs.

Regles:
- Reponds TOUJOURS en francais
- Utilise geocode si l'utilisateur donne une adresse sans coordonnees GPS
- Presente les donnees de facon claire avec des tableaux ou listes
- Si une requete echoue, explique pourquoi et propose une alternative
- Ne fabrique JAMAIS de donnees — utilise uniquement les resultats de l'API
- Tu peux enchainer plusieurs appels pour enrichir ta reponse
"""


# ---------------------------------------------------------------------------
# API call helpers
# ---------------------------------------------------------------------------

http = httpx.Client(timeout=30, headers=HEADERS)


def call_api(endpoint: str, params: dict) -> dict:
    """GET on DataCommune API."""
    path = ENDPOINT_MAP.get(endpoint)
    if not path:
        return {"error": f"Endpoint inconnu: {endpoint}"}
    url = f"{API_BASE}{path}"
    try:
        r = http.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        # Truncate large result lists for readability
        if isinstance(data, list) and len(data) > 20:
            data = data[:20]
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list) and len(v) > 20:
                    data[k] = v[:20]
        return data
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:500]}
    except Exception as e:
        return {"error": str(e)}


def geocode(adresse: str) -> dict:
    """Geocode via BAN API."""
    try:
        r = http.get("https://api-adresse.data.gouv.fr/search/", params={"q": adresse, "limit": 1})
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            return {"error": "Adresse non trouvee"}
        props = features[0]["properties"]
        coords = features[0]["geometry"]["coordinates"]
        return {
            "lat": coords[1],
            "lon": coords[0],
            "label": props.get("label", ""),
            "code_insee": props.get("citycode", ""),
            "ville": props.get("city", ""),
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def run_chat():
    client = anthropic.Anthropic()
    messages = []

    print("=== Chat DataCommune (Claude + API live) ===")
    print("Pose une question sur l'immobilier ou le territoire francais.")
    print("Tape 'quit' pour sortir.\n")

    while True:
        try:
            user_input = input("Toi > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break

        messages.append({"role": "user", "content": user_input})

        # Agentic loop: keep going while Claude wants to call tools
        while True:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
            )

            # Collect tool uses and text blocks
            tool_uses = []
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            # If there are tool calls, execute them
            if tool_uses:
                # Add assistant message with all content blocks
                messages.append({"role": "assistant", "content": response.content})

                # Execute each tool and collect results
                tool_results = []
                for tu in tool_uses:
                    print(f"  [API] {tu.name}({json.dumps(tu.input, ensure_ascii=False)})")
                    if tu.name == "query_api":
                        result = call_api(tu.input["endpoint"], tu.input.get("params", {}))
                    elif tu.name == "geocode":
                        result = geocode(tu.input["adresse"])
                    else:
                        result = {"error": f"Outil inconnu: {tu.name}"}

                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    # Cap result size to avoid token explosion
                    if len(result_str) > 8000:
                        result_str = result_str[:8000] + '..."}'

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})

                # If stop_reason is tool_use, loop back for more
                if response.stop_reason == "tool_use":
                    continue

            # Print final text response
            if text_parts:
                print(f"\nClaude > {''.join(text_parts)}\n")

            break


if __name__ == "__main__":
    run_chat()

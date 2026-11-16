#!/usr/bin/env python3
"""DataCommune API — Interactive query tool.

Usage:
    python scripts/query.py              # Interactive mode
    DATACOMMUNE_BASE_URL=https://example.test python scripts/query.py
    python scripts/query.py --help       # Show help
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from urllib.parse import urlencode

try:
    import httpx
except ImportError:
    print("httpx requis: pip install httpx")
    sys.exit(1)

BASE_URL = os.environ.get("DATACOMMUNE_BASE_URL", "http://localhost:8000")
TIMEOUT = 30

# ── Dataset catalogue ──────────────────────────────────────────────
ENDPOINTS: dict[str, dict] = {
    "geocode": {
        "desc": "Geocoder une adresse → coordonnees",
        "path": "/v1/geocode",
        "params": [
            {"name": "q", "prompt": "Adresse a geocoder", "required": True},
            {"name": "limit", "prompt": "Nombre de resultats (1-20)", "default": "5"},
            {"name": "code_postal", "prompt": "Code postal (filtre optionnel)", "default": ""},
        ],
    },
    "reverse": {
        "desc": "Reverse geocoding (GPS → adresse)",
        "path": "/v1/reverse",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "limit", "prompt": "Nombre de resultats (1-10)", "default": "1"},
        ],
    },
    "resolve": {
        "desc": "Recherche polymorphe (adresse/GPS/cadastre) + multi-dataset",
        "path": "/v1/resolve",
        "params": [
            {"name": "q", "prompt": "Recherche (adresse, lat,lon, ou ref cadastrale)", "required": True},
            {"name": "datasets", "prompt": "Datasets (virgule): ban,cadastre,dvf,dpe,loyers,risques,pollutions,urbanisme,batiments,monuments,connectivite,entreprises,scolaire,transports", "default": ""},
        ],
    },
    "dvf": {
        "desc": "Transactions immobilieres (DVF)",
        "path": "/v1/dvf",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (50-5000)", "default": "500"},
            {"name": "annee_min", "prompt": "Annee min", "default": ""},
            {"name": "annee_max", "prompt": "Annee max", "default": ""},
            {"name": "type_local", "prompt": "Type (Appartement/Maison)", "default": ""},
            {"name": "limite", "prompt": "Limite resultats (1-100)", "default": "20"},
        ],
    },
    "dpe": {
        "desc": "Diagnostics energetiques (DPE)",
        "path": "/v1/dpe",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (50-5000)", "default": "500"},
            {"name": "etiquette_dpe", "prompt": "Etiquette DPE (A-G)", "default": ""},
            {"name": "limite", "prompt": "Limite resultats (1-100)", "default": "20"},
        ],
    },
    "loyers": {
        "desc": "Indicateurs de loyers",
        "path": "/v1/loyers",
        "params": [
            {"name": "lat", "prompt": "Latitude (ou vide si code_insee)", "default": ""},
            {"name": "lon", "prompt": "Longitude (ou vide si code_insee)", "default": ""},
            {"name": "code_insee", "prompt": "Code INSEE (5 chiffres, prioritaire)", "default": ""},
        ],
    },
    "parcelle": {
        "desc": "Parcelle cadastrale par coordonnees",
        "path": "/v1/parcelle",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
        ],
    },
    "risques": {
        "desc": "Risques naturels et technologiques (proxy Georisques)",
        "path": "/v1/risques",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (100-10000)", "default": "1000"},
        ],
    },
    "pollutions": {
        "desc": "Sites pollues (proxy Georisques)",
        "path": "/v1/pollutions",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (100-10000)", "default": "1000"},
        ],
    },
    "urbanisme": {
        "desc": "Documents d'urbanisme (PLU)",
        "path": "/v1/urbanisme",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
        ],
    },
    "batiments": {
        "desc": "Batiments (BD TOPO)",
        "path": "/v1/batiments",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (50-2000)", "default": "200"},
            {"name": "limite", "prompt": "Limite resultats (1-100)", "default": "20"},
        ],
    },
    "monuments": {
        "desc": "Monuments historiques",
        "path": "/v1/monuments",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (100-10000)", "default": "1000"},
            {"name": "limite", "prompt": "Limite resultats (1-100)", "default": "20"},
        ],
    },
    "connectivite": {
        "desc": "Eligibilite fibre/ADSL (Arcep)",
        "path": "/v1/connectivite",
        "params": [
            {"name": "code_insee", "prompt": "Code INSEE (5 chiffres)", "required": True},
            {"name": "nom_voie", "prompt": "Nom de voie (filtre optionnel)", "default": ""},
            {"name": "numero", "prompt": "Numero de voie (filtre optionnel)", "default": ""},
        ],
    },
    "entreprises": {
        "desc": "Entreprises a proximite (proxy Sirene)",
        "path": "/v1/entreprises",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_km", "prompt": "Rayon en km (0.1-50)", "default": "5"},
            {"name": "activite_principale", "prompt": "Code NAF/APE (optionnel)", "default": ""},
        ],
    },
    "scolaire": {
        "desc": "Carte scolaire (colleges)",
        "path": "/v1/scolaire",
        "params": [
            {"name": "code_insee", "prompt": "Code INSEE (5 chiffres)", "required": True},
            {"name": "nom_voie", "prompt": "Nom de voie (optionnel)", "default": ""},
        ],
    },
    "transports": {
        "desc": "Arrets de transport a proximite",
        "path": "/v1/transports",
        "params": [
            {"name": "lat", "prompt": "Latitude", "required": True},
            {"name": "lon", "prompt": "Longitude", "required": True},
            {"name": "rayon_m", "prompt": "Rayon en metres (100-5000)", "default": "1000"},
            {"name": "limite", "prompt": "Limite resultats (1-200)", "default": "50"},
        ],
    },
    "datasets": {
        "desc": "Liste des datasets disponibles et leur etat",
        "path": "/v1/datasets",
        "params": [],
    },
    "scoring": {
        "desc": "Scoring renovation energetique",
        "path": "/v1/scoring/renov",
        "params": [
            {"name": "code_departement", "prompt": "Code departement (ex: 44, 75, 2A)", "required": True},
            {"name": "etiquette_dpe", "prompt": "Etiquette DPE (F ou G)", "default": ""},
            {"name": "commune", "prompt": "Nom de commune (optionnel)", "default": ""},
            {"name": "limite", "prompt": "Limite resultats (1-500)", "default": "100"},
        ],
    },
}

# ── Presets (raccourcis metier) ─────────────────────────────────────
PRESETS: dict[str, dict] = {
    "fiche-adresse": {
        "desc": "Fiche complete d'une adresse (tous les datasets)",
        "endpoint": "resolve",
        "prefill": {"datasets": "ban,cadastre,dvf,dpe,loyers,risques,pollutions,urbanisme,batiments,monuments,connectivite,scolaire,transports"},
    },
    "immo": {
        "desc": "Analyse immobiliere (DVF + DPE + loyers + cadastre)",
        "endpoint": "resolve",
        "prefill": {"datasets": "dvf,dpe,loyers,cadastre"},
    },
    "risques-env": {
        "desc": "Risques et environnement (risques + pollutions + urbanisme)",
        "endpoint": "resolve",
        "prefill": {"datasets": "risques,pollutions,urbanisme"},
    },
    "contexte-vie": {
        "desc": "Contexte de vie (transports + scolaire + connectivite + entreprises)",
        "endpoint": "resolve",
        "prefill": {"datasets": "transports,scolaire,connectivite,entreprises"},
    },
}


def show_menu() -> None:
    """Print the main menu."""
    print("\n╔══════════════════════════════════════════════════╗")
    print("║         DataCommune — Query Tool                ║")
    print("╚══════════════════════════════════════════════════╝\n")

    print("── Presets (raccourcis) ──")
    for i, (key, preset) in enumerate(PRESETS.items(), 1):
        print(f"  {i:2d}. [{key}] {preset['desc']}")

    print("\n── Endpoints ──")
    offset = len(PRESETS)
    for i, (key, ep) in enumerate(ENDPOINTS.items(), offset + 1):
        print(f"  {i:2d}. [{key}] {ep['desc']}")

    print("\n   q. Quitter")


def pick_choice() -> tuple[str, dict, dict]:
    """Return (name, endpoint_def, prefill)."""
    all_items: list[tuple[str, dict, dict]] = []
    for key, preset in PRESETS.items():
        all_items.append((key, ENDPOINTS[preset["endpoint"]], preset.get("prefill", {})))
    for key, ep in ENDPOINTS.items():
        all_items.append((key, ep, {}))

    while True:
        raw = input("\nChoix (numero ou nom) : ").strip().lower()
        if raw in ("q", "quit", "exit"):
            sys.exit(0)

        # By number
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(all_items):
                return all_items[idx]

        # By name
        for name, ep, prefill in all_items:
            if raw == name:
                return name, ep, prefill

        print("  Choix invalide, reessaie.")


def collect_params(endpoint: dict, prefill: dict) -> dict[str, str]:
    """Prompt the user for each parameter."""
    params: dict[str, str] = {}
    for p in endpoint["params"]:
        name = p["name"]
        default = prefill.get(name, p.get("default", ""))
        required = p.get("required", False)

        suffix = f" [{default}]" if default else ""
        if required:
            suffix += " *"

        while True:
            val = input(f"  {p['prompt']}{suffix} : ").strip()
            if not val and default:
                val = default
            if not val and required:
                print("    Ce champ est requis.")
                continue
            break

        if val:
            params[name] = val

    return params


def build_curl(url: str, api_key: str) -> str:
    """Build equivalent curl command."""
    return f'curl -H "X-API-Key: {api_key[:8]}..." "{url}"'


def pretty_print(data: dict) -> None:
    """Print JSON response nicely."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def run_query(base_url: str, path: str, params: dict, api_key: str) -> None:
    """Execute the API call and display results."""
    qs = urlencode(params)
    url = f"{base_url}{path}"
    full_url = f"{url}?{qs}" if qs else url

    print("\n── Requete ──")
    print(f"  GET {full_url}")
    print(f"  curl: {build_curl(full_url, api_key)}")
    print()

    try:
        resp = httpx.get(full_url, headers={"X-API-Key": api_key}, timeout=TIMEOUT)
    except httpx.ConnectError:
        print(f"  ERREUR: impossible de se connecter a {base_url}")
        return
    except httpx.TimeoutException:
        print(f"  ERREUR: timeout ({TIMEOUT}s)")
        return

    print(f"  Status: {resp.status_code}")

    try:
        data = resp.json()
    except Exception:
        print(f"  Reponse brute: {resp.text[:500]}")
        return

    if resp.status_code >= 400:
        print(f"  Erreur: {data.get('message', data.get('detail', resp.text[:200]))}")
        return

    # Summary
    if "donnees" in data:
        donnees = data["donnees"]
        if isinstance(donnees, list) and donnees:
            first = donnees[0]
            # Multi-dataset resolve returns a list of objects with "datasets" key
            if isinstance(first, dict) and "datasets" in first:
                print(f"  Type detecte: {first.get('type_detecte', '?')}")
                print(f"  Adresse: {first.get('adresse', '?')}")
                print(f"  Parcelle: {first.get('id_parcelle', '?')}")
                for ds_name, ds_data in first["datasets"].items():
                    count = len(ds_data) if isinstance(ds_data, list) else 1
                    print(f"    {ds_name}: {count} resultats")
                if first.get("erreurs"):
                    for e in first["erreurs"]:
                        print(f"    ERREUR {e['dataset']}: {e['message']}")
            else:
                print(f"  Resultats: {len(donnees)}")
        elif isinstance(donnees, dict) and "datasets" in donnees:
            ds = donnees["datasets"]
            for ds_name, ds_data in ds.items():
                count = len(ds_data) if isinstance(ds_data, list) else 1
                print(f"  {ds_name}: {count} resultats")

    if "pagination" in data and data["pagination"].get("curseur_suivant"):
        print("  (page suivante disponible)")

    print("\n── Reponse ──")
    pretty_print(data)


def main() -> None:
    api_key = getpass.getpass("Cle API (X-API-Key) : ")
    if not api_key:
        print("Cle API requise.")
        sys.exit(1)

    base_url = BASE_URL

    while True:
        show_menu()
        name, endpoint, prefill = pick_choice()
        print(f"\n>> {name} — {endpoint['desc']}")

        params = collect_params(endpoint, prefill)
        run_query(base_url, endpoint["path"], params, api_key)

        again = input("\n\nAutre requete ? (o/N) : ").strip().lower()
        if again not in ("o", "oui", "y", "yes"):
            break

    print("A+")


if __name__ == "__main__":
    main()

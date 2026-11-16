#!/usr/bin/env python3
"""Real-API demonstrations of plan 02-18 (DATA-03, SEM-01), run in the work base CT.

``scope --key <key>``: route-by-route demonstration that the persisted scope
decides what the API serves, without code nor environment variable.

1. read the scope (``python -m app.scope show --json``) and the route
   inventory of the key from the APPLICATION (``app.routes``, dependencies
   declaring ``hosted_datasets``), plus ``/v1/datasets`` and ``/v1/resolve``;
2. for ``dpe``, obtain the signed DPE context and document references while
   the key is hosted (``/v1/resolve?datasets=dpe`` on a golden address, then
   the inventory): kept in memory, never written;
3. remove the key (``python -m app.scope set``), check ``show`` no longer
   lists it, run ``python run_etl.py <key>`` (expected exit 2, ``dataset non
   hébergé``, no new ``etl_runs`` row) -- only once the removal is proven;
4. call every route: ``observations[<route>]`` (status, state, reason, the
   empty form actually served);
5. restore the exact starting scope -- in a ``finally``, whatever happened --
   check ``show`` equals the starting one, call the same routes again:
   ``retabli[<route>]`` with a content count ``nb``.

``states``: the four absence states on real data (measured zero, missing
value, out of coverage with its reasons). The upstream failure
(``temporary_error``) needs a one-off API container and is not run here.

Reports carry opaque case ids only: no address, parcel, BAN id, DPE number
nor signed reference. Loopback base URL only; key from ``--api-key-env``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.measure_route_latencies import require_loopback

BASE_ROUTES = ("/v1/datasets", "/v1/resolve")
DPE_REFUSING = (
    "/v1/bien/dpe/inventaire",
    "/v1/bien/dpe/document",
    "/v1/bien/dpe/comparaison",
)
PRIVATE_INPUT = "../../contracts/golden-cases/v1/private/golden-selection.json"
ADDRESS_CASE = "golden-address-001"  # 44001, category complete (DPE present)
COMMUNE = "44109"
POINT = {"lat": "47.2184", "lon": "-1.5536"}  # public route example (Nantes)


# ------------------------------------------------------------ observations --


def _hosting(body: Any, key: str) -> tuple[str | None, str | None]:
    """(etat, motif) the response declares for *key* in ``meta.hebergement``."""
    meta = body.get("meta") if isinstance(body, Mapping) else None
    hebergement = (meta or {}).get("hebergement") if isinstance(meta, Mapping) else None
    entry = (hebergement or {}).get(key) if isinstance(hebergement, Mapping) else None
    if isinstance(entry, Mapping):
        return entry.get("etat"), entry.get("motif")
    return "heberge", None


def _refusal_reason(body: Any, key: str) -> str | None:
    detail = body.get("detail") if isinstance(body, Mapping) else None
    marker = f"{key}: "
    if isinstance(detail, str) and marker in detail:
        return detail.split(marker, 1)[1].split(",", 1)[0].strip()
    return None


def observe(route: str, key: str, status: int, body: Any) -> dict[str, Any]:  # noqa: C901
    """What *route* served for *key*: state, reason, empty form, content count."""
    obs: dict[str, Any] = {"statut_http": status}
    if status == 409:
        obs.update(
            forme="refus_409",
            etat="hors_couverture",
            motif=_refusal_reason(body, key),
            nb=0,
        )
        return obs
    if status != 200 or not isinstance(body, Mapping):
        obs.update(forme="erreur", etat=None, motif=None, nb=0)
        return obs
    donnees = body.get("donnees")
    etat, motif = _hosting(body, key)
    if route == "/v1/datasets":
        entry = next(
            (
                d
                for d in donnees or []
                if isinstance(d, Mapping) and d.get("nom") == key
            ),
            None,
        )
        heb = (entry or {}).get("hebergement") or {}
        obs.update(
            forme="entree_conservee",
            entree_presente=entry is not None,
            etat=heb.get("etat"),
            motif=heb.get("motif"),
            nb=int((entry or {}).get("nombre_lignes") or 0)
            if heb.get("etat") == "heberge"
            else 0,
        )
    elif route == "/v1/resolve":
        item = donnees[0] if isinstance(donnees, list) and donnees else {}
        state = (item.get("dataset_states") or {}).get(key)
        reason = (state or {}).get("absence_reason")
        if key == "dpe":
            content = len(((item.get("dpe_inventory") or {}).get("items")) or [])
        else:
            content = len(((item.get("datasets") or {}).get(key)) or [])
        obs.update(
            forme="entree_conservee",
            entree_presente=state is not None,
            etat="hors_couverture"
            if reason == "out_of_coverage"
            else (state or {}).get("presence"),
            motif=(state or {}).get("motif"),
            nb=content,
        )
    elif route == "/v1/zone/resolve":
        obs.update(
            forme="enveloppe_vide",
            etat=etat,
            motif=motif,
            results=[] if donnees.get("results") == [] else "non_vide",
            total_count=donnees.get("total_count"),
            has_next=donnees.get("has_next"),
            nb=int(donnees.get("total_count") or 0),
        )
    elif route == "/v1/zone/stats":
        distribution = donnees.get("dpe_distribution")
        obs.update(
            forme="indicateurs_nuls",
            etat=etat,
            motif=motif,
            indicateurs={"dpe_distribution": distribution},
            nb=sum(distribution.values()) if isinstance(distribution, Mapping) else 0,
        )
    elif route == "/v1/bien/logements":
        inventory = (donnees or {}).get("dpe_inventory") or {}
        items = len(inventory.get("items") or [])
        obs.update(
            forme="partielle" if items == 0 else "contenu_dpe_servi",
            etat=etat,
            motif=motif,
            dpe_inventory_items=items,
            dpe_inventory_total=inventory.get("total"),
            nb=items,
        )
    elif route in DPE_REFUSING:
        if route == "/v1/bien/dpe/document":
            count = int(bool(body.get("document_ref")))
        elif route == "/v1/bien/dpe/comparaison":
            count = len(body.get("comparisons") or [])
        else:
            count = len(body.get("items") or [])
        obs.update(forme="contenu", etat=etat, motif=motif, nb=count)
    else:  # plain list routes (/v1/antennes, /v1/dpe)
        rows = donnees if isinstance(donnees, list) else None
        obs.update(forme="liste_vide", etat=etat, motif=motif, nb=len(rows or []))
        if rows == []:
            obs["donnees"] = []
    return obs


# --------------------------------------------------------------- run part --


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def app_inventory() -> dict[str, list[str]]:
    """``key -> sorted /v1 paths`` read from the application (02-08 rule)."""
    from fastapi.routing import APIRoute

    from app.main import app

    def declared(dependant: Any) -> set[str]:
        keys: set[str] = set()
        for dependency in dependant.dependencies:
            keys.update(getattr(dependency.call, "served_keys", ()))
            keys |= declared(dependency)
        return keys

    inventory: dict[str, set[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/v1"):
            for key in declared(route.dependant):
                inventory.setdefault(key, set()).add(route.path)
    return {key: sorted(paths) for key, paths in sorted(inventory.items())}


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, timeout=900
    )


def _scope_json() -> dict[str, Any]:
    done = _run(["-m", "app.scope", "show", "--json"])
    if done.returncode != 0:
        raise RuntimeError("scope show failed")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _scope_line() -> str:
    done = _run(["-m", "app.scope", "show"])
    return next(
        (
            line
            for line in done.stdout.splitlines()
            if line.startswith("SCOPE datasets")
        ),
        "",
    )


def _set_scope(datasets: list[str], departements: list[str]) -> str:
    done = _run(
        [
            "-m",
            "app.scope",
            "set",
            "--datasets",
            ",".join(datasets),
            "--departements",
            ",".join(departements),
        ]
    )
    if done.returncode != 0:
        raise RuntimeError(f"scope set failed: {done.stderr.strip()}")
    return done.stdout.strip().splitlines()[0]


def _etl_runs(key: str) -> int:
    import psycopg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        return int(
            conn.execute(
                "SELECT count(*) FROM etl_runs WHERE dataset_name = %s", (key,)
            ).fetchone()[0]
        )


class Caller:
    """Paced GET/POST on the loopback API; returns (status, parsed body)."""

    def __init__(self, client: httpx.Client, interval_s: float) -> None:
        self.client, self.interval_s, self.last = client, interval_s, None
        self.count = 0

    def __call__(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if self.last is not None:
            remaining = self.interval_s - (time.monotonic() - self.last)
            if remaining > 0:
                time.sleep(remaining)
        self.last = time.monotonic()
        self.count += 1
        response = self.client.request(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = None
        return response.status_code, body


def _dpe_refs(call: Caller, private: Mapping[str, Any]) -> dict[str, str]:
    status, body = call(
        "GET",
        "/v1/resolve",
        params={"q": private["query"], "type": "adresse", "datasets": "dpe"},
    )
    item = (body or {}).get("donnees", [{}])[0] if status == 200 else {}
    context = (item.get("dpe_inventory") or {}).get("context_ref")
    if not context:
        raise RuntimeError("no DPE context for the golden address")
    status, body = call(
        "GET", "/v1/bien/dpe/inventaire", params={"contexte": context, "limite": 1}
    )
    items = (body or {}).get("items") or [] if status == 200 else []
    if not items:
        raise RuntimeError("no DPE document in the golden address inventory")
    return {"contexte": context, "reference": items[0]["document_ref"]}


def _request(route: str, key: str, private, refs) -> tuple[str, str, dict]:  # noqa: C901
    if route == "/v1/datasets":
        return "GET", route, {}
    if route == "/v1/resolve":
        params = {"q": private["query"], "type": "adresse", "datasets": key}
        return "GET", route, {"params": params}
    if route == "/v1/antennes":
        return "GET", route, {"params": {"code_insee": COMMUNE}}
    if route == "/v1/dpe":
        return "GET", route, {"params": {**POINT, "rayon_m": 500}}
    if route == "/v1/zone/resolve":
        params = {"type": "commune", "code": COMMUNE, "datasets": key, "per_page": 1}
        return "GET", route, {"params": params}
    if route == "/v1/zone/stats":
        return "GET", route, {"params": {"type": "commune", "code": COMMUNE}}
    if route == "/v1/bien/logements":
        params = {"id_parcelle": private["id_parcelle"], "ban_id": private["ban_id"]}
        return "GET", route, {"params": params}
    if route == "/v1/bien/dpe/inventaire":
        return "GET", route, {"params": {"contexte": refs["contexte"], "limite": 1}}
    if route == "/v1/bien/dpe/document":
        return "GET", route, {"params": dict(refs)}
    if route == "/v1/bien/dpe/comparaison":
        return "POST", route, {"json": {"contexte": refs["contexte"], "limite": 1}}
    raise ValueError(f"no request defined for {route}")


def _observe_all(call, key, routes, private, refs) -> dict[str, Any]:
    result = {}
    for route in routes:
        method, path, kwargs = _request(route, key, private, refs)
        status, body = call(method, path, **kwargs)
        result[route] = observe(route, key, status, body)
    return result


def run_scope(
    key: str, call: Caller, private: Mapping[str, Any], log: Callable[[str], None]
) -> dict[str, Any]:
    inventory = app_inventory()
    routes = sorted(set(inventory.get(key) or []) | set(BASE_ROUTES))
    before = _scope_json()
    line_before = _scope_line()
    if key not in before["datasets"]:
        raise RuntimeError(f"{key} is not hosted at the start: nothing to demonstrate")
    refs = _dpe_refs(call, private) if key == "dpe" else {}
    report: dict[str, Any] = {
        "cle": key,
        "debut": _now(),
        "inventaire_routes": inventory.get(key) or [],
        "scope_avant": line_before,
    }
    runs_before = _etl_runs(key)
    try:
        remaining = [d for d in before["datasets"] if d != key]
        report["scope_set_retrait"] = _set_scope(remaining, before["departements"])
        removed = _scope_json()
        report["scope_retire"] = _scope_line()
        if key in removed["datasets"]:
            raise RuntimeError("removal not observed: run_etl not launched")
        etl = _run(["run_etl.py", key])
        report["run_etl"] = {
            "commande": f"python run_etl.py {key}",
            "code_sortie": etl.returncode,
            "stderr_non_heberge": [
                line for line in etl.stderr.splitlines() if "non hébergé" in line
            ],
            "etl_runs_avant": runs_before,
            "etl_runs_apres": _etl_runs(key),
        }
        log(f"RUN_ETL {key} exit={etl.returncode}")
        report["observations"] = _observe_all(call, key, routes, private, refs)
    finally:
        report["scope_set_retablissement"] = _set_scope(
            list(before["datasets"]), list(before["departements"])
        )
        after = _scope_json()
        report["scope_apres"] = _scope_line()
        report["scope_identique"] = after == before and report["scope_apres"] == (
            line_before
        )
        log(f"SCOPE restored identical={report['scope_identique']}")
    report["retabli"] = _observe_all(call, key, routes, private, refs)
    report["fin"] = _now()
    return report


def _commune_states(call: Caller, codes: list[str]) -> dict[str, Any]:
    """First measured zero and first declared null of the panel communes."""
    zero = missing = None
    for code in codes:
        status, body = call("GET", "/v1/commune", params={"code_insee": code})
        row = _first(status, body)
        couverture, etats = row.get("couverture") or {}, row.get("etats") or {}
        from app.commune.schemas import COMMUNE_FIELD_SOURCES

        for column, value in row.items():
            source = COMMUNE_FIELD_SOURCES.get(column)
            hosted = (couverture.get(source) or {}).get("etat") == "heberge"
            if zero is None and value == 0 and not isinstance(value, bool) and hosted:
                zero = {
                    "code_insee": code,
                    "colonne": column,
                    "valeur": 0,
                    "source": source,
                    "hebergement": "heberge",
                }
            if missing is None and value is None and column in etats and hosted:
                missing = {
                    "code_insee": code,
                    "colonne": column,
                    "valeur": None,
                    "source": source,
                    "etat_declare": etats[column],
                }
        if zero and missing:
            break
    return {"zero_mesure": zero, "manque": missing}


def _first(status: int, body: Any) -> dict[str, Any]:
    rows = (body or {}).get("donnees") if status == 200 else None
    return rows[0] if isinstance(rows, list) and rows else {}


def _resolve_view(status: int, body: Any, requete: str) -> dict[str, Any]:
    meta = (body or {}).get("meta") or {} if isinstance(body, Mapping) else {}
    item = _first(status, body)
    return {
        "requete": requete,
        "statut_http": status,
        "nb_donnees": len((body or {}).get("donnees") or [])
        if isinstance(body, Mapping)
        else None,
        "code_insee": item.get("code_insee"),
        "dataset_states": item.get("dataset_states"),
        "meta_resolution": meta.get("resolution"),
        "meta_hebergement": meta.get("hebergement"),
        "detail": (body or {}).get("detail") if isinstance(body, Mapping) else None,
    }


def run_states(call: Caller, codes: list[str]) -> dict[str, Any]:
    report: dict[str, Any] = {"debut": _now()}
    report.update(_commune_states(call, codes))
    views = []
    for requete, params in (
        (
            "/v1/resolve?q=<point Bordeaux 33>&type=gps&datasets=dvf,antennes",
            {"q": "44.8378,-0.5792", "type": "gps", "datasets": "dvf,antennes"},
        ),
        (
            "/v1/resolve?q=<adresse publique Bordeaux 33>&type=adresse"
            "&datasets=dvf,antennes",
            {
                "q": "Place de la Bourse 33000 Bordeaux",
                "type": "adresse",
                "datasets": "dvf,antennes",
            },
        ),
    ):
        status, body = call("GET", "/v1/resolve", params=params)
        views.append(_resolve_view(status, body, requete))
    report["territoire_hors_perimetre"] = views
    status, body = call(
        "GET",
        "/v1/resolve",
        params={
            "q": f"{POINT['lat']},{POINT['lon']}",
            "type": "gps",
            "datasets": "permis,antennes",
        },
    )
    report["source_sans_couverture"] = _resolve_view(
        status, body, "/v1/resolve?q=<point Nantes>&type=gps&datasets=permis,antennes"
    )
    status, body = call("GET", "/v1/permis", params={"code_insee": COMMUNE})
    report["route_permis_44"] = {
        "requete": f"/v1/permis?code_insee={COMMUNE}",
        "statut_http": status,
        "corps": body,
    }
    report["fin"] = _now()
    return report


def _read_private(path: str) -> dict[str, Any]:
    from scripts.verify_golden_cases import _read_private_input

    return _read_private_input(path, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("scope", "states", "inventory"))
    parser.add_argument("--key", choices=("antennes", "dpe"))
    parser.add_argument("--base-url", default=os.environ.get("WB_HTTP_URL", ""))
    parser.add_argument("--api-key-env", default="DC_API_KEY")
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--min-interval", type=float, default=6.5)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "inventory":
        print(json.dumps(app_inventory(), sort_keys=True))
        return 0
    try:
        require_loopback(args.base_url)
    except ValueError as exc:
        print(f"DEMO refused: {exc}", file=sys.stderr)
        return 2
    api_key = os.environ.get(args.api_key_env or "")
    if not api_key or args.report is None:
        print("DEMO refused: API key or --report missing", file=sys.stderr)
        return 2
    client = httpx.Client(
        base_url=args.base_url, headers={"X-API-Key": api_key}, timeout=120.0
    )
    call = Caller(client, args.min_interval)
    with client:
        if args.mode == "scope":
            if args.key is None:
                print("DEMO refused: --key is required", file=sys.stderr)
                return 2
            private = _read_private(PRIVATE_INPUT)[ADDRESS_CASE]
            report = run_scope(args.key, call, private, print)
        else:
            codes = json.loads(args.panel.read_text(encoding="utf-8"))["codes"]
            report = run_states(call, codes)
    report["requetes_http"] = call.count
    report["workbase_sha"] = os.environ.get("WORKBASE_SHA")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"DEMO {args.mode} written {args.report} requetes={call.count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

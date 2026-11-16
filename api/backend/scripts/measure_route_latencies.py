#!/usr/bin/env python3
"""Replayable latency measure of the main API routes (PERF-00, plan 02-18).

For every route of ``--routes-json`` (GET only): ``--warmup`` requests that are
sent but never measured, then ``--samples`` sequential measured requests.
Per route it writes the observed HTTP status, the number of FUNCTIONAL
responses and p50/p95/p99/max computed on them alone (nearest rank: every
percentile is an observed value), and apart, in ``errors``, the failures with
their own response times -- never mixed into the budget.

A response is functional when its status is 2xx AND its JSON carries the
route's ``cle_attendue`` (dotted path, list indexes allowed) AND, when the
route declares ``non_vide: true``, that value holds at least one element (for
an indicator object: at least one non-null value). A ``200`` serving an empty
collection measures the cost of an empty answer, not of the journey.
``functional`` describes the LAST measured response. A route with no
functional response, or whose last response is not functional, has no budget
(``no_budget: true``) and the script exits 1.

Loopback only: any other base URL is refused before a single request (exit 2).
The API key is read from the environment variable named by ``--api-key-env``
and only ever sent in the ``X-API-Key`` header. Private parameters (golden
addresses) are read from the gitignored private input through the guard of
``verify_golden_cases.py`` and appear in the output as ``<prive:case.field>``.
``params_from`` takes a parameter from the last functional response of an
earlier route (signed DPE contexts, document references): it appears as
``<derive:route.pointer>``, never with its value.

``--min-interval`` paces the requests (seconds between two request starts):
the admission of the API limits a key and an address per minute; the wait is
outside the timed window.

Usage (in the work base CT, through the work base tool wb-tool.sh)::

    python scripts/measure_route_latencies.py --base-url http://127.0.0.1:8000 \
        --api-key-env DC_API_KEY --routes-json <routes.json> \
        --private-input contracts/golden-cases/v1/private/golden-selection.json \
        --warmup 10 --samples 100 --out reports/latencies.json
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PERCENTILES = (50, 95, 99)
_MISSING = object()


@dataclass(frozen=True)
class Observation:
    """One measured request: status, wall time of the exchange, parsed body."""

    status: int
    elapsed_ms: float
    payload: Any


# ------------------------------------------------------------------ pure part --


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: the smallest observed value covering *p* %."""
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {f"p{p}_ms": None for p in PERCENTILES} | {"max_ms": None}
    stats: dict[str, float | None] = {
        f"p{p}_ms": round(percentile(values, p), 3) for p in PERCENTILES
    }
    stats["max_ms"] = round(max(values), 3)
    return stats


def require_loopback(url: str) -> None:
    """Refuse any base URL that is not plain HTTP(S) on a loopback host."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not host:
        raise ValueError("base URL must be http(s) on a loopback host")
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("base URL host is not a loopback address") from None
    if not address.is_loopback:
        raise ValueError("base URL host is not a loopback address")


def _lookup(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else _MISSING
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _non_empty(value: Any) -> bool:
    if value is None or value is _MISSING:
        return False
    if isinstance(value, Mapping):
        return any(item is not None for item in value.values())
    if isinstance(value, list | str):
        return len(value) > 0
    return True


def functional_check(payload: Any, cle_attendue: str) -> dict[str, Any]:
    """``{cle_attendue, presente, non_vide}`` of one response body."""
    value = _lookup(payload, cle_attendue)
    return {
        "cle_attendue": cle_attendue,
        "presente": value is not _MISSING,
        "non_vide": _non_empty(value),
    }


def _is_functional(obs: Observation, cle_attendue: str, non_vide: bool) -> bool:
    if not 200 <= obs.status < 300:
        return False
    check = functional_check(obs.payload, cle_attendue)
    return check["presente"] and (check["non_vide"] or not non_vide)


def summarize_route(
    observations: Sequence[Observation], *, cle_attendue: str, non_vide: bool
) -> dict[str, Any]:
    """Budget of one route: functional responses only, failures apart."""
    good: list[Observation] = []
    bad: list[Observation] = []
    for obs in observations:
        (good if _is_functional(obs, cle_attendue, non_vide) else bad).append(obs)
    statuses = Counter(
        f"{obs.status}:non_fonctionnel" if 200 <= obs.status < 300 else str(obs.status)
        for obs in bad
    )
    last = observations[-1] if observations else None
    functional = functional_check(last.payload if last else None, cle_attendue)
    reference = good or bad
    observed = Counter(obs.status for obs in reference).most_common(1)
    no_budget = (
        not good or last is None or not _is_functional(last, cle_attendue, non_vide)
    )
    result: dict[str, Any] = {
        "http_status": observed[0][0] if observed else None,
        "requested_samples": len(observations),
        "samples": len(good),
        **_stats([obs.elapsed_ms for obs in good]),
        "errors": {
            "count": len(bad),
            "statuses": dict(sorted(statuses.items())),
            **_stats([obs.elapsed_ms for obs in bad]),
        },
        "functional": functional,
        "non_vide_exige": non_vide,
        "no_budget": no_budget,
    }
    return result


# ------------------------------------------------------------------ run part --


def _parse(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _route_params(
    route: Mapping[str, Any],
    private_cases: Mapping[str, Mapping[str, Any]],
    previous: Mapping[str, Any],
) -> dict[str, str]:
    params = {key: str(value) for key, value in (route.get("params") or {}).items()}
    for name, ref in (route.get("private_params") or {}).items():
        value = (private_cases.get(ref["case_id"]) or {}).get(ref["field"])
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"private parameter {name} of {route['path']} is missing")
        params[name] = value
    for name, ref in (route.get("params_from") or {}).items():
        value = _lookup(previous.get(ref["route"]), ref["pointer"])
        if not isinstance(value, str | int | float) or value in ("", _MISSING):
            raise ValueError(f"derived parameter {name} of {route['path']} is missing")
        params[name] = str(value)
    return params


def public_params(route: Mapping[str, Any]) -> dict[str, str]:
    """Parameters as written in the report: private and derived ones redacted."""
    params = {key: str(value) for key, value in (route.get("params") or {}).items()}
    for name, ref in (route.get("private_params") or {}).items():
        params[name] = f"<prive:{ref['case_id']}.{ref['field']}>"
    for name, ref in (route.get("params_from") or {}).items():
        params[name] = f"<derive:{ref['route']}.{ref['pointer']}>"
    return params


def measure_route(
    client: httpx.Client,
    route: Mapping[str, Any],
    previous: Mapping[str, Any],
    *,
    warmup: int,
    samples: int,
    private_cases: Mapping[str, Mapping[str, Any]] | None = None,
    min_interval_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
    pace: dict[str, float] | None = None,
) -> list[Observation]:
    """Send *warmup* unmeasured then *samples* measured sequential GETs."""
    params = _route_params(route, private_cases or {}, previous)
    observations: list[Observation] = []
    # *pace* carries the last request start across routes: without it the
    # first request of a route follows the previous route's last one at once.
    pace = {} if pace is None else pace
    last_start: float | None = pace.get("last_start")
    for index in range(warmup + samples):
        if last_start is not None and min_interval_s > 0:
            wait = min_interval_s - (clock() - last_start)
            if wait > 0:
                sleep(wait)
        last_start = pace["last_start"] = clock()
        start = clock()
        response = client.get(route["path"], params=params)
        elapsed_ms = (clock() - start) * 1000
        if index >= warmup:
            observations.append(
                Observation(response.status_code, elapsed_ms, _parse(response))
            )
    return observations


def _validate_routes(document: Any) -> list[dict[str, Any]]:
    routes = document.get("routes") if isinstance(document, Mapping) else None
    if not isinstance(routes, list):
        raise ValueError("routes file must hold a routes list")  # noqa: TRY004
    names: set[str] = set()
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("a route is not an object")  # noqa: TRY004
        path = route.get("path")
        if not isinstance(path, str) or not path.startswith("/v1/"):
            raise ValueError(f"route path must start with /v1/: {path!r}")
        if route.get("method", "GET") != "GET":
            raise ValueError(f"{path}: only GET is measured")
        cle = route.get("cle_attendue")
        if not isinstance(cle, str) or not cle.strip():
            raise ValueError(f"{path}: cle_attendue is required")
        if not isinstance(route.get("non_vide"), bool):
            raise ValueError(f"{path}: non_vide must be a boolean")  # noqa: TRY004
        name = route.get("name") or path
        if name in names:
            raise ValueError(f"duplicate route name {name!r}")
        names.add(name)
    return [dict(route) for route in routes]


def _read_private_cases(path: str) -> dict[str, Any]:
    """Private golden input through the guard of the golden verifier."""
    from scripts.verify_golden_cases import _read_private_input

    return _read_private_input(path, None)


def _make_client(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=60.0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.environ.get("WB_HTTP_URL", ""))
    parser.add_argument("--api-key-env", default="DC_API_KEY")
    parser.add_argument("--routes-json", required=True, type=Path)
    parser.add_argument("--private-input", default=None)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--min-interval", type=float, default=0.0)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        require_loopback(args.base_url)
        routes = _validate_routes(
            json.loads(args.routes_json.read_text(encoding="utf-8"))
        )
        if args.warmup < 0 or args.samples < 1 or args.min_interval < 0:
            raise ValueError("warmup >= 0, samples >= 1, min-interval >= 0")
    except (ValueError, OSError) as exc:
        print(f"LATENCIES refused: {exc}", file=sys.stderr)
        return 2
    api_key = os.environ.get(args.api_key_env or "")
    if not api_key:
        print(f"LATENCIES refused: {args.api_key_env} is empty", file=sys.stderr)
        return 2
    needs_private = any(route.get("private_params") for route in routes)
    private_cases = _read_private_cases(args.private_input) if needs_private else {}

    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    latencies: dict[str, Any] = {}
    previous: dict[str, Any] = {}
    pace: dict[str, float] = {}
    exit_code = 0
    with _make_client(args.base_url, api_key) as client:
        for route in routes:
            name = route.get("name") or route["path"]
            try:
                observations = measure_route(
                    client,
                    route,
                    previous,
                    warmup=args.warmup,
                    samples=args.samples,
                    private_cases=private_cases,
                    min_interval_s=args.min_interval,
                    pace=pace,
                )
            except ValueError as exc:
                print(f"LATENCIES {name} not measured: {exc}", file=sys.stderr)
                latencies[route["path"]] = {"no_budget": True, "motif": str(exc)}
                exit_code = 1
                continue
            result = summarize_route(
                observations,
                cle_attendue=route["cle_attendue"],
                non_vide=route["non_vide"],
            )
            result = {"name": name, "params": public_params(route), **result}
            latencies[route["path"]] = result
            functional = [
                obs.payload
                for obs in observations
                if _is_functional(obs, route["cle_attendue"], route["non_vide"])
            ]
            if functional:
                previous[name] = functional[-1]
            if result["no_budget"]:
                exit_code = 1
            print(
                f"LATENCY {route['path']} status={result['http_status']} "
                f"functional={result['samples']}/{result['requested_samples']} "
                f"p50={result['p50_ms']} p95={result['p95_ms']} "
                f"p99={result['p99_ms']} max={result['max_ms']} "
                f"errors={result['errors']['count']}"
                + (" NO_BUDGET" if result["no_budget"] else "")
            )
    report = {
        "measured_at": started,
        "base_url": args.base_url,
        "workbase_sha": os.environ.get("WORKBASE_SHA"),
        "warmup": args.warmup,
        "samples": args.samples,
        "min_interval_s": args.min_interval,
        "percentile_method": "nearest_rank",
        "latencies": latencies,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

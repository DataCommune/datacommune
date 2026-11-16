#!/usr/bin/env python3
"""Semantic audit of REAL API responses (SEM-01, SEM-02, plan 02-18).

Every ``/v1/commune`` response of the panel and every ``/v1/resolve`` response
of the golden address cases goes through the audit of plan 02-09
(:mod:`app.common.semantic_audit`). Controlled families, by exact name:

- ``valeurs_premier_niveau``: first-level values of ``/v1/commune`` (contract
  entry -- unit, definition -- of every numeric value, declared states);
- ``provenance_source``: per-source provenance (``nom``, ``licence``, period
  state consistent with its label);
- ``coverage_resolve``: ``out_of_coverage``/``motif`` consistency of
  ``/v1/resolve``;
- ``origine_periode``: the ORIGIN of a known period, checked on the
  PRODUCER'S FACTS by ``audit_provenance_origin`` -- never on a payload: a
  response cannot tell an honest period from a fabricated one (02-09:269).

Not audited here (named in the report and in 02-SEMANTIQUE.md): nested values
of ``/v1/resolve`` with their units and provenances, DPE documentary answers.

``--self-check`` replays, without any network call, one independent negative
case per family (fabricated payload for the payload families, the mandatory
pair of 02-09:270 for ``origine_periode``). A family holds only when its
negative case is refused AND, for ``origine_periode``, its legitimate case
(label equal to the import date, carried by ``source_updated_at``) accepted.

The live run writes a structured report (``--report``, a path of the machine
it runs on): audited codes and case ids, payload counts, errors per family,
self-check. Private addresses are read from the gitignored golden input
through the guard of ``verify_golden_cases.py`` and never printed nor written;
the base URL must be loopback; the API key comes from ``--api-key-env``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.common.schemas import (
    MetaReponse,
    OutOfCoverageReason,
    ReponseApi,
    ResolutionOutcome,
    SourcePeriod,
)
from app.common.semantic_audit import (
    audit_commune_payload,
    audit_provenance_origin,
    audit_resolve_payload,
)
from app.commune.schemas import (
    COMMUNE_SOURCE_KEYS,
    ResultatCommuneSynthese,
    apply_commune_coverage,
    commune_coverage,
)
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.resolve.schemas import out_of_coverage_state, resolution_state
from app.scope.model import InstanceScope
from scripts.measure_route_latencies import require_loopback

FAMILLES_CONTROLEES = (
    "valeurs_premier_niveau",
    "provenance_source",
    "coverage_resolve",
    "origine_periode",
)
FAMILLES_NON_AUDITEES = (
    "valeurs_imbriquees_resolve",
    "unites_et_provenances_des_valeurs_resolve",
    "reponses_documentaires_dpe",
)
# Same bounded batches as the golden verifier (verify_golden_cases.py): the
# first one carries cadastre, the second the commune synthesis.
RESOLVE_DATASET_BATCHES: tuple[tuple[str, ...], ...] = (
    ("cadastre", "dvf", "dpe", "risques", "urbanisme"),
    ("commune",),
)
DEFAULT_PRIVATE_INPUT = "../../contracts/golden-cases/v1/private/golden-selection.json"
DEFAULT_MANIFEST = "../../contracts/golden-cases/v1/manifest.json"

_D = datetime(2026, 9, 22, 8, 30, tzinfo=UTC)
_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
_ROW = {
    "code_insee": "44109",
    "nom_commune": "Commune de controle",
    "code_departement": "44",
    "code_region": "52",
    "population": 1000,
    "annee_demographie": 2021,
    "nb_equipements": 0,
    "annee_equipements": 2022,
}


# ------------------------------------------------------------------ families --


def audit_commune(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """Errors of a /v1/commune body, each tagged with its family."""
    return [
        {
            "famille": (
                "provenance_source"
                if message.startswith("meta.source")
                else "valeurs_premier_niveau"
            ),
            "message": message,
        }
        for message in audit_commune_payload(payload)
    ]


def audit_resolve(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"famille": "coverage_resolve", "message": message}
        for message in audit_resolve_payload(payload)
    ]


# ---------------------------------------------------------------- self-check --


def _producer_commune_payload() -> dict[str, Any]:
    """A /v1/commune body built by the real producer (coverage + provenance)."""
    scope = InstanceScope(frozenset(COMMUNE_SOURCE_KEYS), ("44", "85"))
    served = apply_commune_coverage(_ROW, commune_coverage(scope, _ROW["code_insee"]))
    provenances = [
        assemble_provenance(key, ProvenanceFacts(), _NOW) for key in COMMUNE_SOURCE_KEYS
    ]
    body = ReponseApi(
        donnees=[ResultatCommuneSynthese(**served)],
        meta=MetaReponse(source=provenances),
    )
    return body.model_dump(mode="json")


def _producer_resolve_payload() -> dict[str, Any]:
    states = {
        "dvf": resolution_state("dvf", [{"valeur_fonciere": 1}], is_error=False),
        "cadastre": out_of_coverage_state(
            "cadastre", OutOfCoverageReason.DATASET_NON_HEBERGE
        ),
    }
    return {
        "statut": "ok",
        "donnees": [
            {
                "dataset_states": {
                    name: state.model_dump(mode="json")
                    for name, state in states.items()
                }
            }
        ],
        "meta": {"resolution": ResolutionOutcome(issue="exact").model_dump()},
    }


def _has(errors: list[dict[str, str]], family: str) -> bool:
    return any(error["famille"] == family for error in errors)


def self_check() -> dict[str, Any]:
    """One independent negative case per family, no network."""
    refus: dict[str, bool] = {}

    base = _producer_commune_payload()
    base_errors = audit_commune(base)
    value_case = json.loads(json.dumps(base))
    value_case["donnees"][0]["nb_licornes"] = 3  # a value without a contract unit
    refus["valeurs_premier_niveau"] = not _has(
        base_errors, "valeurs_premier_niveau"
    ) and _has(audit_commune(value_case), "valeurs_premier_niveau")
    licence_case = json.loads(json.dumps(base))
    del licence_case["meta"]["source"][0]["licence"]  # a source without licence
    refus["provenance_source"] = not _has(base_errors, "provenance_source") and _has(
        audit_commune(licence_case), "provenance_source"
    )

    resolve = _producer_resolve_payload()
    reason_case = json.loads(json.dumps(resolve))
    del reason_case["donnees"][0]["dataset_states"]["cadastre"]["motif"]
    refus["coverage_resolve"] = not audit_resolve(resolve) and bool(
        audit_resolve(reason_case)
    )

    # 02-09:270 -- (i) label equal to the import date, carried by the source
    # update date: legitimate; (ii) the same label with no upstream fact: refused.
    legit_facts = ProvenanceFacts(last_success_at=_D, source_updated_at=_D)
    legit = assemble_provenance("dvf", legit_facts, _NOW)
    accepted = legit.periode == SourcePeriod(
        statut="connue", label=_D.date().isoformat()
    ) and not audit_provenance_origin(legit_facts, legit)
    bare_facts = ProvenanceFacts(last_success_at=_D)
    fabricated = assemble_provenance("dvf", bare_facts, _NOW).model_copy(
        update={"periode": SourcePeriod(statut="connue", label=_D.date().isoformat())}
    )
    refused = bool(audit_provenance_origin(bare_facts, fabricated))
    refus["origine_periode"] = accepted and refused
    return {"refus": refus, "acceptations": {"origine_periode": accepted}}


# ------------------------------------------------------------------ run part --


def _read_private_cases(path: str) -> dict[str, Any]:
    from scripts.verify_golden_cases import _read_private_input

    return _read_private_input(path, None)


def _make_client(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=60.0)


class _Pacer:
    def __init__(self, interval_s: float, sleep: Callable[[float], None]) -> None:
        self.interval_s, self.sleep, self.last = interval_s, sleep, None

    def wait(self) -> None:
        if self.last is not None and self.interval_s > 0:
            remaining = self.interval_s - (time.monotonic() - self.last)
            if remaining > 0:
                self.sleep(remaining)
        self.last = time.monotonic()


def _get(client: httpx.Client, pacer: _Pacer, path: str, params: dict) -> Any:
    """JSON body of a 200 answer, else an error message without parameters."""
    pacer.wait()
    response = client.get(path, params=params)
    if response.status_code != 200:
        return f"HTTP {response.status_code} on {path}"
    try:
        return response.json()
    except ValueError:
        return f"non-JSON body on {path}"


def _audit_communes(client, pacer, codes: list[str]) -> dict[str, Any]:
    audited, errors, payloads = [], [], 0
    for code in codes:
        audited.append(code)
        body = _get(client, pacer, "/v1/commune", {"code_insee": code})
        if isinstance(body, str):
            errors.append({"code_insee": code, "famille": "http", "message": body})
            continue
        payloads += 1
        errors += [{"code_insee": code, **error} for error in audit_commune(body)]
    return {"codes_audites": audited, "payloads": payloads, "erreurs": errors}


def _audit_addresses(client, pacer, case_ids, private) -> dict[str, Any]:
    audited, errors, payloads, responses = [], [], 0, 0
    for case_id in case_ids:
        audited.append(case_id)
        query = (private.get(case_id) or {}).get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append(
                {"case_id": case_id, "famille": "entree", "message": "no private query"}
            )
            continue
        complete = True
        for batch in RESOLVE_DATASET_BATCHES:
            body = _get(
                client,
                pacer,
                "/v1/resolve",
                {"q": query, "type": "adresse", "datasets": ",".join(batch)},
            )
            if isinstance(body, str):
                complete = False
                errors.append({"case_id": case_id, "famille": "http", "message": body})
                continue
            responses += 1
            errors += [{"case_id": case_id, **error} for error in audit_resolve(body)]
        payloads += int(complete)
    return {
        "case_ids_audites": audited,
        "payloads": payloads,
        "reponses": responses,
        "lots_datasets": [list(batch) for batch in RESOLVE_DATASET_BATCHES],
        "erreurs": errors,
        "blocages_declares": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--base-url", default=os.environ.get("WB_HTTP_URL", ""))
    parser.add_argument("--api-key-env", default="DC_API_KEY")
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path(DEFAULT_MANIFEST))
    parser.add_argument("--addresses-from-private-golden", action="store_true")
    parser.add_argument("--private-input", default=DEFAULT_PRIVATE_INPUT)
    parser.add_argument("--min-interval", type=float, default=0.0)
    parser.add_argument("--report", type=Path)
    return parser


def _print_self_check(result: Mapping[str, Any]) -> bool:
    ok = all(result["refus"].values()) and all(result["acceptations"].values())
    for family in FAMILLES_CONTROLEES:
        print(f"SELF_CHECK {family} refus={result['refus'][family]}")
    print(f"SELF_CHECK origine_periode acceptation={result['acceptations']}")
    print("SELF_CHECK OK" if ok else "SELF_CHECK FAILED")
    return ok


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    checked = self_check()
    if args.self_check:
        return 0 if _print_self_check(checked) else 1
    try:
        require_loopback(args.base_url)
        if args.panel is None or args.report is None:
            raise ValueError("--panel and --report are required")
    except ValueError as exc:
        print(f"SEMANTIC_AUDIT refused: {exc}", file=sys.stderr)
        return 2
    api_key = os.environ.get(args.api_key_env or "")
    if not api_key:
        print(f"SEMANTIC_AUDIT refused: {args.api_key_env} is empty", file=sys.stderr)
        return 2
    codes = list(json.loads(args.panel.read_text(encoding="utf-8"))["codes"])
    case_ids: list[str] = []
    private: dict[str, Any] = {}
    if args.addresses_from_private_golden:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        case_ids = [
            case["case_id"]
            for case in manifest["cases"]
            if case.get("type") == "address"
        ]
        private = _read_private_cases(args.private_input)

    pacer = _Pacer(args.min_interval, time.sleep)
    with _make_client(args.base_url, api_key) as client:
        commune = _audit_communes(client, pacer, codes)
        resolve = _audit_addresses(client, pacer, case_ids, private)
    report = {
        "genere_le": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workbase_sha": os.environ.get("WORKBASE_SHA"),
        "base_url": args.base_url,
        "familles_controlees": list(FAMILLES_CONTROLEES),
        "familles_non_auditees": list(FAMILLES_NON_AUDITEES),
        "origine_periode": (
            "controlee sur les faits du producteur (audit_provenance_origin), "
            "jamais sur la charge utile (02-09:269) ; cas du --self-check"
        ),
        "commune": commune,
        "resolve": resolve,
        "self_check": checked,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    errors = commune["erreurs"] + resolve["erreurs"]
    held = all(checked["refus"].values()) and all(checked["acceptations"].values())
    if errors or not held:
        for error in errors:
            where = error.get("code_insee") or error.get("case_id")
            print(
                f"SEMANTIC_AUDIT ERROR {where} {error['famille']}: {error['message']}"
            )
        return 1
    print(
        f"SEMANTIC_AUDIT OK {commune['payloads'] + resolve['payloads']} payloads "
        f"couverture={','.join(FAMILLES_CONTROLEES)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

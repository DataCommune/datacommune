#!/usr/bin/env python3
"""Select and minimize live golden cases from a private candidate pool.

The private input contains exact addresses/parcel references and remains outside
Git. This CLI validates every candidate against the live DB and API using the
same read-only primitives as the verifier, derives categories from observed
dataset states, then writes only the minimized manifest. It never prints case
payloads or private values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import httpx

# Support the documented direct invocation from ``api/backend`` while keeping
# package imports identical when the module is loaded by pytest.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.verify_golden_cases import (
    SMOKE_HEADER,
    GoldenSecurityError,
    _open_read_only_connection,
    _read_private_input,
    _required_env,
    audit_artifact_text,
    collect_live_observation_projected,
    derive_category,
    load_schema,
    observation_is_satisfied,
    redact_entry,
    validate_case_entry,
    validate_manifest,
)

CANDIDATE_CATEGORIES: tuple[str, ...] = (
    "complete",
    "partial",
    "no_dvf",
    "no_dpe",
    "dataset_out_of_coverage",
)

COMMUNE_FIELDS: tuple[str, ...] = (
    "code_insee",
    "nom_commune",
    "code_departement",
    # Referential labels approved by contracts/fiche-fields.v1.json (43-03).
    "nom_departement",
    "code_region",
    "nom_region",
    "population",
    "nb_menages",
    "nb_logements",
    "nb_logements_vacants_recensement",
    "densite",
    "superficie",
    "annee_demographie",
    "revenu_median",
    "taux_pauvrete",
    "indice_gini",
    "part_imposee",
    "annee_revenus",
    "zone_abc",
    "zone_abc_reclassement",
    "taux_vacance",
    "nb_vacants_plus_2ans",
    "annee_vacance",
    "taux_tfpb",
    "taux_th_rs",
    "taux_teom",
    "nom_epci",
    "annee_fiscalite",
    "loyer_m2_appartement",
    "loyer_m2_maison",
    "annee_loyers",
    "prix_m2_median",
    "nb_mutations_12m",
    "dpe_passoires_pct",
    "dpe_median",
    "annee_construction_mediane",
    "nb_equipements",
    "nb_etablissements_scolaires",
    "nb_monuments",
    "nb_proprietaires_pm",
    "surface_pm_ha",
    "fibre_pct",
)

_ADDRESS_DATASETS: tuple[str, ...] = (
    "cadastre",
    "dvf",
    "dpe",
    "risques",
    "urbanisme",
    "commune",
)


def build_candidate_entry(
    *,
    case_id: str,
    case_type: str,
    departement: str,
    code_insee: str,
    category: str,
    frozen_at: str,
    source_millesimes: dict[str, str],
    invariants: dict[str, Any],
) -> dict[str, Any]:
    """Build a closed-shape manifest entry and fail if minimization is unsafe."""
    entry = {
        "case_id": case_id,
        "type": case_type,
        "departement": departement,
        "code_insee": code_insee,
        "category": category,
        "frozen_at": frozen_at,
        "source_millesimes": dict(source_millesimes),
        "invariants": invariants,
        "private_ref": case_id,
    }
    redacted = redact_entry(entry)
    errors = validate_case_entry(redacted, load_schema())
    if errors:
        raise GoldenSecurityError("candidate entry violates the minimized schema")
    return redacted


def extract_private_input(*, case_id: str, geocode: dict[str, Any]) -> dict[str, Any]:
    """Build a private entry; callers must store it only in the gitignored file."""
    return {"case_id": case_id, **dict(geocode)}


def _probe_entry(case_id: str, private: dict[str, Any]) -> dict[str, Any]:
    case_type = private.get("type")
    code = private.get("code_insee")
    if case_type not in {"commune", "address"} or not isinstance(code, str):
        raise GoldenSecurityError("private candidate lacks a valid type/code_insee")
    api_datasets = ("dvf", "dpe") if case_type == "commune" else _ADDRESS_DATASETS
    mapper = (
        {"densite_unit": "density"}
        if case_type == "commune"
        else {"prix_m2_unit": "euro_per_m2"}
    )
    return {
        "case_id": case_id,
        "type": case_type,
        "departement": code[:2],
        "code_insee": code,
        "category": "partial",
        "frozen_at": date.today().isoformat(),
        "source_millesimes": {},
        "invariants": {
            "db": {},
            "api": {f"{dataset}_state": "not_reported" for dataset in api_datasets},
            "mapper": mapper,
        },
        "private_ref": case_id,
    }


# Phase 51: the category derivation moved to verify_golden_cases so the live
# ``manifest_patch`` (Plan 51-07) and this selector cannot label the same
# observation differently. The private name is kept: it IS the selector's
# derivation, it simply no longer owns a second copy of the rule.
_derive_category = derive_category


def _project_commune_payload(
    probe: dict[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep only aggregate commune fields; never return address-level payloads."""
    if probe["type"] == "commune":
        commune = payload
    else:
        datasets = payload.get("datasets")
        commune = datasets.get("commune") if isinstance(datasets, dict) else None
    if not isinstance(commune, dict):
        raise GoldenSecurityError("API projection lacks aggregate commune data")
    extra_keys = set(commune) - set(COMMUNE_FIELDS)
    if extra_keys:
        raise GoldenSecurityError("aggregate commune payload has unapproved fields")
    projected = {field: commune.get(field) for field in COMMUNE_FIELDS}
    if projected["code_insee"] != probe["code_insee"]:
        raise GoldenSecurityError("aggregate commune projection identity mismatch")
    if any(
        value is not None and not isinstance(value, (str, int, float, bool))
        for value in projected.values()
    ):
        raise GoldenSecurityError("aggregate commune projection has nested values")
    return projected


def _dataset_template(
    dataset: str, state: str, *, case_id: str, code_insee: str
) -> object:
    """Map a live state to a fixed non-identifying mapper-shape template."""
    if state == "not_reported":
        return None
    if state in {"out_of_coverage", "not_applicable"}:
        raise KeyError(dataset)
    if state != "value":
        raise GoldenSecurityError("unstable dataset state cannot enter a fixture")
    if dataset == "cadastre":
        suffix = case_id.rsplit("-", 1)[-1]
        return {
            "id_parcelle": f"{code_insee}-GOLDEN-{suffix}",
            "section": None,
            "numero": None,
            "contenance": None,
        }
    if dataset == "dpe":
        return [{"etiquette_dpe": "D"}]
    if dataset == "dvf":
        return [{"prix_m2": 0}]
    if dataset == "risques":
        return {"argiles": None, "risques_naturels": []}
    if dataset == "urbanisme":
        return {
            "zones_plu": [{"properties": {"libelle": "GOLDEN", "typezone": "U"}}],
            "prescriptions": [],
        }
    raise GoldenSecurityError("unapproved dataset in frontend projection")


def _build_frontend_case(
    entry: dict[str, Any], commune: dict[str, Any]
) -> dict[str, Any]:
    """Build a state-derived fixture case with no live property-level value."""
    frontend_case: dict[str, Any] = {
        key: entry[key]
        for key in (
            "case_id",
            "type",
            "departement",
            "code_insee",
            "category",
            "frozen_at",
            "source_millesimes",
            "invariants",
        )
    }
    frontend_case["commune"] = commune
    if entry["type"] == "commune":
        return frontend_case

    datasets: dict[str, object] = {}
    api_states = entry["invariants"]["api"]
    for dataset in _ADDRESS_DATASETS:
        if dataset == "commune":
            continue
        state = api_states[f"{dataset}_state"]
        try:
            datasets[dataset] = _dataset_template(
                dataset,
                state,
                case_id=entry["case_id"],
                code_insee=entry["code_insee"],
            )
        except KeyError:
            continue
    frontend_case["resolve"] = {
        "type_detecte": "adresse",
        "latitude": 0,
        "longitude": 0,
        "adresse": None,
        "id_parcelle": None,
        "commune": None,
        "code_postal": None,
        "score": None,
        "datasets": datasets,
        "erreurs": [],
        "avertissements": [],
    }
    return frontend_case


def _render_frontend_fixture(cases: list[dict[str, Any]]) -> str:
    body = json.dumps(cases, ensure_ascii=False, indent=2)
    return (
        "import type { GoldenSyntheticCase } from './golden-cases.synthetic';\n\n"
        "export type GoldenRealCase = GoldenSyntheticCase & {\n"
        "  source_millesimes: Record<string, string>;\n"
        "  invariants: {\n"
        "    db: Record<string, boolean>;\n"
        "    api: Record<string, string>;\n"
        "    mapper: Record<string, string>;\n"
        "  };\n"
        "  // Referential labels only present in the multi resolve commune block\n"
        "  // (contracts/fiche-fields.v1.json) — /v1/commune does not return them,\n"
        "  // so CommuneSynthese deliberately stays without them.\n"
        "  commune: GoldenSyntheticCase['commune'] & {\n"
        "    nom_departement?: string | null;\n"
        "    nom_region?: string | null;\n"
        "  };\n"
        "};\n\n"
        "// Derived from one protected GET pass. Property-level values use fixed\n"
        "// non-identifying templates. Commune fields use a closed allowlist.\n"
        f"export const GOLDEN_REAL_CORPUS = {body} as GoldenRealCase[];\n"
    )


def _observe_candidates(
    private_cases: dict[str, Any],
    dsn: str,
    api_base_url: str,
    api_key: str,
    *,
    resolve_logging_redacted: bool,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    conn = _open_read_only_connection(dsn)
    entries: list[dict[str, Any]] = []
    commune_projections: dict[str, dict[str, Any]] = {}
    try:
        with httpx.Client(
            base_url=api_base_url,
            headers={"X-API-Key": api_key, SMOKE_HEADER[0]: SMOKE_HEADER[1]},
            timeout=30,
        ) as client:
            for index, (case_id, private) in enumerate(private_cases.items()):
                if index:
                    # The public API rate-limits per IP; back-to-back resolve
                    # calls 429 after ~10 requests and abort the whole run.
                    time.sleep(7)
                if not isinstance(private, dict):
                    raise GoldenSecurityError("private candidate must be an object")
                probe = _probe_entry(case_id, private)
                # R3-02 metrics are report-only material: the selector freezes
                # boolean invariants, never raw counters.
                observed, millesimes, _metrics, commune_projection = (
                    collect_live_observation_projected(
                        conn,
                        client,
                        probe,
                        private,
                        partial(_project_commune_payload, probe),
                        resolve_logging_redacted=resolve_logging_redacted,
                    )
                )
                category = _derive_category(observed["api"])
                if category is None:
                    # A transient upstream failure is not a stable golden state.
                    # Exclude this candidate without exposing its case or input.
                    continue
                # R3-02 remains report-only until a green replay has produced
                # the reviewed evidence. Observation may compute the boolean,
                # but corpus regeneration must not freeze it prematurely.
                frozen_db = {
                    key: value
                    for key, value in observed["db"].items()
                    if key != "dpe_rattachement_ok"
                }
                entries.append(
                    build_candidate_entry(
                        case_id=case_id,
                        case_type=probe["type"],
                        departement=probe["departement"],
                        code_insee=probe["code_insee"],
                        category=category,
                        frozen_at=probe["frozen_at"],
                        source_millesimes=millesimes,
                        # The live observation only yields db/api layers; the
                        # mapper block is the probe's fixed non-identifying
                        # template and must be re-attached for the schema.
                        invariants={
                            "db": frozen_db,
                            "api": observed["api"],
                            "mapper": probe["invariants"]["mapper"],
                        },
                    )
                )
                commune_projections[case_id] = commune_projection
    finally:
        conn.rollback()
        conn.close()
    return entries, commune_projections


def _observation_coverage_mask(
    entry: dict[str, Any], observation_reqs: tuple[dict[str, Any], ...]
) -> int:
    """Bit mask of which ``required_observations`` entries this candidate satisfies.

    Same generalized mechanism as ``category_bits`` below, applied to
    ``required_observations`` instead of a closed category set (Phase 48,
    schema.json). Both the triple and the conjunctive shape are honoured, via
    the single ``observation_is_satisfied`` shared with the manifest
    validation: selection and validation cannot diverge.
    """
    mask = 0
    for index, requirement in enumerate(observation_reqs):
        if observation_is_satisfied(entry, requirement):
            mask |= 1 << index
    return mask


def _departement_coverage_mask(
    entry: dict[str, Any], coverage: Mapping[str, Any] | None
) -> tuple[int, ...]:
    """This candidate's contribution to ``required_departement_coverage``.

    Same generalized coverage mechanism as ``_observation_coverage_mask``
    above, applied to the departement dimension (Phase 57 Plan 03, todo folded
    from 2026-07-27) -- but additive per departement rather than a single
    OR'd bit: ``min_par_departement`` needs an actual count of matching
    candidates, and OR is idempotent (a second address in the same departement
    would never move a presence bit). Returns one integer per departement
    declared in ``coverage["departements"]``, in that order -- 1 when this
    candidate's ``type`` matches ``coverage["type"]`` AND its ``code_insee``
    prefix equals that departement, 0 otherwise (a candidate belongs to at
    most one departement, so at most one position is ever 1). Returns ``()``
    when ``coverage`` is absent (the key is a no-op then, exactly like
    ``pinned_cases`` and ``required_observations`` when absent).
    """
    if not coverage:
        return ()
    departements = coverage["departements"]
    if entry.get("type") != coverage.get("type"):
        return tuple(0 for _ in departements)
    code_insee = entry.get("code_insee")
    prefix = code_insee[:2] if isinstance(code_insee, str) else None
    return tuple(1 if prefix == dept else 0 for dept in departements)


def _add_departement_contribution(
    state: tuple[int, ...], contribution: tuple[int, ...], cap: int
) -> tuple[int, ...]:
    """Add one candidate's per-departement contribution, capped at ``cap``.

    Capping at ``min_par_departement`` (rather than letting counts grow
    unbounded) keeps the dynamic-programming state space in
    ``_select_minimal_entries`` small and deterministic: once a departement's
    requirement is met, further matching candidates cannot move the state
    anywhere new. ``zip`` naturally yields ``()`` when both tuples are empty
    (no coverage requirement declared), so no requirement means no-op here
    too, without a separate branch.
    """
    return tuple(
        min(current + delta, cap)
        for current, delta in zip(state, contribution, strict=True)
    )


def _pinned_entries(
    by_id: dict[str, dict[str, Any]], schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Resolve ``pinned_cases`` against the observed pool, failing closed.

    ``pinned_cases`` is the mechanical answer to incident 48-12 9.2: a frozen
    case carrying no unique requirement (``golden-address-009``) was evicted by
    minimization alone, silently. Absent from the schema, the key is a no-op --
    the selection is then exactly the one that runs today.

    What it guarantees: the frozen corpus survives minimization, whatever
    smaller covering subset exists. What it costs, named because it is real
    (48-GOLDEN-EVIDENCE 9.5, option B): every pin consumes one slot of the
    selection margin -- ``cardinality.address.max`` being 10, ten pinned
    addresses would leave the selector no freedom at all.
    """
    pinned_ids = list(schema.get("pinned_cases", ()))
    missing = [case_id for case_id in pinned_ids if case_id not in by_id]
    if missing:
        # A case_id is an opaque, schema-constrained identifier: no address, no
        # cadastral reference and no DSN can transit through this message.
        raise GoldenSecurityError(
            "pinned corpus case absent from the candidate pool: "
            + ", ".join(sorted(missing))
        )
    pinned = [by_id[case_id] for case_id in sorted(set(pinned_ids))]
    for case_type, bounds in schema["cardinality"].items():
        if sum(entry["type"] == case_type for entry in pinned) > bounds["max"]:
            raise GoldenSecurityError(
                f"pinned corpus cases exceed the {case_type} cardinality maximum"
            )
    return pinned


def _pinned_seed(
    pinned: list[dict[str, Any]],
    category_bits: dict[str, int],
    observation_reqs: tuple[dict[str, Any], ...],
    departement_coverage: Mapping[str, Any] | None,
) -> tuple[tuple[int, int, int, int, tuple[int, ...]], tuple[str, ...]]:
    """Fold the pinned cases into the initial dynamic-programming state."""
    mask = 0
    obs_mask = 0
    dept_min = (
        departement_coverage["min_par_departement"] if departement_coverage else 0
    )
    dept_state: tuple[int, ...] = (
        tuple(0 for _ in departement_coverage["departements"])
        if departement_coverage
        else ()
    )
    for entry in pinned:
        mask |= category_bits[entry["category"]]
        obs_mask |= _observation_coverage_mask(entry, observation_reqs)
        dept_state = _add_departement_contribution(
            dept_state,
            _departement_coverage_mask(entry, departement_coverage),
            dept_min,
        )
    state = (
        sum(entry["type"] == "commune" for entry in pinned),
        sum(entry["type"] == "address" for entry in pinned),
        mask,
        obs_mask,
        dept_state,
    )
    return state, tuple(entry["case_id"] for entry in pinned)


def _select_minimal_entries(  # noqa: C901
    entries: list[dict[str, Any]], schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Select the smallest deterministic subset satisfying D-10.

    The private pool may deliberately contain more candidates than the frozen
    corpus permits. Dynamic programming keeps only type counts, the category
    coverage mask and the ``required_observations`` coverage mask (Phase 48);
    raw candidate inputs never enter this function.

    ``required_observations`` is enforced exactly like ``required_categories``:
    without it, a regeneration could silently drop the multi-logement signal
    cases the moment a smaller covering subset exists (48-RESEARCH.md Q6). An
    entry of that list is either a ``{layer, invariant, value}`` triple or a
    conjunction ``{"all_of": [triple, ...]}`` -- the conjunctive shape is what
    makes the composite "maison divisee" archetype expressible at all
    (48-GOLDEN-EVIDENCE 13.1); everything else here is unchanged, same dynamic
    programming state, same determinism, same firm failure.

    ``pinned_cases`` (see ``_pinned_entries``) seeds that dynamic programming
    instead of being proposed to it: pinned cases are always part of the
    result, and the complement keeps the same deterministic lexicographic
    tie-break. They lead the returned list; the complement follows in ascending
    ``case_id`` order.

    ``required_departement_coverage`` (Phase 57 Plan 03) adds a fifth state
    dimension on the same model, generalized to the departement of the
    ``code_insee`` prefix instead of a category or an invariant -- same
    selector, same firm failure ``candidate pool cannot satisfy corpus
    coverage``. Absent from the schema, it is a no-op, exactly like
    ``required_observations`` and ``pinned_cases`` when absent.
    """
    categories = tuple(schema["allowed_categories"])
    category_bits = {category: 1 << index for index, category in enumerate(categories)}
    # Coverage is driven by required_categories: 'partial' stays allowed but is
    # unobservable on the 44/85 live surface (43-09 amendment, see schema.json).
    full_mask = 0
    for category in schema["required_categories"]:
        full_mask |= category_bits[category]
    observation_reqs = tuple(schema.get("required_observations", ()))
    obs_full_mask = (1 << len(observation_reqs)) - 1 if observation_reqs else 0
    departement_coverage = schema.get("required_departement_coverage")
    dept_min = (
        departement_coverage["min_par_departement"] if departement_coverage else 0
    )
    dept_target: tuple[int, ...] = (
        tuple(dept_min for _ in departement_coverage["departements"])
        if departement_coverage
        else ()
    )
    cardinality = schema["cardinality"]
    max_communes = cardinality["commune"]["max"]
    max_addresses = cardinality["address"]["max"]

    by_id = {entry["case_id"]: entry for entry in entries}
    if len(by_id) != len(entries):
        raise GoldenSecurityError("candidate pool contains duplicate case IDs")
    if any(validate_case_entry(entry, schema) for entry in entries):
        raise GoldenSecurityError("candidate pool contains an invalid redacted entry")

    pinned = _pinned_entries(by_id, schema)
    pinned_ids = {entry["case_id"] for entry in pinned}
    seed_state, seed_ids = _pinned_seed(
        pinned, category_bits, observation_reqs, departement_coverage
    )

    # state -> lexicographically-smallest case_id tuple reaching it (every tuple
    # shares the same pinned prefix, so the tie-break is unaffected)
    states: dict[tuple[int, int, int, int, tuple[int, ...]], tuple[str, ...]] = {
        seed_state: seed_ids
    }
    for entry in sorted(entries, key=lambda candidate: candidate["case_id"]):
        if entry["case_id"] in pinned_ids:
            continue  # already folded into the seed state, never re-proposed
        case_type = entry.get("type")
        category = entry.get("category")
        if case_type not in cardinality or category not in category_bits:
            raise GoldenSecurityError("observed candidate is outside the closed schema")
        entry_obs_mask = _observation_coverage_mask(entry, observation_reqs)
        entry_dept_contribution = _departement_coverage_mask(
            entry, departement_coverage
        )
        for (
            communes,
            addresses,
            mask,
            obs_mask,
            dept_state,
        ), selected_ids in list(states.items()):
            next_communes = communes + (case_type == "commune")
            next_addresses = addresses + (case_type == "address")
            if next_communes > max_communes or next_addresses > max_addresses:
                continue
            next_state = (
                next_communes,
                next_addresses,
                mask | category_bits[category],
                obs_mask | entry_obs_mask,
                _add_departement_contribution(
                    dept_state, entry_dept_contribution, dept_min
                ),
            )
            next_ids = (*selected_ids, entry["case_id"])
            incumbent = states.get(next_state)
            if incumbent is None or next_ids < incumbent:
                states[next_state] = next_ids

    valid: list[tuple[int, int, int, tuple[str, ...]]] = []
    for state_key, selected_ids in states.items():
        communes, addresses, mask, obs_mask, dept_state = state_key
        if (
            cardinality["commune"]["min"] <= communes <= cardinality["commune"]["max"]
            and cardinality["address"]["min"]
            <= addresses
            <= cardinality["address"]["max"]
            and mask & full_mask == full_mask
            and obs_mask & obs_full_mask == obs_full_mask
            and dept_state == dept_target
        ):
            valid.append((len(selected_ids), communes, addresses, selected_ids))
    if not valid:
        raise GoldenSecurityError("candidate pool cannot satisfy corpus coverage")

    selected_ids = min(valid)[-1]
    return [by_id[case_id] for case_id in selected_ids]


def _safe_manifest_path(raw: str) -> Path:
    target = Path(raw).resolve()
    expected_parent = (
        Path(__file__).resolve().parents[3] / "contracts" / "golden-cases" / "v1"
    ).resolve()
    if target.parent != expected_parent or target.name != "manifest.json":
        raise GoldenSecurityError(
            "manifest output must be contracts/golden-cases/v1/manifest.json"
        )
    return target


def _safe_frontend_fixture_path(raw: str) -> Path:
    target = Path(raw).resolve()
    expected = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "lib"
        / "data"
        / "__fixtures__"
        / "golden-cases.ts"
    ).resolve()
    if target != expected:
        raise GoldenSecurityError(
            "frontend output must be the protected golden-cases.ts fixture"
        )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--frontend-fixture", required=True)
    private = parser.add_mutually_exclusive_group(required=True)
    private.add_argument("--private-input", help="Path to gitignored private JSON")
    private.add_argument(
        "--private-input-env", help="Environment variable containing private JSON"
    )
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--dsn-env", default="DATABASE_URL")
    parser.add_argument("--api-base-url-env", default="API_BASE_URL")
    parser.add_argument("--api-key-env", default="DC_API_KEY")
    parser.add_argument(
        "--resolve-log-redaction-env",
        default="DC_GOLDEN_RESOLVE_LOG_REDACTED",
    )
    args = parser.parse_args(argv)
    if not args.read_only:
        parser.error("selection requires --read-only")
    try:
        private_cases = _read_private_input(args.private_input, args.private_input_env)
        if os.getenv(args.resolve_log_redaction_env) != "1":
            raise GoldenSecurityError("resolve query-log redaction is not confirmed")
        observed_entries, commune_projections = _observe_candidates(
            private_cases,
            _required_env(args.dsn_env),
            _required_env(args.api_base_url_env),
            _required_env(args.api_key_env),
            resolve_logging_redacted=True,
        )
        entries = _select_minimal_entries(observed_entries, load_schema())
        manifest = {
            "schema_version": load_schema()["schema_version"],
            "description": (
                "Live-selected minimized golden corpus; private inputs remain "
                "gitignored."
            ),
            "cases": entries,
        }
        errors = validate_manifest(manifest, load_schema())
        if errors:
            print(f"selection: BLOCKED ({len(errors)} contract errors)")
            return 1
        fixture_cases = [
            _build_frontend_case(entry, commune_projections[entry["case_id"]])
            for entry in entries
        ]
        fixture_text = _render_frontend_fixture(fixture_cases)
        manifest_text = json.dumps(manifest, indent=2) + "\n"
        violations = audit_artifact_text(manifest_text, "manifest")
        violations += audit_artifact_text(fixture_text, "frontend-fixture")
        if violations:
            print(f"selection: BLOCKED ({len(set(violations))} leak indicators)")
            return 1
        manifest_output = _safe_manifest_path(args.manifest)
        fixture_output = _safe_frontend_fixture_path(args.frontend_fixture)
        manifest_output.write_text(manifest_text, encoding="utf-8")
        fixture_output.write_text(fixture_text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        # Operational exceptions can embed DSNs or address query strings. Fail
        # closed without rendering the exception or any private candidate value.
        print("selection: BLOCKED")
        return 1
    print(f"selection: OK ({len(entries)} redacted cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

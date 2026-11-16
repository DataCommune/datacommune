#!/usr/bin/env python3
"""Replay the fidelity comparison of the work base with the old base (D-25 to D-28).

Compares the frozen references of the old base (``--reference-dir``, produced
by ``extract_fidelity_reference.py`` in plan 02-03) with an extraction of the
work base made by the same extractor on the same panel (``--candidate-dir``):

- level 1: row counts per table x departement (44, 85, total);
- level 2: ``v_commune_synthese`` values on the fixed commune panel;
- level 3: consolidation of the redacted golden report of plan 02-13
  (``--golden-report`` + ``--golden-map``): a failed case brings each dataset
  it exercises to ``regression`` unless a valid classification explains it;
  the pseudo-cases ``runner`` and ``manifest`` are infrastructure errors.

Every gap carries a state from a closed list (``correction_voulue``,
``source_differente``, ``regression``, ``methode_de_reference_invalide``,
``inexpliquee``). A state other than ``inexpliquee`` comes only from an entry
of ``--classifications`` whose evidence is present and whose two fingerprints
(SHA-256 of the serialized reference and candidate observations) match the
observation: a classification never absolves a value it has not seen. No
numeric tolerance exists anywhere.

Without a golden report the global verdict is ``golden_non_rejoue``, never
``conforme``.

Exit codes: 0 when no compared dataset is ``regression`` or ``inexpliquee``
and no golden failure, coverage error or infrastructure error is open; 1
otherwise; 2 on a structural error (falsified reference, run-ids missing or
unresolvable, incomplete perimeter, malformed input). The report is written
before exit 1, never on exit 2.

Stdlib only; runs with any Python 3.12 from the repository root::

    python api/backend/scripts/compare_fidelity.py \\
        --reference-dir api/backend/tests/fixtures/fidelity_ct275 \\
        --candidate-dir <phase>/fidelite/candidate \\
        --classifications <phase>/fidelite/classifications.json \\
        --run-ids <phase>/fidelite/run-ids.json \\
        --out <phase>/fidelite/report.json [--dataset vacance] \\
        [--markdown <phase>/02-FIDELITE.md] \\
        [--golden-report <phase>/fidelite/golden-live-report.redacted.json \\
         --golden-map <phase>/fidelite/golden-map.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE_DIR = REPO_ROOT / ".planning" / "phases" / "02-donnees-reparees-rejouables"

DEPARTEMENTS: tuple[str, ...] = ("44", "85")
CELLS: tuple[str, ...] = ("44", "85", "total")

CATEGORIES: tuple[str, ...] = (
    "correction_voulue",
    "source_differente",
    "regression",
    "methode_de_reference_invalide",
    "inexpliquee",
)
# Worst first. A dataset takes the worst verdict of its gaps.
VERDICT_ORDER: tuple[str, ...] = (
    "regression",
    "inexpliquee",
    "comparaison_impossible",
    "ecarts_expliques",
    "conforme",
)
CATEGORY_VERDICT = {
    "regression": "regression",
    "inexpliquee": "inexpliquee",
    "methode_de_reference_invalide": "comparaison_impossible",
    "correction_voulue": "ecarts_expliques",
    "source_differente": "ecarts_expliques",
}

# State of each key of the V1 perimeter (02-DATASETS.md v1-keys), from the
# source -> pipeline -> receipt correspondence of plan 02-11
# (02-DATASETS.md:10-12, 33, 42, 73-79).
PERIMETRE: dict[str, str] = {
    **{
        key: f"importe:{key}"
        for key in (
            "antennes",
            "ban",
            "batiments",
            "cadastre",
            "connectivite",
            "copropriete",
            "demographie",
            "dpe",
            "dvf",
            "equipements",
            "fiscalite",
            "foncier_pm",
            "loyers",
            "monuments",
            "revenus",
            "scolaire",
            "transports",
            "vacance",
            "zonage_abc",
        )
    },
    "gares": "derive_de:transports",
    "permis": "hors_couverture",
    "rnb": "proxy:rnb_liens",
    **{key: "proxy" for key in ("bdnb", "errial", "georisques", "sirene", "urbanisme")},
}
PERIMETRE_STATE_RE = re.compile(
    r"^(importe:[a-z0-9_-]+|derive_de:[a-z0-9_-]+|proxy(:[a-z0-9_-]+)?|hors_couverture)$"
)
# run-ids.json is indexed by pipeline (directory imports/<pipeline>/), never
# by catalogue key. The only two catalogue keys served by a pipeline of
# another name:
ALIASES: dict[str, str] = {"rnb": "rnb_liens", "gares": "transports"}
LOCAL_PIPELINES: frozenset[str] = frozenset(
    {v.split(":", 1)[1] for v in PERIMETRE.values() if v.startswith("importe:")}
    | {"rnb_liens"}
)

# Compared dataset -> reference tables of level 1 (02-DATASETS.md:20-42).
DATASET_TABLES: dict[str, tuple[str, ...]] = {
    "antennes": ("antennes",),
    "ban": ("adresses",),
    "batiments": ("batiments",),
    "cadastre": ("parcelles",),
    "connectivite": ("eligibilite_fixe",),
    "copropriete": ("copropriete",),
    "demographie": ("demographie",),
    "dpe": ("diagnostics_dpe",),
    "dvf": ("mutations", "mutations_grain", "mutation_parcelles"),
    "equipements": ("equipements",),
    "fiscalite": ("fiscalite_locale",),
    "foncier_pm": ("foncier_pm",),
    "gares": ("gares",),
    "loyers": ("indicateurs_loyers",),
    "monuments": ("monuments_historiques",),
    "revenus": ("revenus",),
    "rnb_liens": ("rnb_liens",),
    "scolaire": ("carte_scolaire",),
    "transports": ("arrets_transport",),
    "vacance": ("vacance",),
    "zonage_abc": ("zonage_abc",),
}
# Referentials: compared at level 1 on a full run, reported apart (they are
# not keys of the perimeter). Table -> receipt directory.
REFERENTIAL_TABLES: dict[str, str] = {
    "geo_communes": "geo-boundaries",
    "geo_departements": "geo-boundaries",
    "geo_sections": "geo-boundaries",
    "v_commune_synthese": "refresh-commune-synthese",
}
REFERENTIAL_RECEIPTS: dict[str, str] = {
    "geo-boundaries": "geo-boundaries",
    "refresh-commune-synthese": "refresh-commune-synthese",
}
# Reference tables deliberately not compared, with the reason.
NOT_COMPARED: dict[str, str] = {
    "permis": (
        "permis hors couverture sur 44/85 (source locale au 93, KI-16, "
        "02-DATASETS.md:12)"
    ),
    "encadrement_loyers": "hors V1 (quarantaine, 02-DATASETS.md:12)",
    "scoring_renov_target": "absente de l'ancienne base (level1_counts.json)",
}
# Tables whose departement attribution by the old extractor is known false
# (02-05-SUMMARY m1/m2): `code_departement` on three digits for carte_scolaire,
# no spatial attribution for arrets_transport and gares.
METHODE_INVALIDE_TABLES: tuple[str, ...] = (
    "carte_scolaire",
    "arrets_transport",
    "gares",
)

# v_commune_synthese column -> dataset it comes from (app/commune/matview_ddl.py,
# CREATE_SQL): d = demographie_latest, r = revenus_latest (first term of the
# COALESCE for the departement and region names), z, v, f, l and the
# per-dataset intermediate matviews.
LEVEL2_COLUMNS: dict[str, str] = {
    **dict.fromkeys(
        (
            "code_insee",
            "nom_commune",
            "code_departement",
            "code_region",
            "population",
            "nb_menages",
            "nb_logements",
            "nb_logements_vacants_recensement",
            "densite",
            "superficie",
            "annee_demographie",
        ),
        "demographie",
    ),
    **dict.fromkeys(
        (
            "nom_departement",
            "nom_region",
            "revenu_median",
            "taux_pauvrete",
            "indice_gini",
            "part_imposee",
            "annee_revenus",
        ),
        "revenus",
    ),
    **dict.fromkeys(("zone_abc", "zone_abc_reclassement"), "zonage_abc"),
    **dict.fromkeys(
        ("taux_vacance", "nb_vacants_plus_2ans", "annee_vacance"), "vacance"
    ),
    **dict.fromkeys(
        ("taux_tfpb", "taux_th_rs", "taux_teom", "nom_epci", "annee_fiscalite"),
        "fiscalite",
    ),
    **dict.fromkeys(
        (
            "loyer_m2_appartement",
            "loyer_m2_maison",
            "annee_loyers",
            "loyers_appartement",
            "loyers_maison",
        ),
        "loyers",
    ),
    **dict.fromkeys(
        (
            "prix_m2_median",
            "nb_mutations_12m",
            "prix_m2_median_maison",
            "nb_mutations_12m_maison",
            "prix_m2_median_appartement",
            "nb_mutations_12m_appartement",
        ),
        "dvf",
    ),
    **dict.fromkeys(
        ("dpe_passoires_pct", "dpe_median", "annee_construction_mediane"), "dpe"
    ),
    **dict.fromkeys(("nb_equipements", "annee_equipements"), "equipements"),
    "nb_etablissements_scolaires": "scolaire",
    "nb_monuments": "monuments",
    **dict.fromkeys(("nb_proprietaires_pm", "surface_pm_ha"), "foncier_pm"),
    "fibre_pct": "connectivite",
}

# Pseudo-cases of verify_golden_cases.py: failures of the verifier itself,
# never business cases (02-13).
INFRASTRUCTURE_CASES: tuple[str, ...] = ("runner", "manifest")
MD_START = "<!-- compare_fidelity:synthese:start -->"
MD_END = "<!-- compare_fidelity:synthese:end -->"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class StructureError(Exception):
    """Input that cannot be compared: exit 2, no report."""


# --------------------------------------------------------------------- utils --


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StructureError(f"{what} absent: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StructureError(f"{what} illisible: {path}: {exc}") from exc


def dumps_report(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def worst(verdicts: Iterable[str]) -> str:
    found = set(verdicts)
    for verdict in VERDICT_ORDER:
        if verdict in found:
            return verdict
    return "conforme"


# ---------------------------------------------------------------- manifests --


def verify_manifest(directory: Path, what: str) -> dict[str, Any]:
    """Check name, size and SHA-256 of every file listed by manifest.json."""
    manifest = read_json(directory / "manifest.json", f"manifeste {what}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise StructureError(f"manifeste {what} sans fichiers")
    for entry in files:
        path = directory / str(entry.get("name"))
        if not path.is_file():
            raise StructureError(f"{what}: fichier du manifeste absent: {path.name}")
        if path.stat().st_size != entry.get("bytes") or file_sha256(path) != entry.get(
            "sha256"
        ):
            raise StructureError(
                f"{what}: empreinte differente du manifeste: {path.name}"
            )
    return manifest


# ---------------------------------------------------------------- perimetre --


def read_v1_keys(datasets_md: Path) -> set[str]:
    try:
        text = datasets_md.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StructureError(f"02-DATASETS.md absent: {datasets_md}") from exc
    block = re.search(r"<!-- v1-keys:start -->(.*?)<!-- v1-keys:end -->", text, re.S)
    if block is None:
        raise StructureError("bloc v1-keys absent de 02-DATASETS.md")
    return {
        k.strip() for k in block.group(1).replace("\n", " ").split(",") if k.strip()
    }


def validate_perimetre(perimetre: dict[str, str], keys: set[str]) -> None:
    missing = sorted(keys - set(perimetre))
    unknown = sorted(set(perimetre) - keys)
    invalid = sorted(k for k, v in perimetre.items() if not PERIMETRE_STATE_RE.match(v))
    if missing or unknown or invalid:
        raise StructureError(
            f"perimetre incomplet ou invalide: sans_etat={missing} "
            f"cles_inconnues={unknown} etat_invalide={invalid}"
        )
    for key, state in perimetre.items():
        if ":" in state and state.split(":", 1)[1] not in LOCAL_PIPELINES:
            raise StructureError(
                f"perimetre: {key} designe un pipeline inconnu: {state}"
            )


# ------------------------------------------------------------------ run-ids --


def git_has_commit(sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def resolve_receipt(imports_dir: Path, pipeline: str, run_id: str) -> dict[str, Any]:
    """Single pointer-resolution rule of plan 02-10; raise on any mismatch."""
    where = f"imports/{pipeline}"
    pointer_path = imports_dir / pipeline / "latest.json"
    if not pointer_path.is_file():
        raise StructureError(f"{where}: pointeur latest.json absent")
    pointer = read_json(pointer_path, f"{where}/latest.json")
    if str(pointer.get("run_id")) != run_id:
        raise StructureError(f"{where}: run_id {run_id} different du pointeur")
    if pointer.get("manifest") != f"{run_id}.json":
        raise StructureError(f"{where}: manifest du pointeur != {run_id}.json")
    receipt_path = imports_dir / pipeline / f"{run_id}.json"
    if not receipt_path.is_file():
        raise StructureError(f"{where}: recu {run_id}.json absent")
    receipt = read_json(receipt_path, f"{where}/{run_id}.json")
    if not isinstance(receipt, dict) or not all(
        str(receipt.get(k) or "").strip() for k in ("run_id", "dataset", "repo_sha")
    ):
        raise StructureError(f"{where}: recu vide ou sans run_id/dataset/repo_sha")
    if str(receipt["run_id"]) != run_id:
        raise StructureError(f"{where}: run_id interne different")
    if receipt["dataset"] != pipeline or pointer.get("dataset") != pipeline:
        raise StructureError(f"{where}: dataset different de {pipeline}")
    if receipt["repo_sha"] != pointer.get("repo_sha"):
        raise StructureError(f"{where}: repo_sha different du pointeur")
    sha = str(receipt["repo_sha"])
    if not _HEX40.match(sha) or not git_has_commit(sha):
        raise StructureError(f"{where}: repo_sha hors historique git")
    return receipt


def load_run_ids(
    path: Path | None, imports_dir: Path, datasets: Sequence[str]
) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Validate run-ids.json; map each dataset to (pipeline, run_id, receipt)."""
    if not datasets:
        return {}
    if path is None:
        raise StructureError("--run-ids requis")
    data = read_json(path, "run-ids.json")
    pipelines = data.get("pipelines") if isinstance(data, dict) else None
    aliases = data.get("alias") if isinstance(data, dict) else None
    if not isinstance(pipelines, dict) or not isinstance(aliases, dict):
        raise StructureError("run-ids.json: schema {pipelines, alias} attendu")
    unknown = sorted(set(pipelines) - LOCAL_PIPELINES)
    if unknown:
        raise StructureError(
            "run-ids.json: cles hors des pipelines locaux (cle du catalogue ?): "
            f"{unknown}"
        )
    bad_alias = sorted(
        k
        for k, v in aliases.items()
        if k in LOCAL_PIPELINES or ALIASES.get(k) != v or v not in pipelines
    )
    if bad_alias:
        raise StructureError(
            f"run-ids.json: alias invalide ou vers un pipeline absent: {bad_alias}"
        )
    receipts = {
        p: resolve_receipt(imports_dir, p, str(run_id))
        for p, run_id in pipelines.items()
    }
    resolved: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for dataset in datasets:
        pipeline = dataset if dataset in pipelines else aliases.get(dataset)
        if pipeline is None or pipeline not in pipelines:
            raise StructureError(f"run-ids.json: aucune entrée ni alias pour {dataset}")
        resolved[dataset] = (pipeline, str(pipelines[pipeline]), receipts[pipeline])
    return resolved


def receipt_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    """What the fidelity report cites from a receipt: status, counting, publication."""
    run = receipt.get("etl_run") or {}
    tables = receipt.get("tables") or {}
    return {
        "statut": run.get("status"),
        "erreur": run.get("error_message"),
        "counting": receipt.get("counting"),
        "publiees_par_departement": {
            t: (v or {}).get("published_rows_by_departement") for t, v in tables.items()
        },
        "repo_sha": receipt.get("repo_sha"),
    }


# ------------------------------------------------------------------ level 1 --


def _cell(entry: dict[str, Any] | None, cell: str) -> Any:
    """Serialized observation of one level-1 cell (what the fingerprint covers)."""
    if entry is None:
        return {"status": "absent"}
    counts = entry.get("counts")
    return {
        "status": entry.get("status"),
        "method": entry.get("method"),
        "column": entry.get("column"),
        "value": counts.get(cell) if isinstance(counts, dict) else None,
    }


def compare_level1(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    tables: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Gaps per table x cell; ``tables`` maps a table to its dataset."""
    ref_tables = reference.get("tables") or {}
    cand_tables = candidate.get("tables") or {}
    gaps: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    for table, dataset in tables.items():
        ref = ref_tables.get(table)
        cand = cand_tables.get(table)
        detail: dict[str, Any] = {
            "dataset": dataset,
            "reference": {c: _cell(ref, c)["value"] for c in CELLS},
            "candidate": {c: _cell(cand, c)["value"] for c in CELLS},
            "methode_reference": (ref or {}).get("method"),
            "methode_candidate": (cand or {}).get("method"),
            "statut_candidate": (cand or {}).get("status", "absent"),
        }
        details[table] = detail
        if ref is None or ref.get("status") != "ok":
            detail["sans_reference"] = True
            continue
        cand_missing = cand is None or cand.get("status") != "ok"
        for cell in CELLS:
            ref_obs = _cell(ref, cell)
            cand_obs = _cell(cand, cell)
            if not cand_missing and ref_obs["value"] == cand_obs["value"]:
                continue
            gap = {
                "cle": f"1:{table}:{cell}",
                "niveau": 1,
                "dataset": dataset,
                "table": table,
                "departement": cell,
                "reference": ref_obs["value"],
                "candidate": (
                    {"status": (cand or {}).get("status", "absent")}
                    if cand_missing
                    else cand_obs["value"]
                ),
                "bloquant": cand_missing,
                "reference_sha256": fingerprint(ref_obs),
                "candidate_sha256": fingerprint(cand_obs),
            }
            if isinstance(ref_obs["value"], int) and isinstance(cand_obs["value"], int):
                gap["delta"] = cand_obs["value"] - ref_obs["value"]
            gap["observation"] = (
                f"table {table} absente ou illisible du candidat"
                if cand_missing
                else f"{cell}: {ref_obs['value']} -> {cand_obs['value']}"
                + (f" ({gap['delta']:+d})" if "delta" in gap else "")
            )
            gaps.append(gap)
    return gaps, details


# ------------------------------------------------------------------ level 2 --


def _describe_change(ref: Any, cand: Any) -> str:
    if isinstance(ref, dict) and isinstance(cand, dict):
        keys = sorted(k for k in set(ref) | set(cand) if ref.get(k) != cand.get(k))
        return f"cles differentes: {keys}"
    return f"{canonical(ref)} -> {canonical(cand)}"


def compare_level2(
    reference: dict[str, Any], candidate: dict[str, Any], datasets: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ref_cols = list(reference.get("columns") or [])
    unmapped = sorted(c for c in ref_cols if c not in LEVEL2_COLUMNS)
    if unmapped:
        raise StructureError(f"niveau 2: colonnes sans dataset: {unmapped}")
    if list(reference.get("panel") or []) != list(candidate.get("panel") or []):
        raise StructureError("niveau 2: panel du candidat different de la reference")
    cand_ok = candidate.get("status") == "ok"
    cand_cols = list(candidate.get("columns") or []) if cand_ok else []
    cand_rows = (candidate.get("rows") or {}) if cand_ok else {}
    ref_rows = reference.get("rows") or {}
    gaps: list[dict[str, Any]] = []
    for code in reference.get("panel") or []:
        ref_row = ref_rows.get(code)
        if ref_row is None:
            continue  # absent from the old view: nothing to compare against
        cand_row = cand_rows.get(code)
        for column in ref_cols:
            dataset = LEVEL2_COLUMNS[column]
            if dataset not in datasets:
                continue
            ref_value = ref_row.get(column)
            if cand_row is None:
                cand_obs: Any = {"absent": "commune"}
            elif column not in cand_cols:
                cand_obs = {"absent": "colonne"}
            else:
                cand_obs = cand_row.get(column)
                if canonical(ref_value) == canonical(cand_obs):
                    continue
            blocking = cand_row is None or column not in cand_cols
            gaps.append(
                {
                    "cle": f"2:{code}:{column}",
                    "niveau": 2,
                    "dataset": dataset,
                    "code_insee": code,
                    "colonne": column,
                    "reference": ref_value,
                    "candidate": cand_obs,
                    "bloquant": blocking,
                    "reference_sha256": fingerprint(ref_value),
                    "candidate_sha256": fingerprint(cand_obs),
                    "observation": (
                        f"commune {code} absente du candidat"
                        if cand_row is None
                        else f"colonne {column} absente du candidat"
                        if blocking
                        else _describe_change(ref_value, cand_obs)
                    ),
                }
            )
    new_columns = sorted(set(cand_cols) - set(ref_cols))
    return gaps, {
        "colonnes_nouvelles": new_columns,
        "statut_candidate": candidate.get("status"),
    }


# --------------------------------------------------------------- LOVAC KI-D1 --


def compare_false_zeros(
    reference: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    """KI-D1 recount per departement (``lovac_false_zeros.json``).

    ``v_commune_synthese`` already masks a zero rate without denominator
    (``taux_vacance`` null on both sides) and level 1 counts rows, so neither
    level sees a repaired false zero: this recount is compared on its own,
    under the pseudo-table ``lovac_faux_zeros`` of dataset ``vacance``.
    """
    gaps = []
    cand_ok = candidate.get("status") == "ok"
    for dept in DEPARTEMENTS:
        ref_obs = {
            "rule": reference.get("rule"),
            "status": reference.get("status"),
            "value": (reference.get("counts") or {}).get(dept),
        }
        cand_obs = {
            "rule": candidate.get("rule"),
            "status": candidate.get("status"),
            "value": (candidate.get("counts") or {}).get(dept),
        }
        if cand_ok and ref_obs == cand_obs:
            continue
        gap = {
            "cle": f"1:lovac_faux_zeros:{dept}",
            "niveau": 1,
            "dataset": "vacance",
            "table": "lovac_faux_zeros",
            "departement": dept,
            "reference": ref_obs["value"],
            "candidate": cand_obs["value"]
            if cand_ok
            else {"status": cand_obs["status"]},
            "bloquant": not cand_ok,
            "reference_sha256": fingerprint(ref_obs),
            "candidate_sha256": fingerprint(cand_obs),
        }
        if isinstance(ref_obs["value"], int) and isinstance(cand_obs["value"], int):
            gap["delta"] = cand_obs["value"] - ref_obs["value"]
        gap["observation"] = (
            f"faux zeros KI-D1 {dept}: {ref_obs['value']} -> {cand_obs['value']}"
            if cand_ok
            else "recompte KI-D1 absent ou illisible du candidat"
        )
        gaps.append(gap)
    return gaps


# ---------------------------------------------------------- classification --


def recount_ok(recount: dict[str, Any], table: str) -> bool:
    entry = recount.get(table) if isinstance(recount, dict) else None
    if not isinstance(entry, dict) or not str(entry.get("methode") or "").strip():
        return False
    counts = entry.get("par_departement")
    return (
        isinstance(counts, dict)
        and set(counts) == set(DEPARTEMENTS)
        and all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 0
            for v in counts.values()
        )
    )


def justification_ok(entry: dict[str, Any]) -> bool:
    proof = entry.get("justificatif")
    if not isinstance(proof, dict):
        return False
    if set(proof) == {"chemin"}:
        rel = Path(str(proof["chemin"]))
        if rel.is_absolute() or ".." in rel.parts:
            return False
        return (REPO_ROOT / rel).is_file()
    if set(proof) == {"url", "sha256"}:
        return bool(str(proof["url"]).strip()) and bool(
            _HEX64.match(str(proof["sha256"]))
        )
    return False


def refusal(entry: Any, gap: dict[str, Any], recount: dict[str, Any]) -> str | None:  # noqa: C901
    """Why a classification entry cannot classify ``gap`` (None when valid)."""
    if not isinstance(entry, dict):
        return "refuse: entree mal formee"
    category = entry.get("categorie")
    if category not in CATEGORIES or category == "inexpliquee":
        return f"refuse: categorie {category!r} hors liste"
    if not str(entry.get("preuve") or "").strip():
        return "refuse: sans preuve"
    if (entry.get("reference_sha256"), entry.get("candidate_sha256")) != (
        gap["reference_sha256"],
        gap["candidate_sha256"],
    ):
        return "preuve_perimee"
    if category == "source_differente":
        vintages = (entry.get("millesime_reference"), entry.get("millesime_candidat"))
        if not all(
            str(v or "").strip() and str(v).strip() != "inconnu" for v in vintages
        ):
            return "refuse: millesime non nomme des deux cotes"
        if not justification_ok(entry):
            return "refuse: justificatif non resoluble"
    if category == "methode_de_reference_invalide":
        table = gap.get("table")
        if gap["niveau"] != 1 or table not in METHODE_INVALIDE_TABLES:
            return "refuse: methode_de_reference_invalide hors des trois tables"
        if not recount_ok(recount, table):
            return "refuse: recomptage complementaire absent ou invalide"
    return None


def apply_classifications(
    gaps: list[dict[str, Any]], classifications: dict[str, Any], recount: dict[str, Any]
) -> None:
    """Give every gap its state; a refused or stale entry leaves the default."""
    for gap in gaps:
        default = "regression" if gap["bloquant"] else "inexpliquee"
        entry = classifications.get(gap["cle"])
        if entry is None:
            gap.update(categorie=default, preuve=None, classement="absent")
            continue
        reason = refusal(entry, gap, recount)
        if reason is None:
            gap.update(
                categorie=entry["categorie"],
                preuve=entry["preuve"],
                classement="applique",
            )
            for key in ("millesime_reference", "millesime_candidat", "justificatif"):
                if key in entry:
                    gap[key] = entry[key]
        else:
            gap.update(categorie=default, preuve=None, classement=reason)


def arbitration_list(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gaps handed to the checkpoint of plan 02-13: inexpliquee and regression."""
    items = []
    for gap in gaps:
        if gap["categorie"] not in ("inexpliquee", "regression"):
            continue
        why = gap["observation"]
        if gap["categorie"] == "regression" and gap.get("preuve"):
            why = f"{why} ; regression : {gap['preuve']}"
        elif gap["classement"] not in ("absent", "applique"):
            why = f"{why} ; classement {gap['classement']}"
        items.append({"cle": gap["cle"], "dataset": gap["dataset"], "motif": why})
    return items


# ------------------------------------------------------------------- golden --


def load_golden_map(path: Path) -> dict[str, Any]:
    """``{"cases": {case_id: [dataset, ...]}, "infrastructure_cases": [...]}``."""
    data = read_json(path, "golden-map.json")
    cases = data.get("cases") if isinstance(data, dict) else None
    infra = data.get("infrastructure_cases") if isinstance(data, dict) else None
    if (
        not isinstance(cases, dict)
        or not cases
        or not all(
            isinstance(v, list) and v and all(isinstance(d, str) for d in v)
            for v in cases.values()
        )
        or sorted(infra or []) != sorted(INFRASTRUCTURE_CASES)
    ):
        raise StructureError(
            "golden-map.json: schema {cases, infrastructure_cases} attendu"
        )
    return data


def consolidate_golden(  # noqa: C901
    report: dict[str, Any],
    raw: bytes,
    source: str,
    golden_map: dict[str, Any],
    classifications: dict[str, Any],
) -> tuple[
    dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]
]:
    """Level 3: attribute every failed golden case to the datasets it exercises.

    Returns ``(golden block, level-3 gaps, infrastructure errors, coverage
    errors)``. Coverage is checked both ways: a reported case unknown to the
    map, and a mapped case absent from the report, are coverage errors. A
    failed case yields one gap per dataset of the map
    (``3:<case_id>:<dataset>``), blocking: ``regression`` unless a valid
    classification (evidence + the two fingerprints) explains it. The
    reference observation is the expected success, the candidate one the
    observed failure with its reasons.
    """
    cases_in = report.get("cases") if isinstance(report, dict) else None
    if not isinstance(cases_in, list):
        raise StructureError("rapport golden: liste cases absente")
    mapping = golden_map["cases"]
    infra_ids = set(golden_map["infrastructure_cases"])
    gaps: list[dict[str, Any]] = []
    infra: list[dict[str, Any]] = []
    coverage: list[dict[str, str]] = []
    unknown: set[str] = set()
    cases: dict[str, Any] = {}
    for case in cases_in:
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str):
            raise StructureError("rapport golden: case_id absent")
        reasons = [str(r) for r in (case.get("reasons") or [])]
        if case_id in infra_ids:
            infra.append({"case_id": case_id, "reasons": reasons})
            continue
        if case_id in cases or case_id in unknown:
            raise StructureError(f"rapport golden: case_id en double: {case_id}")
        if not isinstance(case.get("ok"), bool):
            raise StructureError(f"rapport golden: ok non booleen pour {case_id}")
        if case_id not in mapping:
            unknown.add(case_id)
            coverage.append(
                {"case_id": case_id, "motif": "inconnu_de_la_correspondance"}
            )
            continue
        datasets = list(mapping[case_id])
        case_gaps: list[dict[str, Any]] = []
        if not case["ok"]:
            expected = {"case_id": case_id, "ok": True}
            observed = {"case_id": case_id, "ok": False, "reasons": reasons}
            case_gaps = [
                {
                    "cle": f"3:{case_id}:{dataset}",
                    "niveau": 3,
                    "dataset": dataset,
                    "case_id": case_id,
                    "reference": expected,
                    "candidate": observed,
                    "bloquant": True,
                    "reference_sha256": fingerprint(expected),
                    "candidate_sha256": fingerprint(observed),
                    "observation": f"cas golden {case_id} en echec: {reasons}",
                }
                for dataset in datasets
            ]
            apply_classifications(case_gaps, classifications, {})
            gaps.extend(case_gaps)
        classified = {
            g["dataset"]: g["categorie"]
            for g in case_gaps
            if g["classement"] == "applique"
        }
        cases[case_id] = {
            "ok": case["ok"],
            "datasets": datasets,
            # Set only when every dataset of a failed case is validly classified.
            "classement": (
                classified if case_gaps and len(classified) == len(case_gaps) else None
            ),
            "reasons": reasons,
        }
    coverage.extend(
        {"case_id": case_id, "motif": "absent_du_rapport"}
        for case_id in sorted(set(mapping) - set(cases))
    )
    par_dataset: dict[str, Any] = {}
    for case_id, entry in sorted(cases.items()):
        for dataset in entry["datasets"]:
            slot = par_dataset.setdefault(
                dataset, {"cas": [], "echecs": [], "verdict": "conforme"}
            )
            slot["cas"].append(case_id)
            if not entry["ok"]:
                slot["echecs"].append(case_id)
    for dataset, slot in par_dataset.items():
        slot["verdict"] = level_verdict([g for g in gaps if g["dataset"] == dataset])
    block = {
        "source": source,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "served_checkout_sha": report.get("served_checkout_sha"),
        "cases": cases,
        "par_dataset": par_dataset,
    }
    return block, gaps, infra, coverage


# ------------------------------------------------------------------- report --


def level_verdict(gaps: list[dict[str, Any]]) -> str:
    return worst(CATEGORY_VERDICT[g["categorie"]] for g in gaps)


def build_markdown_block(report: dict[str, Any]) -> str:
    golden = report["golden"]
    par = golden.get("par_dataset") or {}
    lines = [
        MD_START,
        "",
        "Tableau produit par `compare_fidelity.py` (ne pas éditer à la main).",
        "",
        "| Dataset | `run_id` | Niveau 1 | Niveau 2 | Golden | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for name, entry in sorted(report["datasets"].items()):
        gold = (
            (par.get(name) or {}).get("verdict", "aucun cas")
            if golden.get("source")
            else "non rejoué"
        )
        lines.append(
            f"| {name} | `{entry['run_id']}` | {entry['niveaux']['1']} | "
            f"{entry['niveaux']['2']} | {gold} | **{entry['verdict']}** |"
        )
    for name, entry in sorted(report["referentiels"].items()):
        lines.append(
            f"| {name} (référentiel) | `{entry['run_id']}` | "
            f"{entry['niveaux']['1']} | — | — | "
            f"**{entry['verdict']}** |"
        )
    lines += [
        "",
        f"Verdict niveaux 1-2 : **{report['verdict_niveaux_1_2']}** ; "
        f"verdict global : **{report['verdict_global']}** ; "
        f"écarts : {len(report['ecarts'])} ; "
        f"à arbitrer (02-13) : {len(report['a_arbitrer'])} ; "
        f"erreurs d'infrastructure golden : {len(report['erreurs_infrastructure'])}.",
        "",
        MD_END,
    ]
    return "\n".join(lines)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StructureError(f"--markdown: fichier absent: {path}") from exc
    start, end = text.find(MD_START), text.find(MD_END)
    if start < 0 or end < start:
        raise StructureError(f"--markdown: marqueurs {MD_START} / {MD_END} absents")
    new = text[:start] + build_markdown_block(report) + text[end + len(MD_END) :]
    path.write_text(new, encoding="utf-8", newline="\n")


def compare(args: argparse.Namespace) -> tuple[dict[str, Any], int]:  # noqa: C901
    keys = read_v1_keys(args.datasets_md)
    validate_perimetre(PERIMETRE, keys)

    datasets = sorted(set(args.dataset or DATASET_TABLES))
    unknown = sorted(set(datasets) - set(DATASET_TABLES))
    if unknown:
        raise StructureError(f"--dataset inconnu: {unknown}")
    full_run = not args.dataset
    run_ids = load_run_ids(args.run_ids, args.imports_dir, datasets)

    ref_manifest = verify_manifest(args.reference_dir, "reference")
    cand_manifest = verify_manifest(args.candidate_dir, "candidat")
    ref_l1 = read_json(args.reference_dir / "level1_counts.json", "reference niveau 1")
    cand_l1 = read_json(args.candidate_dir / "level1_counts.json", "candidat niveau 1")
    ref_l2 = read_json(args.reference_dir / "level2_panel.json", "reference niveau 2")
    cand_l2 = read_json(args.candidate_dir / "level2_panel.json", "candidat niveau 2")
    ref_fz = read_json(args.reference_dir / "lovac_false_zeros.json", "reference LOVAC")
    cand_fz = read_json(args.candidate_dir / "lovac_false_zeros.json", "candidat LOVAC")
    recount_path = args.candidate_dir / "departemental_recount.json"
    recount = read_json(recount_path, "recomptage") if recount_path.is_file() else {}
    classifications = (
        read_json(args.classifications, "classifications.json")
        if args.classifications
        else {}
    )
    if not isinstance(classifications, dict):
        raise StructureError("classifications.json: objet attendu")

    referentials: dict[str, dict[str, Any]] = {}
    if full_run:
        for name, pipeline in REFERENTIAL_RECEIPTS.items():
            pointer = read_json(
                args.imports_dir / pipeline / "latest.json", f"imports/{pipeline}"
            )
            run_id = str(pointer.get("run_id"))
            referentials[name] = {
                "run_id": run_id,
                "recu": resolve_receipt(args.imports_dir, pipeline, run_id),
            }

    tables = {t: d for d in datasets for t in DATASET_TABLES[d]}
    if full_run:
        tables.update(REFERENTIAL_TABLES)
    gaps1, details1 = compare_level1(ref_l1, cand_l1, tables)
    gaps2, details2 = compare_level2(ref_l2, cand_l2, set(datasets))
    gaps = gaps1 + gaps2
    if "vacance" in datasets:
        gaps += compare_false_zeros(ref_fz, cand_fz)
    apply_classifications(gaps, classifications, recount)

    golden: dict[str, Any] = {
        "source": None,
        "sha256": None,
        "served_checkout_sha": None,
        "cases": {},
        "par_dataset": {},
    }
    infra_errors: list[dict[str, Any]] = []
    coverage_errors: list[dict[str, str]] = []
    gaps3: list[dict[str, Any]] = []
    if args.golden_report or args.golden_map:
        if not (args.golden_report and args.golden_map):
            raise StructureError("--golden-report et --golden-map vont ensemble")
        golden_map = load_golden_map(args.golden_map)
        try:
            golden_raw = args.golden_report.read_bytes()
        except FileNotFoundError as exc:
            raise StructureError(
                f"rapport golden absent: {args.golden_report}"
            ) from exc
        golden, gaps3, infra_errors, coverage_errors = consolidate_golden(
            read_json(args.golden_report, "rapport golden"),
            golden_raw,
            args.golden_report.as_posix(),
            golden_map,
            classifications,
        )
    gaps += gaps3
    gaps.sort(key=lambda g: (g["niveau"], g["cle"]))

    impossible: list[str] = []
    report_datasets: dict[str, Any] = {}
    for dataset in datasets:
        pipeline, run_id, receipt = run_ids[dataset]
        own = [g for g in gaps if g["dataset"] == dataset]
        levels = {
            "1": level_verdict([g for g in own if g["niveau"] == 1]),
            # No column of v_commune_synthese comes from this dataset: level 2
            # compared nothing (ignored by worst(), never read as conforme).
            "2": (
                level_verdict([g for g in own if g["niveau"] == 2])
                if dataset in LEVEL2_COLUMNS.values()
                else "sans_objet"
            ),
        }
        for table in DATASET_TABLES[dataset]:
            if table in METHODE_INVALIDE_TABLES:
                # The old extractor's attribution is known false: equal counts
                # obtained with the same instrument prove nothing.
                if levels["1"] in ("conforme", "ecarts_expliques"):
                    levels["1"] = "comparaison_impossible"
                if levels["1"] == "comparaison_impossible":
                    impossible.append(table)
        # Level 3: a failed golden case brings the dataset to regression at
        # least, unless every gap it opened is validly classified.
        golden_verdict = (golden["par_dataset"].get(dataset) or {}).get("verdict")
        entry = {
            "pipeline": pipeline,
            "run_id": run_id,
            "perimetre": PERIMETRE.get(dataset)
            or "pipeline annexe de rnb (proxy:rnb_liens)",
            "tables": list(DATASET_TABLES[dataset]),
            "niveaux": levels,
            "golden": golden_verdict,
            "verdict": worst(
                [*levels.values(), *([golden_verdict] if golden_verdict else [])]
            ),
            "ecarts": len(own),
            "recu": receipt_summary(receipt),
        }
        if dataset == "vacance":
            entry["faux_zeros"] = {
                "regle": ref_fz.get("rule"),
                "reference": {
                    d: (ref_fz.get("counts") or {}).get(d) for d in DEPARTEMENTS
                },
                "candidate": {
                    d: (cand_fz.get("counts") or {}).get(d) for d in DEPARTEMENTS
                },
            }
        report_datasets[dataset] = entry

    report_refs: dict[str, Any] = {}
    for name, info in referentials.items():
        own = [g for g in gaps if g["dataset"] == name]
        verdict = level_verdict(own)
        report_refs[name] = {
            "run_id": info["run_id"],
            "tables": sorted(t for t, r in REFERENTIAL_TABLES.items() if r == name),
            "niveaux": {"1": verdict},
            "verdict": verdict,
            "recu": receipt_summary(info["recu"]),
        }

    orphans = (
        sorted(set(classifications) - {g["cle"] for g in gaps}) if full_run else []
    )
    all_verdicts = [e["verdict"] for e in report_datasets.values()] + [
        e["verdict"] for e in report_refs.values()
    ]
    level12 = worst(
        [e["niveaux"]["1"] for e in report_datasets.values()]
        + [e["niveaux"]["2"] for e in report_datasets.values()]
        + [e["verdict"] for e in report_refs.values()]
    )
    global_labels = {
        "comparaison_impossible": "conforme_avec_comparaisons_impossibles",
        "ecarts_expliques": "conforme",
    }
    level12_label = global_labels.get(level12, level12)
    # Datasets exercised by a golden case but not compared at levels 1-2
    # (upstream proxies, --dataset restriction) still weigh on the verdict.
    golden_only = [
        v["verdict"]
        for d, v in golden["par_dataset"].items()
        if d not in report_datasets
    ]
    if not golden["source"]:
        verdict_global = "golden_non_rejoue"
    elif infra_errors:
        verdict_global = "golden_bloque_infrastructure"
    else:
        overall = worst(all_verdicts + golden_only)
        verdict_global = global_labels.get(overall, overall)
        # A golden coverage error exits 1: never let it read conforme*; a
        # regression or an unexplained gap stays the more informative verdict.
        if coverage_errors and verdict_global.startswith("conforme"):
            verdict_global = "golden_couverture_incomplete"
    report = {
        "sources": {
            "reference_dir": args.reference_dir.as_posix(),
            "reference_label": (ref_manifest.get("source") or {}).get("label"),
            "reference_manifest_sha256": file_sha256(
                args.reference_dir / "manifest.json"
            ),
            "candidate_dir": args.candidate_dir.as_posix(),
            "candidate_label": (cand_manifest.get("source") or {}).get("label"),
            "candidate_alembic": (cand_manifest.get("source") or {}).get(
                "alembic_version"
            ),
            "candidate_manifest_sha256": file_sha256(
                args.candidate_dir / "manifest.json"
            ),
            "extracteur_sha256": cand_manifest.get("script_sha256"),
            "recomptage_sha256": file_sha256(recount_path)
            if recount_path.is_file()
            else None,
            "classifications_sha256": (
                file_sha256(args.classifications) if args.classifications else None
            ),
            "run_ids_sha256": file_sha256(args.run_ids) if args.run_ids else None,
            "comparateur_sha256": file_sha256(Path(__file__)),
            "datasets_compares": datasets,
        },
        "perimetre": dict(sorted(PERIMETRE.items())),
        "non_compares": NOT_COMPARED,
        "datasets": report_datasets,
        "referentiels": report_refs,
        "niveau_1": details1,
        "niveau_2": details2,
        "recomptage": recount,
        "ecarts": gaps,
        "a_arbitrer": arbitration_list(gaps),
        "classements_orphelins": orphans,
        "comparaisons_impossibles": sorted(set(impossible)),
        "golden": golden,
        "erreurs_couverture_golden": coverage_errors,
        "erreurs_infrastructure": infra_errors,
        "verdict_niveaux_1_2": level12_label,
        "verdict_global": verdict_global,
    }
    failing = any(v in ("regression", "inexpliquee") for v in all_verdicts)
    # A failed golden case without a valid classification forbids exit 0.
    open_golden = any(g["categorie"] in ("regression", "inexpliquee") for g in gaps3)
    code = 1 if failing or open_golden or coverage_errors or infra_errors else 0
    return report, code


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--classifications", type=Path, default=None)
    parser.add_argument("--run-ids", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument(
        "--dataset",
        action="append",
        default=None,
        help="restrict the comparison to one dataset (repeatable)",
    )
    parser.add_argument("--golden-report", type=Path, default=None)
    parser.add_argument("--golden-map", type=Path, default=None)
    parser.add_argument("--imports-dir", type=Path, default=PHASE_DIR / "imports")
    parser.add_argument(
        "--datasets-md", type=Path, default=PHASE_DIR / "02-DATASETS.md"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report, code = compare(args)
        if args.markdown is not None:
            write_markdown(args.markdown, report)
    except StructureError as exc:
        print(f"FIDELITY_STRUCTURE_ERROR {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps_report(report), encoding="utf-8", newline="\n")
    counts: dict[str, int] = {}
    for gap in report["ecarts"]:
        counts[gap["categorie"]] = counts.get(gap["categorie"], 0) + 1
    print(
        f"FIDELITY verdict_global={report['verdict_global']} "
        f"niveaux_1_2={report['verdict_niveaux_1_2']} "
        f"datasets={len(report['datasets'])} "
        f"ecarts={len(report['ecarts'])} {dict(sorted(counts.items()))} "
        f"a_arbitrer={len(report['a_arbitrer'])} exit={code}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())

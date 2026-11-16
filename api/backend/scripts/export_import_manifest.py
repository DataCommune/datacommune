#!/usr/bin/env python3
"""Immutable import manifest of one ETL run (plan 02-10, PERF-00, D-33, DATA-01).

One receipt per run, ``imports/<dataset>/<run_id>.json``, never rewritten,
plus a pointer ``imports/<dataset>/latest.json`` naming it. The receipt
carries what the base and the launcher measured, nothing reconstructed:

- ``etl_run``: the ``etl_runs`` row (status, durations, ``stages`` with
  ``rows_affected``, ``scope_snapshot``, ``source_files``);
- ``dataset_metadata``: its row;
- ``tables``: for each table of ``DATASET_TABLES[dataset]``, published rows
  per departement, table/index/TOAST sizes, sizes per partition when the
  table is partitioned, row bytes per departement otherwise
  (``sum(pg_column_size(t.*))``: row data, indexes excluded);
- ``disk_peak``: the maximum of the samples taken by the launcher (volume and
  interval named) — a sampled maximum, never the real peak;
- ``scenario``: ``amorcage`` (declared), ``initial`` or ``rejeu``, from the
  database only (successful runs of the dataset before this one);
  ``sources_locales`` reports the download cache apart (the cache is purged
  after every publication: the sources live in the source archive);
- ``source_archive`` (plan 02-14): where the sources of the run come from,
  read in the archive record ``<dataset>/runs/<run_id>.json`` under
  ``--source-archive-dir`` (default ``SOURCE_ARCHIVE_DIR``): record path and
  SHA-256, ``origin`` (``telechargement`` or ``archive``), ``replayed_from``,
  files; ``non_configuree`` without an archive directory, ``absente`` without
  a record. A record whose (name, bytes, SHA-256) differ from
  ``etl_run.source_files`` is refused (exit 2). A DPE record that archived its
  generation ``manifest.json`` (plan 04-15) also holds the evidence that manifest
  names: it must hold exactly the manifest (digest read back) and those files, with
  their declared digests, and its processing units must equal the journal (C23);
- ``counting``: unit per table, published reference table, observed counters
  (``stages[].rows_affected``, scope-filter exclusions read in the run log);
  anything the instrument does not provide is ``"non mesuré"``.

Run id: ``<etl_runs.id>-<started_at UTC, YYYYMMDDTHHMMSSZ>``.

Exit codes: 0 written; 3 the receipt already exists (``reçu existant,
écrasement refusé``, file untouched); 2 any other refusal (unknown dataset or
run id, divergent names, repo_sha outside the git history, contradictory
scenario, unreachable base, invalid option).

``--script`` (plan 02-11): receipt of an import script of ``SCRIPT_TABLES``
(no ``etl_runs`` row; run id = UTC stamp ``YYYYMMDDTHHMMSSZ``): status and
duration from the launcher, source files from the ``SOURCE_FILE`` lines of
its log, ``source_archive`` checked against them by the same function, or
``sans_source_externe`` for a script that downloads nothing.

``--pointer-only`` rewrites a lost ``latest.json`` from the existing receipt
``--out`` (only read; its own ``repo_sha`` is checked against the history).

stdlib + psycopg 3; sizes from ``measure_db_sizes``, departement attribution
from ``extract_fidelity_reference``. The DSN is read from ``DATABASE_URL``
only and never printed. Usage (from ``api/backend``)::

    python -m scripts.export_import_manifest --dataset vacance \
        --run-id 2-20260923T001512Z --repo-sha <sha> \
        --out /imports/vacance/2-20260923T001512Z.json \
        --pointer /imports/vacance/latest.json [--scenario rejeu] ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_RECEIPT_EXISTS = 3
RECEIPT_EXISTS_MESSAGE = "reçu existant, écrasement refusé"
POINTER_MESSAGE = "pointeur autoréférent ou nom divergent"
REPO_SHA_MESSAGE = "repo_sha hors historique git"

NOT_MEASURED = "non mesuré"
UNIT_CSV_LINE = "ligne physique CSV"
UNIT_PUBLISHED = "enregistrement publié"
ROW_DATA_NATURE = "données de ligne, hors index"
DISK_PEAK_NATURE = "maximum échantillonné, pas le pic réel"
SCENARIOS = ("amorcage", "initial", "rejeu")
SOURCES_LOCALES = ("presentes", "absentes")

_RUN_ID_RE = re.compile(r"^([1-9][0-9]*)-([0-9]{8}T[0-9]{6}Z)$")
_SCRIPT_RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
_SOURCE_FILE_RE = re.compile(
    r"^SOURCE_FILE (?P<name>[A-Za-z0-9._-]+) (?P<bytes>[0-9]+) "
    r"(?P<sha256>[0-9a-f]{64})$",
    re.M,
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SCOPE_FILTER_RE = re.compile(
    r"Scope filter for (?P<dataset>\S+), (?P<table>\S+): (?P<removed>\d+) rows "
    r"removed, of which (?P<unattributed>\d+) without departement"
)
REPO_ROOT = Path(__file__).resolve().parents[3]

# Tables of each import. `published` = the table whose rows are the published
# rows of the dataset. Unit of the source rows of each table:
# UNIT_CSV_LINE when the pipeline downloads CSV (or CSV.gz) files, whose
# physical lines etl_runs.source_files[].physical_lines counts (header
# excluded; key "rows" before plan 04-15);
# UNIT_PUBLISHED otherwise (archives, JSON, GeoJSON, GeoPackage, derived
# tables): only published records are counted. Declared from the download
# code of each pipeline; confirmed per run by source_files in the manifest.
DATASET_TABLES: dict[str, dict[str, Any]] = {
    "monuments": {
        "published": "monuments_historiques",
        "tables": {"monuments_historiques": UNIT_CSV_LINE},
    },
    "loyers": {
        "published": "indicateurs_loyers",
        "tables": {"indicateurs_loyers": UNIT_CSV_LINE},
    },
    "transports": {
        "published": "arrets_transport",
        "tables": {"arrets_transport": UNIT_PUBLISHED, "gares": UNIT_PUBLISHED},
    },
    "scolaire": {
        "published": "carte_scolaire",
        "tables": {"carte_scolaire": UNIT_CSV_LINE},
    },
    "zonage_abc": {"published": "zonage_abc", "tables": {"zonage_abc": UNIT_CSV_LINE}},
    "fiscalite": {
        "published": "fiscalite_locale",
        "tables": {"fiscalite_locale": UNIT_CSV_LINE},
    },
    "revenus": {"published": "revenus", "tables": {"revenus": UNIT_PUBLISHED}},
    "demographie": {
        "published": "demographie",
        "tables": {"demographie": UNIT_PUBLISHED},
    },
    "vacance": {"published": "vacance", "tables": {"vacance": UNIT_CSV_LINE}},
    "copropriete": {
        "published": "copropriete",
        "tables": {"copropriete": UNIT_CSV_LINE},
    },
    "permis": {"published": "permis", "tables": {"permis": UNIT_CSV_LINE}},
    "antennes": {"published": "antennes", "tables": {"antennes": UNIT_CSV_LINE}},
    "equipements": {
        "published": "equipements",
        "tables": {"equipements": UNIT_PUBLISHED},
    },
    "rnb_liens": {"published": "rnb_liens", "tables": {"rnb_liens": UNIT_PUBLISHED}},
    "bdnb_batiment_parcelle": {
        "published": "bdnb_batiment_parcelle",
        "tables": {"bdnb_batiment_parcelle": UNIT_PUBLISHED},
    },
    "foncier_pm": {
        "published": "foncier_pm",
        "tables": {"foncier_pm": UNIT_PUBLISHED},
    },
    "dvf": {
        "published": "mutations",
        "tables": {
            "mutations": UNIT_CSV_LINE,
            "mutations_grain": UNIT_PUBLISHED,
            "mutation_parcelles": UNIT_PUBLISHED,
        },
    },
    "dpe": {
        "published": "diagnostics_dpe",
        "tables": {"diagnostics_dpe": UNIT_PUBLISHED},
    },
    "ban": {"published": "adresses", "tables": {"adresses": UNIT_CSV_LINE}},
    "cadastre": {"published": "parcelles", "tables": {"parcelles": UNIT_PUBLISHED}},
    "batiments": {"published": "batiments", "tables": {"batiments": UNIT_PUBLISHED}},
    "connectivite": {
        "published": "eligibilite_fixe",
        "tables": {"eligibilite_fixe": UNIT_CSV_LINE},
    },
}

# Import scripts measured like a pipeline (`workbase-import.sh --script`, plan
# 02-11): no etl_runs row, run id = UTC stamp of the launch. `archive_dataset`
# names the dataset of their source-archive record; None for a script that
# downloads nothing (``source_archive: "sans_source_externe"``).
SCRIPT_TABLES: dict[str, dict[str, Any]] = {
    # Geographic referentials (scripts/import_geo_boundaries.py).
    "geo-boundaries": {
        "published": "geo_communes",
        "tables": {
            "geo_communes": UNIT_PUBLISHED,
            "geo_departements": UNIT_PUBLISHED,
            "geo_sections": UNIT_PUBLISHED,
            "geo_arrondissements": UNIT_PUBLISHED,
        },
        "archive_dataset": "geo_boundaries",
    },
    # Former host timers (app/scheduler/run_job.py): derived views only.
    "refresh-commune-synthese": {
        "published": "v_commune_synthese",
        "tables": {"v_commune_synthese": UNIT_PUBLISHED},
        "archive_dataset": None,
    },
    "refresh-communes-lookup": {
        "published": "communes_lookup",
        "tables": {"communes_lookup": UNIT_PUBLISHED},
        "archive_dataset": None,
    },
}
WITHOUT_EXTERNAL_SOURCE = "sans_source_externe"
SCRIPT_STATUSES = ("success", "failed")

ETL_RUN_COLUMNS = (
    "id",
    "dataset_name",
    "status",
    "started_at",
    "completed_at",
    "total_rows_loaded",
    "total_duration_seconds",
    "error_message",
    "stages",
    "scope_snapshot",
    "source_files",
)


class ExportError(RuntimeError):
    """A refusal with its own message (exit 2)."""


class ReceiptExistsError(ExportError):
    """The receipt already exists (exit 3)."""


# --------------------------------------------------------------------- pure --


def dumps_sorted(payload: Any) -> str:
    """Deterministic JSON: sorted keys, stable separators, trailing newline."""
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def parse_run_id(run_id: str) -> tuple[int, str] | None:
    """``(etl_runs.id, started_at stamp)`` of a run id, or None if malformed."""
    match = _RUN_ID_RE.fullmatch(run_id or "")
    if match is None:
        return None
    return int(match.group(1)), match.group(2)


def compose_run_id(etl_id: int, started_at: datetime) -> str:
    stamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{etl_id}-{stamp}"


def deduce_scenario(successful_runs_before: int) -> str:
    """``initial`` when the dataset was never published in this base, else ``rejeu``.

    Only the database decides: the download cache is purged after every
    successful publication (``cleanup_download_dir``), so its absence says
    nothing about a first load.
    """
    return "initial" if successful_runs_before == 0 else "rejeu"


def sources_locales_state(*, cache_present: bool) -> str:
    return "presentes" if cache_present else "absentes"


def check_scenario(declared: str, *, successful_runs_before: int) -> None:
    """Refuse a declared ``initial``/``rejeu`` the database contradicts."""
    if declared not in SCENARIOS:
        raise ExportError(f"scenario inconnu: {declared}")
    if declared == "amorcage":
        return
    expected = deduce_scenario(successful_runs_before)
    if declared != expected:
        raise ExportError(
            f"scenario {declared} contredit par la base "
            f"({successful_runs_before} exécution(s) réussie(s) avant ce run: "
            f"{expected})"
        )


def disk_peak_block(
    peak_bytes: int | None,
    volume: str | None,
    interval_s: int | None,
    *,
    baseline_bytes: int | None = None,
    samples: int | None = None,
) -> dict[str, Any]:
    """Qualified sampled maximum of the used bytes of one named volume."""
    if peak_bytes is None:
        return {
            "bytes": NOT_MEASURED,
            "volume": volume or NOT_MEASURED,
            "sample_interval_s": interval_s if interval_s is not None else NOT_MEASURED,
            "baseline_bytes": NOT_MEASURED,
            "samples": NOT_MEASURED,
            "nature": DISK_PEAK_NATURE,
        }
    return {
        "bytes": int(peak_bytes),
        "volume": volume or NOT_MEASURED,
        "sample_interval_s": int(interval_s)
        if interval_s is not None
        else NOT_MEASURED,
        "baseline_bytes": int(baseline_bytes)
        if baseline_bytes is not None
        else NOT_MEASURED,
        "samples": int(samples) if samples is not None else NOT_MEASURED,
        "nature": DISK_PEAK_NATURE,
    }


def parse_scope_filter_log(text: str, dataset: str) -> dict[str, dict[str, int]] | None:
    """Scope-filter exclusions per staging table, as the runner logged them.

    None when the log carries no recognised line: an empty result would read
    as "nothing excluded" while nothing was observed (D-33).
    """
    found: dict[str, dict[str, int]] = {}
    for match in _SCOPE_FILTER_RE.finditer(text):
        if match.group("dataset") != dataset:
            continue
        found[match.group("table")] = {
            "removed": int(match.group("removed")),
            "without_departement": int(match.group("unattributed")),
        }
    return found or None


def _physical_lines(entry: dict[str, Any]) -> Any:
    # Runs journaled before plan 04-15 (C8) wrote the same count as "rows".
    return entry.get("physical_lines", entry.get("rows"))


def _source_rows(etl_run: dict[str, Any]) -> int | str:
    files = etl_run.get("source_files") or []
    if not files or any(not isinstance(_physical_lines(f), int) for f in files):
        return NOT_MEASURED
    return sum(int(_physical_lines(f)) for f in files)


def _counting(
    dataset: str,
    etl_run: dict[str, Any],
    scope_filter_log: dict[str, dict[str, int]] | None,
) -> dict[str, Any]:
    entry = DATASET_TABLES[dataset]
    stages: dict[str, Any] = {}
    for stage in etl_run.get("stages") or []:
        value = stage.get("rows_affected")
        stages[stage["stage_name"]] = value if isinstance(value, int) else NOT_MEASURED
    return {
        "published_table": entry["published"],
        "source_rows": _source_rows(etl_run),
        "source_rows_unit": entry["tables"][entry["published"]],
        "source_rows_origin": (
            "etl_runs.source_files[].physical_lines (rows avant 04-15), en-tête exclu"
        ),
        "stages_rows_affected": stages or NOT_MEASURED,
        "scope_filter": NOT_MEASURED if scope_filter_log is None else scope_filter_log,
        "tables": {
            table: {"unit": unit, "published_table": entry["published"]}
            for table, unit in entry["tables"].items()
        },
    }


INCOHERENT_JOURNAL = "archive incohérente avec etl_runs.source_files"
INCOHERENT_GENERATION = "archive incohérente avec son manifeste de génération"
# Datasets whose archive keeps the generation manifest and every piece of evidence it
# names (metadata, page proofs, partitions: plan 04-15), while etl_runs.source_files
# journals only the processing units the ETL read (C23, WINDOWS n° 79).
GENERATION_MANIFEST_DATASETS = frozenset({"dpe"})
GENERATION_MANIFEST = "manifest.json"


def _triplets(entries: list[Any]) -> list[tuple[str, str, str]]:
    return sorted(
        (str(e.get("name")), str(e.get("bytes")), str(e.get("sha256")))
        for e in entries
        if isinstance(e, dict)
    )


def _check_generation_record(
    base: Path, files: list[Any], manifest_entries: list[dict[str, Any]]
) -> set[str]:
    """Check a record against the generation manifest it archived; return unit paths.

    The manifest object is read back and its digest checked. The record must hold
    exactly the manifest and the files it names, each with the digest it declares, at
    its path relative to the generation directory. Only the processing units (the
    manifest's ``files``) are then compared with the journal, by the caller.
    """
    entry = manifest_entries[0]
    sha = str(entry.get("sha256"))
    if len(manifest_entries) != 1 or entry.get("object") != f"objects/{sha[:2]}/{sha}":
        raise ExportError(INCOHERENT_GENERATION)
    try:
        data = (base / entry["object"]).read_bytes()
    except OSError as exc:
        raise ExportError(INCOHERENT_GENERATION) from exc
    if hashlib.sha256(data).hexdigest() != sha or len(data) != entry.get("bytes"):
        raise ExportError(INCOHERENT_GENERATION)
    try:
        manifest = json.loads(data)
        directory = manifest["directory"]
        flavour = PureWindowsPath if PureWindowsPath(directory).drive else PurePosixPath
        origin = flavour(directory)

        def named(artifact: dict[str, Any]) -> tuple[str, str]:
            relative = flavour(artifact["path"]).relative_to(origin)
            if ".." in relative.parts:
                raise ValueError("outside the generation")
            return relative.as_posix(), str(artifact["sha256"])

        units = [named(unit) for unit in manifest["files"]]
        evidence = []
        for chain in [
            *manifest["files"],
            *(p for u in manifest["files"] for p in u["partitions"]),
        ]:
            evidence += [named(chain["metadata_start"]), named(chain["metadata_end"])]
            evidence += [named(page) for page in chain["pages"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ExportError(INCOHERENT_GENERATION) from exc
    recorded = [
        (str(e.get("relpath")), str(e.get("sha256")))
        for e in files
        if isinstance(e, dict)
    ]
    declared = {(GENERATION_MANIFEST, sha), *units, *evidence}
    if len(recorded) != len(files) or len(set(recorded)) != len(recorded):
        raise ExportError(INCOHERENT_GENERATION)
    if set(recorded) != declared:
        raise ExportError(INCOHERENT_GENERATION)
    return {relpath for relpath, _ in units}


def source_archive_block(
    root: str | None, dataset: str, run_id: str, etl_run: dict[str, Any]
) -> Any:
    """Origin of the run's sources, read in its archive record (plan 02-14)."""
    if not root:
        return "non_configuree"
    relative = f"{dataset}/runs/{run_id}.json"
    path = Path(root) / relative
    if not path.is_file():
        return "absente"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"archive illisible: {relative}") from exc
    files = record.get("files") if isinstance(record, dict) else None
    if not isinstance(files, list):
        raise ExportError(f"archive illisible: {relative}")

    journal = etl_run.get("source_files") or []
    manifest = [
        e
        for e in files
        if isinstance(e, dict) and e.get("relpath") == GENERATION_MANIFEST
    ]
    if dataset in GENERATION_MANIFEST_DATASETS and manifest:
        units = _check_generation_record(path.parent.parent, files, manifest)
        files_of_units = [e for e in files if e.get("relpath") in units]
        if _triplets(files_of_units) != _triplets(journal):
            raise ExportError(INCOHERENT_JOURNAL)
    elif _triplets(files) != _triplets(journal):
        raise ExportError(INCOHERENT_JOURNAL)
    return {
        "record": relative,
        "record_sha256": file_sha256(path),
        "origin": record.get("origin"),
        "replayed_from": record.get("replayed_from"),
        "files": files,
    }


def parse_source_files_log(text: str) -> list[dict[str, Any]]:
    """``SOURCE_FILE <name> <bytes> <sha256>`` lines a script printed."""
    return [
        {"name": m["name"], "bytes": int(m["bytes"]), "sha256": m["sha256"]}
        for m in _SOURCE_FILE_RE.finditer(text)
    ]


def script_source_archive(
    root: str | None, name: str, run_id: str, source_files: list[dict[str, Any]]
) -> Any:
    """Archive block of a script receipt, checked like a pipeline's (same function).

    The files the script printed play the part of ``etl_runs.source_files``.
    """
    archive_dataset = SCRIPT_TABLES[name]["archive_dataset"]
    if archive_dataset is None:
        return WITHOUT_EXTERNAL_SOURCE
    return source_archive_block(
        root, archive_dataset, run_id, {"source_files": source_files}
    )


def build_script_manifest(
    *,
    run_id: str,
    name: str,
    status: str,
    duration_s: float | None,
    tables: dict[str, dict[str, Any]],
    repo_sha: str,
    gabarit: dict[str, Any] | None,
    disk_peak: dict[str, Any],
    scenario: str,
    sources_locales: str,
    source_files: list[dict[str, Any]],
    source_archive: Any,
    command: list[str],
    exported_at: str,
    exporter_sha256: str | None = None,
) -> dict[str, Any]:
    """Receipt of one script run: same identity keys as a pipeline receipt."""
    return {
        "run_id": run_id,
        "dataset": name,
        "kind": "script",
        "command": command,
        "status": status,
        "duration_s": duration_s if duration_s is not None else NOT_MEASURED,
        "tables": {t: _table_block(info) for t, info in tables.items()},
        "repo_sha": repo_sha,
        "gabarit": gabarit if gabarit is not None else NOT_MEASURED,
        "disk_peak": disk_peak,
        "scenario": scenario,
        "sources_locales": sources_locales,
        "source_files": source_files,
        "source_archive": source_archive,
        "exported_at": exported_at,
        "exporter_sha256": exporter_sha256 or NOT_MEASURED,
    }


def _table_block(info: dict[str, Any]) -> dict[str, Any]:
    block: dict[str, Any] = {
        "kind": info.get("kind"),
        "count_method": info.get("count_method"),
        "total_rows": info.get("total_rows", NOT_MEASURED),
        "published_rows_by_departement": info.get("rows_by_departement")
        if info.get("rows_by_departement") is not None
        else NOT_MEASURED,
        "sizes": info.get("sizes", NOT_MEASURED),
    }
    if info.get("partitions") is not None:
        block["partitions"] = info["partitions"]
    if info.get("row_bytes_by_departement") is not None:
        block["row_bytes_by_departement"] = {
            dep: {"bytes": int(value), "nature": ROW_DATA_NATURE}
            for dep, value in info["row_bytes_by_departement"].items()
        }
    return block


def build_manifest(
    *,
    run_id: str,
    dataset: str,
    etl_run: dict[str, Any],
    dataset_metadata: dict[str, Any] | None,
    tables: dict[str, dict[str, Any]],
    repo_sha: str,
    gabarit: dict[str, Any] | None,
    disk_peak: dict[str, Any],
    scenario: str,
    sources_locales: str,
    scope_filter_log: dict[str, dict[str, int]] | None,
    exported_at: str,
    exporter_sha256: str | None = None,
    source_archive: Any = "non_configuree",
) -> dict[str, Any]:
    """Assemble the receipt. Same inputs → same bytes (no clock read here)."""
    return {
        "run_id": run_id,
        "dataset": dataset,
        "etl_run": etl_run,
        "dataset_metadata": dataset_metadata
        if dataset_metadata is not None
        else NOT_MEASURED,
        "tables": {name: _table_block(info) for name, info in tables.items()},
        "repo_sha": repo_sha,
        "gabarit": gabarit if gabarit is not None else NOT_MEASURED,
        "disk_peak": disk_peak,
        "scenario": scenario,
        "sources_locales": sources_locales,
        "source_archive": source_archive,
        "counting": _counting(dataset, etl_run, scope_filter_log),
        "exported_at": exported_at,
        "exporter_sha256": exporter_sha256 or NOT_MEASURED,
    }


def pointer_for(manifest: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    """Pointer to a receipt; its file name is always composed from the run id."""
    return {
        "dataset": manifest["dataset"],
        "run_id": manifest["run_id"],
        "manifest": f"{manifest['run_id']}.json",
        "repo_sha": manifest["repo_sha"],
        "scenario": manifest["scenario"],
        "generated_at": generated_at,
    }


def write_pointer(path: Path, pointer: dict[str, Any], receipt: Path) -> None:
    """Rewrite ``latest.json`` atomically; refuse any name that does not match."""
    expected = f"{pointer.get('run_id')}.json"
    if (
        not pointer.get("run_id")
        or pointer.get("manifest") != expected
        or receipt.name != expected
        or path.name == expected
        or path.resolve() == receipt.resolve()
    ):
        raise ExportError(POINTER_MESSAGE)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps_sorted(pointer), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def verify_repo_sha(sha: str, repo_dir: Path) -> None:
    """40 lowercase hex characters AND a commit of the git history."""
    if not _SHA_RE.fullmatch(sha or ""):
        raise ExportError(REPO_SHA_MESSAGE)
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ExportError(f"{REPO_SHA_MESSAGE} (git indisponible)") from exc
    if result.returncode != 0:
        raise ExportError(REPO_SHA_MESSAGE)


def write_receipt(path: Path, text: str) -> None:
    """Create the receipt; never overwrite one (exclusive creation)."""
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ReceiptExistsError(RECEIPT_EXISTS_MESSAGE) from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ----------------------------------------------------------------- database --


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def fetch_etl_run(conn: Any, dataset: str, run_id: str) -> dict[str, Any]:
    parsed = parse_run_id(run_id)
    if parsed is None:
        raise ExportError(f"run_id inconnu: {run_id}")
    etl_id = parsed[0]
    row = conn.execute(
        f"SELECT {', '.join(ETL_RUN_COLUMNS)} FROM etl_runs "  # identifiers: constants
        "WHERE id = %s AND dataset_name = %s",
        (etl_id, dataset),
    ).fetchone()
    if row is None:
        raise ExportError(f"run_id inconnu: {run_id}")
    run = {
        col: _jsonable(value) for col, value in zip(ETL_RUN_COLUMNS, row, strict=True)
    }
    if compose_run_id(etl_id, row[ETL_RUN_COLUMNS.index("started_at")]) != run_id:
        raise ExportError(f"run_id inconnu: {run_id} (horodatage différent)")
    return run


def successful_runs_before(conn: Any, dataset: str, etl_id: int) -> int:
    return int(
        conn.execute(
            "SELECT count(*) FROM etl_runs WHERE dataset_name = %s "
            "AND status = 'success' AND id < %s",
            (dataset, etl_id),
        ).fetchone()[0]
    )


def fetch_dataset_metadata(conn: Any, dataset: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT row_to_json(m) FROM dataset_metadata m WHERE dataset_name = %s",
        (dataset,),
    ).fetchone()
    return row[0] if row else None


def collect_table(conn: Any, name: str, sizes: dict[str, Any]) -> dict[str, Any]:
    """Rows per departement, sizes, partitions or row bytes of one table."""
    from psycopg import sql

    from scripts.extract_fidelity_reference import (
        Catalog,
        _insee_departement_sql,
        choose_method,
    )

    catalog = Catalog(conn)
    status = catalog.status(name)
    if status != "ok":
        return {"kind": status}
    kind = {"r": "table", "p": "partitioned", "m": "matview", "v": "view"}[
        catalog.describe(name)[0]
    ]
    method = choose_method(catalog, name)
    ident = sql.Identifier("public", name)
    info: dict[str, Any] = {
        "kind": kind,
        "count_method": method,
        "total_rows": int(
            conn.execute(
                sql.SQL("SELECT {} FROM {}").format(_row_count_sql(conn, name), ident)
            ).fetchone()[0]
        ),
    }
    agg = sizes["tables"].get(name)
    relation = next((r for r in sizes["relations"] if r["name"] == name), None)
    if agg is not None and relation is not None:
        info["sizes"] = {
            "total_bytes": agg["total_bytes"],
            "indexes_bytes": agg["indexes_bytes"],
            "table_bytes": relation["pg_relation_size"],
            "toast_bytes": relation["toast_bytes"],
        }
    children = [r for r in sizes["relations"] if r["parent"] == name]
    if kind == "partitioned":
        info["partitions"] = {
            (child["departement"] or child["name"]): {
                "name": child["name"],
                "total_bytes": child["pg_total_relation_size"],
                "table_bytes": child["pg_relation_size"],
                "indexes_bytes": child["pg_indexes_size"],
                "toast_bytes": child["toast_bytes"],
            }
            for child in children
        }
    if method["method"] == "partition":
        rows: dict[str, int] = {}
        for child in children:
            count = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier("public", child["name"])
                )
            ).fetchone()[0]
            rows[child["departement"] or child["name"]] = int(count)
        info["rows_by_departement"] = rows
        return info
    if method["method"] == "code_departement":
        expr: Any = sql.Identifier(method["column"])
    elif method["method"] == "departement_from_insee":
        expr = _insee_departement_sql(sql.Identifier(method["column"]))
    else:
        info["rows_by_departement"] = None
        return info
    grouped = conn.execute(
        sql.SQL(
            "SELECT coalesce({e}::text, ''), {n}, sum(pg_column_size(t.*)) "
            "FROM {t} t GROUP BY 1 ORDER BY 1"
        ).format(e=expr, n=_row_count_sql(conn, name), t=ident)
    ).fetchall()
    info["rows_by_departement"] = {
        dep or "sans_departement": int(n) for dep, n, _ in grouped
    }
    if kind != "partitioned":
        info["row_bytes_by_departement"] = {
            dep or "sans_departement": int(b or 0) for dep, _, b in grouped
        }
    return info


# Plan 04.1-16: ``eligibilite_fixe`` stores one row per building, the source
# offer rows in ``offres``; it is counted in source rows, like its archive.
_COLONNE_OFFRES_SQL = (
    "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
    "AND table_name = %s AND column_name = 'offres'"
)


def _row_count_sql(conn: Any, table: str) -> Any:
    """``count(*)``, or the offer rows of the per-building eligibility table."""
    from psycopg import sql

    if (
        table == "eligibilite_fixe"
        and conn.execute(_COLONNE_OFFRES_SQL, (table,)).fetchone()
    ):
        return sql.SQL("coalesce(sum(cardinality(offres)), 0)")
    return sql.SQL("count(*)")


# ---------------------------------------------------------------------- CLI --


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.export_import_manifest",
        description="Write the immutable import manifest of one ETL run.",
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo-sha", help="required unless --pointer-only")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pointer", type=Path)
    parser.add_argument("--gabarit-json", help="JSON object describing the machine")
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--sources-locales", choices=SOURCES_LOCALES)
    parser.add_argument("--disk-peak-bytes", type=int)
    parser.add_argument("--disk-peak-volume")
    parser.add_argument("--disk-peak-interval-s", type=int)
    parser.add_argument("--disk-baseline-bytes", type=int)
    parser.add_argument("--disk-samples", type=int)
    parser.add_argument("--run-log", type=Path, help="captured log of the run")
    parser.add_argument("--repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--source-archive-dir",
        help="archive root (default: SOURCE_ARCHIVE_DIR; empty: non_configuree)",
    )
    parser.add_argument(
        "--script",
        action="store_true",
        help="receipt of an import script (SCRIPT_TABLES), not of an etl_runs row",
    )
    parser.add_argument("--script-status", choices=SCRIPT_STATUSES)
    parser.add_argument("--script-duration-s", type=float)
    parser.add_argument("--script-command", help="the command run, as a JSON list")
    parser.add_argument(
        "--pointer-only",
        action="store_true",
        help="rewrite --pointer from the existing receipt --out (lost pointer)",
    )
    return parser


def regenerate_pointer(args: argparse.Namespace) -> Path:
    """Rewrite a lost pointer from its existing receipt; the receipt is only read."""
    if args.pointer is None:
        raise ExportError("--pointer-only exige --pointer")
    if not args.out.is_file():
        raise ExportError(f"reçu absent: {args.out.name}")
    try:
        manifest = json.loads(args.out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"reçu illisible: {args.out.name}") from exc
    if manifest.get("run_id") != args.run_id or manifest.get("dataset") != args.dataset:
        raise ExportError("reçu incohérent avec --run-id ou --dataset")
    verify_repo_sha(str(manifest.get("repo_sha") or ""), args.repo_dir)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_pointer(
        args.pointer, pointer_for(manifest, generated_at=generated_at), args.out
    )
    print(f"POINTER {args.pointer.name} -> {args.out.name}")
    return args.pointer


def _export(args: argparse.Namespace) -> Path:  # noqa: C901
    if args.script:
        if args.dataset not in SCRIPT_TABLES:
            raise ExportError(f"script inconnu: {args.dataset}")
        if not _SCRIPT_RUN_ID_RE.fullmatch(args.run_id or ""):
            raise ExportError(f"run_id inconnu: {args.run_id}")
        parsed = None
    else:
        if args.dataset not in DATASET_TABLES:
            raise ExportError(f"dataset inconnu: {args.dataset}")
        parsed = parse_run_id(args.run_id)
        if parsed is None:
            raise ExportError(f"run_id inconnu: {args.run_id}")
    # Names first: a self-referencing or divergent name is refused whatever
    # exists on disk.
    if args.out.name != f"{args.run_id}.json":
        raise ExportError(POINTER_MESSAGE)
    if args.pointer is not None and (
        args.pointer.name == args.out.name
        or args.pointer.resolve() == args.out.resolve()
    ):
        raise ExportError(POINTER_MESSAGE)
    if args.pointer_only:
        return regenerate_pointer(args)
    if args.out.exists():
        raise ReceiptExistsError(RECEIPT_EXISTS_MESSAGE)
    verify_repo_sha(args.repo_sha or "", args.repo_dir)
    gabarit = None
    if args.gabarit_json:
        try:
            gabarit = json.loads(args.gabarit_json)
        except json.JSONDecodeError as exc:
            raise ExportError("--gabarit-json n'est pas du JSON") from exc
        if not isinstance(gabarit, dict):
            raise ExportError("--gabarit-json doit être un objet JSON")
    scope_log = None
    if args.run_log is not None:
        try:
            scope_log = parse_scope_filter_log(
                args.run_log.read_text(encoding="utf-8", errors="replace"), args.dataset
            )
        except OSError as exc:
            raise ExportError(f"journal du run illisible: {args.run_log}") from exc

    if args.script:
        return _export_script(args, gabarit)
    assert parsed is not None
    from scripts.measure_db_sizes import collect_sizes

    conn = _read_only_connection()
    with conn:
        etl_run = fetch_etl_run(conn, args.dataset, args.run_id)
        before = successful_runs_before(conn, args.dataset, parsed[0])
        scenario = args.scenario or deduce_scenario(before)
        check_scenario(scenario, successful_runs_before=before)
        metadata = fetch_dataset_metadata(conn, args.dataset)
        sizes = collect_sizes(conn)
        tables = {
            name: collect_table(conn, name, sizes)
            for name in DATASET_TABLES[args.dataset]["tables"]
        }
    archive_dir = (
        args.source_archive_dir
        if args.source_archive_dir is not None
        else os.environ.get("SOURCE_ARCHIVE_DIR", "")
    )
    archive = source_archive_block(archive_dir, args.dataset, args.run_id, etl_run)
    manifest = build_manifest(
        run_id=args.run_id,
        dataset=args.dataset,
        etl_run=etl_run,
        dataset_metadata=metadata,
        tables=tables,
        repo_sha=args.repo_sha,
        gabarit=gabarit,
        disk_peak=disk_peak_block(
            args.disk_peak_bytes,
            args.disk_peak_volume,
            args.disk_peak_interval_s,
            baseline_bytes=args.disk_baseline_bytes,
            samples=args.disk_samples,
        ),
        scenario=scenario,
        sources_locales=args.sources_locales or NOT_MEASURED,
        scope_filter_log=scope_log,
        exported_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        exporter_sha256=file_sha256(Path(__file__)),
        source_archive=archive,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_receipt(args.out, dumps_sorted(manifest))
    if args.pointer is not None:
        write_pointer(
            args.pointer,
            pointer_for(manifest, generated_at=manifest["exported_at"]),
            args.out,
        )
    print(
        f"MANIFEST {args.out.name} run_id={args.run_id} dataset={args.dataset} "
        f"status={etl_run['status']} scenario={scenario}"
    )
    return args.out


def _read_only_connection() -> Any:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise ExportError("DATABASE_URL absent")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    import psycopg

    from scripts.measure_db_sizes import open_read_only

    try:
        return open_read_only(dsn)
    except psycopg.Error:
        raise ExportError("base injoignable") from None


def _export_script(args: argparse.Namespace, gabarit: dict[str, Any] | None) -> Path:
    """Receipt of a script run: status and duration from the launcher, sources
    from the ``SOURCE_FILE`` lines of its log, tables measured in the base."""
    if args.script_status is None:
        raise ExportError("--script-status requis avec --script")
    if args.run_log is None:
        raise ExportError("--run-log requis avec --script")
    try:
        log_text = args.run_log.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExportError(f"journal du run illisible: {args.run_log}") from exc
    try:
        command = json.loads(args.script_command) if args.script_command else []
    except json.JSONDecodeError as exc:
        raise ExportError("--script-command n'est pas du JSON") from exc
    if args.scenario not in ("initial", "rejeu"):
        raise ExportError("--scenario initial|rejeu requis avec --script")
    source_files = parse_source_files_log(log_text)

    from scripts.measure_db_sizes import collect_sizes

    conn = _read_only_connection()
    with conn:
        sizes = collect_sizes(conn)
        tables = {
            name: collect_table(conn, name, sizes)
            for name in SCRIPT_TABLES[args.dataset]["tables"]
        }
    archive_dir = (
        args.source_archive_dir
        if args.source_archive_dir is not None
        else os.environ.get("SOURCE_ARCHIVE_DIR", "")
    )
    manifest = build_script_manifest(
        run_id=args.run_id,
        name=args.dataset,
        status=args.script_status,
        duration_s=args.script_duration_s,
        tables=tables,
        repo_sha=args.repo_sha,
        gabarit=gabarit,
        disk_peak=disk_peak_block(
            args.disk_peak_bytes,
            args.disk_peak_volume,
            args.disk_peak_interval_s,
            baseline_bytes=args.disk_baseline_bytes,
            samples=args.disk_samples,
        ),
        scenario=args.scenario,
        sources_locales=args.sources_locales or NOT_MEASURED,
        source_files=source_files,
        source_archive=script_source_archive(
            archive_dir, args.dataset, args.run_id, source_files
        ),
        command=command,
        exported_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        exporter_sha256=file_sha256(Path(__file__)),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_receipt(args.out, dumps_sorted(manifest))
    if args.pointer is not None:
        write_pointer(
            args.pointer,
            pointer_for(manifest, generated_at=manifest["exported_at"]),
            args.out,
        )
    print(
        f"MANIFEST {args.out.name} run_id={args.run_id} dataset={args.dataset} "
        f"status={args.script_status} scenario={args.scenario}"
    )
    return args.out


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.pointer_only and not args.repo_sha:
        parser.error("--repo-sha requis (sauf avec --pointer-only)")
    try:
        _export(args)
    except ReceiptExistsError:
        print(RECEIPT_EXISTS_MESSAGE, file=sys.stderr)
        return EXIT_RECEIPT_EXISTS
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # never leak a DSN in a traceback  # noqa: BLE001
        print(f"export impossible: {type(exc).__name__}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

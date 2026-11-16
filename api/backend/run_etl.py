"""Run all ETL pipelines sequentially.

Usage:
    python -m run_etl                    # Run every hosted dataset (instance scope)
    python -m run_etl ban cadastre       # Run specific datasets only
    python -m run_etl --dry-run ban      # Download + validate only (no DB writes;
                                         # still reads the instance scope from
                                         # the application database)
    python -m run_etl --dry-run          # Dry-run all datasets
    python -m run_etl dpe --dpe-manifest /private/import-manifest.json
    python -m run_etl vacance --from-archive 2-20260923T001821Z
                                         # Reimport from the archived sources
                                         # of that run (SOURCE_ARCHIVE_DIR),
                                         # verified before any write
    python -m run_etl ban --millesime 2026-06-17
                                         # Target that vintage of the producer
                                         # (pipelines declaring a mechanism;
                                         # malformed label: exit 2)
"""

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from app.common.source_catalog import covered_departements
from app.config import settings
from app.etl import runner as etl_runner
from app.etl.base import BasePipeline
from app.etl.errors import (
    DatasetNotHostedError,
    EmptyScopeError,
    MalformedMillesimeError,
    MillesimeNotTargetableError,
    ScopePrerequisiteError,
)
from app.etl.hooks import build_default_hooks
from app.etl.runner import PipelineRunner
from app.etl.schemas import EtlResult
from app.etl.source_archive import SourceArchiveError, SourceReplay, load_replay

# v5.0 pipelines (small, non-partitioned)
from app.etl_antennes.pipeline import AntennesPipeline
from app.etl_arcep.pipeline import ArcepPipeline

# Import all pipeline modules so they self-register
from app.etl_ban.pipeline import BanPipeline
from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
from app.etl_bdtopo.pipeline import BdTopoPipeline
from app.etl_cadastre.pipeline import CadastrePipeline
from app.etl_copropriete.pipeline import CoproprietePipeline
from app.etl_demographie.pipeline import DemographiePipeline
from app.etl_dpe.pipeline import DpePipeline
from app.etl_dvf.pipeline import DvfPipeline
from app.etl_equipements.pipeline import EquipementsPipeline
from app.etl_fiscalite.pipeline import FiscalitePipeline
from app.etl_loyers.pipeline import LoyersPipeline

# v6.0 — MAJIC personnes morales (partitioned, heavy)
from app.etl_majic_pm.pipeline import MajicPmPipeline
from app.etl_monuments.pipeline import MonumentsPipeline
from app.etl_permis.pipeline import PermisPipeline
from app.etl_revenus.pipeline import RevenusPipeline
from app.etl_rnb_liens.pipeline import RnbLiensPipeline
from app.etl_scolaire.pipeline import ScolairePipeline
from app.etl_transport.pipeline import TransportPipeline
from app.etl_vacance.pipeline import VacancePipeline
from app.etl_zonage_abc.pipeline import ZonageAbcPipeline

# Ordered by size (smallest first for quick wins)
ALL_PIPELINES = [
    MonumentsPipeline,  # ~45K rows
    LoyersPipeline,  # ~4K rows
    TransportPipeline,  # ~150K rows
    ScolairePipeline,  # ~100K rows
    # v5.0 — small datasets
    ZonageAbcPipeline,  # ~35K rows
    FiscalitePipeline,  # ~35K rows
    RevenusPipeline,  # ~35K rows
    DemographiePipeline,  # ~35K rows
    VacancePipeline,  # ~35K rows
    CoproprietePipeline,  # ~528K rows
    PermisPipeline,  # ~500K rows
    AntennesPipeline,  # ~250K rows
    EquipementsPipeline,  # ~2.5M rows
    RnbLiensPipeline,  # ~2M rows (44+85)
    BdnbBatimentParcellePipeline,  # ~1.4M rows (44+85)
    # v1.0 — large datasets
    MajicPmPipeline,  # ~17M rows
    DvfPipeline,  # ~25M rows
    DpePipeline,  # ~35M rows
    BanPipeline,  # ~26M rows
    CadastrePipeline,  # ~100M rows
    BdTopoPipeline,  # ~100M rows
    ArcepPipeline,  # ~100M rows
]

PIPELINE_MAP = {cls().dataset_name: cls for cls in ALL_PIPELINES}

# Run only when named (04.7-19 D10): on a national scope, a run without names
# would otherwise start the national BDNB import without its decision.
NAMED_ONLY = frozenset({"bdnb_batiment_parcelle"})


def _log_disk_space(log: logging.Logger) -> None:
    """Log disk space for temp dir."""
    temp_dir = settings.etl_temp_dir
    try:
        usage = shutil.disk_usage(temp_dir)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        log.info(
            "Disk space (%s): %.1f GB free / %.1f GB total",
            temp_dir,
            free_gb,
            total_gb,
        )
    except OSError:
        log.warning("Could not check disk space for %s", temp_dir)


def _parse_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="*")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "download and validate only, no database writes; the application "
            "database must still be reachable to read the instance scope"
        ),
    )
    parser.add_argument("--dpe-manifest", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--from-archive", metavar="RUN_ID")
    parser.add_argument(
        "--millesime",
        metavar="LABEL",
        help=(
            "target this vintage of the producer instead of its current one "
            "(one dataset whose pipeline declares a mechanism; plan 02-11, D-27)"
        ),
    )
    options = parser.parse_intermixed_args(args)
    if options.millesime is not None and (
        len(options.datasets) != 1
        or options.from_archive is not None
        or options.source_manifest is not None
        or options.dpe_manifest is not None
    ):
        parser.error(
            "--millesime requires exactly one dataset, without --from-archive, "
            "--source-manifest or --dpe-manifest"
        )
    if options.from_archive is not None and (
        len(options.datasets) != 1
        or options.source_manifest is not None
        or options.dpe_manifest is not None
        or options.dry_run
    ):
        parser.error(
            "--from-archive requires exactly one dataset, without "
            "--source-manifest, --dpe-manifest or --dry-run"
        )
    if options.source_manifest is not None and (
        options.datasets not in (["vacance"], ["loyers"])
        or options.dpe_manifest is not None
    ):
        parser.error(
            "--source-manifest requires exactly one dataset: vacance or loyers, "
            "without --dpe-manifest"
        )
    if "encadrement_loyers" in options.datasets:
        parser.error("encadrement_loyers is quarantined; no ETL execution allowed")
    if options.dpe_manifest is not None and options.datasets != ["dpe"]:
        parser.error("--dpe-manifest requires exactly one explicit dataset: dpe")
    if any(name not in PIPELINE_MAP for name in options.datasets):
        parser.error("Unknown dataset")
    return options


def _covers_scope(name: str, departements: tuple[str, ...]) -> bool:
    """True when a source of pipeline *name* publishes a hosted departement.

    Expected departements = scope ∩ source coverage (catalogue, D-22/D-23).
    """
    keys = PIPELINE_MAP[name]().scope_dataset_keys
    return bool(covered_departements(keys, departements))


def _hosted_selection(requested: list[str], log: logging.Logger) -> list[str]:
    """Datasets to run under the instance scope (D-22); exit 2 on refusal.

    Without names: every pipeline outside ``NAMED_ONLY`` whose catalogue keys
    are all hosted, minus
    sources that publish none of the hosted departements (catalogue
    ``source_coverage``; permis: 93 only). A named dataset outside the scope,
    or whose source covers none of its departements, is refused before any
    pipeline starts, hence before any download.
    """
    scope = etl_runner.default_scope_loader(BasePipeline.get_sync_dsn())
    if scope.is_empty:
        print(
            "périmètre vide: aucun département hébergé, aucun import "
            "(python -m app.scope set)",
            file=sys.stderr,
        )
        sys.exit(2)
    if not requested:
        hosted = [
            name
            for name, cls in PIPELINE_MAP.items()
            if name not in NAMED_ONLY
            and all(scope.hosts(key) for key in cls().scope_dataset_keys)
        ]
        for name in hosted:
            if not _covers_scope(name, scope.departements):
                log.warning(
                    "source sans couverture sur le périmètre: %s (not run)", name
                )
        selected = [name for name in hosted if _covers_scope(name, scope.departements)]
        if not selected:
            print(
                "aucun dataset à importer: aucun dataset hébergé ne couvre le "
                "périmètre (python -m app.scope show)",
                file=sys.stderr,
            )
            sys.exit(2)
        log.info("Hosted datasets selected from the instance scope")
        return selected
    for name in requested:
        for key in PIPELINE_MAP[name]().scope_dataset_keys:
            if not scope.hosts(key):
                print(f"dataset non hébergé: {key}", file=sys.stderr)
                sys.exit(2)
        if not _covers_scope(name, scope.departements):
            print(f"source sans couverture sur le périmètre: {name}", file=sys.stderr)
            sys.exit(2)
    return requested


def _load_replay(name: str, run_id: str) -> SourceReplay:
    """Verified archived sources of *run_id*; exit 2 before any runner or connection.

    No fallback to a download: a refused archive stops the command.
    """
    if not getattr(PIPELINE_MAP[name], "archive_replay", False):
        print(f"archive_replay_non_supporte: {name}", file=sys.stderr)
        sys.exit(2)
    if not settings.source_archive_dir:
        print("source_archive_non_configuree", file=sys.stderr)
        sys.exit(2)
    try:
        return load_replay(Path(settings.source_archive_dir), name, run_id)
    except SourceArchiveError as exc:
        print(f"SOURCE_ARCHIVE_REFUSED {exc.reason} {exc.path}")
        sys.exit(2)


def _pinned_pipeline(name: str, label: str) -> BasePipeline:
    """Pipeline *name* pinned to vintage *label*; exit 2 before anything else.

    Refused when the pipeline declares no mechanism to target a vintage, or
    when the label is malformed: before the scope read, the runner and any
    download.
    """
    pipeline = PIPELINE_MAP[name]()
    try:
        pipeline.pin_millesime(label)
    except (MillesimeNotTargetableError, MalformedMillesimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    return pipeline


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-20s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("etl.main")

    # Parse CLI args
    options = _parse_args(sys.argv[1:])
    pinned = (
        _pinned_pipeline(options.datasets[0], options.millesime)
        if options.millesime is not None
        else None
    )
    # The record and every fingerprint are checked before the runner exists,
    # hence before any etl_runs row, staging table or network access.
    replay = (
        _load_replay(options.datasets[0], options.from_archive)
        if options.from_archive is not None
        else None
    )
    runner = PipelineRunner(hooks=build_default_hooks())
    dry_run = options.dry_run
    if dry_run:
        log.info("*** DRY RUN MODE — no DB writes ***")

    # encadrement_loyers is not in PIPELINE_MAP: quarantine stays enforced.
    requested = _hosted_selection(options.datasets, log)

    # Pre-flight info
    log.info("ETL temp dir: %s", settings.etl_temp_dir)
    log.info(
        "Datasets to run: %s",
        ", ".join(requested),
    )
    _log_disk_space(log)

    results = []
    t0 = time.perf_counter()

    for name in requested:
        if name not in PIPELINE_MAP:
            log.error(
                "Unknown dataset: %s (available: %s)",
                name,
                list(PIPELINE_MAP.keys()),
            )
            continue

        log.info("=" * 60)
        log.info("STARTING PIPELINE: %s", name)
        log.info("=" * 60)

        if pinned is not None:
            pipeline = pinned
            log.info("Vintage targeted: %s %s", name, pinned.pinned_millesime)
        elif options.source_manifest is not None:
            pipeline = PIPELINE_MAP[name].from_manifest(options.source_manifest)
        elif options.dpe_manifest is not None:
            pipeline = DpePipeline.from_manifest(options.dpe_manifest)
        else:
            pipeline = PIPELINE_MAP[name]()
        try:
            if replay is not None:
                result = runner.run(pipeline, dry_run=False, source_replay=replay)
            else:
                result = runner.run(pipeline, dry_run=dry_run)
        except (DatasetNotHostedError, EmptyScopeError, ScopePrerequisiteError) as exc:
            # Scope changed after selection, or a reference the filter needs is
            # missing: refuse this dataset only.
            result = EtlResult(
                dataset_name=name,
                stages=[],
                total_rows_loaded=0,
                total_duration_seconds=0,
                success=False,
                error=str(exc),
            )
        results.append(result)

        status = "OK" if result.success else "FAILED"
        log.info(
            "PIPELINE %s: %s — %d rows in %.1fs",
            name,
            status,
            result.total_rows_loaded,
            result.total_duration_seconds,
        )
        if result.error:
            log.error("Error: %s", result.error)

    total = time.perf_counter() - t0

    # Final summary table
    log.info("=" * 60)
    log.info("ALL PIPELINES COMPLETE in %.1fs", total)
    log.info(
        "  %-6s %-20s %12s %10s  %s",
        "Status",
        "Dataset",
        "Rows",
        "Duration",
        "Error",
    )
    log.info("  %s", "-" * 70)
    for r in results:
        status = " OK " if r.success else "FAIL"
        err = r.error[:40] if r.error else ""
        log.info(
            "  [%s] %-20s %12d %8.1fs  %s",
            status,
            r.dataset_name,
            r.total_rows_loaded,
            r.total_duration_seconds,
            err,
        )

    _log_disk_space(log)

    # Exit code: 1 if any pipeline failed
    if any(not r.success for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

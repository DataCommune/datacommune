"""DPE (Diagnostics de Performance Energetique) concrete ETL pipeline.

Downloads both official ADEME JSON sources for every hosted departement
(``DEPARTEMENTS_HEBERGEABLES``, DROM included: D-122), verifies complete
immutable snapshots and writes traceable private CSV files.

Validates schema, transforms (coords extraction, dept derivation),
bulk-loads via COPY into a flat staging table, INSERT SELECTs
with ST_MakePoint into the production-like staging table, and swaps
atomically.
"""

import asyncio
import csv
import json
import logging
import math
import os
import shutil
import tempfile
import time
import weakref
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from itertools import islice
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.dpe.lifecycle import LifecycleGraph
from app.etl.base import BasePipeline
from app.etl.checkpoint import CheckpointManager
from app.etl.download import get_download_dir
from app.etl.errors import ScopePrerequisiteError
from app.etl.runner import PipelineRunner
from app.etl.source_archive import SourceReplay
from app.etl_dpe.adresse_brute import rattacher_candidate
from app.etl_dpe.config import (
    CSV_PAGE_SIZE,
    DOWNLOAD_TIMEOUT,
    EXPECTED_COLUMNS,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    RETRY_MAX_WAIT_SECONDS,
)
from app.etl_dpe.reconciliation import collect_presence, reconcile_observations
from app.etl_dpe.sources import (
    DPE_SOURCES,
    MAPPING_VERSION,
    DpeArtifact,
    DpeImportManifest,
    DpePageProof,
    DpeSourceDescriptor,
    DpeSourceFile,
    csv_value,
    decode_json,
    expected_pairs,
    page_cursor,
    sha256_file,
    source_version,
    validate_source_row,
)
from app.scope.model import DEPARTEMENTS_HEBERGEABLES

logger = logging.getLogger(__name__)

# Receipt of one complete (source, departement) pair, next to its files.
PAIR_RECEIPT_SUFFIX = ".pair.json"

# Projection of coordonnee_cartographique_x/y_ban, per territory (D-122).
# Measured on the ADEME API (2026-10-13): DROM points are in the legal UTM
# projection of their territory, not in Lambert 93 (a Lambert 93 reading puts
# 971 in the Gulf of Guinea, as ADEME's own _geopoint does). Same EPSG as BD
# TOPO (app.etl_bdtopo.config.BDTOPO_TERRITORIES); Lambert 93 elsewhere.
SOURCE_SRID_BY_DEPARTEMENT: dict[str, int] = {
    "971": 5490,  # Guadeloupe, RGAF09 / UTM 20N
    "972": 5490,  # Martinique, RGAF09 / UTM 20N
    "973": 2972,  # Guyane, RGFG95 / UTM 22N
    "974": 2975,  # La Réunion, RGR92 / UTM 40S
    "976": 4471,  # Mayotte, RGM04 / UTM 38S
}
SOURCE_SRID_METROPOLE = 2154
# SQL of the source SRID of a flat staging row (code constants only).
SOURCE_SRID_SQL = (
    "CASE code_departement "
    + " ".join(
        f"WHEN '{code}' THEN {srid}"
        for code, srid in sorted(SOURCE_SRID_BY_DEPARTEMENT.items())
    )
    + f" ELSE {SOURCE_SRID_METROPOLE} END"
)


def _close_on_failure(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.close()
            raise

    return wrapped


def _close_after(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            self.close()

    return wrapped


class _RetryableHTTPError(Exception):
    """Wrapper for HTTP 429/5xx errors so tenacity can target them."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _log_request_retry(state: RetryCallState) -> None:
    """One WARNING per retry: a silent retry looked like no retry (run 61).

    Endpoint and departement only: the query (cursor, filter) stays out of
    the journal, as the httpx request logs do (app.logging_config).
    """
    endpoint = state.args[2] if len(state.args) > 2 else state.kwargs.get("endpoint")
    error = state.outcome.exception() if state.outcome is not None else None
    logger.warning(
        "DPE request retry: endpoint=%s departement=%s attempt=%d/%d wait=%.0fs "
        "error=%s: %s",
        endpoint,
        state.kwargs.get("department") or "-",
        state.attempt_number,
        MAX_RETRIES,
        state.next_action.sleep if state.next_action is not None else 0,
        type(error).__name__,
        error,
    )


def _receipt_refusal(
    payload: dict, entry: DpeSourceFile, departements: list[str]
) -> str | None:
    """Why a pair receipt cannot serve this import's scope and projection."""
    if payload.get("departements") != sorted(departements) or entry.pair not in (
        expected_pairs(departements)
    ):
        return "scope"
    source = DPE_SOURCES.get(entry.source_slug)
    if (
        source is None
        or entry.partitions
        or (entry.endpoint, entry.filter, entry.columns, entry.mapping_version)
        != (
            source.endpoint,
            source.filter_for(entry.department),
            source.columns,
            source.mapping_version,
        )
    ):
        return "columns"
    return None


class DpePipeline(BasePipeline):
    """Import every source on every hosted departement in one generation."""

    archive_replay = True

    @classmethod
    def from_manifest(cls, path: Path) -> "DpePipeline":
        """Select an existing verified generation without fabricating a checkpoint."""
        manifest = DpeImportManifest.load(path)
        pipeline = cls()
        pipeline._manifest_path = path
        pipeline._import_manifest = manifest
        pipeline._import_id = manifest.import_id
        return pipeline

    # -- abstract properties ---------------------------------------------------

    def close(self) -> None:
        """Release private SQLite evidence on success, failure and dry runs."""
        finalizer = getattr(self, "_presence_finalizer", None)
        if finalizer is not None and finalizer.alive:
            try:
                finalizer()
            except Exception:  # noqa: BLE001
                logger.warning("DPE presence cleanup failed", exc_info=True)
        self._presence_registry = None
        for workspace in getattr(self, "_source_workspaces", []):
            workspace.cleanup()
        self._source_workspaces = []

    def _source_workspace(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        workspace = tempfile.TemporaryDirectory(prefix="dpe-source-", dir=directory)
        if not hasattr(self, "_source_workspaces"):
            self._source_workspaces = []
        self._source_workspaces.append(workspace)
        return Path(workspace.name)

    def archive_source_inputs(self, files: list[Path]) -> tuple[Path, list[Path]]:
        manifest = self.import_manifest
        manifest.validate()
        if len(files) != len(manifest.files) or {path.resolve() for path in files} != {
            Path(entry.path).resolve() for entry in manifest.files
        }:
            raise ValueError("DPE processing units differ from generation")
        manifest_path = self._manifest_path
        # A caller may select a differently named manifest. Preserve its bytes
        # under the one reserved replay name without rewriting durable evidence.
        if manifest_path.name != "manifest.json":
            canonical = (
                self._source_workspace(get_download_dir("dpe")) / "manifest.json"
            )
            canonical.write_bytes(manifest_path.read_bytes())
            manifest_path = canonical
        original = DpeImportManifest.load(
            manifest_path, relocated_directory=Path(manifest.directory)
        )
        if original != manifest:
            raise ValueError("DPE archived manifest differs from selected generation")
        return Path(manifest.directory), [manifest_path, *manifest.artifact_paths()]

    def restore_source_inputs(
        self, replay: SourceReplay, directory: Path
    ) -> list[Path]:
        restored = self._source_workspace(directory)
        replay.materialize(restored)
        path = restored / "manifest.json"
        manifest = DpeImportManifest.load(path, relocated_directory=restored)
        expected = {"manifest.json"} | {
            str(item.relative_to(restored).as_posix())
            for item in manifest.artifact_paths()
        }
        if {entry["relpath"] for entry in replay.files} != expected:
            raise ValueError("DPE archive inventory differs from generation")
        self._manifest_path = path
        self._import_manifest = manifest
        self._import_id = manifest.import_id
        return [Path(entry.path) for entry in manifest.files]

    def _ensure_presence(self) -> None:
        if getattr(self, "_presence_registry", None) is not None:
            return
        self._presence_registry = collect_presence(self.import_manifest)
        self._presence_finalizer = weakref.finalize(self, self._presence_registry.close)
        self._source_entries = {
            entry.pair: entry for entry in self.import_manifest.files
        }
        self._source_snapshot = max(
            datetime.fromisoformat(entry.observed_at.replace("Z", "+00:00"))
            for entry in self.import_manifest.files
        ).isoformat()
        # Kept with a null position and counted, never rejected (DATA-02).
        self._documents_without_geometry = set()
        for entry in self.import_manifest.files:
            for row in entry.source_rows():
                if self._invalid_geometry(row):
                    self._documents_without_geometry.add(row["numero_dpe"])

    @staticmethod
    def _invalid_geometry(row: dict) -> bool:
        try:
            x = float(row.get("coordonnee_cartographique_x_ban"))
            y = float(row.get("coordonnee_cartographique_y_ban"))
            return not (math.isfinite(x) and math.isfinite(y)) or (x == y == 0)
        except (TypeError, ValueError):
            return True

    def stage_counters(self) -> dict[str, int]:
        """Documents stored without source geometry, one etl_runs step."""
        self._ensure_presence()
        return {"documents_sans_geometrie": len(self._documents_without_geometry)}

    def swap_counters(self) -> dict[str, int]:
        """Raw-address attachment counts of the candidate (plan 04.1-13)."""
        return dict(getattr(self, "_swap_counters", {}))

    def _source_facts(self, number: str) -> dict:
        pairs, issues = self._presence_registry.observation(number)
        observations = []
        for pair in pairs:
            entry = self._source_entries[pair]
            observations.append(
                dict(
                    source_slug=pair[0],
                    department=pair[1],
                    source_version=entry.version_start,
                    observed_at=entry.observed_at,
                    last_seen_at=entry.observed_at,
                    state="present",
                    import_id=entry.import_id,
                )
            )
        return dict(
            source_observations=observations,
            source_state="present",
            source_snapshot_at=self._source_snapshot,
            is_tombstone=False,
            lifecycle_facts=dict(
                replacement_state="not_checked", graph_issues=[], date_issues=issues
            ),
            revision_evidence=dict(
                status="unverified",
                kind=None,
                revision_date=None,
                verified_at=None,
                evidence_sha256=None,
                official_source=None,
            ),
        )

    def _validate_transformed_facts(self, path: Path) -> None:
        """Reject malformed or forged lifecycle fields before PostgreSQL COPY."""
        self._ensure_presence()
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != OUTPUT_COLUMNS:
                raise ValueError("Invalid DPE lifecycle CSV projection")
            for row in reader:
                facts = self._source_facts(row["numero_dpe"])
                for field in (
                    "source_observations",
                    "lifecycle_facts",
                    "revision_evidence",
                ):
                    if json.loads(row[field]) != facts[field]:
                        raise ValueError("Invalid DPE lifecycle JSON")
                if (
                    any(
                        row[field] != facts[field]
                        for field in ("source_state", "source_snapshot_at")
                    )
                    or row["is_tombstone"] != "false"
                ):
                    raise ValueError("Invalid DPE lifecycle state")

    def _reconcile_staging(self, conn: psycopg.Connection) -> list[str]:
        """Exhaust reconciliation privately into ``_dpe_reconciled``.

        Reads the incoming rows of ``_dpe_incoming``; the logged candidate is
        written later, once (``_materialize_lifecycle_staging``). Returns the
        candidate columns, ``id`` excluded.
        """
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE _dpe_reconciled "
                "(LIKE diagnostics_dpe_staging INCLUDING ALL EXCLUDING INDEXES) "
                "ON COMMIT DROP"
            )
            cur.execute("SELECT * FROM diagnostics_dpe_staging LIMIT 0")
            columns = [column.name for column in cur.description if column.name != "id"]
        column_sql = ", ".join(columns)
        copy_sql = f"COPY _dpe_reconciled ({column_sql}) FROM STDIN"
        with (
            conn.cursor(name="dpe_previous", row_factory=dict_row) as previous,
            conn.cursor(name="dpe_incoming", row_factory=dict_row) as incoming,
            conn.cursor() as writer,
        ):
            previous.execute("SELECT * FROM diagnostics_dpe")
            incoming.execute("SELECT * FROM _dpe_incoming")
            reconciled = reconcile_observations(
                previous,
                incoming,
                self.import_manifest,
                registry=self._presence_registry,
            )
            # Fetch outside COPY: server cursor reads and COPY cannot overlap.
            while batch := list(islice(reconciled, 1000)):
                with writer.copy(copy_sql) as copy:
                    for row in batch:
                        copy.write_row(
                            [
                                Jsonb(row[column])
                                if column
                                in {
                                    "source_observations",
                                    "lifecycle_facts",
                                    "revision_evidence",
                                }
                                else row[column]
                                for column in columns
                            ]
                        )
        with conn.cursor() as cur:
            # Incoming rows are no longer needed; their files go at the commit of
            # phase A1 (04.1-REVIEW IN-01), so they still count in its peak.
            cur.execute("DROP TABLE _dpe_incoming")
        return columns

    @staticmethod
    def _materialize_lifecycle_staging(
        conn: psycopg.Connection, columns: list[str]
    ) -> None:
        """Write the candidate once: reconciled rows with their global facts.

        The graph is read whole (server cursor closed), then its facts are
        streamed into COPY: no dictionary of results (plan 04.1-11). The
        candidate is logged and published without rewrite (plan 04.1-04): an
        UPDATE of every row would leave half of it dead and double its WAL
        (plan 04.1-20), so the facts are joined at its only INSERT.
        """
        with LifecycleGraph() as graph:
            with conn.cursor(name="dpe_graph", row_factory=dict_row) as reader:
                reader.execute(
                    "SELECT numero_dpe, numero_dpe_remplace, lifecycle_facts "
                    "FROM _dpe_reconciled"
                )
                graph.index(reader)
            with conn.cursor() as writer:
                writer.execute(
                    "CREATE TEMP TABLE _dpe_graph_facts "
                    "(numero_dpe TEXT NOT NULL, lifecycle_facts JSONB NOT NULL) "
                    "ON COMMIT DROP"
                )
                with writer.copy("COPY _dpe_graph_facts FROM STDIN") as copy:
                    for number, lifecycle in graph.facts():
                        copy.write_row((number, Jsonb(lifecycle)))
        select_sql = ", ".join(
            "coalesce(facts.lifecycle_facts, reconciled.lifecycle_facts)"
            if column == "lifecycle_facts"
            else f"reconciled.{column}"
            for column in columns
        )
        with conn.cursor() as writer:
            writer.execute("ANALYZE _dpe_graph_facts")
            writer.execute(
                f"INSERT INTO diagnostics_dpe_staging ({', '.join(columns)}) "
                f"SELECT {select_sql} FROM _dpe_reconciled AS reconciled "
                "LEFT JOIN _dpe_graph_facts AS facts "
                "ON facts.numero_dpe = reconciled.numero_dpe"
            )

    @staticmethod
    def _reset_staging_load(cur: psycopg.Cursor) -> None:
        """Allow a retry after a failed phase A1 to reload the flat rows.

        Exact copies included. After A1 commits the flat table is gone: a
        later failure is retried by restaging the sources (plan 04.1-04).
        """
        constraints = cur.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass "
            "AND contype IN ('p', 'u')",
            ("diagnostics_dpe_staging",),
        ).fetchall()
        for (name,) in constraints:
            cur.execute(
                sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                    sql.Identifier("diagnostics_dpe_staging"), sql.Identifier(name)
                )
            )
        cur.execute("TRUNCATE diagnostics_dpe_staging")

    @property
    def dataset_name(self) -> str:
        return "dpe"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "diagnostics_dpe_staging"

    @property
    def production_table_name(self) -> str:
        return "diagnostics_dpe"

    @property
    def flat_staging_table_name(self) -> str:
        return "_dpe_staging_flat"

    @property
    def staging_is_partitioned(self) -> bool:
        return False

    @property
    def required_staging_constraints(self) -> tuple[str, ...]:
        return ("PRIMARY KEY (id)", "UNIQUE (numero_dpe)")

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def expected_file_count(self) -> int:
        return len(expected_pairs(self.department_scope))

    @property
    def department_scope(self) -> list[str]:
        """Hosted departements of the bound scope (migration 101).

        An overseas collectivity is never hosted, even when a scope written by
        code names it (D-123, ``hosting_for``): it is left out here, as every
        coverage rule leaves it out.
        """
        return [
            code
            for code in self.scope.departements
            if code in DEPARTEMENTS_HEBERGEABLES
        ]

    def check_scope_prerequisites(self, dsn: str) -> None:
        """Refuse, before any download, a scope without a hosted departement.

        Every hosted departement is accepted: the import manifest, the
        reconciliation and the quality checks read the pairs of the
        generation (``expected_pairs`` of this scope), never a fixed list.
        """
        if not self.department_scope:
            raise ScopePrerequisiteError(
                "DPE import needs at least one hostable departement; the scope "
                f"names {list(self.scope.departements)}."
            )

    @property
    def min_success_ratio(self) -> float:
        return 1.0

    @property
    def import_manifest(self) -> DpeImportManifest:
        manifest = getattr(self, "_import_manifest", None)
        if manifest is None:
            raise ValueError("DPE import has no validated manifest")
        return manifest

    @property
    def checkpoint_scope_key(self) -> str:
        self._prepare_generation()
        return self._generation_key(self._import_id)

    def _generation_key(self, import_id: str) -> str:
        return f"dpe:{','.join(self.department_scope)}:{MAPPING_VERSION}:{import_id}"

    def _prepare_generation(self) -> None:
        if hasattr(self, "_import_id"):
            return
        checkpoint = CheckpointManager("dpe", self.staging_table_name).load()
        path = get_download_dir("dpe") / "manifest.json"
        if checkpoint is not None and path.exists():
            try:
                manifest = DpeImportManifest.load(path)
                key = self._generation_key(manifest.import_id)
                names = {Path(entry.path).name for entry in manifest.files}
                if checkpoint.get("scope_key") == key and set(
                    checkpoint.get("loaded_files", [])
                ).issubset(names):
                    self._manifest_path = path
                    self._import_manifest = manifest
                    self._import_id = manifest.import_id
                    return
            except (ValueError, OSError, KeyError, TypeError):
                logger.warning("DPE cached generation failed validation; restarting")
        self._import_id = uuid4().hex

    # -- quality gate configuration -------------------------------------------

    @property
    def min_staging_rows(self) -> int:
        # Runner check disabled: total_rows reflects flat staging rows, NOT the
        # final partitioned count.  GATE-01 is enforced inside swap() instead
        # via min_staging_rows_final after the INSERT SELECT.
        return 0

    @property
    def min_staging_rows_final(self) -> int:
        # 80% of ~14M production rows — hard floor for the partitioned staging
        # table checked inside swap() after the flat -> partitioned INSERT SELECT.
        return self.scoped_min_rows(10_000_000)

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        # Partitioned staging is only populated inside swap(); runner cannot
        # check it before swap() is called.  Schema drift check runs inside
        # swap() via PipelineRunner._check_schema_drift_conn() instead.
        return True

    # -- stage methods ---------------------------------------------------------

    @_close_on_failure
    def download(self) -> list[Path]:
        """Collect complete official JSON pages and validate their CSV conversion."""
        paths = asyncio.run(self._async_download())
        self._ensure_presence()
        return paths

    async def _async_download(self) -> list[Path]:
        """Publish a private manifest only after every pair is verified."""
        self._prepare_generation()
        cached = getattr(self, "_import_manifest", None)
        if cached is not None:
            cached.validate()
            return [Path(entry.path) for entry in cached.files]
        directory = get_download_dir("dpe") / "raw" / self._import_id
        directory.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(settings.dpe_download_concurrency)
        departements = self.department_scope
        pairs = expected_pairs(departements)
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            reused = await self._reuse_pairs(client, directory, departements)

            async def collect(source: DpeSourceDescriptor, department: str):
                entry = reused.get((source.slug, department))
                if entry is not None:
                    return entry
                return await self._download_source(
                    client, sem, source, department, directory
                )

            # gather keeps the argument order: the manifest order is stable.
            files = await asyncio.gather(
                *(
                    collect(source, department)
                    for source in DPE_SOURCES.values()
                    for department in departements
                    if (source.slug, department) in pairs
                )
            )
        manifest = DpeImportManifest(
            self._import_id, str(directory.resolve()), tuple(files)
        )
        self._manifest_path = get_download_dir("dpe") / "manifest.json"
        manifest.save(self._manifest_path)
        self._import_manifest = manifest
        return [Path(entry.path) for entry in files]

    async def _download_source(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        source: DpeSourceDescriptor,
        department: str,
        directory: Path,
    ) -> DpeSourceFile:
        async with sem:
            tag = f"{self._import_id}-{source.slug}-{department}"
            start, metadata_start = await self._metadata(
                client, source, directory / f"{tag}-start.json", department
            )
            initial_total = await self._source_count(client, source, department)
            output = directory / f"{tag}.csv"
            pages, rows = await self._collect_pages(client, source, department, output)
            final_total = await self._source_count(client, source, department)
            end, metadata_end = await self._metadata(
                client, source, directory / f"{tag}-end.json", department
            )
            entry = DpeSourceFile(
                source_slug=source.slug,
                department=department,
                import_id=self._import_id,
                path=str(output.resolve()),
                sha256=sha256_file(output),
                endpoint=source.endpoint,
                filter=source.filter_for(department),
                columns=source.columns,
                mapping_version=source.mapping_version,
                version_start=start,
                version_end=end,
                initial_total=initial_total,
                final_total=final_total,
                rows=rows,
                pagination_complete=True,
                observed_at=datetime.now(UTC).isoformat(),
                metadata_start=metadata_start,
                metadata_end=metadata_end,
                pages=tuple(pages),
            )
            self._write_pair_receipt(directory, entry)
            return entry

    # -- reuse of the complete pairs of an unpublished import (04.2-03) ---------

    def _write_pair_receipt(self, directory: Path, entry: DpeSourceFile) -> None:
        """Record a complete pair, so that a later run of this scope may reuse it.

        Written only once the whole page chain is on disk; a pair cut by a
        failure has no receipt. A publication erases ``raw/`` with its receipts
        (``cleanup_download_dir``).
        """
        if entry.version_start != entry.version_end or not (
            entry.rows == entry.initial_total == entry.final_total
        ):
            return
        path = directory / f"{Path(entry.path).stem}{PAIR_RECEIPT_SUFFIX}"
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "receipt_version": 1,
                    "departements": sorted(self.department_scope),
                    "entry": asdict(entry),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    async def _reuse_pairs(
        self, client: httpx.AsyncClient, directory: Path, departements: list[str]
    ) -> dict[tuple[str, str], DpeSourceFile]:
        """Complete pairs of previous unpublished imports, bound to this import.

        A pair is reused only for the same departements, the same columns and
        mapping, the current source version, within the age bound, and after
        its CSV and every page proof are verified again under this import.
        """
        max_age = timedelta(hours=settings.dpe_download_reuse_max_age_hours)
        if max_age <= timedelta(0):
            return {}
        refused: Counter[str] = Counter()
        newest = self._reuse_candidates(directory, departements, max_age, refused)
        versions = {}
        for slug in sorted({pair[0] for pair in newest}):
            source = DPE_SOURCES[slug]
            response = await self._request_json(
                client, source.endpoint.removesuffix("/lines")
            )
            versions[slug] = source_version(decode_json(response.text), source)
        reused: dict[tuple[str, str], DpeSourceFile] = {}
        for pair, (_, origin, entry) in sorted(newest.items()):
            if entry.version_start != versions[pair[0]]:
                refused["version"] += 1
                continue
            linked = self._link_pair(entry, origin, directory)
            if linked is None:
                refused["invalid"] += 1
                continue
            reused[pair] = linked
        if refused:
            logger.warning(
                "DPE download reuse refused: %s",
                ", ".join(f"{reason}={n}" for reason, n in sorted(refused.items())),
            )
        for origin_id, count in sorted(
            Counter(entry.reused_from for entry in reused.values()).items()
        ):
            logger.info("DPE download reuse: %d pairs from import %s", count, origin_id)
        return reused

    @staticmethod
    def _reuse_candidates(
        directory: Path,
        departements: list[str],
        max_age: timedelta,
        refused: Counter[str],
    ) -> dict[tuple[str, str], tuple[datetime, Path, DpeSourceFile]]:
        """Newest acceptable receipt of each pair, in the sibling imports."""
        now = datetime.now(UTC)
        newest: dict[tuple[str, str], tuple[datetime, Path, DpeSourceFile]] = {}
        for receipt in sorted(directory.parent.glob(f"*/*{PAIR_RECEIPT_SUFFIX}")):
            if receipt.parent == directory:
                continue
            try:
                payload = json.loads(receipt.read_text(encoding="utf-8"))
                entry = DpeSourceFile.from_dict(payload["entry"])
                observed = datetime.fromisoformat(entry.observed_at)
                age = now - observed
            except (OSError, ValueError, KeyError, TypeError):
                refused["unreadable"] += 1
                continue
            reason = _receipt_refusal(payload, entry, departements)
            if reason is None and not timedelta(0) <= age <= max_age:
                reason = "age"
            if reason is not None:
                refused[reason] += 1
            elif entry.pair not in newest or observed > newest[entry.pair][0]:
                newest[entry.pair] = (observed, receipt.parent, entry)
        return newest

    def _link_pair(
        self, entry: DpeSourceFile, origin: Path, directory: Path
    ) -> DpeSourceFile | None:
        """Bind the files of a complete pair to this import, then verify them.

        Hard links (copy across file systems): the bytes stay those that were
        downloaded. Nothing ever writes to these names again; a pair refused
        here loses its links before it is downloaded under the same names.
        """
        tag = f"{self._import_id}-{entry.source_slug}-{entry.department}"
        created: list[Path] = []

        def link(artifact: DpeArtifact, name: str) -> str:
            source_path, target = origin / Path(artifact.path).name, directory / name
            if target.exists():
                raise ValueError("DPE reuse target already exists")
            try:
                os.link(source_path, target)
            except OSError:
                shutil.copyfile(source_path, target)
            created.append(target)
            return str(target.resolve())

        try:
            linked = replace(
                entry,
                import_id=self._import_id,
                path=link(DpeArtifact(entry.path, entry.sha256), f"{tag}.csv"),
                metadata_start=replace(
                    entry.metadata_start,
                    path=link(entry.metadata_start, f"{tag}-start.json"),
                ),
                metadata_end=replace(
                    entry.metadata_end, path=link(entry.metadata_end, f"{tag}-end.json")
                ),
                pages=tuple(
                    replace(page, path=link(page, f"{tag}-page-{number}.json"))
                    for number, page in enumerate(entry.pages, start=1)
                ),
                reused_from=entry.reused_from or entry.import_id,
            )
            linked.validate(self._import_id, directory)
        except (OSError, ValueError) as exc:
            for path in created:
                path.unlink(missing_ok=True)
            logger.warning(
                "DPE download reuse refused for %s %s: %s",
                entry.source_slug,
                entry.department,
                exc,
            )
            return None
        self._write_pair_receipt(directory, linked)
        return linked

    async def _metadata(
        self,
        client: httpx.AsyncClient,
        source: DpeSourceDescriptor,
        path: Path,
        department: str,
    ) -> tuple[str, DpeArtifact]:
        response = await self._request_json(
            client, source.endpoint.removesuffix("/lines"), department=department
        )
        version = source_version(decode_json(response.text), source)
        path.write_bytes(response.content)
        return version, DpeArtifact(str(path.resolve()), sha256_file(path))

    async def _source_count(
        self,
        client: httpx.AsyncClient,
        source: DpeSourceDescriptor,
        department: str,
    ) -> int:
        response = await self._request_json(
            client,
            source.endpoint,
            {
                "size": 1,
                "select": "numero_dpe",
                "qs": source.filter_for(department),
            },
            department=department,
        )
        total = decode_json(response.text).get("total")
        if type(total) is not int or total < 0:
            raise ValueError("Missing DPE source count")
        return total

    async def _collect_pages(
        self,
        client: httpx.AsyncClient,
        source: DpeSourceDescriptor,
        department: str,
        output: Path,
    ) -> tuple[list[DpePageProof], int]:
        pages, seen, after, count = [], set(), None, 0
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, quoting=csv.QUOTE_NOTNULL)
            writer.writerow(source.columns)
            while True:
                params = {
                    "size": CSV_PAGE_SIZE,
                    "select": ",".join(source.columns),
                    "qs": source.filter_for(department),
                    "sort": "numero_dpe",
                }
                if after is not None:
                    params["after"] = after
                response = await self._request_json(
                    client, source.endpoint, params, department=department
                )
                payload = decode_json(response.text)
                rows = payload.get("results")
                if not isinstance(rows, list):
                    raise ValueError("Missing DPE page results")  # noqa: TRY004
                next_after = page_cursor(payload.get("next"), source.endpoint)
                if next_after in seen or (next_after is not None and not rows):
                    raise ValueError("Repeated or empty DPE pagination page")
                path = output.with_name(f"{output.stem}-page-{len(pages) + 1}.json")
                path.write_bytes(response.content)
                pages.append(
                    DpePageProof(
                        str(path.resolve()),
                        sha256_file(path),
                        len(rows),
                        after,
                        next_after,
                    )
                )
                for row in rows:
                    validate_source_row(row, department)
                    writer.writerow([csv_value(row.get(key)) for key in source.columns])
                count += len(rows)
                if next_after is None:
                    return pages, count
                seen.add(next_after)
                after = next_after

    @retry(
        retry=retry_if_exception_type(
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                _RetryableHTTPError,
            )
        ),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=2, min=2, max=RETRY_MAX_WAIT_SECONDS),
        before_sleep=_log_request_retry,
        reraise=True,
    )
    async def _request_json(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict | None = None,
        *,
        department: str | None = None,
    ) -> httpx.Response:
        # The body is read inside client.get: a truncated body
        # (RemoteProtocolError) is retried like a failed connection.
        response = await client.get(endpoint, params=params)
        if response.status_code == 429 or response.status_code >= 500:
            raise _RetryableHTTPError("DPE source request failed", response.status_code)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Incomplete DPE HTTP response")
        return response

    @_close_on_failure
    def validate_schema(self, file_path: Path) -> None:
        """Validate that a department CSV contains expected columns."""
        self.import_manifest.resolve(file_path)
        with open(file_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=",")
            header = next(reader)

        actual = {col.strip() for col in header}
        expected = set(EXPECTED_COLUMNS)

        missing = expected - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "DPE CSV %s has %d extra columns",
                file_path.name,
                len(extra),
            )

        if missing:
            msg = (
                f"DPE schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    @_close_on_failure
    def transform(self, file_path: Path) -> Path:
        """Transform only a file whose exact provenance passed the manifest gate."""
        entry = self.import_manifest.resolve(file_path)
        self._ensure_presence()
        regime_dpe = DPE_SOURCES[entry.source_slug].regime
        transformed_dir = get_download_dir("dpe") / "transformed" / entry.import_id
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / f"{file_path.stem}-transformed.csv"

        row_count = 0
        with (
            open(file_path, encoding="utf-8-sig") as fin,
            open(output_path, "w", encoding="utf-8", newline="") as fout,
        ):
            reader = csv.DictReader(fin)
            writer = csv.writer(fout)
            writer.writerow(OUTPUT_COLUMNS)

            for row in reader:
                facts = self._source_facts(row["numero_dpe"])
                for field, issue in (
                    ("date_etablissement_dpe", "invalid_establishment_date"),
                    ("date_fin_validite_dpe", "invalid_end_date"),
                ):
                    if issue in facts["lifecycle_facts"]["date_issues"]:
                        row[field] = ""
                # A row without usable source coordinates is kept with empty
                # coordinates, hence a null position at swap (DATA-02): it stays
                # consultable by reference in its BAN context. The 58.2 baseline
                # found no such row with a proven exact address identity, so
                # never borrow BAN geometry (nor a neighbour's, nor a previous one).
                if self._invalid_geometry(row):
                    latitude = longitude = ""
                else:
                    longitude = str(float(row["coordonnee_cartographique_x_ban"]))
                    latitude = str(float(row["coordonnee_cartographique_y_ban"]))

                # Use code_departement_ban directly from API when available
                code_dept = row.get("code_departement_ban", "").strip()
                code_insee = row.get("code_insee_ban", "").strip()

                # Fallback: derive from code_insee if API field empty
                if not code_dept and code_insee:
                    code_dept = _derive_code_departement(code_insee)

                if not code_dept:
                    continue

                out_row = [
                    row.get("numero_dpe", "").strip(),
                    code_dept,
                    row.get("date_etablissement_dpe", "").strip(),
                    row.get("annee_construction", "").strip(),
                    row.get("periode_construction", "").strip(),
                    row.get("type_batiment", "").strip(),
                    row.get("surface_habitable_logement", "").strip(),
                    row.get("etiquette_dpe", "").strip(),
                    row.get("etiquette_ges", "").strip(),
                    row.get("type_installation_chauffage", "").strip(),
                    row.get("type_energie_principale_chauffage", "").strip(),
                    row.get("conso_5_usages_ep", "").strip(),
                    row.get("emission_ges_5_usages", "").strip(),
                    row.get("cout_total_5_usages", "").strip(),
                    row.get("qualite_isolation_enveloppe", "").strip(),
                    row.get("code_postal_ban", "").strip(),
                    code_insee,
                    row.get("nom_commune_ban", "").strip(),
                    latitude,
                    longitude,
                    # Phase 48 (Multi-logements honnete, Wave 0) -- appended at the
                    # END to match OUTPUT_COLUMNS' positional contract. No cleanup,
                    # no guessing: floor/complement normalization is the Python
                    # service's job (D-10, Plan 48-03).
                    row.get("numero_etage_appartement", "").strip(),
                    row.get("complement_adresse_logement", "").strip(),
                    row.get("identifiant_ban", "").strip(),
                    row.get("numero_dpe_remplace", "").strip(),
                    row.get("date_fin_validite_dpe", "").strip(),
                    # R4-01 (Plan A'') -- appended at the END (positional
                    # contract with OUTPUT_COLUMNS). Raw pass-through: casts
                    # happen in swap()'s INSERT SELECT.
                    row.get("id_rnb", "").strip(),
                    row.get("provenance_id_rnb", "").strip(),
                    row.get("score_ban", "").strip(),
                    # Phase 58 (D-02/D-16) -- appended at the END, same
                    # positional contract as R4-01 above. Raw pass-through,
                    # NO cleanup: casts happen in swap()'s INSERT SELECT.
                    # adresse_brut/adresse_complete_brut MAY contain literal
                    # `\n` (ADEME free text, Pitfall 7): .strip() only trims
                    # the edges, csv.writer (stdlib) quotes/escapes internal
                    # newlines correctly for a CSV round-trip. NEVER
                    # post-process this intermediate CSV with a line-based
                    # tool (grep/sed/readlines) -- it would split a single
                    # logical row across multiple physical lines.
                    row.get("adresse_brut", "").strip(),
                    row.get("adresse_complete_brut", "").strip(),
                    row.get("nom_commune_brut", "").strip(),
                    row.get("code_postal_brut", "").strip(),
                    row.get("adresse_ban", "").strip(),
                    row.get("numero_voie_ban", "").strip(),
                    row.get("nom_rue_ban", "").strip(),
                    row.get("statut_geocodage", "").strip(),
                    # regime_dpe (D-14): pipeline-computed above, not a CSV
                    # field -- see docstring.
                    regime_dpe,
                    json.dumps(facts["source_observations"]),
                    facts["source_state"],
                    facts["source_snapshot_at"],
                    "false",
                    json.dumps(facts["lifecycle_facts"]),
                    json.dumps(facts["revision_evidence"]),
                    # Plan 04-15 (C1 to C3) -- appended at the END (positional
                    # contract with OUTPUT_COLUMNS). Raw pass-through.
                    row.get("version_dpe", "").strip(),
                    row.get("date_derniere_modification_dpe", "").strip(),
                    row.get("date_reception_dpe", "").strip(),
                    row.get("date_visite_diagnostiqueur", "").strip(),
                    row.get("conso_5_usages_par_m2_ep", "").strip(),
                    row.get("emission_ges_5_usages_par_m2", "").strip(),
                    row.get("numero_dpe_immeuble_associe", "").strip(),
                    row.get("numero_immatriculation_copropriete", "").strip(),
                ]
                writer.writerow(out_row)
                row_count += 1

        logger.info("Transformed %s: %d rows", file_path.name, row_count)
        return output_path

    @_close_on_failure
    def stage(self, file_path: Path) -> int:
        """Bulk-load a transformed CSV into _dpe_staging_flat via COPY.

        adresse_brut/adresse_complete_brut may carry a literal `\\n` (ADEME
        free text, Pitfall 7): this method streams the transformed CSV to
        Postgres COPY byte-for-byte (`for line in f: copy.write(...)`),
        never re-parsing or re-joining lines -- COPY's own CSV parser
        reconstructs the quoted multiline field, exactly like csv.writer
        (stdlib) encoded it in transform(). Do NOT insert a line-based
        pre-processing step (grep/sed/readlines) between transform() and
        this COPY: it would split a single logical row.

        CREATE TABLE IF NOT EXISTS is safe here ONLY because
        BasePipeline.create_staging_table() (called as runner Stage 0,
        before download/transform/stage, on every non-resume run) already
        drops this flat staging table with CASCADE first (Pitfall 6): an
        IF NOT EXISTS with no upstream drop would silently keep a
        previous run's stale 28-column schema and the COPY below would
        succeed against the WRONG (shifted) column set.
        """
        self._validate_transformed_facts(file_path)
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Unlogged: rebuilt from the sources after a crash, never
                # published (04.1-04).
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _dpe_staging_flat (
                        numero_dpe TEXT,
                        code_departement VARCHAR(3),
                        date_etablissement_dpe TEXT,
                        annee_construction TEXT,
                        periode_construction TEXT,
                        type_batiment TEXT,
                        surface_habitable TEXT,
                        etiquette_dpe TEXT,
                        etiquette_ges TEXT,
                        type_installation_chauffage TEXT,
                        type_energie_principale_chauffage TEXT,
                        conso_5_usages_ep TEXT,
                        emission_ges_5_usages TEXT,
                        cout_total_5_usages TEXT,
                        qualite_isolation_enveloppe TEXT,
                        code_postal TEXT,
                        code_insee TEXT,
                        nom_commune TEXT,
                        latitude TEXT,
                        longitude TEXT,
                        numero_etage_appartement TEXT,
                        complement_adresse_logement TEXT,
                        identifiant_ban TEXT,
                        numero_dpe_remplace TEXT,
                        date_fin_validite_dpe TEXT,
                        id_rnb TEXT,
                        provenance_id_rnb TEXT,
                        score_ban TEXT,
                        adresse_brut TEXT,
                        adresse_complete_brut TEXT,
                        nom_commune_brut TEXT,
                        code_postal_brut TEXT,
                        adresse_ban TEXT,
                        numero_voie_ban TEXT,
                        nom_rue_ban TEXT,
                        statut_geocodage TEXT,
                        regime_dpe TEXT,
                        source_observations JSONB NOT NULL,
                        source_state TEXT NOT NULL,
                        source_snapshot_at TIMESTAMPTZ NOT NULL,
                        is_tombstone BOOLEAN NOT NULL,
                        lifecycle_facts JSONB NOT NULL,
                        revision_evidence JSONB NOT NULL,
                        version_dpe TEXT,
                        date_derniere_modification_dpe TEXT,
                        date_reception_dpe TEXT,
                        date_visite_diagnostiqueur TEXT,
                        conso_5_usages_par_m2_ep TEXT,
                        emission_ges_5_usages_par_m2 TEXT,
                        numero_dpe_immeuble_associe TEXT,
                        numero_immatriculation_copropriete TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _dpe_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _dpe_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _dpe_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info(
            "Staged %s: flat table now has %d total rows",
            file_path.name,
            row_count,
        )
        return row_count

    @_close_after
    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A1: INSERT SELECT flat->staging + reconciliation + safety checks
                  + DROP of the flat table, one transaction.
        Phase A2: CREATE INDEX on the candidate, own transaction: the flat
                  table is already gone, so both never share the disk (04.1-04).
        Phase B: Atomic swap via swap_tables() — logged guard + DROP old +
                 two RENAMEs in one WAL-atomic commit.
        Phase C: Post-swap cleanup — idempotent flat drop, rename sequence,
                 register _old (dropped after publication).

        Interrupted between A1 and A2, the flat table is missing: the runner's
        resume rebuilds the candidate from the sources (a second swap() in
        the same process has no flat rows left to reload).
        """
        self.import_manifest.validate()
        self._ensure_presence()
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "diagnostics_dpe"
        old = f"{prod}_old"

        # --- Phase A1: transfer, then free the flat table ---------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()
                self._reset_staging_load(cur)

                # Temporary, hence unlogged: the logged candidate receives
                # its rows once, after reconciliation and lifecycle (04.1-20).
                cur.execute(
                    "CREATE TEMP TABLE _dpe_incoming "
                    "(LIKE diagnostics_dpe_staging INCLUDING ALL EXCLUDING INDEXES) "
                    "ON COMMIT DROP"
                )
                # Source X/Y -> WGS84 (EPSG:4326). Flat "longitude"/"latitude"
                # are coordonnee_cartographique_x/y_ban, in the projection of
                # the departement's territory (SOURCE_SRID_SQL).
                cur.execute(f"""
                    INSERT INTO _dpe_incoming
                        (numero_dpe, code_departement,
                         date_etablissement_dpe, annee_construction,
                         periode_construction,
                         type_batiment, surface_habitable,
                         etiquette_dpe, etiquette_ges,
                         type_installation_chauffage,
                         type_energie_principale_chauffage,
                         conso_5_usages_ep, emission_ges_5_usages,
                         cout_total_5_usages,
                         qualite_isolation_enveloppe,
                         code_postal, code_insee, nom_commune,
                         numero_etage_appartement, complement_adresse_logement,
                         identifiant_ban, numero_dpe_remplace,
                         date_fin_validite_dpe,
                         id_rnb, provenance_id_rnb, score_ban,
                         adresse_brut, adresse_complete_brut, nom_commune_brut,
                         code_postal_brut, adresse_ban, numero_voie_ban,
                         nom_rue_ban, statut_geocodage, regime_dpe,
                         longitude, latitude, position,
                         source_observations, source_state, source_snapshot_at,
                         is_tombstone, lifecycle_facts, revision_evidence,
                         version_dpe, date_derniere_modification_dpe,
                         date_reception_dpe, date_visite_diagnostiqueur,
                         conso_5_usages_par_m2_ep, emission_ges_5_usages_par_m2,
                         numero_dpe_immeuble_associe,
                         numero_immatriculation_copropriete)
                    SELECT
                        numero_dpe, code_departement,
                        NULLIF(date_etablissement_dpe, '')::date,
                        NULLIF(annee_construction, '')::integer,
                        NULLIF(periode_construction, ''),
                        NULLIF(type_batiment, ''),
                        -- PostgreSQL LEAST ignores NULL arguments: guard
                        -- missing values before applying a numeric bound.
                        CASE WHEN NULLIF(surface_habitable, '') IS NOT NULL
                        THEN LEAST(
                            surface_habitable::numeric,
                            999999.99
                        ) END::numeric(8, 2),
                        LEFT(NULLIF(etiquette_dpe, ''), 1),
                        LEFT(NULLIF(etiquette_ges, ''), 1),
                        NULLIF(type_installation_chauffage, ''),
                        NULLIF(type_energie_principale_chauffage, ''),
                        CASE WHEN NULLIF(conso_5_usages_ep, '') IS NOT NULL
                        THEN LEAST(
                            conso_5_usages_ep::numeric,
                            99999999.99
                        ) END::numeric(10, 2),
                        CASE WHEN NULLIF(emission_ges_5_usages, '') IS NOT NULL
                        THEN LEAST(
                            emission_ges_5_usages::numeric,
                            99999999.99
                        ) END::numeric(10, 2),
                        CASE WHEN NULLIF(cout_total_5_usages, '') IS NOT NULL
                        THEN LEAST(
                            cout_total_5_usages::numeric,
                            9999999999.99
                        ) END::numeric(12, 2),
                        NULLIF(qualite_isolation_enveloppe, ''),
                        LEFT(NULLIF(code_postal, ''), 5),
                        LEFT(NULLIF(code_insee, ''), 5),
                        NULLIF(nom_commune, ''),
                        NULLIF(numero_etage_appartement, '')::integer,
                        NULLIF(complement_adresse_logement, ''),
                        LEFT(NULLIF(identifiant_ban, ''), 50),
                        NULLIF(numero_dpe_remplace, ''),
                        NULLIF(date_fin_validite_dpe, '')::date,
                        LEFT(NULLIF(id_rnb, ''), 32),
                        NULLIF(provenance_id_rnb, ''),
                        NULLIF(score_ban, '')::numeric,
                        -- Phase 58 (D-02/D-16/D-14): all TEXT, no length bound
                        -- identified by RESEARCH -- NULLIF only, no LEFT().
                        -- code_postal_brut is NEVER cast ::integer (Pitfall 8:
                        -- ADEME types it integer, which drops the leading
                        -- zero of postal codes below 10000).
                        NULLIF(adresse_brut, ''),
                        NULLIF(adresse_complete_brut, ''),
                        NULLIF(nom_commune_brut, ''),
                        NULLIF(code_postal_brut, ''),
                        NULLIF(adresse_ban, ''),
                        NULLIF(numero_voie_ban, ''),
                        NULLIF(nom_rue_ban, ''),
                        NULLIF(statut_geocodage, ''),
                        NULLIF(regime_dpe, ''),
                        ST_X(wgs84_pt),
                        ST_Y(wgs84_pt),
                        wgs84_pt,
                        source_observations, source_state, source_snapshot_at,
                        is_tombstone, lifecycle_facts, revision_evidence,
                        -- Plan 04-15 (C1, C3): kept as published, no cast that
                        -- could fail the swap or lose a value (a malformed
                        -- source date stays visible as such).
                        NULLIF(version_dpe, ''),
                        NULLIF(date_derniere_modification_dpe, ''),
                        NULLIF(date_reception_dpe, ''),
                        NULLIF(date_visite_diagnostiqueur, ''),
                        -- C2: exact numeric, neither bounded nor rounded.
                        NULLIF(conso_5_usages_par_m2_ep, '')::numeric,
                        NULLIF(emission_ges_5_usages_par_m2, '')::numeric,
                        NULLIF(numero_dpe_immeuble_associe, ''),
                        NULLIF(numero_immatriculation_copropriete, '')
                    FROM (
                        -- No source coordinates: null position, row kept
                        -- (DATA-02). Never a BAN or neighbour geometry.
                        SELECT *,
                            CASE WHEN NULLIF(longitude, '') IS NOT NULL
                                  AND NULLIF(latitude, '') IS NOT NULL
                            THEN ST_Transform(
                                ST_SetSRID(
                                    ST_MakePoint(
                                        longitude::double precision,
                                        latitude::double precision
                                    ),
                                    {SOURCE_SRID_SQL}
                                ),
                                4326
                            ) END AS wgs84_pt
                        FROM _dpe_staging_flat
                    ) sub
                """)

                columns = self._reconcile_staging(conn)
                self._materialize_lifecycle_staging(conn, columns)
                # Plan 04.1-13: orphans named by their exact raw address,
                # on the candidate only, one commune at a time.
                self._swap_counters = rattacher_candidate(conn)
                cur.execute("SELECT count(*) FROM diagnostics_dpe_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, "diagnostics_dpe")

                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                # GATE-01: enforce absolute row count floor on the final
                # production-shaped staging table (flat-staging pitfall: runner's
                # total_rows reflects flat rows, not this count).
                final_min = self.min_staging_rows_final
                if final_min > 0 and staging_count < final_min:
                    msg = (
                        f"GATE-01 row count failed: staging has {staging_count:,} rows "
                        f"(minimum {final_min:,} required) — swap aborted"
                    )
                    raise RuntimeError(msg)
                if final_min > 0:
                    # Log the pass explicitly: a silent floor was mistaken
                    # for an absent one during the 48-10 prod run (GATE-B').
                    logger.info(
                        "GATE-01 final floor passed: %d rows >= %d minimum",
                        staging_count,
                        final_min,
                    )

                cur.execute("DROP TABLE IF EXISTS _dpe_staging_flat CASCADE")

            conn.commit()  # A1 commits: candidate loaded, flat table freed

        # --- Phase A2: indexes on the candidate alone --------------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)
            conn.commit()  # Phase A2 commits before Phase B opens

        # GATE-02: schema drift check on final staging (after INSERT SELECT
        # and before Phase B rename).  Raises RuntimeError if abort mode is set.
        PipelineRunner._check_schema_drift_conn(self, dsn, logger)

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _dpe_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed sequence")

    @staticmethod
    def _create_staging_indexes(cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        """Create production-equivalent indexes on the staging table."""
        # The planar GiST index on position left with migration 107 (registry
        # of plan 04.1-23: in no plan of the 57 measured statements); the
        # geography one below stays.
        indexes = [
            (
                "idx_dpe_staging_position_geog",
                "CREATE INDEX idx_dpe_staging_position_geog "
                "ON diagnostics_dpe_staging USING GIST (geography(position))",
            ),
            (
                "idx_dpe_staging_code_insee",
                "CREATE INDEX idx_dpe_staging_code_insee "
                "ON diagnostics_dpe_staging (code_insee)",
            ),
            (
                "idx_dpe_staging_code_departement",
                "CREATE INDEX idx_dpe_staging_code_departement "
                "ON diagnostics_dpe_staging (code_departement)",
            ),
            (
                "idx_dpe_staging_etiquette_dpe",
                "CREATE INDEX idx_dpe_staging_etiquette_dpe "
                "ON diagnostics_dpe_staging (etiquette_dpe)",
            ),
            (
                "idx_dpe_staging_date_etablissement",
                "CREATE INDEX idx_dpe_staging_date_etablissement "
                "ON diagnostics_dpe_staging (date_etablissement_dpe)",
            ),
            # The one below comes from a MIGRATION, not from this pipeline's
            # original design, and that is exactly why it was missing:
            # this list is the real definition of what survives a swap, and a
            # migration that adds an index to diagnostics_dpe without adding it
            # HERE is undone by the next refresh. Discovered 2026-08-05 --
            # idx_dpe_identifiant_ban (migration 077) was already gone from
            # production, so the primary DPE attachment path had been running
            # unindexed since the following refresh, with `alembic current`
            # still reporting head. Nothing detects it: the swap renames a
            # staging table over the production one, indexes and all.
            (
                "idx_dpe_staging_identifiant_ban",
                "CREATE INDEX idx_dpe_staging_identifiant_ban "
                "ON diagnostics_dpe_staging (identifiant_ban)",
            ),
            # An index on ``id_rnb`` sat here from 2026-08-05 to 2026-08-06,
            # rebuilt for the Lot C2 building-proof path. Both are gone
            # (migration 082): no query reads that column any more, and
            # rebuilding a ~261 k-entry index on every refresh to serve nobody
            # is a recurring cost plus a false signal that the column is on a
            # hot path. ``id_rnb`` itself STAYS -- the ETL still fills it
            # (migration 078, corroboration data), it is simply not indexed.
            # Migration 104 (plan 04.1-13): the BAN key an orphan names by its
            # exact raw address, looked up by address; partial (mostly NULL).
            (
                "idx_dpe_staging_ban_id_adresse_brute",
                "CREATE INDEX idx_dpe_staging_ban_id_adresse_brute "
                "ON diagnostics_dpe_staging (ban_id_adresse_brute) "
                "WHERE ban_id_adresse_brute IS NOT NULL",
            ),
        ]

        for name, ddl in indexes:
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(ddl)


def _derive_code_departement(code_insee: str) -> str:
    """Derive department code from INSEE commune code (fallback)."""
    if not code_insee or len(code_insee) < 2:
        return ""
    if code_insee.startswith("97") and len(code_insee) >= 3:
        return code_insee[:3]
    if code_insee.startswith("2A"):
        return "2A"
    if code_insee.startswith("2B"):
        return "2B"
    if code_insee.startswith("20") and len(code_insee) >= 5:
        commune_num = int(code_insee[2:5])
        return "2A" if commune_num < 200 else "2B"
    return code_insee[:2]


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(DpePipeline)

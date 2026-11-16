"""RNB BAN<->building links concrete ETL pipeline (Lot B).

Downloads one zipped CSV export per department from the RNB open-data bucket,
validates the header, streams it into a COPY-ready CSV of (building, BAN key)
pairs, bulk-loads into a flat staging table, INSERT SELECTs into the
production-shaped staging table, and swaps atomically.

Feeds ``app.logements.queries.cles_ban_batiment_rnb``, which since Lot C1
reads THIS table instead of calling the RNB API live. This pipeline changes
NO attachment rule: it only fills the table that rule reads.
"""

import asyncio
import datetime as dt
import logging
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, verified_download
from app.etl.registry import register_pipeline
from app.etl.runner import PipelineRunner
from app.etl_rnb_liens.config import (
    DOWNLOAD_TIMEOUT,
    EXPECTED_COLUMNS,
    MIN_PAIRS_PER_DEPARTEMENT,
    OUTPUT_COLUMNS,
    RNB_EXPORT_URL_TEMPLATE,
)
from app.etl_rnb_liens.transform import transform_rnb_export, validate_export_header

logger = logging.getLogger(__name__)


@register_pipeline("rnb_liens")
class RnbLiensPipeline(BasePipeline):
    """Concrete ETL pipeline for the RNB link table (~2M pairs on 44+85)."""

    def __init__(self) -> None:
        super().__init__()
        # Upstream publication date per department, read from the export's
        # HTTP Last-Modified. Empty when the header is absent -- the millesime
        # column then stays NULL rather than carrying an import timestamp.
        self._millesime_by_dept: dict[str, str] = {}

    # -- abstract properties ---------------------------------------------------

    @property
    def dataset_name(self) -> str:
        return "rnb_liens"

    @property
    def scope_dataset_keys(self) -> tuple[str, ...]:
        # The RNB links table is published under the catalogue key "rnb".
        return ("rnb",)

    @property
    def expected_columns(self) -> list[str]:
        # Header validation itself is delegated to validate_export_header,
        # which must open the CSV inside the zip.
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "rnb_liens_staging"

    @property
    def production_table_name(self) -> str:
        return "rnb_liens"

    @property
    def flat_staging_table_name(self) -> str:
        return "_rnb_liens_staging_flat"

    # -- scope -----------------------------------------------------------------

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def has_department_partitions(self) -> bool:
        # Departement coverage is still checked (supports_department_scope):
        # every configured departement must be present in staging.
        return False

    @property
    def staging_is_partitioned(self) -> bool:
        return False

    @property
    def expected_file_count(self) -> int:
        return len(self.department_scope)

    @property
    def min_success_ratio(self) -> float:
        # Two files, both mandatory: a partial refresh would silently drop a
        # whole department's links.
        return 1.0

    # -- quality gate configuration -------------------------------------------

    @property
    def min_staging_rows(self) -> int:
        # Runner check disabled: total_rows reflects flat staging rows, NOT the
        # final table count. GATE-01 is enforced inside swap() via
        # min_staging_rows_final after the INSERT SELECT.
        return 0

    @property
    def min_staging_rows_final(self) -> int:
        return MIN_PAIRS_PER_DEPARTEMENT * len(self.department_scope)

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        # Production-shaped staging is only populated inside swap().
        return True

    # -- provenance (PROV-03) --------------------------------------------------

    @property
    def source_updated_at(self) -> dt.datetime | None:
        """Newest export publication date across downloaded departments.

        UTC-anchored: the source gives a date, and ``dataset_metadata`` stores a
        timestamptz -- a naive value would be reinterpreted in the session
        timezone and could drift the published millesime by a day.
        """
        dates = [
            dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC)
            for value in self._millesime_by_dept.values()
            if value
        ]
        return max(dates) if dates else None

    @property
    def source_period_label(self) -> str | None:
        updated = self.source_updated_at
        return f"Export RNB {updated:%Y-%m}" if updated else None

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download one zipped CSV export per department."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        raw_dir = get_download_dir("rnb_liens") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        departements = self.department_scope
        self._millesime_by_dept = {}

        async def _download_one(client: httpx.AsyncClient, dept: str) -> Path | None:
            url = RNB_EXPORT_URL_TEMPLATE.format(dept=dept)
            output_path = raw_dir / f"RNB_{dept}.csv.zip"
            self._millesime_by_dept[dept] = await self._read_millesime(client, url)
            try:
                ok = await verified_download(client, url, output_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RNB export %s failed after retries: %s", dept, exc)
                return None
            if not ok:
                logger.warning("No RNB export for %s (404)", dept)
                return None
            logger.info(
                "Downloaded RNB export %s (%.0f MB, millesime %s)",
                dept,
                output_path.stat().st_size / 1e6,
                self._millesime_by_dept[dept] or "inconnu",
            )
            return output_path

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            results = await asyncio.gather(
                *[_download_one(client, dept) for dept in departements]
            )

        paths = [path for path in results if path is not None]
        if len(paths) != len(departements):
            logger.warning(
                "RNB export download: %d/%d succeeded",
                len(paths),
                len(departements),
            )
        return paths

    @staticmethod
    async def _read_millesime(client: httpx.AsyncClient, url: str) -> str:
        """Read the export's publication date from HTTP Last-Modified.

        Returns ``""`` when the header is missing or unparseable: an unknown
        millesime stays NULL and is never replaced by the import time (PROV-03).
        """
        try:
            response = await client.head(url, follow_redirects=True)
            header = response.headers.get("last-modified")
            if not header:
                return ""
            return parsedate_to_datetime(header).date().isoformat()
        except (httpx.HTTPError, TypeError, ValueError):
            logger.warning("Could not read Last-Modified for %s", url)
            return ""

    def validate_schema(self, file_path: Path) -> None:
        """Validate the zipped CSV header contains the ingested columns."""
        validate_export_header(file_path)

    def transform(self, file_path: Path) -> Path:
        """Stream the export into a COPY-ready CSV of (building, BAN key) rows."""
        dept_code = self._dept_from_path(file_path)
        transformed_dir = get_download_dir("rnb_liens") / "transformed"
        output_path = transformed_dir / f"rnb-liens-{dept_code}.csv"

        row_count = transform_rnb_export(
            file_path,
            output_path,
            dept_code,
            self._millesime_by_dept.get(dept_code, ""),
        )
        logger.info("Transformed %s: %d rows", file_path.name, row_count)
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load a transformed CSV into _rnb_liens_staging_flat via COPY."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Idempotent create — create_staging_table() already dropped any
                # stale flat table before the per-file loop started.
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _rnb_liens_staging_flat (
                        id_rnb TEXT,
                        cle_ban TEXT,
                        code_departement VARCHAR(3),
                        statut TEXT,
                        parcelles TEXT,
                        millesime TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _rnb_liens_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _rnb_liens_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _rnb_liens_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info(
            "Staged %s: %d rows loaded (flat table total: %d)",
            file_path.name,
            row_count,
            count_after,
        )
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, index, swap.

        Phase A: INSERT SELECT + index builds (own transaction).
        Phase B: atomic swap via swap_tables().
        Phase C: post-swap cleanup — drop flat staging, rename indexes/sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "rnb_liens"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                # DISTINCT ON: a building may appear once per source file when
                # a future scope overlaps, and (id_rnb, cle_ban) is UNIQUE on
                # production — dedup here rather than failing the constraint.
                cur.execute("""
                    INSERT INTO rnb_liens_staging
                        (id_rnb, cle_ban, code_departement, statut,
                         parcelles, millesime)
                    SELECT DISTINCT ON (id_rnb, cle_ban)
                        id_rnb,
                        cle_ban,
                        code_departement,
                        NULLIF(statut, ''),
                        parcelles::text[],
                        NULLIF(millesime, '')::date
                    FROM _rnb_liens_staging_flat
                    WHERE id_rnb IS NOT NULL AND id_rnb != ''
                      AND cle_ban IS NOT NULL AND cle_ban != ''
                    ORDER BY id_rnb, cle_ban, code_departement
                """)

                cur.execute("SELECT count(*) FROM rnb_liens_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, prod)

                # Safety: abort if staging has <50% of production rows
                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                # GATE-01: absolute floor on the final staging table (the
                # runner's total_rows reflects flat rows, not this count).
                final_min = self.min_staging_rows_final
                if final_min > 0 and staging_count < final_min:
                    msg = (
                        f"GATE-01 row count failed: staging has {staging_count:,} rows "
                        f"(minimum {final_min:,} required) — swap aborted"
                    )
                    raise RuntimeError(msg)
                if final_min > 0:
                    logger.info(
                        "GATE-01 final floor passed: %d rows >= %d minimum",
                        staging_count,
                        final_min,
                    )

            conn.commit()  # Phase A commits before Phase B opens

        logger.info("Swap ready: %d rows (was %d)", staging_count, old_count)

        # GATE-02: schema drift check on staging (after INSERT SELECT, before
        # the Phase B rename). Raises RuntimeError if abort mode is set.
        PipelineRunner._check_schema_drift_conn(self, dsn, logger)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _rnb_liens_staging_flat CASCADE")

                # Index renames are NOT done here: swap_tables() already ran
                # rename_indexes_after_swap() inside the swap transaction
                # (DB-HYGIENE-01). Repeating them here dropped the live
                # production indexes, since the prod-named index at this point
                # is the freshly renamed one, not the leftover on rnb_liens_old.

                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
            conn.commit()

        # --- Phase D: ANALYZE, mandatory for the read path ------------------
        # The freshly renamed table carries NO statistics. Measured 2026-08-05
        # on a 1,5 M-row copy: without them the planner puts ``porteurs`` on
        # the hash side and the widening query degenerates into a Parallel Seq
        # Scan -- 52 ms and 23 176 buffers per call, against 0,14 ms and 22
        # buffers once analyzed. That query is on the hot path of the public
        # /v1/bien/logements endpoint (Lot C1), so the window between the swap
        # and the autovacuum daemon's own pass is a real latency cliff.
        #
        # Non-fatal on purpose: the data IS live and correct at this point:
        # the swap succeeded. A failed ANALYZE degrades latency until
        # autovacuum catches up, it does not warrant failing the run.
        try:
            with psycopg.connect(dsn) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    self.set_statement_timeout(cur)
                    cur.execute(f"ANALYZE {prod}")
            logger.info("ANALYZE complete on %s", prod)
        except psycopg.Error as exc:
            logger.warning(
                "ANALYZE on %s failed, autovacuum will catch up: %s", prod, exc
            )

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed sequence")

    @staticmethod
    def _dept_from_path(file_path: Path) -> str:
        """Extract the department code from ``RNB_44.csv.zip``."""
        return file_path.name.split(".", 1)[0].split("_", 1)[1]

    @staticmethod
    def _create_staging_indexes(cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        """Create the production-equivalent indexes on the staging table.

        The UNIQUE (id_rnb, cle_ban) natural key is not created here: it is a
        constraint, restored by ``ensure_staging_constraints`` just before the
        swap (base class contract).
        """
        indexes = [
            (
                "idx_rnb_liens_staging_cle_ban",
                "CREATE INDEX idx_rnb_liens_staging_cle_ban "
                "ON rnb_liens_staging (cle_ban)",
            ),
            (
                "idx_rnb_liens_staging_parcelles",
                "CREATE INDEX idx_rnb_liens_staging_parcelles "
                "ON rnb_liens_staging USING GIN (parcelles)",
            ),
        ]
        for name, ddl in indexes:
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(ddl)

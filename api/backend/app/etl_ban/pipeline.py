"""BAN (Base Adresse Nationale) concrete ETL pipeline.

Downloads 102 departement CSV files from adresse.data.gouv.fr,
validates the schema, transforms via pandas, bulk-loads via COPY
into a flat staging table, INSERT SELECTs with ST_MakePoint into
the partitioned staging table, and swaps atomically.
"""

import asyncio
import csv
import logging
import time
from pathlib import Path

import httpx
import psycopg

from app.etl.base import MILLESIME_DATE, BasePipeline, check_millesime_label
from app.etl.download import get_download_dir, verified_download_gz
from app.etl.runner import PipelineRunner
from app.etl_ban.config import (
    BAN_CSV_SEPARATOR,
    BAN_OUTPUT_COLUMNS,
    DOWNLOAD_TIMEOUT,
    EXPECTED_BAN_COLUMNS,
    OPTIONAL_BAN_COLUMNS,
    ban_base_url,
)
from app.etl_ban.transform import transform_ban_csv

logger = logging.getLogger(__name__)

# Above this share of 404s, treat the absences as a source outage rather than
# legitimate gaps, and keep the full success-ratio denominator so the gate
# aborts an incomplete refresh.
_MAX_NOT_FOUND_RATIO = 0.4


class BanPipeline(BasePipeline):
    """Concrete ETL pipeline for the BAN dataset (~26M addresses)."""

    # Weekly dated folders next to `latest` (plan 02-11, D-27).
    millesime_mechanism = "segment_date"

    def validate_millesime(self, label: str) -> None:
        check_millesime_label(self.dataset_name, label, MILLESIME_DATE)

    def __init__(self) -> None:
        super().__init__()
        # 404s seen during download(): departments with no BAN file at the
        # source.  Excluded from the success-ratio denominator.
        self._not_found_count: int = 0

    @property
    def dataset_name(self) -> str:
        return "ban"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_BAN_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "adresses_staging"

    @property
    def production_table_name(self) -> str:
        return "adresses"

    @property
    def flat_staging_table_name(self) -> str:
        return "_ban_staging_flat"

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def expected_file_count(self) -> int:
        """Files the success ratio divides by (read by the runner after download).

        A department absent from the source returns 404 and produces no file.
        Those legitimate gaps are subtracted so a complete refresh is not
        failed by files that cannot exist.  A 5xx/timeout raises (not a 404)
        and stays counted, so the gate still catches a real outage.

        Guard: if 404s exceed _MAX_NOT_FOUND_RATIO of the theoretical max, the
        source is likely down — keep the full denominator so the gate aborts.
        """
        theoretical = len(self.expected_department_coverage)
        if self._not_found_count > theoretical * _MAX_NOT_FOUND_RATIO:
            return theoretical
        return theoretical - self._not_found_count

    # -- quality gate configuration -------------------------------------------

    @property
    def min_staging_rows(self) -> int:
        # Runner check disabled: total_rows reflects flat staging rows, NOT the
        # final partitioned count.  GATE-01 is enforced inside swap() instead
        # via min_staging_rows_final after the INSERT SELECT.
        return 0

    @property
    def min_staging_rows_final(self) -> int:
        # 80% of ~26M production addresses — hard floor for the partitioned
        # staging table checked inside swap() after the flat -> partitioned
        # INSERT SELECT.
        return self.scoped_min_rows(20_000_000)

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        # Partitioned staging is only populated inside swap(); runner cannot
        # check it before swap() is called.  Schema drift check runs inside
        # swap() via PipelineRunner._check_schema_drift_conn() instead.
        return True

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download 102 departement CSV files via streaming with retry."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        download_dir = get_download_dir("ban")
        raw_dir = download_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        # Departements configured in base, so the download matrix matches
        # expected_file_count.
        departements = self.expected_department_coverage
        total = len(departements)
        self._not_found_count = 0  # reset for idempotent re-runs
        base_url = ban_base_url(self.pinned_millesime)

        async def _download_one(
            client: httpx.AsyncClient, dept: str, index: int
        ) -> Path | None:
            output_path = raw_dir / f"adresses-{dept}.csv"
            url = f"{base_url}/adresses-{dept}.csv.gz"
            try:
                ok = await verified_download_gz(client, url, output_path)
                if ok:
                    logger.info("Downloaded %s (%d/%d)", dept, index, total)
                    return output_path
                # 404: no BAN file for this department at the source.  Counted
                # separately so it is excluded from the success-ratio denominator.
                self._not_found_count += 1
                logger.warning("No BAN file for %s (404) (%d/%d)", dept, index, total)
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Dept %s failed after retries: %s (%d/%d)",
                    dept,
                    exc,
                    index,
                    total,
                )
                return None

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            results = await asyncio.gather(
                *[
                    _download_one(client, dept, i)
                    for i, dept in enumerate(departements, 1)
                ]
            )

        paths = [p for p in results if p is not None]
        # Real failures = missing files that were NOT legitimate 404s.
        failed_count = len(results) - len(paths) - self._not_found_count
        if failed_count or self._not_found_count:
            logger.warning(
                "BAN download: %d/%d succeeded, %d failed, %d not found (404)",
                len(paths),
                total,
                failed_count,
                self._not_found_count,
            )

        return paths

    def validate_schema(self, file_path: Path) -> None:
        """Validate BAN CSV header matches expected columns."""
        with open(file_path, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=BAN_CSV_SEPARATOR)
            header = next(reader)

        actual = {col.strip().lower() for col in header}
        expected = {col.lower() for col in EXPECTED_BAN_COLUMNS}
        optional = {col.lower() for col in OPTIONAL_BAN_COLUMNS}

        required = expected - optional
        missing = required - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "BAN CSV %s has extra columns: %s",
                file_path.name,
                sorted(extra),
            )
        if missing:
            msg = (
                f"BAN schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform a raw BAN CSV into a clean CSV for COPY."""
        stem = file_path.stem
        dept_code = stem.split("-", 1)[1]

        download_dir = get_download_dir("ban")
        transformed_dir = download_dir / "transformed"
        output_path = transformed_dir / f"adresses-{dept_code}-transformed.csv"

        row_count = transform_ban_csv(file_path, output_path, dept_code)
        logger.info("Transformed %s: %d rows", file_path.name, row_count)
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load a transformed CSV into _ban_staging_flat via COPY."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Idempotent create — base.create_staging_table() already dropped
                # any stale flat table before the per-file loop started.
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _ban_staging_flat (
                        code_departement VARCHAR(3) NOT NULL,
                        ban_id TEXT,
                        numero VARCHAR(10),
                        rep VARCHAR(10),
                        nom_voie TEXT,
                        code_postal VARCHAR(5),
                        code_commune VARCHAR(5),
                        nom_commune TEXT,
                        lon TEXT,
                        lat TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _ban_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(BAN_OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _ban_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _ban_staging_flat")
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
        """Transfer flat staging -> partitioned staging, rebuild indexes, swap.

        Phase A: INSERT SELECT flat->partitioned + CREATE INDEX (own transaction).
        Phase B: Atomic swap via swap_tables() — logged guard, DROP old, two RENAMEs.
        Phase C: Post-swap cleanup — drop flat staging, rename sequence + partitions.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "adresses"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                logger.info("Transferring flat staging to partitioned staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO adresses_staging
                        (code_departement, ban_id, numero, rep,
                         nom_voie, code_postal, code_commune,
                         nom_commune, position)
                    SELECT
                        code_departement, ban_id, numero, rep,
                        nom_voie, code_postal, code_commune,
                        nom_commune,
                        ST_SetSRID(
                            ST_MakePoint(
                                lon::double precision,
                                lat::double precision
                            ),
                            4326
                        )
                    FROM _ban_staging_flat
                    WHERE lon IS NOT NULL AND lon != ''
                      AND lat IS NOT NULL AND lat != ''
                """)

                cur.execute("SELECT count(*) FROM adresses_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Partitioned staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, "adresses")

                # Safety: abort if staging has <50% of production rows
                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                # GATE-01: enforce absolute row count floor on the final
                # partitioned staging table (flat-staging pitfall: runner's
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

            conn.commit()  # Phase A commits before Phase B opens

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # GATE-02: schema drift check on partitioned staging (after INSERT SELECT
        # and before Phase B rename).  Raises RuntimeError if abort mode is set.
        PipelineRunner._check_schema_drift_conn(self, dsn, logger)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                # Drop flat staging
                cur.execute("DROP TABLE IF EXISTS _ban_staging_flat CASCADE")

                # Index renames are NOT done here: swap_tables() already ran
                # rename_indexes_after_swap() inside the swap transaction
                # (DB-HYGIENE-01). Repeating them here dropped the live
                # production indexes, since the prod-named index at this point
                # is the freshly renamed one, not the leftover on adresses_old.

                # Rename staging sequence to production name (IF EXISTS guards
                # make this safe even if no sequence exists on this staging table)
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                self.rename_partitions_after_swap(cur)
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed sequence")

    def validate_data(self, dsn: str) -> list[str]:
        """Check INSEE code quality on BAN staging table."""
        from app.etl.validators import check_insee_codes

        return check_insee_codes(dsn, self.staging_table_name, col="code_commune")

    @staticmethod
    def _create_staging_indexes(cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        indexes = [
            (
                "idx_adresses_staging_position",
                "CREATE INDEX idx_adresses_staging_position "
                "ON adresses_staging USING GIST (position)",
            ),
            (
                "idx_adresses_staging_position_geog",
                "CREATE INDEX idx_adresses_staging_position_geog "
                "ON adresses_staging USING GIST (geography(position))",
            ),
            (
                "idx_adresses_staging_ban_id",
                "CREATE INDEX idx_adresses_staging_ban_id ON adresses_staging (ban_id)",
            ),
            (
                "idx_adresses_staging_search_trgm",
                "CREATE INDEX idx_adresses_staging_search_trgm "
                "ON adresses_staging USING GIN (search_text gin_trgm_ops)",
            ),
            (
                "idx_adresses_staging_search_fts",
                "CREATE INDEX idx_adresses_staging_search_fts "
                "ON adresses_staging USING GIN "
                "(to_tsvector('french', search_text))",
            ),
            (
                "idx_adresses_staging_code_postal",
                "CREATE INDEX idx_adresses_staging_code_postal "
                "ON adresses_staging (code_postal)",
            ),
            (
                "idx_adresses_staging_code_commune",
                "CREATE INDEX idx_adresses_staging_code_commune "
                "ON adresses_staging (code_commune)",
            ),
        ]
        for name, ddl in indexes:
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(ddl)


from app.etl.registry import registry  # noqa: E402

registry.register(BanPipeline)

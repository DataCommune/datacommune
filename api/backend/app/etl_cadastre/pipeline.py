"""Cadastre (parcelles) concrete ETL pipeline.

Downloads 102 departement GeoJSON.gz files from cadastre.data.gouv.fr,
validates the schema, loads via ogr2ogr into a flat staging table,
INSERT SELECTs with departement derivation into the partitioned staging
table, and swaps atomically.
"""

import asyncio
import gzip
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx
import psycopg

from app.etl.base import (
    MILLESIME_DATE,
    BasePipeline,
    check_millesime_label,
    split_dsn_credentials,
)
from app.etl.download import get_download_dir, verified_download
from app.etl_cadastre.config import (
    DOWNLOAD_TIMEOUT,
    EXPECTED_PROPERTIES,
    MAX_RETRIES,
    cadastre_base_url,
)

logger = logging.getLogger(__name__)

# Maximum bytes to read when validating schema (1 MB)
_VALIDATE_BUFFER_SIZE = 1_048_576

# Above this share of 404s, treat the absences as a source outage rather than
# legitimate gaps, and keep the full success-ratio denominator so the gate
# aborts an incomplete refresh.
_MAX_NOT_FOUND_RATIO = 0.4

# Departement of a flat staging row: overseas communes carry three digits.
_COMMUNE_DEPARTEMENT_SQL = (
    "CASE WHEN LEFT(commune, 2) = '97' THEN LEFT(commune, 3) ELSE LEFT(commune, 2) END"
)


class CadastrePipeline(BasePipeline):
    """Concrete ETL pipeline for the cadastre dataset (~94M parcelles)."""

    # Quarterly dated folders next to `latest` (plan 02-11, D-27).
    millesime_mechanism = "segment_date"

    def validate_millesime(self, label: str) -> None:
        check_millesime_label(self.dataset_name, label, MILLESIME_DATE)

    def __init__(self) -> None:
        super().__init__()
        # 404s seen during download(): departments with no cadastre file at
        # the source.  Excluded from the success-ratio denominator.
        self._not_found_count: int = 0

    # -- abstract properties ---------------------------------------------------

    @property
    def dataset_name(self) -> str:
        return "cadastre"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_PROPERTIES

    @property
    def staging_table_name(self) -> str:
        return "parcelles_staging"

    @property
    def production_table_name(self) -> str:
        return "parcelles"

    @property
    def flat_staging_table_name(self) -> str:
        return "_cadastre_staging_flat"

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def coverage_departement_sql(self) -> dict[str, str]:
        # The flat table only has `commune`; code_departement is derived in swap().
        return {self.flat_staging_table_name: _COMMUNE_DEPARTEMENT_SQL}

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

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~88M production parcelles rows
        return self.scoped_min_rows(70_000_000)

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download 102 departement GeoJSON.gz files from cadastre.data.gouv.fr.

        Uses asyncio internally for concurrent HTTP requests with retry
        and exponential backoff on 5xx errors and timeouts.
        Files are kept gzip-compressed (ogr2ogr reads /vsigzip/ directly).
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        raw_dir = get_download_dir("cadastre") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        # Departements configured in base, so the download matrix matches
        # expected_file_count.
        departements = self.expected_department_coverage

        paths: list[Path] = []
        failed_depts: list[str] = []
        self._not_found_count = 0  # reset for idempotent re-runs
        total = len(departements)
        base_url = cadastre_base_url(self.pinned_millesime)

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            for i, dept in enumerate(departements, 1):
                output_path = raw_dir / f"cadastre-{dept}-parcelles.json.gz"
                url = f"{base_url}/{dept}/cadastre-{dept}-parcelles.json.gz"

                try:
                    # Save gzip as-is (no decompression -- ogr2ogr reads /vsigzip/)
                    ok = await verified_download(
                        client, url, output_path, max_retries=MAX_RETRIES
                    )
                    if ok:
                        paths.append(output_path)
                    else:
                        # 404: no cadastre file for this department at the
                        # source.  Excluded from the success-ratio denominator.
                        self._not_found_count += 1
                        logger.warning("No cadastre file for %s (404)", dept)
                except Exception as exc:  # noqa: BLE001
                    failed_depts.append(dept)
                    logger.warning("Dept %s failed after retries: %s", dept, exc)

                if i % 10 == 0:
                    logger.info(
                        "Download progress: %d/%d departments (%d failed)",
                        i,
                        total,
                        len(failed_depts),
                    )

        if failed_depts or self._not_found_count:
            logger.warning(
                "Cadastre download: %d/%d succeeded, %d failed, %d not found (404): %s",
                len(paths),
                total,
                len(failed_depts),
                self._not_found_count,
                failed_depts[:10],
            )
        else:
            logger.info("Downloaded %d cadastre files total", len(paths))

        return paths

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the GeoJSON contains expected property keys.

        Reads the first 1 MB of the gzipped file, extracts the first
        ``"properties"`` block, and checks that all expected keys are
        present.  Tolerant on extras, strict on missing required keys.
        """
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            buffer = f.read(_VALIDATE_BUFFER_SIZE)

        # Find the first "properties" block
        marker = '"properties"'
        pos = buffer.find(marker)
        if pos == -1:
            msg = f"No 'properties' found in first 1MB of {file_path.name}"
            raise ValueError(msg)

        # Find the opening brace of the properties object
        brace_start = buffer.find("{", pos + len(marker))
        if brace_start == -1:
            msg = f"Malformed properties block in {file_path.name}"
            raise ValueError(msg)

        # Find the matching closing brace (simple nesting)
        depth = 0
        brace_end = brace_start
        for i in range(brace_start, len(buffer)):
            if buffer[i] == "{":
                depth += 1
            elif buffer[i] == "}":
                depth -= 1
                if depth == 0:
                    brace_end = i
                    break

        props_json = buffer[brace_start : brace_end + 1]
        props = json.loads(props_json)
        actual_keys = set(props.keys())
        expected_keys = set(EXPECTED_PROPERTIES)

        extra = actual_keys - expected_keys
        if extra:
            logger.warning(
                "Cadastre GeoJSON %s has extra properties: %s",
                file_path.name,
                sorted(extra),
            )

        missing = expected_keys - actual_keys
        if missing:
            msg = (
                f"Cadastre schema mismatch in {file_path.name}: "
                f"missing required properties {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """No-op: ogr2ogr handles GeoJSON directly.

        Returns the input path unchanged.
        """
        return file_path

    def _build_ogr_cmd(self, source: str, dsn: str) -> tuple[list[str], dict[str, str]]:
        """Build the ogr2ogr command for *source* into the flat staging table.

        Returns (command, extra_env). The DSN password is stripped from
        argv and carried via PGPASSWORD instead (A-02: argv is readable
        in /proc/<pid>/cmdline for the whole load).
        """
        safe_dsn, pg_env = split_dsn_credentials(dsn)
        ogr_cmd = [
            "ogr2ogr",
            "-f",
            "PostgreSQL",
            f"PG:{safe_dsn}",
            source,
            "-nln",
            "_cadastre_staging_flat",
            "-nlt",
            "MULTIPOLYGON",
            "-a_srs",
            "EPSG:4326",
            "-lco",
            "GEOMETRY_NAME=geometrie",
            "--config",
            "PG_USE_COPY",
            "YES",
            "-gt",
            "65536",
            "-append",
        ]
        return ogr_cmd, pg_env

    def stage(self, file_path: Path) -> int:
        """Bulk-load a GeoJSON.gz into _cadastre_staging_flat via ogr2ogr.

        Uses ogr2ogr with /vsigzip/ to read gzip-compressed GeoJSON
        directly into PostgreSQL.  The flat table has no partitioning
        for maximum loading throughput.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent). Unlogged: rebuilt
                # from the sources after a crash, never published (04.1-04).
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _cadastre_staging_flat (
                        id VARCHAR(14),
                        commune VARCHAR(5),
                        prefixe VARCHAR(3),
                        section VARCHAR(2),
                        numero VARCHAR(4),
                        contenance INTEGER,
                        arpente BOOLEAN DEFAULT FALSE,
                        created DATE,
                        updated DATE,
                        geometrie GEOMETRY(MultiPolygon, 4326)
                    )
                """)
            conn.commit()

        # Count before ogr2ogr to compute delta
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM _cadastre_staging_flat")
            count_before: int = cur.fetchone()[0]  # type: ignore[index]

        # Build ogr2ogr command -- /vsigzip/ lets ogr2ogr read .gz natively.
        # Credentials go to PGPASSWORD env, never argv (A-02).
        file_posix = str(file_path).replace("\\", "/")
        ogr_cmd, pg_env = self._build_ogr_cmd(f"/vsigzip/{file_posix}", dsn)

        try:
            result = subprocess.run(
                ogr_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=1800,  # 30 min timeout per file
                env={**os.environ, **pg_env},
            )
            if result.stderr:
                logger.debug("ogr2ogr stderr: %s", result.stderr)
        except FileNotFoundError as exc:
            msg = (
                "ogr2ogr not found. Install GDAL: "
                "apt-get install gdal-bin (Debian/Ubuntu)"
            )
            raise RuntimeError(msg) from exc
        except subprocess.CalledProcessError as exc:
            msg = f"ogr2ogr failed for {file_path.name}: {exc.stderr}"
            raise RuntimeError(msg) from exc

        # Return row delta (not cumulative count)
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM _cadastre_staging_flat")
            count_after: int = cur.fetchone()[0]  # type: ignore[index]

        row_count = count_after - count_before
        logger.info(
            "Staged %s: %d rows added (flat table total: %d)",
            file_path.name,
            row_count,
            count_after,
        )
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> partitioned staging, rebuild indexes, swap.

        Phase A1: INSERT SELECT flat->partitioned + safety check + DROP of the
                  flat table, one transaction.
        Phase A2: CREATE INDEX on the candidate, own transaction: the flat
                  table is already gone, so both never share the disk (04.1-04).
        Phase B: Atomic swap via swap_tables() — logged guard + DROP old +
                 two RENAMEs in one WAL-atomic commit.
        Phase C: Post-swap cleanup — idempotent flat drop, rename sequence,
                 rename partitions, register _old (dropped after publication).

        Interrupted between A1 and A2, the flat table is missing: the runner's
        resume rebuilds the candidate from the sources.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "parcelles"
        old = f"{prod}_old"

        # --- Phase A1: transfer, then free the flat table ---------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                logger.info("Transferring flat staging to partitioned staging")
                t0 = time.perf_counter()

                # Identify parcelles whose geometry degenerates to empty after
                # ST_MakeValid (D-14 traceability). True auto-intersections are
                # repaired by MakeValid; only truly degenerate shapes are excluded.
                log = logger  # short alias used in per-row warnings below
                _extract = "ST_CollectionExtract(ST_MakeValid(geometrie), 3)"
                cur.execute(f"""
                    SELECT id
                    FROM _cadastre_staging_flat
                    WHERE geometrie IS NOT NULL
                      AND (
                          {_extract} IS NULL
                          OR ST_IsEmpty({_extract})
                      )
                """)
                degenerate_rows = cur.fetchall()
                if degenerate_rows:
                    for row in degenerate_rows:
                        log.warning("parcelle skipped id=%s: empty_makevalid", row[0])
                    log.warning(
                        "Total degenerate parcelles excluded: %d",
                        len(degenerate_rows),
                    )

                cur.execute(f"""
                    INSERT INTO parcelles_staging
                        (code_departement, id_parcelle, commune, prefixe,
                         section, numero, contenance, arpente,
                         created, updated, geometrie)
                    SELECT
                        {_COMMUNE_DEPARTEMENT_SQL} AS code_departement,
                        id AS id_parcelle,
                        commune, prefixe, section, numero,
                        contenance, arpente,
                        created::date, updated::date,
                        {_extract} AS geometrie
                    FROM _cadastre_staging_flat
                    WHERE geometrie IS NULL
                       OR (
                           {_extract} IS NOT NULL
                           AND NOT ST_IsEmpty({_extract})
                       )
                """)

                cur.execute("SELECT count(*) FROM parcelles_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Partitioned staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, "parcelles")

                # Safety: abort if staging has <50% of production rows
                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                cur.execute("DROP TABLE IF EXISTS _cadastre_staging_flat CASCADE")

            conn.commit()  # A1 commits: candidate loaded, flat table freed

        # --- Phase A2: indexes on the candidate alone --------------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)
            conn.commit()  # Phase A2 commits before Phase B opens

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _cadastre_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                self.rename_partitions_after_swap(cur)
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed partitions")

    def validate_data(self, dsn: str) -> list[str]:
        """Check INSEE code quality on cadastre staging table."""
        from app.etl.validators import check_insee_codes

        return check_insee_codes(dsn, self.staging_table_name, col="commune")

    @staticmethod
    def _create_staging_indexes(
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        """Create production-equivalent indexes on the staging table."""
        indexes = [
            (
                "idx_parcelles_staging_geometrie",
                "CREATE INDEX idx_parcelles_staging_geometrie "
                "ON parcelles_staging USING GIST (geometrie)",
            ),
            (
                "idx_parcelles_staging_id_parcelle",
                "CREATE INDEX idx_parcelles_staging_id_parcelle "
                "ON parcelles_staging (id_parcelle)",
            ),
            (
                "idx_parcelles_staging_commune",
                "CREATE INDEX idx_parcelles_staging_commune "
                "ON parcelles_staging (commune)",
            ),
        ]

        for name, ddl in indexes:
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(ddl)


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(CadastrePipeline)

"""Zonage ABC concrete ETL pipeline.

Downloads the commune-level ABC zoning CSV from data.gouv.fr,
validates the schema, transforms via pandas (column mapping, zone
extraction), bulk-loads via COPY into a flat staging table,
and swaps atomically.

Data source: Ministere de la Transition Ecologique.
"""

import asyncio
import logging
import time
from pathlib import Path

import httpx
import pandas as pd
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl_zonage_abc.config import (
    CSV_SEPARATOR,
    DATE_ARRETE,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    EXPECTED_COLUMNS,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


class ZonageAbcPipeline(BasePipeline):
    """Concrete ETL pipeline for Zonage ABC (~35k rows)."""

    # -- abstract properties ---------------------------------------------------

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 28_000

    @property
    def dataset_name(self) -> str:
        return "zonage_abc"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "zonage_abc_staging"

    @property
    def production_table_name(self) -> str:
        return "zonage_abc"

    @property
    def flat_staging_table_name(self) -> str:
        return "_zonage_abc_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_zonage_abc_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~35k production zonage_abc (commune-level) rows
        return self.scoped_min_rows(self.national_min_rows)

    # -- override: non-partitioned staging (small dataset) --------------------

    def create_staging_table(self, dsn: str) -> None:
        """Create non-partitioned staging table."""
        staging = self.staging_table_name
        production = self.production_table_name

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
                # Flat table too, as the base: stage() appends to it, and
                # a run failed before swap() would publish its rows again.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
                # EXCLUDING INDEXES (DB-HYGIENE-01): INCLUDING ALL copies
                # every production index under an auto-generated name, which
                # then survives the swap and gets re-copied on the next run.
                cur.execute(
                    f"CREATE TABLE {staging} "
                    f"(LIKE {production} INCLUDING ALL EXCLUDING INDEXES)"
                )
            conn.commit()

    def drop_staging_table(self, dsn: str) -> None:
        """Drop the non-partitioned staging table."""
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # Flat table too, as the base: a run failed before swap() would
                # otherwise hand its flat rows to the next one.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
            conn.commit()

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download the ABC zoning CSV from data.gouv.fr."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        raw_dir = get_download_dir("zonage_abc") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        output_path = raw_dir / "zonage_abc.csv"
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            success = await streaming_download(
                client, DOWNLOAD_URL, output_path, max_retries=MAX_RETRIES
            )
            if success:
                logger.info("Downloaded zonage_abc.csv")
                return [output_path]
            else:
                logger.error("Failed to download zonage_abc.csv")
                return []

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the CSV header contains expected columns.

        The zone column name changes with each decree, so we only
        check the stable columns (CODGEO, DEP, LIBGEO).
        """
        with open(file_path, encoding="utf-8", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        expected = set(EXPECTED_COLUMNS)

        missing = expected - actual
        if missing:
            msg = (
                f"Zonage ABC schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform the raw Zonage ABC CSV into a clean CSV for COPY.

        - Read with pandas (semicolon separator)
        - Identify the zone column (4th column, name varies by decree)
        - Identify the reclassement column (5th column)
        - Apply column mapping
        - Write cleaned CSV
        """
        transformed_dir = get_download_dir("zonage_abc") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        output_path = transformed_dir / "zonage_abc-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8",
        )

        # The 4th column is the zone (name includes decree date)
        zone_col = [c for c in df.columns if c not in {"CODGEO", "DEP", "LIBGEO"}]
        if len(zone_col) >= 1:
            # First unknown column is the zone
            df = df.rename(columns={zone_col[0]: "zone"})
        if len(zone_col) >= 2:
            # Second unknown column is reclassement
            df = df.rename(columns={zone_col[1]: "reclassement_raw"})
            df["reclassement"] = (
                df["reclassement_raw"]
                .str.strip()
                .str.lower()
                .map({"oui": "true", "non": "false"})
                .fillna("false")
            )
        else:
            df["reclassement"] = "false"

        # Apply standard column mapping
        df = df.rename(
            columns={
                "CODGEO": "code_insee",
                "DEP": "code_departement",
                "LIBGEO": "nom_commune",
            }
        )

        # Clean zone values
        df["zone"] = df["zone"].str.strip()

        # Filter rows with missing code_insee or zone
        df = df[df["code_insee"].notna() & (df["code_insee"].str.strip() != "")]
        df = df[df["zone"].notna() & (df["zone"].str.strip() != "")]

        # Select output columns
        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed zonage_abc: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV into _zonage_abc_staging_flat via COPY."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _zonage_abc_staging_flat (
                        code_insee TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        zone TEXT,
                        reclassement TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _zonage_abc_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _zonage_abc_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _zonage_abc_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged zonage_abc: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "zonage_abc"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS zonage_abc_staging CASCADE")
                cur.execute("DROP SEQUENCE IF EXISTS zonage_abc_staging_id_seq CASCADE")
                cur.execute("""
                    CREATE TABLE zonage_abc_staging
                    (LIKE zonage_abc INCLUDING ALL EXCLUDING INDEXES)
                """)
                # LIKE may not copy sequence binding for SERIAL id
                cur.execute("""
                    CREATE SEQUENCE zonage_abc_staging_id_seq
                """)
                cur.execute("""
                    ALTER TABLE zonage_abc_staging
                    ALTER COLUMN id
                    SET DEFAULT nextval('zonage_abc_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute(f"""
                    INSERT INTO zonage_abc_staging
                        (code_insee, nom_commune, code_departement,
                         zone, reclassement, date_arrete)
                    SELECT DISTINCT
                        NULLIF(code_insee, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(zone, ''),
                        CASE WHEN reclassement = 'true' THEN TRUE ELSE FALSE END,
                        '{DATE_ARRETE}'::date
                    FROM _zonage_abc_staging_flat
                    WHERE NULLIF(code_insee, '') IS NOT NULL
                      AND NULLIF(zone, '') IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM zonage_abc_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "zonage_abc")

                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

            conn.commit()  # Phase A commits before Phase B opens

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _zonage_abc_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden — default True,
                # but zonage_abc has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 026) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_zonage_abc_code_insee", "(code_insee)"),
        ("idx_zonage_abc_zone", "(zone)"),
        ("idx_zonage_abc_code_departement", "(code_departement)"),
    )

    def _create_staging_indexes(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        """Create production-equivalent indexes on the staging table."""
        staging = self.staging_table_name
        for canonical, spec in self.STAGING_INDEX_SPECS:
            name = self.staging_index_name(canonical)
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(f"CREATE INDEX {name} ON {staging} {spec}")


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(ZonageAbcPipeline)

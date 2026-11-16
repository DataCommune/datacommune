"""Monuments Historiques concrete ETL pipeline.

Downloads the full Merimee CSV published on data.gouv.fr ("|"-separated,
mixed-case column names), validates the schema, transforms (column mapping,
coordinate extraction, filtering), bulk-loads
via COPY into a flat staging table, INSERT SELECTs with ST_MakePoint
into the production-like staging table, and swaps atomically.
"""

import asyncio
import csv
import logging
import time
from pathlib import Path

import httpx
import pandas as pd
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl.registry import register_pipeline
from app.etl.runner import PipelineRunner
from app.etl_monuments.config import (
    COLUMN_MAPPING,
    CSV_SEPARATOR,
    EXPECTED_COLUMNS,
    MONUMENTS_CSV_URL,
    OUTPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


@register_pipeline("monuments")
class MonumentsPipeline(BasePipeline):
    """Concrete ETL pipeline for Monuments Historiques (~45k rows)."""

    # -- abstract properties ---------------------------------------------------

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 30_000

    @property
    def dataset_name(self) -> str:
        return "monuments"

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "monuments_historiques_staging"

    @property
    def production_table_name(self) -> str:
        return "monuments_historiques"

    @property
    def flat_staging_table_name(self) -> str:
        return "_monuments_staging_flat"

    # -- quality gate configuration -------------------------------------------

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_monuments_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # Runner check disabled: total_rows reflects flat staging rows, NOT the
        # final partitioned count.  GATE-01 is enforced inside swap() instead
        # via min_staging_rows_final after the INSERT SELECT.
        return 0

    @property
    def min_staging_rows_final(self) -> int:
        # 80% of ~45k production monuments — hard floor for the staging
        # table checked inside swap() after the flat -> staging INSERT SELECT.
        return self.scoped_min_rows(self.national_min_rows)

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        # Staging table is only populated inside swap(); runner cannot
        # check it before swap() is called.  Schema drift check runs inside
        # swap() via PipelineRunner._check_schema_drift_conn() instead.
        return True

    # -- override: non-partitioned staging ------------------------------------

    def create_staging_table(self, dsn: str) -> None:
        """Fresh import: drop flat rows left by a failed run, create the candidate.

        swap() recreates only the candidate (``_create_candidate``): the flat
        table then holds this run's rows. stage() appends to the flat table, so
        a fresh run must not inherit the rows of a run that failed before swap()
        (monuments run 62 published every monument twice, 2026-10-29).
        """
        self.drop_flat_staging(dsn)
        self._create_candidate(dsn)

    def _create_candidate(self, dsn: str) -> None:
        """Create non-partitioned staging table (small dataset, ~45k rows)."""
        staging = self.staging_table_name
        production = self.production_table_name

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {staging}_id_seq CASCADE")
                # EXCLUDING INDEXES (DB-HYGIENE-01): INCLUDING ALL copies
                # every production index under an auto-generated name, which
                # then survives the swap and gets re-copied on the next run.
                cur.execute(
                    f"CREATE TABLE {staging} "
                    f"(LIKE {production} INCLUDING ALL EXCLUDING INDEXES)"
                )
                # LIKE does not copy sequence binding for SERIAL/BIGSERIAL
                cur.execute(f"CREATE SEQUENCE {staging}_id_seq")
                cur.execute(
                    f"ALTER TABLE {staging} "
                    f"ALTER COLUMN id SET DEFAULT nextval('{staging}_id_seq')"
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
        """Download the Merimee CSV published on data.gouv.fr.

        Single CSV download via httpx. Returns [path].
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        raw_dir = get_download_dir("monuments") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        output_path = raw_dir / "merimee.csv"
        timeout = httpx.Timeout(60.0, read=300.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            ok = await streaming_download(client, MONUMENTS_CSV_URL, output_path)
            if not ok:
                msg = "Monuments CSV not found (404)"
                raise RuntimeError(msg)

        logger.info("Downloaded monuments CSV: %s", output_path.name)
        return [output_path]

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the CSV header contains expected columns.

        "|"-separated; names compared lower-cased (the producer publishes
        them in mixed case). Tolerates extras, rejects missing.
        """
        with open(file_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=CSV_SEPARATOR)
            header = next(reader)

        actual = {col.strip().lower() for col in header}
        expected = set(EXPECTED_COLUMNS)

        missing = expected - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "Monuments CSV %s has %d extra columns",
                file_path.name,
                len(extra),
            )

        if missing:
            msg = (
                f"Monuments schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform raw Monuments CSV into a clean CSV for COPY.

        - Read with pandas ("|" separator), lower-case the column names
        - Parse coordonnees_au_format_wgs84 ("lat,lon" format)
        - Filter rows without coordinates
        - Rename columns via COLUMN_MAPPING
        - Write cleaned CSV
        """
        transformed_dir = get_download_dir("monuments") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        output_path = transformed_dir / "monuments-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            quotechar='"',
            dtype=str,
            encoding="utf-8-sig",
        )
        df.columns = [col.strip().lower() for col in df.columns]

        # Filter rows without coordinates
        total_before = len(df)
        df = df[
            df["coordonnees_au_format_wgs84"].notna()
            & (df["coordonnees_au_format_wgs84"].str.strip() != "")
        ]
        skipped = total_before - len(df)
        logger.info(
            "Filtered %d monuments without coordinates (kept %d)",
            skipped,
            len(df),
        )

        # Parse coordonnees_au_format_wgs84: "lat, lon" format
        coords = df["coordonnees_au_format_wgs84"].str.split(",", n=1, expand=True)
        df = df.copy()
        df["latitude"] = pd.to_numeric(coords[0].str.strip(), errors="coerce")
        df["longitude"] = pd.to_numeric(coords[1].str.strip(), errors="coerce")

        # Drop rows where coordinate parsing failed
        before_parse = len(df)
        df = df.dropna(subset=["latitude", "longitude"])
        if len(df) < before_parse:
            logger.warning(
                "Dropped %d rows with unparseable coordinates",
                before_parse - len(df),
            )

        # Rename columns via mapping
        df = df.rename(columns=COLUMN_MAPPING)

        # Clean multi-valued INSEE codes: keep only first 5-digit code
        if "code_insee" in df.columns:
            df["code_insee"] = df["code_insee"].str.extract(r"(\d{5})", expand=False)

        # Multi-valued departements ("12;81": a monument on two departements)
        # keep their first code, the departement of the first INSEE code kept
        # above. Not derived from code_insee: Corsican INSEE codes (2A004)
        # fail the 5-digit extraction, and 977xx/978xx protected before 2007
        # carry 971 in the producer file. 97\d before \d{2} keeps 975/977
        # whole, for the scope filter to remove; an unrecognised value keeps
        # its first 3 characters (varchar(3)) and is removed the same way.
        if "code_departement" in df.columns:
            raw = df["code_departement"].str.strip()
            first = raw.str.upper().str.extract(r"^(2[AB]|97\d|\d{2})", expand=False)
            df["code_departement"] = first.fillna(raw.str[:3])

        # Ensure all output columns exist
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Write output
        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed monuments: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV into _monuments_staging_flat via COPY.

        Flat staging table with TEXT columns (no geometry) for maximum
        COPY throughput. Geometry creation happens during swap.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent)
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _monuments_staging_flat (
                        reference TEXT,
                        denomination TEXT,
                        adresse TEXT,
                        commune TEXT,
                        code_insee TEXT,
                        code_departement TEXT,
                        protection TEXT,
                        date_protection TEXT,
                        latitude TEXT,
                        longitude TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _monuments_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _monuments_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _monuments_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info(
            "Staged %s: flat table now has %d total rows",
            file_path.name,
            row_count,
        )
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "monuments_historiques"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        # Create non-partitioned staging table first
        self._create_candidate(dsn)

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO monuments_historiques_staging
                        (reference, denomination, adresse, commune,
                         code_insee, code_departement, protection,
                         date_protection, latitude, longitude, position)
                    SELECT
                        NULLIF(reference, ''),
                        NULLIF(denomination, ''),
                        NULLIF(adresse, ''),
                        NULLIF(commune, ''),
                        NULLIF(code_insee, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(protection, ''),
                        NULLIF(date_protection, ''),
                        latitude::double precision,
                        longitude::double precision,
                        ST_SetSRID(
                            ST_MakePoint(
                                longitude::double precision,
                                latitude::double precision
                            ),
                            4326
                        )
                    FROM _monuments_staging_flat
                    WHERE latitude IS NOT NULL AND latitude != ''
                      AND longitude IS NOT NULL AND longitude != ''
                """)

                cur.execute("SELECT count(*) FROM monuments_historiques_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "monuments_historiques")

                # Safety: abort if staging has <50% of production rows
                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                # GATE-01: enforce absolute row count floor on the final staging
                # table (flat-staging pitfall: runner's total_rows reflects flat
                # rows copied via COPY, not this final count).
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

        # GATE-02: schema drift check on staging table (after INSERT SELECT and
        # before Phase B rename).  Raises RuntimeError if abort mode is set.
        PipelineRunner._check_schema_drift_conn(self, dsn, logger)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _monuments_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden — default True,
                # but monuments has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed sequence")

    # Production index names (migration 009) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_monuments_position", "USING GIST (position)"),
        ("idx_monuments_code_insee", "(code_insee)"),
        ("idx_monuments_reference", "(reference)"),
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

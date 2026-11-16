"""Carte Scolaire concrete ETL pipeline.

Downloads the national CSV (~520k rows) from data.gouv.fr, validates
the schema, transforms (column selection, code_rne filtering),
bulk-loads via COPY into a flat staging table, and swaps atomically
into the carte_scolaire production table.

Data source: Carte Scolaire des Colleges Publics, Ministere de
l'Education Nationale.
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
from app.etl_scolaire.config import (
    CARTE_SCOLAIRE_CSV_URL,
    COLUMN_MAPPING,
    EXPECTED_COLUMNS,
    OUTPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


class ScolairePipeline(BasePipeline):
    """Concrete ETL pipeline for Carte Scolaire (~520k rows)."""

    # -- abstract properties ---------------------------------------------------

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 416_000

    @property
    def dataset_name(self) -> str:
        return "scolaire"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "carte_scolaire_staging"

    @property
    def production_table_name(self) -> str:
        return "carte_scolaire"

    @property
    def flat_staging_table_name(self) -> str:
        return "_carte_scolaire_staging"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22). The
        # source writes code_departement on three digits ("044"): the
        # departement comes from the commune code.
        return {"_carte_scolaire_staging": "dc_departement_from_insee(code_insee)"}

    @property
    def production_departement_sql(self) -> str:
        return "dc_departement_from_insee(code_insee)"

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~520k production carte_scolaire rows
        return self.scoped_min_rows(self.national_min_rows)

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
        """Create non-partitioned staging table (~520k rows, no benefit)."""
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
        """Download the Carte Scolaire CSV from data.gouv.fr."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        download_dir = get_download_dir("scolaire")
        raw_dir = download_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        output_path = raw_dir / "carte_scolaire.csv"
        timeout = httpx.Timeout(60.0, read=300.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            ok = await streaming_download(client, CARTE_SCOLAIRE_CSV_URL, output_path)
            if not ok:
                msg = "Carte scolaire CSV download returned 404"
                raise RuntimeError(msg)

        logger.info("Downloaded carte scolaire CSV: %s", output_path.name)
        return [output_path]

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the CSV header contains expected columns.

        Detects separator (comma or semicolon). Tolerates extras,
        rejects missing required columns.
        """
        with open(file_path, encoding="utf-8-sig") as f:
            first_line = f.readline().strip()

        # Detect separator
        separator = ";" if ";" in first_line else ","
        actual = {col.strip().strip('"') for col in first_line.split(separator)}
        expected = set(EXPECTED_COLUMNS)

        missing = expected - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "Scolaire CSV %s has %d extra columns",
                file_path.name,
                len(extra),
            )

        if missing:
            msg = (
                f"Scolaire schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

        # Verify code_rne not all empty
        df_sample = pd.read_csv(file_path, sep=separator, nrows=100, dtype=str)
        if "code_rne" in df_sample.columns:
            non_empty = df_sample["code_rne"].notna().sum()
            if non_empty == 0:
                msg = "code_rne column is empty in first 100 rows"
                raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform raw Carte Scolaire CSV into a clean CSV for COPY.

        - Read with pandas (auto-detect separator)
        - Select OUTPUT_COLUMNS
        - Filter rows where code_rne is null or empty
        - Write cleaned CSV
        """
        download_dir = get_download_dir("scolaire")
        transformed_dir = download_dir / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        output_path = transformed_dir / "carte_scolaire-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            dtype=str,
            encoding="utf-8-sig",
        )

        # Rename columns that changed upstream
        df = df.rename(columns=COLUMN_MAPPING)

        # Ensure all output columns exist
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Filter rows where code_rne is null or empty
        total_before = len(df)
        df = df[df["code_rne"].notna() & (df["code_rne"].str.strip() != "")]
        skipped = total_before - len(df)
        if skipped > 0:
            logger.warning(
                "Filtered %d rows without code_rne (kept %d)",
                skipped,
                len(df),
            )

        # Select output columns and write
        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed carte scolaire: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV into _carte_scolaire_staging via COPY.

        Flat staging table with all TEXT columns for maximum COPY
        throughput. Type casting happens during swap.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent)
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _carte_scolaire_staging (
                        code_region TEXT,
                        code_departement TEXT,
                        code_postal TEXT,
                        code_insee TEXT,
                        libelle_commune TEXT,
                        lieu_dit TEXT,
                        type_et_libelle TEXT,
                        n_de_voie_debut TEXT,
                        n_de_voie_fin TEXT,
                        parite TEXT,
                        code_rne TEXT,
                        secteur_unique TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _carte_scolaire_staging")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _carte_scolaire_staging ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _carte_scolaire_staging")
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
        prod = self.production_table_name  # "carte_scolaire"
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
                    INSERT INTO carte_scolaire_staging
                        (code_region, code_departement, code_postal,
                         code_insee, libelle_commune, lieu_dit,
                         type_et_libelle, n_de_voie_debut, n_de_voie_fin,
                         parite, code_rne, secteur_unique)
                    SELECT
                        NULLIF(code_region, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(code_postal, ''),
                        NULLIF(code_insee, ''),
                        NULLIF(libelle_commune, ''),
                        NULLIF(lieu_dit, ''),
                        NULLIF(type_et_libelle, ''),
                        NULLIF(n_de_voie_debut, '')::real,
                        NULLIF(n_de_voie_fin, '')::real,
                        NULLIF(parite, ''),
                        NULLIF(code_rne, ''),
                        NULLIF(secteur_unique, '')
                    FROM _carte_scolaire_staging
                    WHERE NULLIF(code_insee, '') IS NOT NULL
                      AND NULLIF(code_rne, '') IS NOT NULL
                """)

                cur.execute("SELECT count(*) FROM carte_scolaire_staging")
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
                old_count = self.count_production_rows(cur, "carte_scolaire")

                # No 50% check: carte_scolaire has no row-count guarantee
                # (source covers only metropolitan France primary schools).

            conn.commit()  # Phase A commits before Phase B opens

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _carte_scolaire_staging CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden — default True,
                # but carte_scolaire has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed sequence")

    # Production index names (migration 011) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_scolaire_code_insee", "(code_insee)"),
        ("idx_scolaire_code_insee_rne", "(code_insee, code_rne)"),
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

registry.register(ScolairePipeline)

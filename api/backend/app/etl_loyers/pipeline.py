"""Carte des Loyers concrete ETL pipeline.

Downloads 4 CSV files (one per housing type) from data.gouv.fr,
validates the schema, transforms via pandas (column mapping, type_habitat
injection, filtering), bulk-loads via COPY into a flat staging table,
and swaps atomically.

Data source: Estimations ANIL, a partir des donnees du Groupe SeLoger
et de leboncoin. Published by Ministere de la Transition Ecologique.
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
from app.etl.source_manifest import FrozenSources, prepare_sources
from app.etl_loyers.config import (
    ANNEE,
    BASE_URL,
    COLUMN_MAPPING,
    CSV_RESOURCES,
    DOWNLOAD_TIMEOUT,
    EXPECTED_COLUMNS,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    REFERENCE_SURFACES,
)

logger = logging.getLogger(__name__)


class LoyersPipeline(BasePipeline):
    """Concrete ETL pipeline for Carte des Loyers (~35k rows)."""

    _frozen_sources: FrozenSources | None = None

    @classmethod
    def from_manifest(cls, path: Path) -> "LoyersPipeline":
        pipeline = cls()
        pipeline._frozen_sources = prepare_sources(
            path, "loyers", pipeline.validate_schema
        )
        return pipeline

    def close(self) -> None:
        if self._frozen_sources is not None:
            self._frozen_sources.close()

    # -- abstract properties ---------------------------------------------------

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 100_000

    @property
    def dataset_name(self) -> str:
        return "loyers"

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "indicateurs_loyers_staging"

    @property
    def production_table_name(self) -> str:
        return "indicateurs_loyers"

    @property
    def flat_staging_table_name(self) -> str:
        return "_loyers_staging_flat"

    @property
    def checkpoint_scope_key(self) -> str:
        """Reject pre-metadata CSV/staging generations and other vintages."""
        if self._frozen_sources is not None:
            return self._frozen_sources.checkpoint_scope_key
        return f"loyers-prediction-metadata-v1:{ANNEE}"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_loyers_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~150k production indicateurs_loyers rows, full France;
        # scaled to the bound scope (D-23).
        return self.scoped_min_rows(self.national_min_rows)

    # -- override: non-partitioned staging (small dataset, ~4K rows) ----------

    def create_staging_table(self, dsn: str) -> None:
        """Create non-partitioned staging table."""
        staging = self.staging_table_name
        production = self.production_table_name

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # A fresh generation must not reuse an interrupted older COPY
                # schema or its rows. Resume skips this method entirely.
                cur.execute(f"DROP TABLE IF EXISTS {self.flat_staging_table_name}")
                cur.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
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
                cur.execute(f"DROP TABLE IF EXISTS {self.flat_staging_table_name}")
            conn.commit()

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download the 4 CSV files from data.gouv.fr (one per housing type).

        Uses asyncio internally for HTTP requests with retry and
        exponential backoff on 5xx errors and timeouts. Handles 404
        gracefully (logged, not raised).
        """
        if self._frozen_sources is not None:
            return self._frozen_sources.verified_paths()
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        raw_dir = get_download_dir("loyers") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            for type_habitat, resource_id in CSV_RESOURCES.items():
                output_path = raw_dir / f"{type_habitat}.csv"
                url = f"{BASE_URL}{resource_id}"
                success = await streaming_download(
                    client, url, output_path, max_retries=MAX_RETRIES
                )
                if success:
                    paths.append(output_path)
                    logger.info(
                        "Downloaded %s (%d/%d)",
                        type_habitat,
                        len(paths),
                        len(CSV_RESOURCES),
                    )
                else:
                    logger.warning(
                        "Skipped %s (download failed or 404)",
                        type_habitat,
                    )

        return paths

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the CSV header contains expected columns.

        Detects separator (comma or semicolon) and tolerates extra
        columns. Rejects missing required columns with a ValueError.
        """
        with open(file_path, encoding="utf-8", errors="replace") as f:
            first_line = f.readline().strip()

        # Detect separator: semicolon if present, else comma
        separator = ";" if ";" in first_line else ","

        actual = {col.strip().strip('"') for col in first_line.split(separator)}
        expected = set(EXPECTED_COLUMNS)

        missing = expected - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "Loyers CSV %s has extra columns: %s",
                file_path.name,
                sorted(extra),
            )

        if missing:
            msg = (
                f"Loyers schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform a raw Carte des Loyers CSV into a clean CSV for COPY.

        - Read with pandas (auto-detect separator)
        - Apply COLUMN_MAPPING for renaming
        - Add type_habitat column derived from filename
        - Add annee column with ANNEE constant
        - Preserve source scope, quality, and reviewed reference metadata
        - Filter rows where loyer_m2 is NULL or zero
        - Derive code_departement from code_insee if missing
        - Write cleaned CSV
        """
        transformed_dir = get_download_dir("loyers") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        output_path = transformed_dir / f"{file_path.stem}-transformed.csv"

        # Derive type_habitat from filename (e.g., "appartement.csv")
        type_habitat = file_path.stem
        if type_habitat not in REFERENCE_SURFACES:
            raise ValueError(f"Unknown rental reference type: {type_habitat}")
        if ANNEE != 2025:
            raise ValueError(
                "Rental reference contract requires review for this vintage"
            )
        self.validate_schema(file_path)

        # Read CSV with separator detection (source may be Latin-1)
        df = pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            dtype=str,
            encoding="latin-1",
        )

        # Rename columns via mapping
        df = df.rename(columns=COLUMN_MAPPING)

        # Add type_habitat and annee
        df["type_habitat"] = type_habitat
        df["annee"] = str(ANNEE)

        # Preserve the official scope and quality fields, never merge commune
        # observations with the wider model mesh. Keep legacy aliases additive.
        df["r2_ajuste"] = df["r2_ajuste"].str.replace(",", ".")
        df["r2"] = df["r2_ajuste"]
        df["nb_observations"] = df["nb_observations_commune"]
        df["trimestre_reference"] = "3"
        df["charges_comprises"] = "true"
        df["meuble"] = "false"
        surface, surface_piece = REFERENCE_SURFACES[type_habitat]
        df["surface_reference_m2"] = str(surface)
        df["surface_piece_reference_m2"] = str(surface_piece)
        # Official 2025 house and 3+ room files spell this scope as "EPCI".
        # Preserve the existing API value and leave other source labels strict.
        df["type_prediction"] = df["type_prediction"].replace({"EPCI": "epci"})
        known_scopes = {"commune", "epci", "maille", "maile"}
        if not df["type_prediction"].isin(known_scopes).all():
            raise ValueError("Unknown rental prediction scope")

        # Derive code_departement from code_insee if column empty or absent
        if "code_departement" not in df.columns:
            df["code_departement"] = ""
        mask_empty_dept = df["code_departement"].isna() | (
            df["code_departement"].str.strip() == ""
        )
        if mask_empty_dept.any():
            df.loc[mask_empty_dept, "code_departement"] = (
                df.loc[mask_empty_dept, "code_insee"]
                .fillna("")
                .apply(_derive_code_departement)
            )

        # Filter rows where loyer_m2 is NULL/empty or zero
        df["loyer_m2"] = pd.to_numeric(
            df["loyer_m2"].str.replace(",", "."), errors="coerce"
        )
        df = df[df["loyer_m2"].notna() & (df["loyer_m2"] != 0)]

        # Clean numeric columns (handle decimal comma)
        for col in ["loyer_m2_bas", "loyer_m2_haut", "r2"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", "."),
                    errors="coerce",
                )

        # Ensure all output columns exist
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Select and write output columns
        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        row_count = len(df)
        logger.info(
            "Transformed %s: %d rows",
            file_path.name,
            row_count,
        )
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load a transformed CSV into _loyers_staging_flat via COPY.

        Simple flat staging table (no geometry, no partitioning) for
        maximum COPY throughput. Type casting happens during swap.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent)
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _loyers_staging_flat (
                        code_insee TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        code_epci TEXT,
                        code_region TEXT,
                        type_habitat TEXT,
                        loyer_m2 TEXT,
                        loyer_m2_bas TEXT,
                        loyer_m2_haut TEXT,
                        r2 TEXT,
                        nb_observations TEXT,
                        annee TEXT,
                        id_zone TEXT,
                        type_prediction TEXT,
                        nb_observations_commune TEXT,
                        nb_observations_maille TEXT,
                        r2_ajuste TEXT,
                        trimestre_reference TEXT,
                        charges_comprises TEXT,
                        meuble TEXT,
                        surface_reference_m2 TEXT,
                        surface_piece_reference_m2 TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _loyers_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _loyers_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _loyers_staging_flat")
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
        prod = self.production_table_name  # "indicateurs_loyers"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS indicateurs_loyers_staging CASCADE")
                cur.execute(
                    "DROP SEQUENCE IF EXISTS indicateurs_loyers_staging_id_seq CASCADE"
                )
                cur.execute("""
                    CREATE TABLE indicateurs_loyers_staging
                    (LIKE indicateurs_loyers INCLUDING ALL EXCLUDING INDEXES)
                """)
                # LIKE does not copy sequence binding for SERIAL/BIGSERIAL
                cur.execute("CREATE SEQUENCE indicateurs_loyers_staging_id_seq")
                cur.execute("""
                    ALTER TABLE indicateurs_loyers_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('indicateurs_loyers_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO indicateurs_loyers_staging
                        (code_insee, nom_commune, code_departement,
                         code_epci, code_region, type_habitat,
                         loyer_m2, loyer_m2_bas, loyer_m2_haut,
                         r2, nb_observations, annee,
                         id_zone, type_prediction, nb_observations_commune,
                         nb_observations_maille, r2_ajuste, trimestre_reference,
                         charges_comprises, meuble, surface_reference_m2,
                         surface_piece_reference_m2)
                    SELECT DISTINCT
                        NULLIF(code_insee, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(code_epci, ''),
                        NULLIF(code_region, ''),
                        NULLIF(type_habitat, ''),
                        NULLIF(loyer_m2, '')::numeric(6, 2),
                        NULLIF(loyer_m2_bas, '')::numeric(6, 2),
                        NULLIF(loyer_m2_haut, '')::numeric(6, 2),
                        NULLIF(r2, '')::numeric(4, 3),
                        NULLIF(nb_observations, '')::integer,
                        NULLIF(annee, '')::integer,
                        NULLIF(id_zone, ''),
                        NULLIF(type_prediction, ''),
                        NULLIF(nb_observations_commune, '')::integer,
                        NULLIF(nb_observations_maille, '')::integer,
                        NULLIF(r2_ajuste, '')::numeric,
                        NULLIF(trimestre_reference, '')::smallint,
                        NULLIF(charges_comprises, '')::boolean,
                        NULLIF(meuble, '')::boolean,
                        NULLIF(surface_reference_m2, '')::numeric,
                        NULLIF(surface_piece_reference_m2, '')::numeric
                    FROM _loyers_staging_flat
                    WHERE NULLIF(code_insee, '') IS NOT NULL
                      AND NULLIF(type_habitat, '') IS NOT NULL
                      AND NULLIF(annee, '') IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM indicateurs_loyers_staging")
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
                old_count = self.count_production_rows(cur, "indicateurs_loyers")

                # Safety: abort if staging has <50% of production rows
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
                cur.execute("DROP TABLE IF EXISTS _loyers_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions = False — no partition rename needed
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed sequence")

    # Production index names (migration 007) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_loyers_code_insee", "(code_insee)"),
        ("idx_loyers_code_departement", "(code_departement)"),
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


def _derive_code_departement(code_insee: str) -> str:
    """Derive department code from INSEE commune code.

    Metro France: first 2 digits (e.g., '75056' -> '75').
    DOM (97x): first 3 digits (e.g., '97105' -> '971').
    Corse: '2A' for 200xx, '2B' for 201xx-209xx.
    """
    if not code_insee or len(code_insee) < 2:
        return ""

    # DOM: starts with 97
    if code_insee.startswith("97") and len(code_insee) >= 3:
        return code_insee[:3]

    # Corse: 2A (Corse-du-Sud) and 2B (Haute-Corse)
    if code_insee.startswith("20"):
        if code_insee.startswith("2A"):
            return "2A"
        if code_insee.startswith("2B"):
            return "2B"
        # Historical numeric codes: 200xx = 2A, 201xx+ = 2B
        if len(code_insee) >= 3:
            third_digit = int(code_insee[2])
            return "2A" if third_digit == 0 else "2B"

    return code_insee[:2]


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(LoyersPipeline)

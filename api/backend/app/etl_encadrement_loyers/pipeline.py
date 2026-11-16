"""Encadrement des loyers concrete ETL pipeline.

Downloads the rent control CSV from data.gouv.fr, transforms
(column mapping, centroid extraction, drop heavy geo_shape),
bulk-loads via COPY, and swaps atomically.

Data source: DRIHL / data.gouv.fr.
"""

import asyncio
import logging
import time
from pathlib import Path

import httpx
import pandas as pd
import psycopg

from app.common.unqualified_surfaces import refuse
from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl_encadrement_loyers.config import (
    COLUMN_MAPPING,
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    EXPECTED_COLUMNS,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


class EncadrementLoyersPipeline(BasePipeline):
    """Concrete ETL for Encadrement des Loyers (~18k rows)."""

    def __init__(self) -> None:
        """Stop manual and scheduled construction before any runner side effect."""
        refuse("encadrement_loyers")

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 14_000

    @property
    def dataset_name(self) -> str:
        return "encadrement_loyers"

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "encadrement_loyers_staging"

    @property
    def production_table_name(self) -> str:
        return "encadrement_loyers"

    @property
    def flat_staging_table_name(self) -> str:
        return "_encadrement_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {
            "_encadrement_staging_flat": (
                "dc_departement_from_insee(code_insee_quartier)"
            ),
        }

    @property
    def production_departement_sql(self) -> str:
        return "dc_departement_from_insee(code_insee_quartier)"

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~18k rows (Paris rent control dataset, not France-wide)
        return self.scoped_min_rows(self.national_min_rows)

    def create_staging_table(self, dsn: str) -> None:
        """Create non-partitioned staging table."""
        refuse("encadrement_loyers")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # EXCLUDING INDEXES (DB-HYGIENE-01): INCLUDING ALL copies
                # every production index under an auto-generated name, which
                # then survives the swap and gets re-copied on the next run.
                cur.execute(
                    f"CREATE TABLE {self.staging_table_name} "
                    f"(LIKE {self.production_table_name} "
                    f"INCLUDING ALL EXCLUDING INDEXES)"
                )
            conn.commit()

    def drop_staging_table(self, dsn: str) -> None:
        """Drop the non-partitioned staging table."""
        refuse("encadrement_loyers")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
            conn.commit()

    def download(self) -> list[Path]:
        """Download the encadrement loyers CSV."""
        refuse("encadrement_loyers")
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        refuse("encadrement_loyers")
        raw_dir = get_download_dir("encadrement_loyers") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        output_path = raw_dir / "encadrement_loyers.csv"
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT,
        ) as client:
            success = await streaming_download(
                client,
                DOWNLOAD_URL,
                output_path,
                max_retries=MAX_RETRIES,
            )
            if success:
                logger.info("Downloaded encadrement_loyers.csv")
                return [output_path]
            else:
                logger.error("Failed to download")
                return []

    def validate_schema(self, file_path: Path) -> None:
        """Validate CSV header columns."""
        refuse("encadrement_loyers")
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(EXPECTED_COLUMNS) - actual
        if missing:
            msg = f"Encadrement loyers schema mismatch: missing {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform: rename columns, extract centroid, drop geometry."""
        refuse("encadrement_loyers")
        transformed_dir = get_download_dir("encadrement_loyers") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "encadrement-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
        )

        # Rename columns
        df = df.rename(columns=COLUMN_MAPPING)

        # Extract lat/lon from geo_point_2d ("48.86, 2.34")
        if "geo_point_2d" in df.columns:
            coords = df["geo_point_2d"].str.split(", ", expand=True)
            df["latitude"] = coords[0]
            df["longitude"] = coords[1]
        else:
            df["latitude"] = ""
            df["longitude"] = ""

        # Clean numeric columns
        for col in [
            "loyer_reference",
            "loyer_reference_majore",
            "loyer_reference_minore",
        ]:
            df[col] = pd.to_numeric(
                df[col].str.replace(",", "."),
                errors="coerce",
            )

        # Filter rows with missing ville
        df = df[df["ville"].notna() & (df["ville"].str.strip() != "")]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed encadrement_loyers: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV via COPY."""
        refuse("encadrement_loyers")
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _encadrement_staging_flat (
                        annee TEXT,
                        secteur TEXT,
                        numero_quartier TEXT,
                        nom_quartier TEXT,
                        nb_pieces TEXT,
                        epoque_construction TEXT,
                        type_location TEXT,
                        loyer_reference TEXT,
                        loyer_reference_majore TEXT,
                        loyer_reference_minore TEXT,
                        ville TEXT,
                        code_insee_quartier TEXT,
                        latitude TEXT,
                        longitude TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _encadrement_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _encadrement_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _encadrement_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged encadrement_loyers: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat -> production staging with geometry, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        refuse("encadrement_loyers")
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "encadrement_loyers"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS encadrement_loyers_staging CASCADE")
                cur.execute(
                    "DROP SEQUENCE IF EXISTS encadrement_loyers_staging_id_seq CASCADE"
                )
                cur.execute("""
                    CREATE TABLE encadrement_loyers_staging
                    (LIKE encadrement_loyers INCLUDING ALL EXCLUDING INDEXES)
                """)
                # LIKE may not copy sequence binding for SERIAL id
                cur.execute("""
                    CREATE SEQUENCE
                    encadrement_loyers_staging_id_seq
                """)
                cur.execute("""
                    ALTER TABLE encadrement_loyers_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval(
                        'encadrement_loyers_staging_id_seq'
                    )
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO encadrement_loyers_staging
                        (annee, secteur, numero_quartier,
                         nom_quartier, nb_pieces,
                         epoque_construction, type_location,
                         loyer_reference, loyer_reference_majore,
                         loyer_reference_minore, ville,
                         code_insee_quartier, geom)
                    SELECT DISTINCT
                        NULLIF(annee, '')::integer,
                        NULLIF(secteur, '')::integer,
                        NULLIF(numero_quartier, '')::integer,
                        NULLIF(nom_quartier, ''),
                        NULLIF(nb_pieces, ''),
                        NULLIF(epoque_construction, ''),
                        NULLIF(type_location, ''),
                        NULLIF(loyer_reference, '')::numeric(6, 1),
                        NULLIF(loyer_reference_majore, '')
                            ::numeric(6, 1),
                        NULLIF(loyer_reference_minore, '')
                            ::numeric(6, 1),
                        NULLIF(ville, ''),
                        NULLIF(code_insee_quartier, ''),
                        CASE
                            WHEN NULLIF(longitude, '') IS NOT NULL
                             AND NULLIF(latitude, '') IS NOT NULL
                            THEN ST_SetSRID(ST_MakePoint(
                                longitude::double precision,
                                latitude::double precision
                            ), 4326)
                        END
                    FROM _encadrement_staging_flat
                    WHERE NULLIF(ville, '') IS NOT NULL
                      AND NULLIF(annee, '') IS NOT NULL
                """)

                # PostgreSQL UNIQUE treats NULL neighborhoods as distinct.
                # Reject divergent published rows at that incomplete key too;
                # a missing neighborhood never authorizes an arbitrary price.
                cur.execute("""
                    SELECT 1 FROM encadrement_loyers_staging
                    WHERE numero_quartier IS NULL
                    GROUP BY annee, ville, nb_pieces, epoque_construction,
                             type_location
                    HAVING count(*) > 1
                    LIMIT 1
                """)
                if cur.fetchone() is not None:
                    raise RuntimeError(
                        "Ambiguous encadrement prices for a missing neighborhood"
                    )

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM encadrement_loyers_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "encadrement_loyers")

                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging {staging_count} "
                        f"vs production {old_count}"
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
                cur.execute("DROP TABLE IF EXISTS _encadrement_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions = False — no partition rename needed
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 028) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_encadrement_ville", "(ville)"),
        ("idx_encadrement_annee", "(annee)"),
        ("idx_encadrement_geom", "USING GIST (geom)"),
        ("idx_encadrement_quartier", "(code_insee_quartier)"),
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


# -- Registry registration ---------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(EncadrementLoyersPipeline)

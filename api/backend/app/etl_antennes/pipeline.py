"""ANFR antennes concrete ETL pipeline.

Downloads the ANFR antenna CSV, transforms via pandas,
bulk-loads via COPY, and swaps atomically.

Data source: ANFR, Licence Ouverte 2.0.
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
from app.etl_antennes.config import (
    CSV_SEPARATOR,
    DOWNLOAD_HEADERS,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    LOCAL_FALLBACK_PATH,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)


class AntennesPipeline(BasePipeline):
    """Concrete ETL for ANFR antennes (~250k rows)."""

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 200_000

    @property
    def dataset_name(self) -> str:
        return "antennes"

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "antennes_staging"

    @property
    def production_table_name(self) -> str:
        return "antennes"

    @property
    def flat_staging_table_name(self) -> str:
        return "_antennes_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_antennes_staging_flat": "dc_departement_from_insee(code_insee)"}

    @property
    def production_departement_sql(self) -> str:
        return "dc_departement_from_insee(code_insee)"

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~250k production antennes rows
        return self.scoped_min_rows(self.national_min_rows)

    def create_staging_table(self, dsn: str) -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # Flat table too, as the base: stage() appends to it, and
                # a run failed before swap() would publish its rows again.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
                # EXCLUDING INDEXES (DB-HYGIENE-01): INCLUDING ALL copies every
                # production index under an auto-generated name, which then
                # survives the swap and gets re-copied on the next run.
                # _create_staging_indexes() is the single source of truth;
                # PK/UNIQUE are restored by ensure_staging_constraints() from
                # prepare_staging_constraints(), called inside swap_tables().
                cur.execute(
                    f"CREATE TABLE {self.staging_table_name} "
                    f"(LIKE {self.production_table_name} "
                    f"INCLUDING ALL EXCLUDING INDEXES)"
                )
            conn.commit()

    def drop_staging_table(self, dsn: str) -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # Flat table too, as the base: a run failed before swap() would
                # otherwise hand its flat rows to the next one.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
            conn.commit()

    def download(self) -> list[Path]:
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        raw_dir = get_download_dir("antennes") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        csv_path = raw_dir / "anfr_antennes.csv"

        # Try direct download first (with browser-like headers)
        try:
            async with httpx.AsyncClient(
                timeout=DOWNLOAD_TIMEOUT,
                headers=DOWNLOAD_HEADERS,
                follow_redirects=True,
            ) as client:
                success = await streaming_download(
                    client,
                    DOWNLOAD_URL,
                    csv_path,
                    max_retries=MAX_RETRIES,
                )
                if success:
                    logger.info("Downloaded anfr_antennes.csv from ANFR")
                    return [csv_path]
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "ANFR direct download failed (HTTP %s), trying local fallback",
                exc.response.status_code,
            )

        # Fallback: use pre-staged CSV on the server
        # (for when ANFR blocks OVH/cloud IPs)
        fallback = Path(LOCAL_FALLBACK_PATH)
        if fallback.exists():
            import shutil

            shutil.copy2(fallback, csv_path)
            logger.info(
                "Using pre-staged ANFR CSV from %s",
                LOCAL_FALLBACK_PATH,
            )
            return [csv_path]

        logger.error(
            "Failed to download ANFR CSV and no fallback at %s",
            LOCAL_FALLBACK_PATH,
        )
        return []

    def validate_schema(self, file_path: Path) -> None:
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(SOURCE_COLUMNS) - actual
        if missing:
            msg = f"ANFR schema mismatch: missing {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        transformed_dir = get_download_dir("antennes") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "antennes-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
        )

        # Map columns — real ANFR CSV column names (2025)
        # Raw: id, adm_lb_nom, sup_id, emr_lb_systeme, emr_dt,
        #   sta_nm_dpt, code_insee, generation, date_maj,
        #   sta_nm_anfr, ..., adr_lb_lieu, ..., com_cd_insee,
        #   coordonnees, coord, statut
        col_map = {
            "adm_lb_nom": "operateur",
            "emr_lb_systeme": "bande_frequence",
            "sta_nm_anfr": "numero_station",
            "sup_id": "numero_support",
            "adr_lb_lieu": "adresse",
            "emr_dt": "date_mise_en_service",
            # generation, code_insee, statut already have correct names
        }
        df = df.rename(columns=col_map)

        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Extract lat/lon from coordonnees if present
        # Format: "46.177499 , 3.374166" (space-comma-space)
        if "coordonnees" in df.columns:
            coords = df["coordonnees"].str.split(r"\s*,\s*", expand=True, regex=True)
            if coords.shape[1] >= 2:
                df["latitude"] = coords[0].str.strip()
                df["longitude"] = coords[1].str.strip()
            else:
                df["latitude"] = ""
                df["longitude"] = ""
        else:
            if "latitude" not in df.columns:
                df["latitude"] = ""
            if "longitude" not in df.columns:
                df["longitude"] = ""

        # Clean date (ISO format: 2023-02-02)
        df["date_mise_en_service"] = pd.to_datetime(
            df["date_mise_en_service"],
            errors="coerce",
        ).dt.strftime("%Y-%m-%d")

        df = df[df["operateur"].notna() & (df["operateur"].str.strip() != "")]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed antennes: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _antennes_staging_flat (
                        operateur TEXT,
                        generation TEXT,
                        bande_frequence TEXT,
                        numero_station TEXT,
                        numero_support TEXT,
                        code_insee TEXT,
                        adresse TEXT,
                        latitude TEXT,
                        longitude TEXT,
                        date_mise_en_service TEXT,
                        statut TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _antennes_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _antennes_staging_flat ({columns}) "
                    f"FROM STDIN WITH "
                    f"(FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _antennes_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged antennes: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "antennes"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                # Recreate staging table fresh
                cur.execute("DROP TABLE IF EXISTS antennes_staging CASCADE")
                cur.execute("DROP SEQUENCE IF EXISTS antennes_staging_id_seq CASCADE")
                # EXCLUDING INDEXES (DB-HYGIENE-01) — see create_staging_table().
                cur.execute(
                    "CREATE TABLE antennes_staging "
                    "(LIKE antennes INCLUDING ALL EXCLUDING INDEXES)"
                )
                cur.execute("CREATE SEQUENCE antennes_staging_id_seq")
                cur.execute("""
                    ALTER TABLE antennes_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('antennes_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO antennes_staging
                        (operateur, generation,
                         bande_frequence,
                         numero_station, numero_support,
                         code_insee, adresse,
                         latitude, longitude,
                         date_mise_en_service, statut,
                         geom)
                    SELECT
                        NULLIF(operateur, ''),
                        NULLIF(generation, ''),
                        NULLIF(bande_frequence, ''),
                        NULLIF(numero_station, ''),
                        NULLIF(numero_support, ''),
                        NULLIF(code_insee, ''),
                        NULLIF(adresse, ''),
                        NULLIF(latitude, '')
                            ::double precision,
                        NULLIF(longitude, '')
                            ::double precision,
                        NULLIF(date_mise_en_service, '')
                            ::date,
                        NULLIF(statut, ''),
                        CASE
                            WHEN NULLIF(longitude, '')
                                IS NOT NULL
                             AND NULLIF(latitude, '')
                                IS NOT NULL
                            THEN ST_SetSRID(ST_MakePoint(
                                longitude::double precision,
                                latitude::double precision
                            ), 4326)
                        END
                    FROM _antennes_staging_flat
                    WHERE NULLIF(operateur, '')
                        IS NOT NULL
                """)

                cur.execute("SELECT count(*) FROM antennes_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "antennes")

                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging "
                        f"{staging_count} "
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
                cur.execute("DROP TABLE IF EXISTS _antennes_staging_flat CASCADE")
                # Sequence rename — IF EXISTS guards safe for pipelines without
                # a staging sequence (e.g. no SERIAL id column)
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

    # Production index names (migration 033) paired with their column spec.
    # Staging twins are derived via BasePipeline.staging_index_name() so the
    # post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_antennes_code_insee", "(code_insee)"),
        ("idx_antennes_generation", "(generation)"),
        ("idx_antennes_operateur", "(operateur)"),
        ("idx_antennes_geom", "USING GIST (geom)"),
    )

    def _create_staging_indexes(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        staging = self.staging_table_name
        for canonical, spec in self.STAGING_INDEX_SPECS:
            name = self.staging_index_name(canonical)
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(f"CREATE INDEX {name} ON {staging} {spec}")


# -- Registry registration ---------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(AntennesPipeline)

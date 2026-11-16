"""Fiscalite locale concrete ETL pipeline.

Downloads the Fiscalite locale des particuliers CSV from data.gouv.fr,
validates the schema, transforms via pandas (column mapping, rate
conversion), bulk-loads via COPY, and swaps atomically.

Data source: DGFiP — Fiscalite locale des particuliers.
"""

import asyncio
import logging
import time
from pathlib import Path

import httpx
import pandas as pd
import psycopg
from pandas._libs.parsers import STR_NA_VALUES

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl_fiscalite.config import (
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)


# Read as written, validated in transform().
COLONNES_BRUTES = frozenset({"IND_MAJOTHRS", "THSURTAXRSTAU"})


class FiscalitePipeline(BasePipeline):
    """Concrete ETL pipeline for Fiscalite Locale (~35k rows)."""

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 28_000

    @property
    def dataset_name(self) -> str:
        return "fiscalite"

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "fiscalite_locale_staging"

    @property
    def production_table_name(self) -> str:
        return "fiscalite_locale"

    @property
    def flat_staging_table_name(self) -> str:
        return "_fiscalite_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_fiscalite_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~35k production fiscalite_locale (commune-level) rows
        return self.scoped_min_rows(self.national_min_rows)

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

    def download(self) -> list[Path]:
        """Download the fiscalite CSV from data.gouv.fr."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async download implementation."""
        raw_dir = get_download_dir("fiscalite") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        csv_path = raw_dir / "fiscalite_particuliers.csv"
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT,
        ) as client:
            success = await streaming_download(
                client,
                DOWNLOAD_URL,
                csv_path,
                max_retries=MAX_RETRIES,
            )
            if success:
                logger.info("Downloaded fiscalite_particuliers.csv")
                return [csv_path]
            else:
                logger.error("Failed to download fiscalite CSV")
                return []

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the CSV contains required columns."""
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(SOURCE_COLUMNS) - actual
        if missing:
            msg = f"Fiscalite schema mismatch: missing columns {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform: map columns, convert rates to decimals."""
        transformed_dir = get_download_dir("fiscalite") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "fiscalite-transformed.csv"

        # Plan 04.7-08: the surcharge columns are read as written (« N/A » stays
        # « N/A », to be refused); every other column keeps the default
        # missing values of pandas, as before.
        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
            keep_default_na=False,
        )
        for colonne in df.columns:
            if colonne not in COLONNES_BRUTES:
                df[colonne] = df[colonne].mask(df[colonne].isin(STR_NA_VALUES))

        # Use the pre-built INSEE COM column
        df["code_insee"] = df["INSEE COM"].str.strip()

        # Map columns
        df["nom_commune"] = df["LIBCOM"].str.strip()
        df["code_departement"] = df["DEP"].str.strip()
        df["nom_departement"] = df["LIBDEP"].str.strip()
        df["nom_region"] = df["LIBREG"].str.strip()
        df["nom_epci"] = df["Q03"].str.strip()

        # Tax rates — already expressed as percentage points by DGFiP
        # (e.g., 57.54 means 57.54%). D-01: stored and served as points,
        # never converted to a 0-1 fraction — see
        # contracts/fiche-fields.v1.json (fiscalite.taux_*, unit=percent_point).
        for src, dst in [
            ("Taux_Global_TFB", "taux_tfpb"),
            ("Taux_Global_TFNB", "taux_tfpnb"),
            ("Taux_Global_TH", "taux_th_rs"),
            ("Taux_Plein_TEOM", "taux_teom"),
        ]:
            df[dst] = pd.to_numeric(
                df[src].str.replace(",", "."),
                errors="coerce",
            )

        # No base/produit in this dataset — set empty.
        # D6 (2026-08-22): these two columns are therefore NULL for every row of
        # every run, and are deliberately absent from the public surface — not in
        # /v1/fiscalite, not in the semantic contract. They stay here only
        # because fiscalite_locale still declares
        # them (migration 027) and `CREATE TABLE ... (LIKE fiscalite_locale)`
        # requires the INSERT column list to match. Do NOT re-expose them without
        # a source that actually carries an imposition base.
        df["base_tfpb"] = ""
        df["produit_tfpb"] = ""

        # Year from EXERCICE column
        df["annee"] = df["EXERCICE"].str.strip()

        # Second-home surtax (THRS): IND_MAJOTHRS is a strict OUI/NON flag. The
        # full national file (174 668 rows, checked 2026-10-31) only carries
        # those two values, so anything else means the source changed shape and
        # must fail the run rather than load a guessed boolean.
        indicateur = df["IND_MAJOTHRS"].str.strip()
        invalid = indicateur[~indicateur.isin(["OUI", "NON"])]
        if not invalid.empty:
            msg = (
                "Fiscalite IND_MAJOTHRS: expected OUI or NON, got "
                f"{sorted({str(v) for v in invalid})[:5]} "
                f"({len(invalid)} rows)"
            )
            raise ValueError(msg)
        df["majoration_thrs"] = indicateur.map({"OUI": "true", "NON": "false"})
        # Rate kept as published (NON rows carry an empty rate; empty -> NULL).
        # A rate written but unreadable fails the run: NULL would read as an
        # unknown rate of a surcharge in force.
        brut = df["THSURTAXRSTAU"].fillna("").astype(str).str.strip()
        df["taux_majoration_thrs"] = pd.to_numeric(
            brut.str.replace(",", "."), errors="coerce"
        )
        illisibles = brut[(brut != "") & df["taux_majoration_thrs"].isna()]
        if not illisibles.empty:
            msg = (
                "Fiscalite THSURTAXRSTAU: unreadable rates "
                f"{sorted(set(illisibles))[:5]} ({len(illisibles)} rows)"
            )
            raise ValueError(msg)

        # Filter rows with valid code_insee
        df = df[df["code_insee"].notna() & (df["code_insee"].str.len() == 5)]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed fiscalite: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV via COPY."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _fiscalite_staging_flat (
                        code_insee TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        nom_departement TEXT,
                        nom_region TEXT,
                        nom_epci TEXT,
                        taux_tfpb TEXT,
                        taux_tfpnb TEXT,
                        taux_th_rs TEXT,
                        taux_teom TEXT,
                        base_tfpb TEXT,
                        produit_tfpb TEXT,
                        annee TEXT,
                        majoration_thrs TEXT,
                        taux_majoration_thrs TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _fiscalite_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _fiscalite_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _fiscalite_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged fiscalite: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "fiscalite_locale"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS fiscalite_locale_staging CASCADE")
                cur.execute(
                    "DROP SEQUENCE IF EXISTS fiscalite_locale_staging_id_seq CASCADE"
                )
                cur.execute("""
                    CREATE TABLE fiscalite_locale_staging
                    (LIKE fiscalite_locale INCLUDING ALL EXCLUDING INDEXES)
                """)
                # LIKE may not copy sequence binding for SERIAL id
                cur.execute("""
                    CREATE SEQUENCE
                    fiscalite_locale_staging_id_seq
                """)
                cur.execute("""
                    ALTER TABLE fiscalite_locale_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('fiscalite_locale_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO fiscalite_locale_staging
                        (code_insee, nom_commune, code_departement,
                         nom_departement, nom_region, nom_epci,
                         taux_tfpb, taux_tfpnb, taux_th_rs,
                         taux_teom, base_tfpb, produit_tfpb,
                         annee, majoration_thrs, taux_majoration_thrs)
                    SELECT DISTINCT
                        NULLIF(code_insee, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(nom_departement, ''),
                        NULLIF(nom_region, ''),
                        NULLIF(nom_epci, ''),
                        NULLIF(taux_tfpb, '')::numeric(5, 2),
                        NULLIF(taux_tfpnb, '')::numeric(5, 2),
                        NULLIF(taux_th_rs, '')::numeric(5, 2),
                        NULLIF(taux_teom, '')::numeric(5, 2),
                        NULLIF(base_tfpb, '')::bigint,
                        NULLIF(produit_tfpb, '')::bigint,
                        NULLIF(annee, '')::integer,
                        NULLIF(majoration_thrs, '')::boolean,
                        NULLIF(taux_majoration_thrs, '')::numeric
                    FROM _fiscalite_staging_flat
                    WHERE NULLIF(code_insee, '') IS NOT NULL
                      AND NULLIF(annee, '') IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM fiscalite_locale_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "fiscalite_locale")

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
                cur.execute("DROP TABLE IF EXISTS _fiscalite_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden — default True,
                # but fiscalite has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 027) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_fiscalite_code_insee", "(code_insee)"),
        ("idx_fiscalite_code_departement", "(code_departement)"),
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

registry.register(FiscalitePipeline)

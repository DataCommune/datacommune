"""Filosofi revenus concrete ETL pipeline.

Downloads the Filosofi commune-level ZIP from INSEE, extracts CSV,
transforms via pandas (column mapping, numeric cleaning),
bulk-loads via COPY, and swaps atomically.

Data source: INSEE Filosofi, Licence Ouverte 2.0.
"""

import asyncio
import logging
import time
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import (
    assert_zip_extraction_safe,
    get_download_dir,
    streaming_download,
)
from app.etl_revenus.config import (
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    MAIN_CSV_NAME,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    POVERTY_CSV_NAME,
    SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)


class RevenusPipeline(BasePipeline):
    """Concrete ETL for Filosofi revenus (~35k rows)."""

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 28_000

    @property
    def dataset_name(self) -> str:
        return "revenus"

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "revenus_staging"

    @property
    def production_table_name(self) -> str:
        return "revenus"

    @property
    def flat_staging_table_name(self) -> str:
        return "_revenus_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_revenus_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~35k production revenus (commune-level) rows
        return self.scoped_min_rows(self.national_min_rows)

    def create_staging_table(self, dsn: str) -> None:
        """Create non-partitioned staging table."""
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                # Flat table too, as the base: stage() appends to it, and
                # a run failed before swap() would publish its rows again.
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
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
        """Download the Filosofi ZIP from INSEE."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        raw_dir = get_download_dir("revenus") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        zip_path = raw_dir / "filosofi_communes.zip"
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT,
        ) as client:
            success = await streaming_download(
                client,
                DOWNLOAD_URL,
                zip_path,
                max_retries=MAX_RETRIES,
            )
            if not success:
                logger.error("Failed to download Filosofi ZIP")
                return []

        # Extract the two CSVs we need from the ZIP. DEC_COM carries the
        # declared income columns; DISP_PAUVRES_COM supplies TP6021 for
        # disposable-income poverty, joined on CODGEO. Only DEC_COM is
        # returned so the runner stages it once — the poverty file is read
        # directly from raw_dir during transform.
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            for wanted in (MAIN_CSV_NAME, POVERTY_CSV_NAME):
                if wanted not in names:
                    logger.error("Missing %s in Filosofi ZIP", wanted)
                    return []
            # Size/ratio caps before extraction (LC-01)
            assert_zip_extraction_safe(zip_path, zf)
            for wanted in (MAIN_CSV_NAME, POVERTY_CSV_NAME):
                zf.extract(wanted, raw_dir)
                logger.info("Extracted %s from ZIP", wanted)
            return [raw_dir / MAIN_CSV_NAME]

    def validate_schema(self, file_path: Path) -> None:
        """Validate CSV header columns."""
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(SOURCE_COLUMNS) - actual
        if missing:
            msg = f"Filosofi schema mismatch: missing {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform: map columns, clean numerics."""
        transformed_dir = get_download_dir("revenus") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "revenus-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
        )

        df["code_insee"] = df["CODGEO"].str.strip()
        # LIBGEO not in this file — leave empty, can be joined later
        df["nom_commune"] = ""
        df["code_departement"] = df["code_insee"].str[:2]
        # Fix Corsica and DOM
        df.loc[
            df["code_insee"].str.startswith("2A")
            | df["code_insee"].str.startswith("2B"),
            "code_departement",
        ] = df["code_insee"].str[:2]
        df.loc[
            df["code_insee"].str[:3].isin(["971", "972", "973", "974", "976"]),
            "code_departement",
        ] = df["code_insee"].str[:3]

        df["nom_departement"] = ""
        df["nom_region"] = ""

        # Numeric columns (may have commas or spaces)
        # Real column names: NBMEN21, Q221 (median), Q121, Q321, D121, D921
        for src, dst in [
            ("NBMEN21", "nb_menages_fiscaux"),
            ("Q221", "revenu_median"),
            ("Q121", "revenu_q1"),
            ("Q321", "revenu_q3"),
            ("D121", "revenu_d1"),
            ("D921", "revenu_d9"),
        ]:
            if src in df.columns:
                df[dst] = pd.to_numeric(
                    df[src].str.replace(",", ".").str.replace(" ", ""),
                    errors="coerce",
                )
            else:
                df[dst] = ""

        for src, dst in [
            ("GI21", "indice_gini"),
            # PMIMP21 = "Part des ménages fiscaux imposés (%)" — the share
            # of taxed households. (Was wrongly mapped from PTSA21, the
            # share of salaries.)
            ("PMIMP21", "part_imposee"),
        ]:
            if src in df.columns:
                df[dst] = pd.to_numeric(
                    df[src].str.replace(",", "."),
                    errors="coerce",
                )
            else:
                df[dst] = ""

        # taux_pauvrete = TP6021 ("Taux de pauvreté au seuil de 60%"). This
        # variable comes from DISP_PAUVRES (after redistribution), deliberately
        # distinct from the declared-income DEC series above. Join it on
        # the commune code. (Was wrongly mapped from PACT21, the share of
        # activity income.)
        poverty_path = file_path.parent / POVERTY_CSV_NAME
        df_pov = pd.read_csv(
            poverty_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
            usecols=["CODGEO", "TP6021"],
        )
        poverty_by_insee = dict(
            zip(df_pov["CODGEO"].str.strip(), df_pov["TP6021"], strict=False)
        )
        df["taux_pauvrete"] = pd.to_numeric(
            df["code_insee"].map(poverty_by_insee).str.replace(",", "."),
            errors="coerce",
        )

        # vintage pinned to source resource (INSEE Filosofi fichier/ opaque
        # ID, multi-year publication lag by nature); not auto-extensible
        # like DVF — bump requires manually finding the next published
        # resource + verifying schema (P38 DATA-03 audit)
        df["annee"] = "2021"

        df = df[df["code_insee"].notna() & (df["code_insee"].str.len() == 5)]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed revenus: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV via COPY."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _revenus_staging_flat (
                        code_insee TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        nom_departement TEXT,
                        nom_region TEXT,
                        nb_menages_fiscaux TEXT,
                        revenu_median TEXT,
                        revenu_q1 TEXT,
                        revenu_q3 TEXT,
                        revenu_d1 TEXT,
                        revenu_d9 TEXT,
                        indice_gini TEXT,
                        taux_pauvrete TEXT,
                        part_imposee TEXT,
                        annee TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _revenus_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _revenus_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _revenus_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged revenus: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "revenus"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS revenus_staging CASCADE")
                cur.execute("DROP SEQUENCE IF EXISTS revenus_staging_id_seq CASCADE")
                cur.execute("""
                    CREATE TABLE revenus_staging
                    (LIKE revenus INCLUDING ALL EXCLUDING INDEXES)
                """)
                cur.execute("CREATE SEQUENCE revenus_staging_id_seq")
                cur.execute("""
                    ALTER TABLE revenus_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('revenus_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO revenus_staging
                        (code_insee, nom_commune,
                         code_departement, nom_departement,
                         nom_region, nb_menages_fiscaux,
                         revenu_median, revenu_q1, revenu_q3,
                         revenu_d1, revenu_d9, indice_gini,
                         taux_pauvrete, part_imposee, annee)
                    SELECT DISTINCT
                        NULLIF(code_insee, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(nom_departement, ''),
                        NULLIF(nom_region, ''),
                        NULLIF(nb_menages_fiscaux, '')::numeric::integer,
                        NULLIF(revenu_median, '')::numeric::integer,
                        NULLIF(revenu_q1, '')::numeric::integer,
                        NULLIF(revenu_q3, '')::numeric::integer,
                        NULLIF(revenu_d1, '')::numeric::integer,
                        NULLIF(revenu_d9, '')::numeric::integer,
                        NULLIF(indice_gini, '')::numeric(5, 4),
                        NULLIF(taux_pauvrete, '')::numeric(5, 2),
                        NULLIF(part_imposee, '')::numeric(5, 2),
                        NULLIF(annee, '')::integer
                    FROM _revenus_staging_flat
                    WHERE NULLIF(code_insee, '') IS NOT NULL
                      AND NULLIF(annee, '') IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM revenus_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "revenus")

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
                cur.execute("DROP TABLE IF EXISTS _revenus_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden — default True,
                # but revenus has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 029) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_revenus_code_insee", "(code_insee)"),
        ("idx_revenus_code_departement", "(code_departement)"),
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

registry.register(RevenusPipeline)

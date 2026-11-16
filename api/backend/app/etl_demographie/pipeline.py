"""Recensement demographie concrete ETL pipeline.

Downloads three INSEE ZIPs in parallel (populations legales + base logement 2022
+ base evolution-structure population 2022), merges on 5-char commune code,
transforms via pandas, bulk-loads via COPY, and swaps atomically.

Data source: INSEE Recensement, Licence Ouverte 2.0.
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
from app.etl_demographie.config import (
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL_LOGEMENT,
    DOWNLOAD_URL_POP_LEGALES,
    DOWNLOAD_URL_POP_STRUCT,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    SOURCE_COLUMNS_LOGEMENT,
    SOURCE_COLUMNS_POP_LEGALES,
    SOURCE_COLUMNS_POP_STRUCT,
)

logger = logging.getLogger(__name__)

# Internal file name for the merged intermediate CSV (output of download()).
MERGED_CSV_NAME = "demographie-merged.csv"

# Columns expected in the merged CSV — validated before transform.
# NB: the join key has already been renamed from COM/CODGEO → code_insee
# inside _merge_sources(), so the post-merge schema is flat.
MERGED_REQUIRED_COLUMNS: list[str] = [
    "code_insee",
    "Commune",
    "PMUN",
    "P22_POPH",
    "P22_POPF",
    "P22_MEN",
    "P22_LOG",
    "P22_RP",
    "P22_LOGVAC",
    "P22_RSECOCC",
]


class DemographiePipeline(BasePipeline):
    """Concrete ETL for Recensement demographie (~35k rows)."""

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 28_000

    @property
    def dataset_name(self) -> str:
        return "demographie"

    @property
    def expected_columns(self) -> list[str]:
        return MERGED_REQUIRED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "demographie_staging"

    @property
    def production_table_name(self) -> str:
        return "demographie"

    @property
    def flat_staging_table_name(self) -> str:
        return "_demographie_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_demographie_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~35k production demographie (commune-level) rows
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

    # -- Download + merge ----------------------------------------------------

    def download(self) -> list[Path]:
        """Download the 3 INSEE ZIPs, extract CSVs, merge into a single file.

        Returns a single-element list pointing at the merged CSV so the runner's
        per-file validate/transform/stage loop stays straightforward.
        """
        return asyncio.run(self._async_download_and_merge())

    async def _async_download_and_merge(self) -> list[Path]:
        raw_dir = get_download_dir("demographie") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        targets = [
            (
                DOWNLOAD_URL_POP_LEGALES,
                raw_dir / "populations_legales.zip",
                "pop_legales",
            ),
            (DOWNLOAD_URL_LOGEMENT, raw_dir / "base_logement_2022.zip", "logement"),
            (
                DOWNLOAD_URL_POP_STRUCT,
                raw_dir / "base_pop_struct_2022.zip",
                "pop_struct",
            ),
        ]

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            results = await asyncio.gather(
                *(
                    streaming_download(client, url, path, max_retries=MAX_RETRIES)
                    for url, path, _ in targets
                ),
                return_exceptions=False,
            )

        for (url, _, label), ok in zip(targets, results, strict=True):
            if not ok:
                logger.error(
                    "Failed to download demographie source '%s' (%s)", label, url
                )
                return []

        pop_csv = self._extract_single_csv(targets[0][1], raw_dir, prefer="communes")
        log_csv = self._extract_single_csv(targets[1][1], raw_dir)
        struct_csv = self._extract_single_csv(targets[2][1], raw_dir)

        if not (pop_csv and log_csv and struct_csv):
            logger.error("One or more demographie CSVs missing after extraction")
            return []

        merged_path = raw_dir / MERGED_CSV_NAME
        self._merge_sources(pop_csv, log_csv, struct_csv, merged_path)
        return [merged_path]

    @staticmethod
    def _extract_single_csv(
        zip_path: Path,
        dest_dir: Path,
        prefer: str | None = None,
    ) -> Path | None:
        """Extract a CSV from a ZIP. If *prefer* is given, prefer a name
        containing that substring (case-insensitive) over other CSVs.

        Excludes "deleguees" files (communes déléguées) from selection — those
        carry a separate schema we don't want to mix in.
        """
        with zipfile.ZipFile(zip_path) as zf:
            csv_names = [
                n
                for n in zf.namelist()
                if (n.lower().endswith(".csv")) and "deleguees" not in n.lower()
            ]
            if not csv_names:
                logger.error("No CSV in %s", zip_path)
                return None

            if prefer:
                preferred = [n for n in csv_names if prefer.lower() in n.lower()]
                csv_name = preferred[0] if preferred else csv_names[0]
            else:
                csv_name = csv_names[0]

            # Size/ratio caps before extraction (B-02)
            assert_zip_extraction_safe(zip_path, zf)
            zf.extract(csv_name, dest_dir)
            logger.info("Extracted %s from %s", csv_name, zip_path.name)
            return dest_dir / csv_name

    @staticmethod
    def _read_csv_any_encoding(path: Path) -> pd.DataFrame:
        """Read an INSEE CSV trying UTF-8 then latin-1 as fallback."""
        for encoding in ("utf-8", "latin-1"):
            try:
                return pd.read_csv(
                    path,
                    sep=CSV_SEPARATOR,
                    dtype=str,
                    encoding=encoding,
                    low_memory=False,
                )
            except UnicodeDecodeError:
                continue
        # Last resort: replace errors so the pipeline doesn't crash.
        return pd.read_csv(
            path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="latin-1",
            encoding_errors="replace",
            low_memory=False,
        )

    def _merge_sources(
        self,
        pop_csv: Path,
        log_csv: Path,
        struct_csv: Path,
        out_path: Path,
    ) -> None:
        """Merge the 3 source CSVs on 5-char commune code into a single file."""
        df_pop = self._read_csv_any_encoding(pop_csv)
        df_log = self._read_csv_any_encoding(log_csv)
        df_struct = self._read_csv_any_encoding(struct_csv)

        # Validate each source has its required columns before merging —
        # fail fast with a useful message if INSEE changed the schema.
        self._assert_columns(df_pop, SOURCE_COLUMNS_POP_LEGALES, pop_csv.name)
        self._assert_columns(df_log, SOURCE_COLUMNS_LOGEMENT, log_csv.name)
        self._assert_columns(df_struct, SOURCE_COLUMNS_POP_STRUCT, struct_csv.name)

        # Normalize join key: both source conventions collapse onto `code_insee`.
        df_pop = df_pop.rename(columns={"COM": "code_insee"})
        df_log = df_log.rename(columns={"CODGEO": "code_insee"})
        df_struct = df_struct.rename(columns={"CODGEO": "code_insee"})

        df_pop["code_insee"] = df_pop["code_insee"].str.strip()
        df_log["code_insee"] = df_log["code_insee"].str.strip()
        df_struct["code_insee"] = df_struct["code_insee"].str.strip()

        # Keep only the columns we will use from each supplementary source.
        log_cols = ["code_insee"] + [
            c for c in SOURCE_COLUMNS_LOGEMENT if c != "CODGEO"
        ]
        struct_cols = ["code_insee"] + [
            c for c in SOURCE_COLUMNS_POP_STRUCT if c != "CODGEO"
        ]

        merged = df_pop.merge(df_log[log_cols], on="code_insee", how="left")
        merged = merged.merge(df_struct[struct_cols], on="code_insee", how="left")

        merged.to_csv(out_path, index=False, sep=CSV_SEPARATOR, encoding="utf-8")
        logger.info(
            "Merged demographie sources: %d rows -> %s",
            len(merged),
            out_path.name,
        )

    @staticmethod
    def _assert_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
        missing = [c for c in required if c not in df.columns]
        if missing:
            msg = f"Demographie source {label}: missing columns {missing}"
            raise ValueError(msg)

    # -- Validate + transform ------------------------------------------------

    def validate_schema(self, file_path: Path) -> None:
        """Validate merged CSV has all required source columns."""
        with open(file_path, encoding="utf-8", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(MERGED_REQUIRED_COLUMNS) - actual
        if missing:
            msg = f"Demographie merged CSV missing columns: {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform: derive dept/region, coerce numerics, emit OUTPUT_COLUMNS."""
        transformed_dir = get_download_dir("demographie") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "demographie-transformed.csv"

        df = self._read_csv_any_encoding(file_path)

        df["code_insee"] = df["code_insee"].str.strip()
        df["nom_commune"] = df["Commune"].str.strip()

        # Departement: prefer DEP column when present (pop_legales), fallback
        # to the first 2 chars of code_insee (DOM uses first 3).
        if "DEP" in df.columns:
            df["code_departement"] = df["DEP"].astype(str).str.strip()
        else:
            df["code_departement"] = df["code_insee"].str[:2]
        dom_mask = df["code_insee"].str[:3].isin(["971", "972", "973", "974", "976"])
        df.loc[dom_mask, "code_departement"] = df["code_insee"].str[:3]

        if "REG" in df.columns:
            df["code_region"] = df["REG"].astype(str).str.strip()
        else:
            df["code_region"] = ""

        # Numeric mapping source -> output column.
        # INSEE RP values can be decimal (computed estimates) — convert via
        # float first, then to nullable Int64 so NaN stays NaN (empty in CSV)
        # and real values serialize without a ".0" suffix that would break
        # the downstream ::integer cast in the SQL INSERT.
        for src, dst in [
            ("PMUN", "population"),
            ("P22_POPH", "population_hommes"),
            ("P22_POPF", "population_femmes"),
            ("P22_MEN", "nb_menages"),
            ("P22_LOG", "nb_logements"),
            ("P22_RP", "nb_residences_principales"),
            ("P22_LOGVAC", "nb_logements_vacants"),
            ("P22_RSECOCC", "nb_residences_secondaires"),
        ]:
            if src in df.columns:
                cleaned = (
                    df[src]
                    .astype(str)
                    .str.replace(",", ".")
                    .str.replace(" ", "")
                    .str.replace("\xa0", "")
                )
                numeric = pd.to_numeric(cleaned, errors="coerce")
                # Float → nullable integer; NaN → pd.NA (empty cell in CSV).
                df[dst] = numeric.round().astype("Int64")
            else:
                df[dst] = ""

        # Superficie + densite are left empty here and backfilled from the
        # administrative geometry (geo_communes) after swap, by
        # app.demographie.backfill.backfill_superficie_densite -- called via
        # the DependentMatviewHook "demographie" entry (app/etl/hooks.py).
        # D-07 forbids computing them from cadastre contenance here.
        df["superficie"] = ""
        df["densite"] = ""
        # vintage pinned to source resource (INSEE fichier/ opaque IDs, config.py);
        # not auto-extensible like DVF — bump requires manually finding the next
        # published resource + verifying schema (P38 DATA-03 audit)
        df["annee"] = "2022"

        df = df[df["code_insee"].notna() & (df["code_insee"].str.len() == 5)]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed demographie: %d rows", len(df))
        return output_path

    # -- Stage + swap --------------------------------------------------------

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV via COPY."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _demographie_staging_flat (
                        code_insee TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        code_region TEXT,
                        population TEXT,
                        population_hommes TEXT,
                        population_femmes TEXT,
                        nb_menages TEXT,
                        nb_logements TEXT,
                        nb_residences_principales TEXT,
                        nb_logements_vacants TEXT,
                        nb_residences_secondaires TEXT,
                        superficie TEXT,
                        densite TEXT,
                        annee TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _demographie_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _demographie_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _demographie_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged demographie: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + PLM aggregates + densite
                 computation + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "demographie"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS demographie_staging CASCADE")
                cur.execute(
                    "DROP SEQUENCE IF EXISTS demographie_staging_id_seq CASCADE"
                )
                cur.execute("""
                    CREATE TABLE demographie_staging
                    (LIKE demographie INCLUDING ALL EXCLUDING INDEXES)
                """)
                cur.execute("CREATE SEQUENCE demographie_staging_id_seq")
                cur.execute("""
                    ALTER TABLE demographie_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('demographie_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO demographie_staging
                        (code_insee, nom_commune,
                         code_departement, code_region,
                         population, population_hommes,
                         population_femmes, nb_menages,
                         nb_logements,
                         nb_residences_principales,
                         nb_logements_vacants,
                         nb_residences_secondaires,
                         superficie, densite, annee)
                    SELECT DISTINCT
                        NULLIF(code_insee, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(code_region, ''),
                        NULLIF(population, '')::integer,
                        NULLIF(population_hommes, '')::integer,
                        NULLIF(population_femmes, '')::integer,
                        NULLIF(nb_menages, '')::integer,
                        NULLIF(nb_logements, '')::integer,
                        NULLIF(nb_residences_principales, '')
                            ::integer,
                        NULLIF(nb_logements_vacants, '')::integer,
                        NULLIF(nb_residences_secondaires, '')
                            ::integer,
                        NULLIF(superficie, '')::numeric(10, 2),
                        NULLIF(densite, '')::numeric(10, 2),
                        NULLIF(annee, '')::integer
                    FROM _demographie_staging_flat
                    WHERE NULLIF(code_insee, '') IS NOT NULL
                      AND NULLIF(annee, '') IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                # Aggregate PLM arrondissements into global commune codes
                self._insert_plm_aggregates(cur)

                # superficie/densite are intentionally left NULL here -- they
                # are backfilled from administrative geometry (D-07) after
                # the swap completes, via
                # app.demographie.backfill.backfill_superficie_densite.

                cur.execute("SELECT count(*) FROM demographie_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "demographie")

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
                cur.execute("DROP TABLE IF EXISTS _demographie_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden → default True,
                # but demographie has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    @staticmethod
    def _insert_plm_aggregates(
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        """Insert aggregated rows for Paris/Lyon/Marseille.

        The INSEE source has arrondissements (751xx, 132xx, 6938x)
        but not global commune codes (75056, 13055, 69123).
        """
        # Derive PLM globals in a subquery so the outer GROUP BY sees the
        # three aggregate labels as plain columns (not CASE expressions) and
        # Postgres no longer complains about ungrouped code_insee references.
        cur.execute("""
            INSERT INTO demographie_staging
                (code_insee, nom_commune, code_departement,
                 code_region, population, population_hommes,
                 population_femmes, nb_menages, nb_logements,
                 nb_residences_principales, nb_logements_vacants,
                 nb_residences_secondaires, annee)
            SELECT
                global_code,
                global_nom,
                global_dept,
                MAX(code_region),
                SUM(population),
                SUM(population_hommes),
                SUM(population_femmes),
                SUM(nb_menages),
                SUM(nb_logements),
                SUM(nb_residences_principales),
                SUM(nb_logements_vacants),
                SUM(nb_residences_secondaires),
                annee
            FROM (
                SELECT
                    CASE
                        WHEN code_insee LIKE '751%' THEN '75056'
                        WHEN code_insee LIKE '132%' THEN '13055'
                        WHEN code_insee LIKE '6938%' THEN '69123'
                    END AS global_code,
                    CASE
                        WHEN code_insee LIKE '751%' THEN 'Paris'
                        WHEN code_insee LIKE '132%' THEN 'Marseille'
                        WHEN code_insee LIKE '6938%' THEN 'Lyon'
                    END AS global_nom,
                    CASE
                        WHEN code_insee LIKE '751%' THEN '75'
                        WHEN code_insee LIKE '132%' THEN '13'
                        WHEN code_insee LIKE '6938%' THEN '69'
                    END AS global_dept,
                    code_region,
                    population,
                    population_hommes,
                    population_femmes,
                    nb_menages,
                    nb_logements,
                    nb_residences_principales,
                    nb_logements_vacants,
                    nb_residences_secondaires,
                    annee
                FROM demographie_staging
                WHERE code_insee LIKE '751__'
                   OR code_insee LIKE '132__'
                   OR code_insee LIKE '6938_'
            ) plm_source
            GROUP BY annee, global_code, global_nom, global_dept
            ON CONFLICT (code_insee, annee) DO NOTHING
        """)
        logger.info("Inserted PLM aggregate rows")

    # Production index names (migration 030) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_demographie_code_insee", "(code_insee)"),
        ("idx_demographie_code_departement", "(code_departement)"),
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

registry.register(DemographiePipeline)

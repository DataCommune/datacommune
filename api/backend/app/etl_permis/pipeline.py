"""Load the quarantined local permits resource without inventing semantics."""

import asyncio
import csv
import hashlib
import logging
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pandas as pd
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl_permis.config import (
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)


class PermisPipeline(BasePipeline):
    """Concrete ETL for a partial local resource (publicly quarantined)."""

    # Source local to 93 (KI-16): only a non-empty departement is required.
    national_min_rows = 1

    @property
    def dataset_name(self) -> str:
        return "permis"

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "permis_staging"

    @property
    def production_table_name(self) -> str:
        return "permis"

    @property
    def flat_staging_table_name(self) -> str:
        return "_permis_staging_flat"

    @property
    def has_department_partitions(self) -> bool:
        # permis is a flat table — no department partitions.
        # Must be False so the 96-dept coverage gate does not block the swap.
        return False

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_permis_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # The published table is populated inside swap(), not stage().
        return 0

    @property
    def min_staging_rows_final(self) -> int:
        return 1

    @property
    def checkpoint_scope_key(self) -> str:
        # A single small, mutable resource must be loaded as one generation.
        # Force a complete normal retry: an old flat schema or the same filename
        # from another generation must never be resumed. The runner reads this
        # once per normal run and never reads it during dry-run.
        return f"permis-cor07-dates-v2-full-reload:{uuid4().hex}"

    def create_staging_table(self, dsn: str) -> None:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS _permis_staging_flat")
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
        raw_dir = get_download_dir("permis") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        csv_path = raw_dir / "sitadel_permis.csv"
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
                logger.info("Downloaded sitadel_permis.csv")
                return [csv_path]
            else:
                logger.error("Failed to download SITADEL CSV")
                return []

    def validate_schema(self, file_path: Path) -> None:
        with file_path.open(encoding="utf-8-sig", newline="") as source:
            actual = next(csv.reader(source, delimiter=CSV_SEPARATOR), [])
        if len(actual) != len(set(actual)):
            raise ValueError("Permis schema mismatch: duplicate columns")
        missing = set(SOURCE_COLUMNS) - set(actual)
        if missing:
            raise ValueError(f"Permis schema mismatch: missing {sorted(missing)}")

    def transform(self, file_path: Path) -> Path:  # noqa: C901
        self.validate_schema(file_path)
        transformed_dir = get_download_dir("permis") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "permis-transformed.csv"
        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
        )
        # Map only the observed local resource's columns.
        # n_commune = 5-digit INSEE code, commune = name
        col_map = {
            "nom_dossier": "numero_dossier",
            "type_dossier": "type_autorisation",
            "date_commencement": "date_commencement",
            "date_decision": "date_decision",
            "n_commune": "code_commune",
            "nom_commune": "nom_commune",
            "departement": "code_departement",
            "nature_construction": "nature_projet",
            "nb_logements": "nb_logements_crees",
            "sdp_creee": "surface_plancher",
            "designation": "destination",
        }
        df = df.rename(columns=col_map)
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        # This resource has no deposit date. Never accept a similarly named
        # unexpected column as qualified evidence without a new source contract.
        df["date_depot"] = ""
        df["source_sha256"] = hashlib.sha256(file_path.read_bytes()).hexdigest()
        for col in ["date_commencement", "date_decision"]:
            # Frozen source uses N/C for an unreported commencement and
            # YYYY-MM-DD 00:00:00 for a reported one (decision uses a date).
            raw = df[col].fillna("").str.strip().replace("N/C", "")
            if not raw.str.fullmatch(r"(?:\d{4}-\d{2}-\d{2}(?: 00:00:00)?)?").all():
                raise ValueError(f"Permis invalid {col}")
            parsed = pd.to_datetime(
                raw.replace("", None), format="mixed", errors="coerce"
            )
            if ((raw != "") & parsed.isna()).any():
                raise ValueError(f"Permis invalid {col}")
            df[col] = parsed.dt.strftime("%Y-%m-%d")
        for col in ["numero_dossier", "code_commune", "code_departement", "annee"]:
            df[col] = df[col].fillna("").str.strip()
        if df.empty or (df["numero_dossier"] == "").any():
            raise ValueError("Permis empty generation or missing dossier")
        if not (
            df["code_departement"].eq("93")
            & df["code_commune"].str.fullmatch(r"93\d{3}")
        ).all():
            raise ValueError(
                "Permis source scope changed: expected local department 93"
            )
        if not df["annee"].str.fullmatch(r"\d{4}").all():
            raise ValueError("Permis invalid annee")
        for col in ["nb_logements_crees", "surface_plancher"]:
            raw = df[col].fillna("").str.strip().str.replace(",", ".", regex=False)
            values = pd.to_numeric(raw.replace("", None), errors="coerce")
            if ((raw != "") & values.isna()).any():
                raise ValueError(f"Permis invalid {col}")
            if col == "nb_logements_crees":
                if (values.dropna() % 1 != 0).any():
                    raise ValueError("Permis fractional dwelling count")
                df[col] = values.astype("Int64")
            else:
                df[col] = values
        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")
        logger.info("Transformed permis: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        dsn = self.get_sync_dsn()
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _permis_staging_flat (
                        numero_dossier TEXT,
                        type_autorisation TEXT,
                        date_depot TEXT,
                        date_commencement TEXT,
                        source_sha256 TEXT,
                        date_decision TEXT,
                        code_commune TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        nature_projet TEXT,
                        nb_logements_crees TEXT,
                        surface_plancher TEXT,
                        type_construction TEXT,
                        destination TEXT,
                        annee TEXT
                    )
                """)
                cur.execute("SELECT count(*) FROM _permis_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _permis_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))
                cur.execute("SELECT count(*) FROM _permis_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before
            conn.commit()
        logger.info("Staged permis: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.
        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "permis"
        old = f"{prod}_old"
        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS permis_staging CASCADE")
                cur.execute("DROP SEQUENCE IF EXISTS permis_staging_id_seq CASCADE")
                cur.execute("""
                    CREATE TABLE permis_staging
                    (LIKE permis INCLUDING ALL EXCLUDING INDEXES)
                """)
                cur.execute("CREATE SEQUENCE permis_staging_id_seq")
                cur.execute("""
                    ALTER TABLE permis_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('permis_staging_id_seq')
                """)
                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()
                cur.execute("""
                    INSERT INTO permis_staging
                        (numero_dossier, type_autorisation,
                         date_depot, date_commencement, source_sha256, date_decision,
                         code_commune, nom_commune,
                         code_departement, nature_projet,
                         nb_logements_crees, surface_plancher,
                         type_construction, destination,
                         annee)
                    SELECT DISTINCT
                        NULLIF(numero_dossier, ''),
                        NULLIF(type_autorisation, ''),
                        NULL::date,
                        NULLIF(date_commencement, '')::date,
                        NULLIF(source_sha256, ''),
                        NULLIF(date_decision, '')::date,
                        NULLIF(code_commune, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(nature_projet, ''),
                        NULLIF(nb_logements_crees, '')::integer,
                        NULLIF(surface_plancher, '')
                            ::numeric(12, 2),
                        NULLIF(type_construction, ''),
                        NULLIF(destination, ''),
                        NULLIF(annee, '')::integer
                    FROM _permis_staging_flat
                    WHERE NULLIF(numero_dossier, '')
                        IS NOT NULL
                """)
                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)
                cur.execute("SELECT count(*) FROM permis_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )
                self._create_staging_indexes(cur)
                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "permis")
                if staging_count == 0 or (
                    old_count > 0 and staging_count < old_count * 0.5
                ):
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
                cur.execute("DROP TABLE IF EXISTS _permis_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions = False — no partition rename needed.
            conn.commit()
        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)
        logger.info("Cleanup complete")

    # Production index names (migration 032) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_permis_code_commune", "(code_commune)"),
        ("idx_permis_annee", "(annee)"),
        ("idx_permis_code_departement", "(code_departement)"),
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

registry.register(PermisPipeline)

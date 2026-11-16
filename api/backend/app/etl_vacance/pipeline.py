"""LOVAC vacance concrete ETL pipeline.

Downloads the LOVAC commune CSV from data.gouv.fr,
transforms via pandas, bulk-loads via COPY, and swaps atomically.

Data source: CEREMA LOVAC, Licence Ouverte 2.0.
"""

import asyncio
import csv
import logging
import re
import time
from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import httpx
import pandas as pd
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl.registry import register_pipeline
from app.etl.source_archive import SourceReplay
from app.etl.source_manifest import FrozenSources, prepare_sources
from app.etl_vacance.config import (
    CSV_SEPARATOR,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)


@register_pipeline("vacance")
class VacancePipeline(BasePipeline):
    """Concrete ETL for LOVAC vacance (~35k rows/year)."""

    _frozen_sources: FrozenSources | None = None
    # Download and replay restore prepare the same column pair from the
    # authentic source file before the runner archives or transforms it.
    archive_replay = True

    def __init__(self) -> None:
        super().__init__()
        self._prepared_columns: tuple[str, str] | None = None
        self._replayed_period_label: str | None = None

    @classmethod
    def from_manifest(cls, path: Path) -> "VacancePipeline":
        pipeline = cls()
        pipeline._frozen_sources = prepare_sources(
            path, "vacance", pipeline.validate_schema
        )
        return pipeline

    def close(self) -> None:
        if self._frozen_sources is not None:
            self._frozen_sources.close()

    @property
    def checkpoint_scope_key(self) -> str:
        if self._frozen_sources is not None:
            return self._frozen_sources.checkpoint_scope_key
        return super().checkpoint_scope_key

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 28_000

    @property
    def dataset_name(self) -> str:
        return "vacance"

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "vacance_staging"

    @property
    def production_table_name(self) -> str:
        return "vacance"

    @property
    def flat_staging_table_name(self) -> str:
        return "_vacance_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file: keep only the hosted departements before any gate.
        return {"_vacance_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~35k production vacance (commune-level) rows, scaled to the
        # hosted departements when the scope is not the whole of France.
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
        self._replayed_period_label = None
        if self._frozen_sources is not None:
            files = self._frozen_sources.verified_paths()
        else:
            files = asyncio.run(self._async_download())
        self._prepare_source_columns(files)
        return files

    def restore_source_inputs(
        self, replay: SourceReplay, directory: Path
    ) -> list[Path]:
        archived_label = replay.record.get("source_period_label")
        self._replayed_period_label = (
            archived_label.strip()
            if isinstance(archived_label, str) and archived_label.strip()
            else None
        )
        files = super().restore_source_inputs(replay, directory)
        self._prepare_source_columns(files)
        return files

    @property
    def source_period_label(self) -> str | None:
        """Return authentic header proof, retaining a distinct replay fact."""
        if self._prepared_columns is None:
            return self._replayed_period_label
        vacant_column, total_column = self._prepared_columns
        vacant_year = _column_year(vacant_column)
        total_year = _column_year(total_column)
        column_proof = (
            f"Logements vacants : {vacant_year} via {vacant_column} ; "
            f"parc privé : {total_year} via {total_column}"
        )
        composed_prefix = "Libellé archivé : "
        if (
            self._replayed_period_label is not None
            and self._replayed_period_label.startswith(composed_prefix)
            and self._replayed_period_label.endswith(f" ; {column_proof}")
        ):
            return self._replayed_period_label
        if (
            self._replayed_period_label is not None
            and self._replayed_period_label != column_proof
        ):
            return f"Libellé archivé : {self._replayed_period_label} ; {column_proof}"
        return column_proof

    def _prepare_source_columns(self, files: list[Path]) -> None:
        self._prepared_columns = None
        pairs: set[tuple[str, str]] = set()
        for file_path in files:
            with file_path.open(encoding="latin-1", errors="replace", newline="") as f:
                columns = next(csv.reader(f, delimiter=CSV_SEPARATOR), [])
            pairs.add(_select_vacance_columns(columns))
        if len(pairs) > 1:
            raise ValueError(
                "LOVAC schema mismatch: source files use different columns"
            )
        if pairs:
            self._prepared_columns = pairs.pop()

    async def _async_download(self) -> list[Path]:
        raw_dir = get_download_dir("vacance") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        csv_path = raw_dir / "lovac_communes.csv"
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
                logger.info("Downloaded lovac_communes.csv")
                return [csv_path]
            else:
                logger.error("Failed to download LOVAC CSV")
                return []

    def validate_schema(self, file_path: Path) -> None:
        with open(file_path, encoding="latin-1", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = {
            col
            for col in SOURCE_COLUMNS
            if col not in actual
            and not any(_is_year_column(col, candidate) for candidate in actual)
        }
        if missing:
            msg = f"LOVAC schema mismatch: missing {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        transformed_dir = get_download_dir("vacance") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "vacance-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="latin-1",
            low_memory=False,
        )

        selected_columns = _select_vacance_columns(df.columns)
        if self._prepared_columns is None:
            raise ValueError("LOVAC prepared column pair is missing")
        if selected_columns != self._prepared_columns:
            raise ValueError(
                "LOVAC prepared column pair mismatch: "
                f"expected {self._prepared_columns}, got {selected_columns}"
            )
        vacant_col, total_col = selected_columns
        year_suffix = vacant_col.rsplit("_", 1)[1]

        # Map columns — LOVAC open data is versioned by year suffix.
        col_map = {
            f"CODGEO_{year_suffix}": "code_commune",
            f"LIBGEO_{year_suffix}": "nom_commune",
            "DEP": "code_departement",
            "LIB_DEP": "nom_departement",
            "LIB_REG": "nom_region",
        }
        df = df.rename(columns=col_map)

        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Derive code_departement if missing
        if (
            df["code_departement"].isna().all()
            or (df["code_departement"].str.strip() == "").all()
        ):
            df["code_departement"] = df["code_commune"].str[:2]
            df.loc[
                df["code_commune"].str[:3].isin(["971", "972", "973", "974", "976"]),
                "code_departement",
            ] = df["code_commune"].str[:3]

        # Map numeric columns from the latest year suffix.
        df["nb_logements_prives"] = pd.to_numeric(
            df.get(total_col, pd.Series(dtype=str)),
            errors="coerce",
        )
        df["nb_logements_vacants"] = pd.to_numeric(
            df.get(vacant_col, pd.Series(dtype=str)),
            errors="coerce",
        )
        df["nb_vacants_plus_2ans"] = pd.to_numeric(
            df.get(f"pp_vacant_plus_2ans_{year_suffix}", pd.Series(dtype=str)),
            errors="coerce",
        )
        # Derive moins 2 ans = total vacants - plus 2 ans
        df["nb_vacants_moins_2ans"] = (
            df["nb_logements_vacants"] - df["nb_vacants_plus_2ans"]
        ).where((df["nb_logements_vacants"] >= 0) & (df["nb_vacants_plus_2ans"] >= 0))

        # Missing/invalid counts make the rate unavailable.
        # Keep observed zero numerators as zero when a denominator is known.
        prives = df["nb_logements_prives"].where(df["nb_logements_prives"] > 0)
        for rate, count in (
            ("taux_vacance", "nb_logements_vacants"),
            ("taux_vacance_moins_2ans", "nb_vacants_moins_2ans"),
            ("taux_vacance_plus_2ans", "nb_vacants_plus_2ans"),
        ):
            numerator = df[count].where(df[count] >= 0)
            # Round exact decimal ties: binary float turns 92/640*100 into
            # 14.374999..., incorrectly yielding 14.37 instead of 14.38.
            df[rate] = [
                float(
                    (Decimal(str(n)) * 100 / Decimal(str(d))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_EVEN
                    )
                )
                if pd.notna(n) and pd.notna(d)
                else None
                for n, d in zip(numerator, prives, strict=True)
            ]

        df["annee"] = str(2000 + int(year_suffix))

        df = df[df["code_commune"].notna() & (df["code_commune"].str.len() == 5)]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed vacance: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _vacance_staging_flat (
                        code_commune TEXT,
                        nom_commune TEXT,
                        code_departement TEXT,
                        nom_departement TEXT,
                        nom_region TEXT,
                        nb_logements_prives TEXT,
                        nb_logements_vacants TEXT,
                        nb_vacants_moins_2ans TEXT,
                        nb_vacants_plus_2ans TEXT,
                        taux_vacance TEXT,
                        taux_vacance_moins_2ans TEXT,
                        taux_vacance_plus_2ans TEXT,
                        annee TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _vacance_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _vacance_staging_flat ({columns}) "
                    f"FROM STDIN WITH "
                    f"(FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _vacance_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged vacance: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "vacance"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                cur.execute("DROP TABLE IF EXISTS vacance_staging CASCADE")
                cur.execute("DROP SEQUENCE IF EXISTS vacance_staging_id_seq CASCADE")
                cur.execute("""
                    CREATE TABLE vacance_staging
                    (LIKE vacance INCLUDING ALL EXCLUDING INDEXES)
                """)
                cur.execute("CREATE SEQUENCE vacance_staging_id_seq")
                cur.execute("""
                    ALTER TABLE vacance_staging
                    ALTER COLUMN id SET DEFAULT
                    nextval('vacance_staging_id_seq')
                """)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO vacance_staging
                        (code_commune, nom_commune,
                         code_departement,
                         nom_departement, nom_region,
                         nb_logements_prives,
                         nb_logements_vacants,
                         nb_vacants_moins_2ans,
                         nb_vacants_plus_2ans,
                         taux_vacance,
                         taux_vacance_moins_2ans,
                         taux_vacance_plus_2ans,
                         annee)
                    SELECT DISTINCT
                        NULLIF(code_commune, ''),
                        NULLIF(nom_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(nom_departement, ''),
                        NULLIF(nom_region, ''),
                        NULLIF(nb_logements_prives, '')
                            ::numeric::integer,
                        NULLIF(nb_logements_vacants, '')
                            ::numeric::integer,
                        NULLIF(nb_vacants_moins_2ans, '')
                            ::numeric::integer,
                        NULLIF(nb_vacants_plus_2ans, '')
                            ::numeric::integer,
                        NULLIF(taux_vacance, '')
                            ::numeric(7, 2),
                        NULLIF(taux_vacance_moins_2ans, '')
                            ::numeric(7, 2),
                        NULLIF(taux_vacance_plus_2ans, '')
                            ::numeric(7, 2),
                        NULLIF(annee, '')::integer
                    FROM _vacance_staging_flat
                    WHERE NULLIF(code_commune, '')
                        IS NOT NULL
                      AND NULLIF(annee, '') IS NOT NULL
                """)

                # Collapse identical published rows only. Conflicting values for
                # a natural key must fail here, before committing any generation.
                self.ensure_staging_constraints(cur)

                cur.execute("SELECT count(*) FROM vacance_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23): a scoped import is
                # never compared with rows outside its scope.
                old_count = self.count_production_rows(cur, "vacance")

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
                cur.execute("DROP TABLE IF EXISTS _vacance_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # has_department_partitions not overridden — default True,
                # but vacance has no partitions — call is safe (no-op).
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 035) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_vacance_code_commune", "(code_commune)"),
        ("idx_vacance_code_departement", "(code_departement)"),
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


def _is_year_column(expected: str, actual: str) -> bool:
    """Return true when columns share the same yearly LOVAC prefix."""
    expected_prefix = re.sub(r"_\d{2}$", "_", expected)
    return (
        actual.startswith(expected_prefix) and re.search(r"_\d{2}$", actual) is not None
    )


def _latest_year_suffix(columns: Iterable[str]) -> str:
    """Detect the latest CODGEO_YY suffix available in the LOVAC export."""
    suffixes = [
        match.group(1)
        for column in columns
        if (match := re.fullmatch(r"CODGEO_(\d{2})", str(column)))
    ]
    if not suffixes:
        raise ValueError("LOVAC schema mismatch: no CODGEO_YY column found")
    return max(suffixes, key=int)


def _first_existing_column(columns: Iterable[str], candidates: list[str]) -> str:
    """Return the first existing column from candidates."""
    available = set(columns)
    for candidate in candidates:
        if candidate in available:
            return candidate
    raise ValueError(f"LOVAC schema mismatch: missing any of {candidates}")


def _select_vacance_columns(columns: Iterable[str]) -> tuple[str, str]:
    """Select the numerator and denominator from the actual LOVAC header."""
    available = tuple(columns)
    year_suffix = _latest_year_suffix(available)
    previous_year_suffix = f"{int(year_suffix) - 1:02d}"
    vacant_column = f"pp_vacant_{year_suffix}"
    if vacant_column not in available:
        raise ValueError(f"LOVAC schema mismatch: missing {vacant_column}")
    total_column = _first_existing_column(
        available,
        [
            f"ff_pp_total_{previous_year_suffix}",
            f"pp_total_{previous_year_suffix}",
            f"ff_pp_total_{year_suffix}",
            f"pp_total_{year_suffix}",
        ],
    )
    return vacant_column, total_column


def _column_year(column: str) -> int:
    """Return the four-digit year encoded by a validated LOVAC column name."""
    return 2000 + int(column.rsplit("_", 1)[1])

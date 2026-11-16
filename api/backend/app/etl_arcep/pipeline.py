"""Arcep Ma Connexion Internet concrete ETL pipeline.

Downloads base_imb (building addresses) and actuel (eligibility) CSV files
department by department from data.arcep.fr, joins on id_imb with pandas,
bulk-loads via COPY into a flat staging table, and swaps into production.

IMPORTANT: Files are LARGE. Transform processes department by department
to avoid OOM. Never load the national file in memory.
"""

import asyncio
import csv
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pandas as pd
import psycopg

from app.etl.base import MILLESIME_QUARTER, BasePipeline, check_millesime_label
from app.etl.download import get_download_dir, verified_download
from app.etl.errors import MalformedMillesimeError
from app.etl_arcep.config import (
    BASE_IMB_EXPECTED_COLUMNS,
    COLUMN_RENAME,
    DOWNLOAD_TIMEOUT,
    ELIGIBILITE_EXPECTED_COLUMNS,
    OUTPUT_COLUMNS,
    arcep_urls,
    served_millesime,
)

if TYPE_CHECKING:
    from app.etl.source_archive import SourceReplay

logger = logging.getLogger(__name__)


# Flat rows kept by the transfer (unchanged rule: identified building and
# known technology).
_FLAT_ROWS_KEPT_SQL = (
    "f.id_imb IS NOT NULL AND f.id_imb != '' "
    "AND f.technologie IS NOT NULL AND f.technologie != ''"
)
# A flat row and its dictionary entry; empty texts are NULL in the dictionary.
# Plain equalities on coalesced texts: a hash join, never a nested loop.
_OFFER_JOIN_SQL = (
    "coalesce(o.operateur, '') = coalesce(f.operateur, '') "
    "AND o.technologie = f.technologie "
    "AND coalesce(o.debit_max, '') = coalesce(f.debit_max, '')"
)


class ArcepPipeline(BasePipeline):
    """Concrete ETL pipeline for Arcep broadband eligibility data."""

    # Quarterly folders next to `last` (plan 02-11, D-27).
    millesime_mechanism = "segment_date"

    def __init__(self) -> None:
        super().__init__()
        # Quarter the files of this run were read from (C24); None until a
        # download proves one (an archive replay downloads nothing).
        self._served_millesime: str | None = None

    def validate_millesime(self, label: str) -> None:
        check_millesime_label(self.dataset_name, label, MILLESIME_QUARTER)

    @property
    def source_period_label(self) -> str | None:
        """The targeted quarter, else the one `last` led every file to (C24),
        else the one the replayed archive record kept (C30)."""
        return self.pinned_millesime or self._served_millesime

    def restore_source_inputs(
        self, replay: "SourceReplay", directory: Path
    ) -> list[Path]:
        """Restore the archived files and the quarter their record kept (C30).

        A record written before C30, or a label that is not a quarter: no
        period, never the import day nor `last`.
        """
        label = replay.record.get("source_period_label")
        self._served_millesime = None
        if isinstance(label, str):
            try:
                self.validate_millesime(label)
            except MalformedMillesimeError:
                pass
            else:
                self._served_millesime = label
        return super().restore_source_inputs(replay, directory)

    # -- abstract properties ---------------------------------------------------

    @property
    def dataset_name(self) -> str:
        return "connectivite"

    @property
    def expected_columns(self) -> list[str]:
        return BASE_IMB_EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "eligibilite_fixe_staging"

    @property
    def production_table_name(self) -> str:
        return "eligibilite_fixe"

    @property
    def flat_staging_table_name(self) -> str:
        return "_eligibilite_fixe_staging_flat"

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def expected_file_count(self) -> int:
        return len(self.expected_department_coverage)

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~375M+ production eligibilite_fixe rows (commune-level aggregation)
        return self.scoped_min_rows(300_000_000)

    # -- override: non-partitioned staging ------------------------------------

    def create_staging_table(self, dsn: str) -> None:
        """Fresh import: drop flat rows left by a failed run, create the candidate.

        swap() recreates only the candidate (``_create_candidate``): the flat
        table then holds this run's rows. A fresh run must not append to the
        flat rows of an earlier run that failed before its flat drop (04.1-04).
        """
        self.drop_flat_staging(dsn)
        self._create_candidate(dsn)

    def _create_candidate(self, dsn: str) -> None:
        """Create non-partitioned staging table with its own id sequence."""
        staging = self.staging_table_name
        production = self.production_table_name

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
                # EXCLUDING INDEXES (DB-HYGIENE-01): INCLUDING ALL copies
                # every production index under an auto-generated name, which
                # then survives the swap and gets re-copied on the next run.
                cur.execute(
                    f"CREATE TABLE {staging} "
                    f"(LIKE {production} INCLUDING ALL EXCLUDING INDEXES)"
                )
                # Drop identity before setting custom sequence
                cur.execute(
                    f"ALTER TABLE {staging} ALTER COLUMN id DROP IDENTITY IF EXISTS"
                )
                # Drop leftover sequence from previous runs — it may be
                # owned by the production table after a swap, so CASCADE.
                cur.execute(f"DROP SEQUENCE IF EXISTS {staging}_id_seq CASCADE")
                cur.execute(f"CREATE SEQUENCE {staging}_id_seq")
                cur.execute(
                    f"ALTER TABLE {staging} "
                    f"ALTER COLUMN id "
                    f"SET DEFAULT nextval('{staging}_id_seq')"
                )
                cur.execute(f"ALTER SEQUENCE {staging}_id_seq OWNED BY {staging}.id")
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
        """Download base_imb + actuel CSV files per department.

        Returns list of department directories, each containing
        base_imb_{dept}.csv.gz and actuel_{dept}.csv.gz.
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage (parallel)."""
        raw_dir = get_download_dir("connectivite") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        departements = self.expected_department_coverage
        total = len(departements)
        final_urls: list[str] = []

        async def _download_one(
            client: httpx.AsyncClient, dept: str, index: int
        ) -> Path | None:
            dept_dir = raw_dir / dept
            dept_dir.mkdir(parents=True, exist_ok=True)

            base_imb_url, eligibilite_url = arcep_urls(dept, self.pinned_millesime)
            try:
                base_ok = await verified_download(
                    client,
                    base_imb_url,
                    dept_dir / f"base_imb_{dept}.csv.gz",
                    on_final_url=final_urls.append,
                )
                elig_ok = await verified_download(
                    client,
                    eligibilite_url,
                    dept_dir / f"actuel_{dept}.csv.gz",
                    on_final_url=final_urls.append,
                )

                if base_ok and elig_ok:
                    logger.info("Downloaded dept %s (%d/%d)", dept, index, total)
                    return dept_dir
                logger.warning(
                    "Dept %s: download returned 404 (%d/%d)", dept, index, total
                )
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Dept %s failed after retries: %s (%d/%d)",
                    dept,
                    exc,
                    index,
                    total,
                )
                return None

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            results = await asyncio.gather(
                *[
                    _download_one(client, dept, i)
                    for i, dept in enumerate(departements, 1)
                ]
            )

        paths = [p for p in results if p is not None]
        self._served_millesime = served_millesime(final_urls)
        failed_count = total - len(paths)
        if failed_count:
            logger.warning(
                "Arcep download: %d/%d succeeded, %d failed",
                len(paths),
                total,
                failed_count,
            )
        else:
            logger.info("Downloaded %d department pairs total", len(paths))

        return paths

    def validate_schema(self, file_path: Path) -> None:
        """Validate that department directory contains valid CSV headers.

        Reads first few lines of each gz file to check expected columns.
        file_path is a department directory containing the gz files.
        """
        dept = file_path.name

        # Validate base_imb
        base_path = file_path / f"base_imb_{dept}.csv.gz"
        self._validate_csv_header(
            base_path,
            BASE_IMB_EXPECTED_COLUMNS,
            f"base_imb_{dept}",
        )

        # Validate actuel
        elig_path = file_path / f"actuel_{dept}.csv.gz"
        self._validate_csv_header(
            elig_path,
            ELIGIBILITE_EXPECTED_COLUMNS,
            f"actuel_{dept}",
        )

    @staticmethod
    def _validate_csv_header(
        path: Path,
        expected: list[str],
        label: str,
    ) -> None:
        """Read gz CSV header and check for required columns."""
        import gzip

        with gzip.open(path, "rt", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader)

        actual = {col.strip().lower() for col in header}
        required = {col.lower() for col in expected}

        extra = actual - required
        missing = required - actual

        if extra:
            logger.warning(
                "Arcep %s has %d extra columns",
                label,
                len(extra),
            )

        if missing:
            msg = (
                f"Arcep schema mismatch in {label}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform a single department: join base_imb + actuel on id_imb.

        file_path is a department directory. Reads both gz CSVs with pandas,
        inner-joins on id_imb, files every row under the departement of the
        file (code_departement), writes merged CSV.
        """
        transformed_dir = get_download_dir("connectivite") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        dept = file_path.name
        base_path = file_path / f"base_imb_{dept}.csv.gz"
        elig_path = file_path / f"actuel_{dept}.csv.gz"

        output_path = transformed_dir / f"merged_{dept}.csv"

        # Read base_imb: keep only address columns
        base_df = pd.read_csv(
            base_path,
            sep=";",
            dtype=str,
            usecols=lambda c: (
                c.strip().lower() in {col.lower() for col in BASE_IMB_EXPECTED_COLUMNS}
            ),
        )
        base_df.columns = [c.strip().lower() for c in base_df.columns]
        base_df = base_df[[c.lower() for c in BASE_IMB_EXPECTED_COLUMNS]].copy()

        # Read actuel: keep only eligibility columns + code_operateur
        elig_cols_lower = {col.lower() for col in ELIGIBILITE_EXPECTED_COLUMNS}
        elig_cols_lower.add("code_operateur")

        elig_df = pd.read_csv(
            elig_path,
            sep=";",
            dtype=str,
            usecols=lambda c: c.strip().lower() in elig_cols_lower,
        )
        elig_df.columns = [c.strip().lower() for c in elig_df.columns]

        # Rename Arcep columns to internal names before join
        rename_lower = {k.lower(): v for k, v in COLUMN_RENAME.items()}
        base_df = base_df.rename(columns=rename_lower)
        elig_df = elig_df.rename(columns=rename_lower)

        # Inner join on id_imb
        merged = base_df.merge(elig_df, on="id_imb", how="inner")

        logger.info(
            "Dept %s: base_imb=%d, actuel=%d, merged=%d",
            dept,
            len(base_df),
            len(elig_df),
            len(merged),
        )

        # The departement of the file, like BD TOPO: a building of this file
        # may be addressed in another departement (04.1-21: 2 buildings of
        # base_imb_67 in 57); code_insee keeps the address (Ianis decision).
        merged["code_departement"] = dept

        # Ensure all output columns exist
        for col in OUTPUT_COLUMNS:
            if col not in merged.columns:
                merged[col] = ""

        # Write output CSV
        merged[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed dept %s: %d merged rows", dept, len(merged))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV into _eligibilite_fixe_staging_flat.

        Flat staging table with all TEXT columns for maximum COPY throughput.
        Type casting happens during swap.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent). Unlogged: rebuilt
                # from the sources after a crash, never published (04.1-04).
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _eligibilite_fixe_staging_flat (
                        id_imb TEXT,
                        code_insee TEXT,
                        voie TEXT,
                        numero TEXT,
                        complement TEXT,
                        technologie TEXT,
                        debit_max TEXT,
                        operateur TEXT,
                        code_departement TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _eligibilite_fixe_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _eligibilite_fixe_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _eligibilite_fixe_staging_flat")
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

        Phase A1: create staging + INSERT SELECT (4h timeout) + count + safety
                  check + DROP of the flat table, one transaction.
        Phase A2: index builds on the candidate, own transaction: the flat table
                  is already gone, so both never share the disk (04.1-04).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — idempotent flat drop, rename sequence.

        Interrupted between A1 and A2, the flat table is missing: the runner's
        resume rebuilds the candidate from the sources.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "eligibilite_fixe"
        old = f"{prod}_old"

        # --- Phase A1: transfer, then free the flat table ---------------------
        # Create non-partitioned staging table first (the flat table stays)
        self._create_candidate(dsn)

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # INSERT SELECT on 405M rows can exceed the default 2h timeout;
                # use a 4h ceiling specific to the connectivite swap.
                self.set_statement_timeout(cur, seconds=14400)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                self._complete_offer_dictionary(cur)
                # One row per building and its address (plan 04.1-16), its
                # offer rows coded in ``offres``, duplicates kept (multiset).
                cur.execute(f"""
                    INSERT INTO eligibilite_fixe_staging
                        (id_imb, code_insee, voie, numero, complement,
                         code_departement, offres)
                    SELECT
                        NULLIF(f.id_imb, ''),
                        NULLIF(f.code_insee, ''),
                        NULLIF(f.voie, ''),
                        NULLIF(f.numero, ''),
                        NULLIF(f.complement, ''),
                        NULLIF(f.code_departement, ''),
                        array_agg(o.code ORDER BY o.code)
                    FROM _eligibilite_fixe_staging_flat f
                    JOIN eligibilite_offres o ON {_OFFER_JOIN_SQL}
                    WHERE {_FLAT_ROWS_KEPT_SQL}
                    GROUP BY 1, 2, 3, 4, 5, 6
                """)

                cur.execute("SELECT count(*) FROM eligibilite_fixe_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, "eligibilite_fixe")

                # Safety: abort if staging has <50% of production rows
                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                cur.execute(
                    "DROP TABLE IF EXISTS _eligibilite_fixe_staging_flat CASCADE"
                )

            conn.commit()  # A1 commits: candidate loaded, flat table freed

        # --- Phase A2: indexes on the candidate alone --------------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur, seconds=14400)
                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)
            conn.commit()  # Phase A2 commits before Phase B opens

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute(
                    "DROP TABLE IF EXISTS _eligibilite_fixe_staging_flat CASCADE"
                )
                # Sequence rename — IF EXISTS guards safe for pipelines without
                # a staging sequence
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # Non-partitioned staging — no partition rename needed
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging")

    @staticmethod
    def _complete_offer_dictionary(cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        """Add the (operator, technology, speed) triples not yet coded.

        The dictionary is never swapped, emptied nor renumbered: a code keeps
        its meaning for every published generation. New codes follow the
        largest one, in a fixed order; the lock serializes two imports.
        """
        cur.execute("LOCK TABLE eligibilite_offres IN EXCLUSIVE MODE")
        cur.execute(f"""
            INSERT INTO eligibilite_offres (code, operateur, technologie, debit_max)
            SELECT
                (SELECT coalesce(max(code), 0) FROM eligibilite_offres)
                    + row_number() OVER (ORDER BY technologie, operateur, debit_max),
                operateur, technologie, debit_max
            FROM (
                SELECT DISTINCT
                    NULLIF(f.operateur, '') AS operateur,
                    NULLIF(f.technologie, '') AS technologie,
                    NULLIF(f.debit_max, '') AS debit_max
                FROM _eligibilite_fixe_staging_flat f
                WHERE {_FLAT_ROWS_KEPT_SQL}
                  AND NOT EXISTS (
                      SELECT 1 FROM eligibilite_offres o WHERE {_OFFER_JOIN_SQL}
                  )
            ) nouveaux
        """)

    # Production index names (migration 010) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_eligibilite_code_insee", "(code_insee)"),
        ("idx_eligibilite_code_insee_voie", "(code_insee, voie)"),
        ("idx_eligibilite_id_imb", "(id_imb)"),
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

registry.register(ArcepPipeline)

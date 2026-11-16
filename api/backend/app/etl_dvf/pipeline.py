"""DVF (Demandes de Valeurs Foncieres) concrete ETL pipeline.

Downloads multi-year, multi-department geolocalized DVF CSV files from
data.gouv.fr, validates the schema, transforms via pandas, bulk-loads
via COPY into a flat staging table, INSERT SELECTs with ST_MakePoint
into the partitioned staging table, and swaps atomically.
"""

import asyncio
import csv
import json
import logging
import time
from datetime import date
from pathlib import Path

import httpx
import psycopg

from app.common.source_catalog import DATASET_CATALOG
from app.etl.base import (
    DEPARTEMENTS,
    MILLESIME_MONTH,
    BasePipeline,
    check_millesime_label,
)
from app.etl.download import get_download_dir, verified_download_gz
from app.etl.errors import FatalInputError
from app.etl.source_archive import (
    SourceArchiveError,
    SourceReplay,
    _check_names,
    compose_run_id,
)
from app.etl_dvf.config import (
    DOWNLOAD_TIMEOUT,
    EXPECTED_COLUMNS,
    MARQUEUR_ADRESSE_SUFFIXE,
    OUTPUT_COLUMNS,
    YEARS,
    get_csv_url,
)
from app.etl_dvf.reconstruction import reconstruit_mutations_grain
from app.etl_dvf.transform import transform_dvf_csv

logger = logging.getLogger(__name__)

# Departements the producer does not publish: read from the catalogue, the
# only territorial authority per source (never a second list here).
DVF_NO_DATA_DEPARTEMENTS = frozenset(
    DATASET_CATALOG["dvf"].source_coverage.departements_exclus
)

# Above this share of 404s, treat the absences as a source outage (mirror
# down) rather than legitimate pruning, and keep the full success-ratio
# denominator so the gate aborts an incomplete refresh.
_MAX_NOT_FOUND_RATIO = 0.4

# Window of the mutation dates of a published DVF table (C24): the period of
# its values. Table names are code constants, never input.
DATE_WINDOW_SQL = "SELECT min(date_mutation), max(date_mutation) FROM {table}"

# Layout of _mutations_staging_flat, part of the resume identity: a flat table
# staged by a code without the `source_line` ordinal (plan 04.4-05) cannot be
# reused, its transfer would fail or follow the physical order again.
_FLAT_LAYOUT = "source_line"


def window_label(first: date | None, last: date | None) -> str | None:
    """Label of the window of mutation dates actually published (C24).

    ``AAAA-MM-JJ → AAAA-MM-JJ``; None for an empty table: no period invented.
    """
    if first is None or last is None:
        return None
    return f"{first.isoformat()} → {last.isoformat()}"


class DvfPipeline(BasePipeline):
    """Concrete ETL pipeline for DVF property transactions (~20M rows)."""

    # Dated release folder next to `latest` (plan 02-11, D-27).
    millesime_mechanism = "segment_date"

    # run_etl --from-archive (plan 04.4-03): same doctrine as BD TOPO, the
    # archived objects are linked, never copied. A replay reimports the exact
    # files of an archived edition (the 04.4 measure) instead of a new one.
    archive_replay = True

    def validate_millesime(self, label: str) -> None:
        check_millesime_label(self.dataset_name, label, MILLESIME_MONTH)

    def __init__(self) -> None:
        super().__init__()
        # Count of 404s seen during download(): vintages pruned from the
        # `latest` mirror, or (year, dept) pairs with no data.  Excluded from
        # the success-ratio denominator (see expected_file_count).
        self._not_found_count: int = 0
        # Files of the archived record during a replay; None otherwise.
        self._replayed_file_count: int | None = None
        # Window of the mutation dates this run publishes, read at the swap
        # (C24); None until then.
        self._published_window: str | None = None

    def restore_source_inputs(
        self, replay: SourceReplay, directory: Path
    ) -> list[Path]:
        """Link the files of an archived DVF record, after checking it.

        Refused before any link (FatalInputError): a path that is not the
        ``raw/<AAAA>/<dept>.csv`` written by ``download()``; a set of
        departements different from the hosted ones the source covers (a
        hosted departement missing, or one the instance does not host); an
        edition that is not every hosted departement of each of its years
        (``_check_complete_edition``); a record whose run, or a run it
        replays, did not succeed (``_check_lineage_succeeded``). The record
        is written before the success gate of its own import (WR-01): its
        files alone never prove that import was accepted.
        """
        departements: set[str] = set()
        pairs: set[tuple[int, str]] = set()
        for entry in replay.files:
            relpath = entry["relpath"]
            parts = relpath.split("/")
            if (
                len(parts) != 3
                or parts[0] != "raw"
                or not (len(parts[1]) == 4 and parts[1].isdigit())
                or not parts[2].endswith(".csv")
                or parts[2][: -len(".csv")] not in DEPARTEMENTS
            ):
                raise FatalInputError(
                    f"DVF: chemin d'archive hors forme raw/<AAAA>/<dept>.csv "
                    f"({relpath}) dans le record {replay.run_id} : rejeu refusé"
                )
            departements.add(parts[2][: -len(".csv")])
            pairs.add((int(parts[1]), parts[2][: -len(".csv")]))
        expected = set(self.expected_department_coverage)
        if departements != expected:
            raise FatalInputError(
                f"DVF: départements du record {replay.run_id} "
                f"{sorted(departements)} différents des départements hébergés "
                f"{sorted(expected)} : rejeu refusé"
            )
        self._check_complete_edition(replay.run_id, pairs)
        self._check_lineage_succeeded(replay)
        paths = replay.link(directory)
        self._replayed_file_count = len(paths)
        return paths

    def _check_complete_edition(self, run_id: str, pairs: set[tuple[int, str]]) -> None:
        """The record is every hosted departement of each of its years.

        Its years are contiguous and among those ``download()`` reads
        (YEARS): the only legitimate absences of a download are the 404s of
        the oldest vintages (pruned from the mirror) and of the newest (not
        yet published), never a year inside the edition nor one departement
        of a year. The 404s are not in the record, so the expected set is
        built from its own years; ``_check_lineage_succeeded`` judges the rest.
        """
        years = sorted({year for year, _ in pairs})
        if (
            years[0] < YEARS[0]
            or years[-1] > YEARS[-1]
            or years != list(range(years[0], years[-1] + 1))
        ):
            raise FatalInputError(
                f"DVF: années du record {run_id} {years} hors de "
                f"{YEARS[0]} à {YEARS[-1]} ou non contiguës : rejeu refusé"
            )
        missing = [
            f"{year}/{dept}"
            for year in years
            for dept in self.expected_department_coverage
            if (year, dept) not in pairs
        ]
        if missing:
            raise FatalInputError(
                f"DVF: le record {run_id} ne couvre pas chaque département "
                f"hébergé de chacune de ses années (manquent : "
                f"{', '.join(missing)}) : rejeu refusé"
            )

    def _check_lineage_succeeded(self, replay: SourceReplay) -> None:
        """etl_runs says ``success`` for the record's run and each run it replays.

        The journal is the only state written after the success gate (the
        record is immutable and written before it). A run is found by the
        composed id of its record (``<etl_runs.id>-<started_at>``): a row
        under the same id with another start is another run. A replay record
        is walked up through ``replayed_from`` to the download it replays;
        an ancestor record missing from the archive leaves the lineage
        unproven, and is refused.
        """
        refused = f"(record {replay.run_id}) : rejeu refusé"
        record = replay.record
        seen: set[str] = set()
        with psycopg.connect(self.get_sync_dsn()) as conn:
            while True:
                run = str(record.get("run_id"))
                if run in seen:
                    raise FatalInputError(f"DVF: lignée cyclique en {run} {refused}")
                seen.add(run)
                etl_id, sep, _ = run.partition("-")
                row = (
                    conn.execute(
                        "SELECT status, started_at FROM etl_runs "
                        "WHERE id = %s AND dataset_name = %s",
                        (int(etl_id), self.dataset_name),
                    ).fetchone()
                    if sep and etl_id.isdigit()
                    else None
                )
                if row is None or compose_run_id(int(etl_id), row[1]) != run:
                    raise FatalInputError(
                        f"DVF: run d'origine {run} absent du journal etl_runs {refused}"
                    )
                if row[0] != "success":
                    raise FatalInputError(
                        f"DVF: run d'origine {run} au statut {row[0]}, "
                        f"pas success {refused}"
                    )
                parent = record.get("replayed_from")
                if not parent:
                    return
                record = self._ancestor_record(replay, str(parent), refused)

    def _ancestor_record(self, replay: SourceReplay, run_id: str, refused: str) -> dict:
        """The archived record of *run_id*, read only for its lineage."""
        try:
            _check_names(self.dataset_name, run_id)
            path = replay.root / self.dataset_name / "runs" / f"{run_id}.json"
            record = json.loads(path.read_bytes())
        except (SourceArchiveError, OSError, ValueError):
            raise FatalInputError(
                f"DVF: record {run_id} absent ou illisible dans l'archive, "
                f"lignée invérifiable {refused}"
            ) from None
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            raise FatalInputError(
                f"DVF: record {run_id} incohérent, lignée invérifiable {refused}"
            )
        return record

    # -- abstract properties ---------------------------------------------------

    @property
    def dataset_name(self) -> str:
        return "dvf"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def source_period_label(self) -> str | None:
        """The window of mutation dates this run published, else the release
        targeted by ``--millesime`` (C24): the dates of the values, not the
        day of the import.
        """
        return self._published_window or super().source_period_label

    @property
    def staging_table_name(self) -> str:
        return "mutations_staging"

    @property
    def production_table_name(self) -> str:
        return "mutations"

    @property
    def published_old_tables(self) -> tuple[str, ...]:
        # The reconstruction swaps both derived tables in the same run.
        return ("mutations_old", "mutations_grain_old", "mutation_parcelles_old")

    @property
    def flat_staging_table_name(self) -> str:
        return "_mutations_staging_flat"

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def expected_file_count(self) -> int:
        """Files the success ratio divides by (read by the runner after download).

        The `latest` mirror prunes old vintages and never publishes DVF for
        Alsace-Moselle (57/67/68); both return 404 and produce no file.  Those
        legitimate absences are subtracted so a complete refresh is not failed
        by files that cannot exist.  A 5xx/timeout raises (not a 404) and stays
        counted, so the gate still catches a real outage.

        Guard: if 404s exceed _MAX_NOT_FOUND_RATIO of the theoretical max, the
        mirror is likely down — keep the full denominator so the gate aborts.

        During a replay, the files of the record: the 404s of the original
        edition are not in it (without this, a 10-file record would be judged
        on 14 and refused below 90 %). The record alone proves nothing: it is
        written before the gate of its own import. ``restore_source_inputs``
        admits it only if it is every hosted departement of each of its
        contiguous years and if its run, and each run it replays, succeeded
        in etl_runs (WR-01).
        """
        if self._replayed_file_count is not None:
            return self._replayed_file_count
        depts = self.expected_department_coverage
        theoretical = len(YEARS) * len(depts)
        if self._not_found_count > theoretical * _MAX_NOT_FOUND_RATIO:
            return theoretical
        return theoretical - self._not_found_count

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~6M production mutations (DVF) rows
        return self.scoped_min_rows(5_000_000)

    def checkpoint_identity(self, scope_key: str) -> str:
        """The common resume identity plus the layout of the flat table."""
        identity = json.loads(super().checkpoint_identity(scope_key))
        identity["flat_layout"] = _FLAT_LAYOUT
        return json.dumps(identity, sort_keys=True, separators=(",", ":"))

    def checkpoint_file_id(self, file_path: Path) -> str:
        """Keep the producer year and department after cache/archive relocation."""
        year = file_path.parent.name
        department = file_path.stem
        if not year.isdigit() or len(year) != 4 or department not in DEPARTEMENTS:
            raise ValueError(f"Invalid DVF source identity: {file_path}")
        return f"{year}/{file_path.name}"

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download DVF CSV files for all years and departments.

        Uses asyncio internally for HTTP requests with retry and
        exponential backoff. Handles 404 gracefully (some departments
        like Alsace-Moselle 57/67/68 have no DVF data).
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        raw_dir = get_download_dir("dvf") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        # Departements configured in base that DVF publishes (Alsace-Moselle
        # excluded), so the download matrix matches expected_file_count.
        departements = self.expected_department_coverage

        paths: list[Path] = []
        failed_count = 0
        self._not_found_count = 0  # reset for idempotent re-runs
        self._replayed_file_count = None
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            total = len(YEARS) * len(departements)
            count = 0
            for year in YEARS:
                year_dir = raw_dir / str(year)
                year_dir.mkdir(parents=True, exist_ok=True)
                for dept in departements:
                    count += 1
                    output_path = year_dir / f"{dept}.csv"
                    try:
                        downloaded = await self._download_file(
                            client, year, dept, output_path
                        )
                        if downloaded:
                            paths.append(output_path)
                        else:
                            # 404: vintage pruned from the mirror, or no data
                            # for this (year, dept).  Excluded from the success
                            # ratio denominator, not a failure.
                            self._not_found_count += 1
                    except Exception as exc:  # noqa: BLE001
                        failed_count += 1
                        logger.warning("DVF %s/%s failed: %s", year, dept, exc)
                    if count % 50 == 0:
                        logger.info(
                            "Download progress: %d/%d files (%d failed)",
                            count,
                            total,
                            failed_count,
                        )

        if failed_count or self._not_found_count:
            logger.warning(
                "DVF download: %d files downloaded, %d failed, %d not found (404)",
                len(paths),
                failed_count,
                self._not_found_count,
            )
        else:
            logger.info("Downloaded %d DVF files total", len(paths))

        return paths

    async def _download_file(
        self,
        client: httpx.AsyncClient,
        year: int,
        dept: str,
        output_path: Path,
    ) -> bool:
        """Download a single DVF CSV with streaming decompression.

        Returns True if the file was downloaded, False if 404 (not found).
        DVF does NOT cover Alsace-Moselle (57, 67, 68) and some DOM
        departments may not have data for all years.

        Retry and backoff are handled internally by streaming_download_gz.
        """
        url = get_csv_url(year, dept, self.pinned_millesime)
        return await verified_download_gz(client, url, output_path)

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the DVF CSV header contains expected columns.

        Tolerates extra columns (logged as WARNING) but rejects missing
        required columns with a ValueError listing the missing ones.
        """
        with open(file_path, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=",")
            header = next(reader)

        # Normalize: strip whitespace, lowercase
        actual = {col.strip().lower() for col in header}
        expected = {col.lower() for col in EXPECTED_COLUMNS}

        missing = expected - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "DVF CSV %s has %d extra columns (expected %d, got %d)",
                file_path.name,
                len(extra),
                len(expected),
                len(actual),
            )

        if missing:
            msg = (
                f"DVF schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform a raw DVF CSV into a clean CSV for COPY.

        Delegates to transform_dvf_csv which handles:
        - Column selection and reordering
        - valeur_fonciere decimal comma fix
        - Keeping rows without coordinates (phase 04.4 D-01: every source
          line is reconstructed; its position is NULL, set at the swap)
        - Injecting code_departement from code_commune
        """
        output_path = transform_dvf_csv(file_path)
        logger.info("Transformed %s", file_path.name)
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load a transformed CSV into _mutations_staging_flat via COPY.

        The flat staging table has no partitioning and no geometry column
        for maximum COPY throughput. Geometry is computed later during swap.

        D-02: the 12 disposition/lot/Carrez columns are staged here as
        TEXT/VARCHAR (same doctrine as the rest of this flat table — wide
        types, typed conversion deferred to the INSERT SELECT in swap()).
        Per RESEARCH.md Pitfall 3, `nombre_lots` is a PER-LOCAL metadatum
        (it varies line-by-line within a single disposition in the real
        geo-dvf file) — it is transported as-is here and in swap(), never
        aggregated (SUM/MAX/COUNT) into a lot total for the mutation.

        Plan 04.4-05: `source_line` is not in the COPY list, so its identity
        numbers the rows in COPY order, file after file in the order the
        runner stages them (the record order), then line after line. A
        resume reuses the table and its sequence: the files staged next
        follow. swap() transfers in that order, never the physical one.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Idempotent create — base.create_staging_table() already dropped
                # any stale flat table before the per-file loop started.
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _mutations_staging_flat (
                        source_line BIGINT GENERATED ALWAYS AS IDENTITY,
                        code_departement VARCHAR(3) NOT NULL,
                        id_mutation TEXT NOT NULL,
                        date_mutation TEXT NOT NULL,
                        nature_mutation VARCHAR(50),
                        valeur_fonciere TEXT,
                        adresse_numero VARCHAR(10),
                        adresse_suffixe TEXT,
                        adresse_nom_voie TEXT,
                        code_postal VARCHAR(5),
                        code_commune VARCHAR(5),
                        nom_commune TEXT,
                        id_parcelle VARCHAR(14),
                        code_type_local VARCHAR(5),
                        type_local VARCHAR(50),
                        surface_reelle_bati TEXT,
                        nombre_pieces_principales TEXT,
                        surface_terrain TEXT,
                        code_nature_culture VARCHAR(5),
                        nature_culture VARCHAR(100),
                        code_nature_culture_speciale VARCHAR(5),
                        nature_culture_speciale VARCHAR(100),
                        longitude TEXT,
                        latitude TEXT,
                        numero_disposition VARCHAR(6),
                        nombre_lots TEXT,
                        lot1_numero VARCHAR(20),
                        lot1_surface_carrez TEXT,
                        lot2_numero VARCHAR(20),
                        lot2_surface_carrez TEXT,
                        lot3_numero VARCHAR(20),
                        lot3_surface_carrez TEXT,
                        lot4_numero VARCHAR(20),
                        lot4_surface_carrez TEXT,
                        lot5_numero VARCHAR(20),
                        lot5_surface_carrez TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _mutations_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _mutations_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _mutations_staging_flat")
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
        """Transfer flat staging -> partitioned staging, rebuild indexes, swap.

        Phase A: INSERT SELECT flat->partitioned + CREATE INDEX (own transaction).
        Phase B: Atomic swap via swap_tables() — logged guard, DROP old, two RENAMEs.
        Phase C: Post-swap cleanup — drop flat staging, rename sequence + partitions.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "mutations"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                # -- Transfer flat -> partitioned staging ------------------
                logger.info("Transferring flat staging to partitioned staging")
                t0 = time.perf_counter()

                # D-02: mutations_staging is created by base.create_staging_table()
                # as `CREATE TABLE mutations_staging (LIKE mutations
                # INCLUDING ALL EXCLUDING INDEXES)` — migration 087's columns on
                # `mutations` therefore already exist on mutations_staging with no
                # change needed to app/etl/base.py. Only this INSERT SELECT (and
                # the flat DDL above) need to name the 12 new columns explicitly.
                # Plan 04.4-05: `id` follows the source order. The
                # reconstruction takes the address of the first line by `id`
                # (D-04); when `id` came from nextval() in the heap's read
                # order, 35 of 294 998 mutations on the work base served another
                # address than the source. `id` is the COPY ordinal itself:
                # no ORDER BY, so no sort of the whole flat table spilling to
                # temporary files beyond work_mem (WR-1, +549 MB at 44/85,
                # unbounded nationally), and no reliance on the evaluation
                # order of nextval() above a Sort (IN-1). Gaps left by an
                # aborted COPY do not matter: only the order is read.
                cur.execute("""
                    INSERT INTO mutations_staging
                        (id, code_departement, id_mutation, date_mutation,
                         nature_mutation, valeur_fonciere,
                         adresse_numero, adresse_suffixe, adresse_nom_voie,
                         code_postal, code_commune, nom_commune,
                         id_parcelle, code_type_local, type_local,
                         surface_reelle_bati, nombre_pieces_principales,
                         surface_terrain,
                         code_nature_culture, nature_culture,
                         code_nature_culture_speciale,
                         nature_culture_speciale,
                         longitude, latitude,
                         numero_disposition, nombre_lots,
                         lot1_numero, lot1_surface_carrez,
                         lot2_numero, lot2_surface_carrez,
                         lot3_numero, lot3_surface_carrez,
                         lot4_numero, lot4_surface_carrez,
                         lot5_numero, lot5_surface_carrez,
                         position)
                    SELECT
                        source_line, code_departement, id_mutation,
                        date_mutation::date,
                        nature_mutation,
                        valeur_fonciere::numeric(15, 2),
                        adresse_numero, adresse_suffixe, adresse_nom_voie,
                        code_postal, code_commune, nom_commune,
                        id_parcelle, code_type_local, type_local,
                        NULLIF(surface_reelle_bati, '')::numeric::integer,
                        NULLIF(nombre_pieces_principales, '')::numeric::integer,
                        NULLIF(surface_terrain, '')::numeric::integer,
                        code_nature_culture, nature_culture,
                        code_nature_culture_speciale,
                        nature_culture_speciale,
                        NULLIF(longitude, '')::double precision,
                        NULLIF(latitude, '')::double precision,
                        numero_disposition,
                        NULLIF(nombre_lots, '')::numeric::smallint,
                        lot1_numero,
                        NULLIF(lot1_surface_carrez, '')::numeric(10, 2),
                        lot2_numero,
                        NULLIF(lot2_surface_carrez, '')::numeric(10, 2),
                        lot3_numero,
                        NULLIF(lot3_surface_carrez, '')::numeric(10, 2),
                        lot4_numero,
                        NULLIF(lot4_surface_carrez, '')::numeric(10, 2),
                        lot5_numero,
                        NULLIF(lot5_surface_carrez, '')::numeric(10, 2),
                        -- D-01 (phase 04.4): no clause on the coordinates,
                        -- every line is loaded. ST_MakePoint and ST_SetSRID
                        -- are STRICT: a missing coordinate gives a NULL
                        -- position, never a point at (0, 0).
                        ST_SetSRID(
                            ST_MakePoint(
                                NULLIF(longitude, '')::double precision,
                                NULLIF(latitude, '')::double precision
                            ),
                            4326
                        )
                    FROM _mutations_staging_flat
                """)
                # The sequence default of `id` was bypassed: move it past
                # the ordinals so a later insert can never collide.
                cur.execute(
                    "SELECT setval('mutations_staging_id_seq', max(id)) "
                    "FROM mutations_staging"
                )

                # C44: this generation carries the suffix; the comment travels
                # with the RENAME and lets the reconstruction mark its grain.
                cur.execute(
                    "COMMENT ON COLUMN mutations_staging.adresse_suffixe IS "
                    f"'{MARQUEUR_ADRESSE_SUFFIXE}'"
                )

                cur.execute("SELECT count(*) FROM mutations_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                cur.execute(DATE_WINDOW_SQL.format(table="mutations_staging"))
                window = cur.fetchone()
                self._published_window = window_label(*window) if window else None
                logger.info(
                    "Partitioned staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                # -- Rebuild indexes on staging ----------------------------
                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)

                # -- Safety check -----------------------------------------
                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, "mutations")

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

                # Drop flat staging
                cur.execute("DROP TABLE IF EXISTS _mutations_staging_flat CASCADE")

                # Rename staging sequence to production name (IF EXISTS guards
                # make this safe even if no sequence exists on this staging table)
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                self.rename_partitions_after_swap(cur)
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed partitions")

        # --- Reconstruction: mutations_grain + mutation_parcelles (D-04) -----
        # Runs in the SAME pass, after `mutations` carries its definitive name
        # and its partitions/indexes are renamed (Phase C above), so the
        # reconstruction reads the exact rows this run just published. No
        # try/except here on purpose: D-04's guarantee is that `mutations`
        # and `mutations_grain` never diverge, so a reconstruction failure
        # must fail the whole run rather than leave a stale derived table
        # behind a freshly swapped `mutations` (T-57-18/T-57-21).
        reconstruction_counts = reconstruit_mutations_grain(
            dsn, self.partition_department_scope
        )
        logger.info(
            "Reconstruction complete: %d mutations, %d parcel links, "
            "%d divergent values",
            reconstruction_counts["mutations_reconstruites"],
            reconstruction_counts["liaisons_parcelles"],
            reconstruction_counts["valeur_divergente_count"],
        )

    @staticmethod
    def _create_staging_indexes(cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        """Create production-equivalent indexes on the staging table."""
        # The planar and geography GiST indexes on position left with
        # migration 107 (registry of plan 04.1-23: in no plan of the 57
        # measured statements).
        indexes = [
            (
                "idx_mutations_staging_code_commune",
                "CREATE INDEX idx_mutations_staging_code_commune "
                "ON mutations_staging (code_commune)",
            ),
            (
                "idx_mutations_staging_id_mutation",
                "CREATE INDEX idx_mutations_staging_id_mutation "
                "ON mutations_staging (id_mutation)",
            ),
            (
                "idx_mutations_staging_date_mutation",
                "CREATE INDEX idx_mutations_staging_date_mutation "
                "ON mutations_staging (date_mutation)",
            ),
            (
                "idx_mutations_staging_dept_date",
                "CREATE INDEX idx_mutations_staging_dept_date "
                "ON mutations_staging (code_departement, date_mutation)",
            ),
            # Migration 072's index, rebuilt here so it survives the swap.
            # `canonical_index_name` strips the staging table name once,
            # yielding `idx_mutations_id_parcelle` — the production name, to
            # the character. Without it the Fiche's nominal access path falls
            # back to a sequential partition scan (57-PERF-EXPLAIN.md § 6.3).
            (
                "idx_mutations_staging_id_parcelle",
                "CREATE INDEX idx_mutations_staging_id_parcelle "
                "ON mutations_staging (id_parcelle)",
            ),
        ]

        for name, ddl in indexes:
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(ddl)


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(DvfPipeline)

"""Transport concrete ETL pipeline (arrets + gares).

Downloads the consolidated GTFS stops CSV (~417 MB) and SNCF gares CSV,
validates schemas, deduplicates stops by name + rounded coordinates,
builds point geometry via ST_MakePoint, and bulk-loads into
arrets_transport and gares PostGIS tables.

Data sources:
- transport.data.gouv.fr (GTFS consolidated stops)
- ressources.data.sncf.com (liste-des-gares)
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
import pandas as pd
import psycopg

from app.common.source_catalog import covered_departements
from app.etl.base import BasePipeline
from app.etl.download import get_download_dir, streaming_download
from app.etl.errors import ScopePrerequisiteError
from app.etl_transport.config import (
    ARRETS_CSV_URL,
    ARRETS_EXPECTED_COLUMNS,
    ARRETS_OUTPUT_COLUMNS,
    GARES_COLUMN_MAPPING,
    GARES_CSV_URL_SNCF_FALLBACK,
    GARES_EXPECTED_COLUMNS,
    GARES_GEOJSON_URL,
    GARES_OUTPUT_COLUMNS,
)
from app.scope.model import POINT_DEPARTEMENT_SQL

logger = logging.getLogger(__name__)


# Stops and stations carry coordinates only (the SNCF 2025+ file dropped
# CODE_INSEE): their departement is the polygon that covers their point.
_ARRET_POINT = (
    "ST_SetSRID(ST_MakePoint(NULLIF(stop_lon, '')::double precision, "
    "NULLIF(stop_lat, '')::double precision), 4326)"
)
_GARE_POINT = (
    "ST_SetSRID(ST_MakePoint(NULLIF(x_wgs84, '')::double precision, "
    "NULLIF(y_wgs84, '')::double precision), 4326)"
)
_PUBLISHED_DEPARTEMENT_SQL = POINT_DEPARTEMENT_SQL.format(point="position")
# Each staging table holds one source: its expected coverage is that source's,
# not the union (SNCF has no station overseas nor in Corsica).
_TABLE_SOURCE_KEYS = {
    "_arrets_transport_staging": ("transports",),
    "_gares_staging": ("gares",),
}


class TransportPipeline(BasePipeline):
    """Concrete ETL pipeline for Transport stops + SNCF stations."""

    # -- abstract properties ---------------------------------------------------

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 360_000

    @property
    def dataset_name(self) -> str:
        return "transports"

    @property
    def scope_dataset_keys(self) -> tuple[str, ...]:
        # One import loads both catalogue datasets (stops and SNCF stations).
        return ("transports", "gares")

    @property
    def has_department_partitions(self) -> bool:
        return False

    @property
    def expected_columns(self) -> list[str]:
        return ARRETS_EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "arrets_transport_staging"

    @property
    def production_table_name(self) -> str:
        return "arrets_transport"

    @property
    def flat_staging_table_name(self) -> str:
        return "_arrets_transport_staging"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National files, filtered to the hosted departements (D-22), both by
        # departement polygon (geo_departements).
        return {
            "_arrets_transport_staging": POINT_DEPARTEMENT_SQL.format(
                point=_ARRET_POINT
            ),
            "_gares_staging": POINT_DEPARTEMENT_SQL.format(point=_GARE_POINT),
        }

    def expected_coverage_for_table(self, table: str) -> list[str]:
        keys = _TABLE_SOURCE_KEYS.get(table)
        if keys is None:
            return super().expected_coverage_for_table(table)
        covered = set(covered_departements(keys, self.department_scope))
        return [dept for dept in self.department_scope if dept in covered]

    @property
    def production_departement_sql(self) -> str:
        return _PUBLISHED_DEPARTEMENT_SQL

    def check_scope_prerequisites(self, dsn: str) -> None:
        """Refuse before download when a hosted departement has no polygon."""
        wanted = list(self.scope.departements)
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            found = {
                row[0]
                for row in conn.execute(
                    "SELECT code FROM geo_departements WHERE code = ANY(%s)",
                    (wanted,),
                ).fetchall()
            }
        missing = [code for code in wanted if code not in found]
        if missing:
            raise ScopePrerequisiteError(
                f"geo_departements has no polygon for {missing}: transport stops "
                "and stations cannot be attributed to a departement. Load the "
                "reference first (scripts/import_geo_boundaries.py)."
            )

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~450k production arrets_transport rows, full France;
        # scaled to the bound scope (D-23).
        return self.scoped_min_rows(self.national_min_rows)

    # -- override: non-partitioned staging ------------------------------------

    @property
    def published_old_tables(self) -> tuple[str, ...]:
        return ("arrets_transport_old", "gares_old")

    @staticmethod
    def _recreate_staging_tables(cur: psycopg.Cursor) -> None:
        """(Re)create both staging tables, each with its own id sequence.

        LIKE copies the production default, which points at the production
        sequence; Phase C drops that sequence with CASCADE, so a staging table
        relying on it would publish a table without default and the next
        import would insert NULL ids (first national import, 2026-10-30).
        """
        for table in ("arrets_transport", "gares"):
            cur.execute(f"DROP TABLE IF EXISTS {table}_staging CASCADE")
            cur.execute(f"DROP SEQUENCE IF EXISTS {table}_staging_id_seq CASCADE")
            cur.execute(
                f"CREATE TABLE {table}_staging "
                f"(LIKE {table} INCLUDING ALL EXCLUDING INDEXES)"
            )
            cur.execute(f"CREATE SEQUENCE {table}_staging_id_seq")
            cur.execute(
                f"ALTER TABLE {table}_staging ALTER COLUMN id SET DEFAULT "
                f"nextval('{table}_staging_id_seq')"
            )

    def create_staging_table(self, dsn: str) -> None:
        """Create non-partitioned staging tables for arrets + gares."""
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Drop flat staging tables (cleanup from failed runs)
                cur.execute("DROP TABLE IF EXISTS _arrets_transport_staging CASCADE")
                cur.execute("DROP TABLE IF EXISTS _gares_staging CASCADE")
                self._recreate_staging_tables(cur)
            conn.commit()

    def drop_staging_table(self, dsn: str) -> None:
        """Drop all staging and flat staging tables."""
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS arrets_transport_staging CASCADE")
                cur.execute("DROP TABLE IF EXISTS gares_staging CASCADE")
                cur.execute("DROP TABLE IF EXISTS _arrets_transport_staging CASCADE")
                cur.execute("DROP TABLE IF EXISTS _gares_staging CASCADE")
            conn.commit()

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download arrets CSV (~417 MB) and gares CSV (~small).

        Uses httpx async with long read timeout for arrets.
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        download_dir = get_download_dir("transport")
        raw_dir = download_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []

        # Download arrets (large file, long timeout)
        arrets_path = raw_dir / "arrets.csv"
        arrets_timeout = httpx.Timeout(60.0, read=600.0)
        async with httpx.AsyncClient(timeout=arrets_timeout) as client:
            ok = await streaming_download(client, ARRETS_CSV_URL, arrets_path)
            if ok:
                logger.info("Downloaded arrets CSV: %s", arrets_path.name)
                paths.append(arrets_path)
            else:
                logger.warning("Arrets CSV download returned 404")

        # Download gares — prefer data.gouv.fr GeoJSON mirror (SNCF 429s)
        gares_path = raw_dir / "gares.csv"
        gares_timeout = httpx.Timeout(60.0, read=120.0)
        gares_ok = False

        # Strategy 1: data.gouv.fr GeoJSON mirror → convert to CSV
        async with httpx.AsyncClient(timeout=gares_timeout) as client:
            geojson_path = raw_dir / "gares.geojson"
            gares_ok = await streaming_download(client, GARES_GEOJSON_URL, geojson_path)
            if gares_ok:
                logger.info("Downloaded gares GeoJSON from data.gouv.fr mirror")
                self._geojson_to_csv(geojson_path, gares_path)
                geojson_path.unlink(missing_ok=True)

        # Strategy 2: fallback to SNCF CSV export (may 429)
        if not gares_ok:
            logger.warning("GeoJSON mirror unavailable, trying SNCF CSV export")
            async with httpx.AsyncClient(
                timeout=gares_timeout,
                headers={"User-Agent": "DataCommune-ETL/1.0 (contact@datacommune.fr)"},
            ) as client:
                gares_ok = await streaming_download(
                    client, GARES_CSV_URL_SNCF_FALLBACK, gares_path
                )

        if gares_ok:
            logger.info("Gares CSV ready: %s", gares_path.name)
            paths.append(gares_path)
        else:
            logger.warning("Gares download failed (both sources)")

        return paths

    @staticmethod
    def _geojson_to_csv(geojson_path: Path, csv_path: Path) -> None:
        """Convert a GeoJSON FeatureCollection to CSV (properties only).

        The data.gouv.fr Hydra mirror stores SNCF gares as GeoJSON.
        We flatten the properties dict of each Feature into a CSV row,
        preserving the original column names (uppercase) so the existing
        schema validation and column mapping work unchanged.
        """
        with open(geojson_path, encoding="utf-8") as f:
            data = json.load(f)

        rows = [feature["properties"] for feature in data["features"]]
        df = pd.DataFrame(rows)

        # Drop computed/redundant columns that aren't in the CSV export
        drop_cols = [c for c in ["Geo Point", "Geo Shape"] if c in df.columns]
        if drop_cols:
            df = df.drop(columns=drop_cols)

        df.to_csv(csv_path, index=False, encoding="utf-8", sep=";")
        logger.info(
            "Converted GeoJSON to CSV: %d rows, columns: %s",
            len(df),
            df.columns.tolist(),
        )

    def validate_schema(self, file_path: Path) -> None:
        """Validate CSV header contains expected columns.

        Detects separator (comma or semicolon). Routes to arrets or
        gares validation based on filename.
        """
        with open(file_path, encoding="utf-8-sig") as f:
            first_line = f.readline().strip()

        separator = ";" if ";" in first_line else ","
        actual = {col.strip().strip('"') for col in first_line.split(separator)}

        # Route based on filename
        if "gares" in file_path.name:
            expected = set(GARES_EXPECTED_COLUMNS)
            label = "Gares"
        else:
            expected = set(ARRETS_EXPECTED_COLUMNS)
            label = "Arrets"

        missing = expected - actual
        extra = actual - expected

        if extra:
            logger.warning(
                "%s CSV %s has %d extra columns",
                label,
                file_path.name,
                len(extra),
            )

        if missing:
            msg = (
                f"{label} schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        """Transform transport CSVs into clean CSVs for COPY.

        Routes to _transform_arrets or _transform_gares based on
        filename. Returns path to the transformed file.
        """
        if "gares" in file_path.name:
            return self._transform_gares(file_path)
        return self._transform_arrets(file_path)

    def _transform_arrets(self, file_path: Path) -> Path:
        """Deduplicate and clean the consolidated GTFS stops CSV.

        CRITICAL: The raw file has millions of duplicate stops.
        Deduplication by stop_name + round(lat, 4) + round(lon, 4)
        reduces to ~300-500k unique stops.
        """
        download_dir = get_download_dir("transport")
        transformed_dir = download_dir / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        output_path = transformed_dir / "arrets-transformed.csv"

        # Read with auto-detect separator
        df = pd.read_csv(file_path, sep=None, engine="python", dtype=str)

        # Convert coordinates to float for dedup + filtering
        df["stop_lat"] = pd.to_numeric(df["stop_lat"], errors="coerce")
        df["stop_lon"] = pd.to_numeric(df["stop_lon"], errors="coerce")

        # Filter rows without valid coordinates
        total_before = len(df)
        df = df[
            df["stop_lat"].notna()
            & df["stop_lon"].notna()
            & (df["stop_lat"] != 0)
            & (df["stop_lon"] != 0)
        ]
        logger.info(
            "Filtered %d rows without coordinates (kept %d)",
            total_before - len(df),
            len(df),
        )

        # Deduplication by stop_name + rounded coords
        df["lat_round"] = df["stop_lat"].round(4)
        df["lon_round"] = df["stop_lon"].round(4)
        before_dedup = len(df)
        df = df.groupby(["stop_name", "lat_round", "lon_round"]).first().reset_index()
        df = df.drop(columns=["lat_round", "lon_round"])
        logger.info(
            "Deduplicated: %d -> %d rows",
            before_dedup,
            len(df),
        )

        # Ensure agency_name column exists
        if "agency_name" not in df.columns:
            df["agency_name"] = ""

        # Ensure all output columns exist
        for col in ARRETS_OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Write output
        df[ARRETS_OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed arrets: %d rows", len(df))
        return output_path

    def _transform_gares(self, file_path: Path) -> Path:
        """Clean and rename the SNCF gares CSV."""
        download_dir = get_download_dir("transport")
        transformed_dir = download_dir / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)

        output_path = transformed_dir / "gares-transformed.csv"

        # Try semicolon first (SNCF convention), fall back to comma
        df = pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            dtype=str,
            encoding="utf-8-sig",
        )

        # Rename columns via mapping
        df = df.rename(columns=GARES_COLUMN_MAPPING)

        # Handle lowercase trigramme (already lowercase in mapping)
        if "trigramme" not in df.columns:
            df["trigramme"] = ""

        # Convert coordinates to float for filtering
        df["x_wgs84"] = pd.to_numeric(df["x_wgs84"], errors="coerce")
        df["y_wgs84"] = pd.to_numeric(df["y_wgs84"], errors="coerce")

        # Filter rows without valid coordinates
        total_before = len(df)
        df = df[df["x_wgs84"].notna() & df["y_wgs84"].notna()]
        skipped = total_before - len(df)
        if skipped > 0:
            logger.warning(
                "Filtered %d gares without coordinates (kept %d)",
                skipped,
                len(df),
            )

        # Ensure all output columns exist
        for col in GARES_OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        # Write output
        df[GARES_OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed gares: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV into flat staging via COPY.

        Routes to arrets or gares staging based on filename.
        """
        if "gares" in file_path.name:
            return self._stage_gares(file_path)
        return self._stage_arrets(file_path)

    def _stage_arrets(self, file_path: Path) -> int:
        """Bulk-load deduplicated arrets CSV into flat staging table."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent)
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _arrets_transport_staging (
                        stop_id TEXT,
                        stop_name TEXT,
                        stop_lat TEXT,
                        stop_lon TEXT,
                        location_type TEXT,
                        agency_name TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _arrets_transport_staging")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(ARRETS_OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _arrets_transport_staging ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _arrets_transport_staging")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged arrets: flat table has %d rows", row_count)
        return row_count

    def _stage_gares(self, file_path: Path) -> int:
        """Bulk-load gares CSV into flat staging table."""
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent)
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _gares_staging (
                        nom_gare TEXT,
                        commune TEXT,
                        code_insee TEXT,
                        x_wgs84 TEXT,
                        y_wgs84 TEXT,
                        code_uic TEXT,
                        code_ligne TEXT,
                        trigramme TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _gares_staging")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(GARES_OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _gares_staging ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _gares_staging")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged gares: flat table has %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production, rebuild indexes, swap.

        Transport manages two production tables (arrets_transport + gares).

        Phase A: Recreate staging tables + INSERT SELECT for both + index
                 builds + safety checks (own transaction).
        Phase B-arrets: Atomic swap for arrets_transport via swap_tables().
        Phase B-gares: Atomic swap for gares (manual, separate transaction).
        Phase C: Post-swap cleanup — drop flat staging, rename sequences.
        """
        dsn = self.get_sync_dsn()

        # --- Phase A: long work in its own transaction -----------------------
        # Recreate staging tables (without dropping flat staging data)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self._recreate_staging_tables(cur)
            conn.commit()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                # -- Arrets: flat -> staging with ST_MakePoint --------
                logger.info("Transferring arrets flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO arrets_transport_staging
                        (stop_id, stop_name, position, location_type,
                         agency_name)
                    SELECT
                        NULLIF(stop_id, ''),
                        NULLIF(stop_name, ''),
                        ST_SetSRID(
                            ST_MakePoint(
                                stop_lon::double precision,
                                stop_lat::double precision
                            ),
                            4326
                        ),
                        NULLIF(location_type, '')::integer,
                        NULLIF(agency_name, '')
                    FROM _arrets_transport_staging
                    WHERE stop_name IS NOT NULL AND stop_name != ''
                      AND stop_lat IS NOT NULL AND stop_lat != ''
                      AND stop_lon IS NOT NULL AND stop_lon != ''
                """)

                cur.execute("SELECT count(*) FROM arrets_transport_staging")
                arrets_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Arrets staging loaded: %d rows in %.1fs",
                    arrets_count,
                    time.perf_counter() - t0,
                )

                # -- Gares: flat -> staging with ST_MakePoint ---------
                logger.info("Transferring gares flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO gares_staging
                        (nom_gare, commune, code_insee, position,
                         code_uic, code_ligne, trigramme)
                    SELECT
                        NULLIF(nom_gare, ''),
                        NULLIF(commune, ''),
                        NULLIF(code_insee, ''),
                        ST_SetSRID(
                            ST_MakePoint(
                                x_wgs84::double precision,
                                y_wgs84::double precision
                            ),
                            4326
                        ),
                        NULLIF(code_uic, ''),
                        NULLIF(code_ligne, ''),
                        NULLIF(trigramme, '')
                    FROM _gares_staging
                    WHERE nom_gare IS NOT NULL AND nom_gare != ''
                      AND x_wgs84 IS NOT NULL AND x_wgs84 != ''
                      AND y_wgs84 IS NOT NULL AND y_wgs84 != ''
                """)

                cur.execute("SELECT count(*) FROM gares_staging")
                gares_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Gares staging loaded: %d rows in %.1fs",
                    gares_count,
                    time.perf_counter() - t0,
                )

                # -- Rebuild indexes on staging -----------------------
                logger.info("Rebuilding indexes on staging tables")
                self._create_staging_indexes(cur)

                # gares is not covered by prepare_staging_constraints() (that
                # helper targets staging_table_name), so restore its PK/UNIQUE
                # here — still outside the swap transaction (DB-HYGIENE-01).
                self.ensure_staging_constraints(cur, "gares", "gares_staging")

                # -- Safety checks -----------------------------------
                logger.info("Running safety checks")
                # Same departement scope as staging (D-23).
                old_arrets = self.count_production_rows(cur, "arrets_transport")

                if old_arrets > 0 and arrets_count < old_arrets * 0.5:
                    msg = (
                        f"Swap aborted: arrets staging has {arrets_count} rows "
                        f"but production has {old_arrets} "
                        f"({arrets_count / old_arrets:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

                old_gares = self.count_production_rows(
                    cur, "gares", _PUBLISHED_DEPARTEMENT_SQL
                )

                if old_gares > 0 and gares_count < old_gares * 0.5:
                    msg = (
                        f"Swap aborted: gares staging has {gares_count} rows "
                        f"but production has {old_gares} "
                        f"({gares_count / old_gares:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)

            conn.commit()  # Phase A commits before Phase B opens

        logger.info(
            "Swap complete: arrets %d (was %d), gares %d (was %d)",
            arrets_count,
            old_arrets,
            gares_count,
            old_gares,
        )

        # --- Phase B-arrets: atomic swap via base class helper ---------------
        # swap_tables() targets self.production_table_name = "arrets_transport"
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase B-gares: atomic swap for gares (manual) ------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                # Same rule as swap_tables(): logged candidate, no rewrite.
                self.assert_staging_logged(cur, "gares_staging")
                cur.execute("DROP TABLE IF EXISTS gares_old CASCADE")
                cur.execute("ALTER TABLE gares RENAME TO gares_old")
                cur.execute("ALTER TABLE gares_staging RENAME TO gares")
                # gares does not go through swap_tables() (that helper targets
                # production_table_name), so run the shared index cleanup on it
                # explicitly, inside the same transaction (DB-HYGIENE-01).
                self.rename_indexes_after_swap(cur, "gares")
            conn.commit()
        logger.info("Atomic swap committed: gares -> gares_old, gares_staging -> gares")

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _arrets_transport_staging CASCADE")
                cur.execute("DROP TABLE IF EXISTS _gares_staging CASCADE")
                # Sequence renames with IF EXISTS guards
                cur.execute("DROP SEQUENCE IF EXISTS arrets_transport_id_seq CASCADE")
                cur.execute(
                    "ALTER SEQUENCE IF EXISTS arrets_transport_staging_id_seq "
                    "RENAME TO arrets_transport_id_seq"
                )
                cur.execute("DROP SEQUENCE IF EXISTS gares_id_seq CASCADE")
                cur.execute(
                    "ALTER SEQUENCE IF EXISTS gares_staging_id_seq "
                    "RENAME TO gares_id_seq"
                )
                # has_department_partitions = False — no partition rename needed
            conn.commit()

        # Trace both _old tables; the runner drops them after publication.
        self.register_old_table(dsn, "arrets_transport_old")
        self.register_old_table(dsn, "gares_old")

        logger.info("Cleanup complete: dropped old tables and flat staging")

    # Production index names (migration 012) paired with their staging table and
    # column spec. Transport publishes two tables, so the staging table is part
    # of the spec. Staging twins are derived via
    # BasePipeline.staging_index_name() so the post-swap rename is a pure
    # suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str, str], ...] = (
        ("idx_arrets_position", "arrets_transport_staging", "USING GIST (position)"),
        ("idx_arrets_stop_name", "arrets_transport_staging", "(stop_name)"),
        ("idx_gares_position", "gares_staging", "USING GIST (position)"),
        ("idx_gares_code_insee", "gares_staging", "(code_insee)"),
    )

    def _create_staging_indexes(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        """Create production-equivalent indexes on both staging tables."""
        for canonical, staging, spec in self.STAGING_INDEX_SPECS:
            name = self.staging_index_name(canonical)
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(f"CREATE INDEX {name} ON {staging} {spec}")


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(TransportPipeline)

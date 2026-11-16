"""BPE equipements concrete ETL pipeline.

Downloads the BPE ZIP from INSEE, extracts the geolocalised CSV,
transforms via pandas, bulk-loads via COPY, and swaps atomically.

Data source: INSEE BPE, Licence Ouverte 2.0.
"""

import asyncio
import logging
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pandas as pd
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import (
    assert_zip_extraction_safe,
    get_download_dir,
    streaming_download,
)
from app.etl.geo_gate import check_bounds_gate
from app.etl.registry import register_pipeline
from app.etl_equipements.config import (
    CSV_SEPARATOR,
    DOMAINE_MAP,
    DOWNLOAD_TIMEOUT,
    DOWNLOAD_URL,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    READ_COLUMNS,
    SOURCE_COLUMNS,
    SOURCE_VINTAGE,
)

logger = logging.getLogger(__name__)


def archive_file_name(url: str) -> str:
    """Local name of the downloaded archive: the file named by *url*."""
    return Path(urlsplit(url).path).name.lower()


@register_pipeline("equipements")
class EquipementsPipeline(BasePipeline):
    """Concrete ETL for BPE equipements (~2.5M rows)."""

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 2_000_000

    @property
    def dataset_name(self) -> str:
        return "equipements"

    @property
    def expected_columns(self) -> list[str]:
        return SOURCE_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "equipements_staging"

    @property
    def production_table_name(self) -> str:
        return "equipements"

    @property
    def flat_staging_table_name(self) -> str:
        return "_equipements_staging_flat"

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_equipements_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~2.5M production equipements rows
        return self.scoped_min_rows(self.national_min_rows)

    def create_staging_table(self, dsn: str) -> None:
        """Create the partitioned staging table declared by migration 041."""
        super().create_staging_table(dsn)

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
        raw_dir = get_download_dir("equipements") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        zip_path = raw_dir / archive_file_name(DOWNLOAD_URL)
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
                logger.error("Failed to download BPE ZIP")
                return []

        with zipfile.ZipFile(zip_path) as zf:
            # Prefer the geolocalised file (xy)
            csv_names = [
                n
                for n in zf.namelist()
                if ("xy" in n.lower() or "ensemble" in n.lower())
                and (n.endswith(".csv") or n.endswith(".CSV"))
            ]
            if not csv_names:
                csv_names = [
                    n for n in zf.namelist() if n.endswith(".csv") or n.endswith(".CSV")
                ]
            if not csv_names:
                logger.error("No CSV found in BPE ZIP")
                return []
            # Size/ratio caps before extraction (LC-01)
            assert_zip_extraction_safe(zip_path, zf)
            csv_name = csv_names[0]
            zf.extract(csv_name, raw_dir)
            logger.info("Extracted %s from ZIP", csv_name)
            return [raw_dir / csv_name]

    def validate_schema(self, file_path: Path) -> None:
        with open(file_path, encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline().strip()

        actual = {col.strip().strip('"') for col in first_line.split(CSV_SEPARATOR)}
        missing = set(SOURCE_COLUMNS) - actual
        if missing:
            msg = f"BPE schema mismatch: missing {sorted(missing)}"
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:
        transformed_dir = get_download_dir("equipements") / "transformed"
        transformed_dir.mkdir(parents=True, exist_ok=True)
        output_path = transformed_dir / "equipements-transformed.csv"

        df = pd.read_csv(
            file_path,
            sep=CSV_SEPARATOR,
            dtype=str,
            encoding="utf-8-sig",
            low_memory=False,
            usecols=lambda c: c in READ_COLUMNS,
        )

        df["type_equipement"] = df["TYPEQU"].str.strip()
        df["code_commune"] = df["DEPCOM"].str.strip()
        df["code_departement"] = df.get("DEP", df["code_commune"].str[:2])
        # Fix DOM departments
        df.loc[
            df["code_commune"].str[:3].isin(["971", "972", "973", "974", "976"]),
            "code_departement",
        ] = df["code_commune"].str[:3]

        df["code_iris"] = df.get("DCIRIS", pd.Series(dtype=str))
        if "DCIRIS" not in df.columns:
            df["code_iris"] = ""

        # Map domain
        if "SDOM" in df.columns:
            df["sous_domaine"] = df["SDOM"].str.strip()
            df["domaine"] = df["SDOM"].str[:1].map(DOMAINE_MAP).fillna("")
        else:
            df["domaine"] = ""
            df["sous_domaine"] = ""

        df["nb_equipements"] = df.get(
            "NB_EQUIP",
            pd.Series("1", index=df.index, dtype=str),
        )
        if "NB_EQUIP" not in df.columns:
            df["nb_equipements"] = "1"

        # Coordinates. The swap (Phase B) treats longitude/latitude as WGS84
        # (ST_SetSRID(..., 4326), NO reprojection — FIX-01). That is only correct
        # when the source provides WGS84 LATITUDE/LONGITUDE. The LAMBERT_X/Y branch
        # below is a raw column passthrough (metres EPSG:2154, NOT reprojected); it
        # does NOT yield valid WGS84 on its own and exists only as a fallback that
        # the WGS84 columns overwrite. INSEE/PostGIS convention: X = easting ->
        # longitude, Y = northing -> latitude.
        for src, dst in [
            ("LAMBERT_X", "longitude"),
            ("LAMBERT_Y", "latitude"),
        ]:
            if src in df.columns:
                df[dst] = pd.to_numeric(
                    df[src].str.replace(",", "."),
                    errors="coerce",
                )
            else:
                df[dst] = ""

        # WGS84 lat/lon are authoritative and overwrite the Lambert passthrough.
        has_wgs84 = "LATITUDE" in df.columns and "LONGITUDE" in df.columns
        if "LATITUDE" in df.columns:
            df["latitude"] = df["LATITUDE"]
        if "LONGITUDE" in df.columns:
            df["longitude"] = df["LONGITUDE"]

        # Fail loud if the source ships no WGS84 coords: the swap would otherwise
        # mislabel raw Lambert metres as degrees (the original out-of-bounds bug).
        # A Lambert-only source needs a reprojection path before swap, not silent
        # passthrough. The bounds gate would abort the swap downstream, but failing
        # here gives an actionable error instead of an opaque gate abort.
        if not has_wgs84:
            msg = (
                "BPE source provides no WGS84 LATITUDE/LONGITUDE columns; the swap "
                "expects WGS84 (no reprojection, FIX-01). A Lambert-only source "
                "requires reinstating a ST_Transform(2154->4326) path before swap."
            )
            raise ValueError(msg)

        # Vintage pinned to the resource named by DOWNLOAD_URL (INSEE
        # fichier/8217525/BPE25.zip, vintage in the file name); not
        # auto-extensible like DVF. Changing the URL requires changing
        # SOURCE_VINTAGE: test_equipements_source guards the pair.
        df["annee"] = SOURCE_VINTAGE

        df = df[
            df["type_equipement"].notna() & (df["type_equipement"].str.strip() != "")
        ]

        df[OUTPUT_COLUMNS].to_csv(output_path, index=False, encoding="utf-8")

        logger.info("Transformed equipements: %d rows", len(df))
        return output_path

    def stage(self, file_path: Path) -> int:
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS
                    _equipements_staging_flat (
                        type_equipement TEXT,
                        code_commune TEXT,
                        code_departement TEXT,
                        code_iris TEXT,
                        domaine TEXT,
                        sous_domaine TEXT,
                        nb_equipements TEXT,
                        latitude TEXT,
                        longitude TEXT,
                        annee TEXT
                    )
                """)

                cur.execute("SELECT count(*) FROM _equipements_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _equipements_staging_flat "
                    f"({columns}) "
                    f"FROM STDIN WITH "
                    f"(FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _equipements_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info("Staged equipements: %d rows", row_count)
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> production staging, rebuild indexes, swap.

        Phase A: Create staging + INSERT SELECT + index builds (own transaction).
                 Uses a 600s statement timeout (short dataset, fast INSERT).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — drop flat staging, rename sequence.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "equipements"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur, 600)

                logger.info("Transferring flat staging to production staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO equipements_staging
                        (type_equipement, code_commune,
                         code_departement, code_iris,
                         domaine, sous_domaine,
                         nb_equipements, latitude, longitude,
                         geom, annee)
                    SELECT
                        NULLIF(type_equipement, ''),
                        NULLIF(code_commune, ''),
                        NULLIF(code_departement, ''),
                        NULLIF(code_iris, ''),
                        NULLIF(domaine, ''),
                        NULLIF(sous_domaine, ''),
                        NULLIF(nb_equipements, '')::integer,
                        ST_Y(wgs84_pt),
                        ST_X(wgs84_pt),
                        wgs84_pt,
                        NULLIF(annee, '')::integer
                    FROM (
                        SELECT *,
                            ST_SetSRID(
                                ST_MakePoint(
                                    longitude::double precision,
                                    latitude::double precision
                                ),
                                4326
                            ) AS wgs84_pt
                        FROM _equipements_staging_flat
                        WHERE NULLIF(longitude, '') IS NOT NULL
                          AND NULLIF(latitude, '') IS NOT NULL
                    ) sub
                    WHERE NULLIF(type_equipement, '') IS NOT NULL
                      AND NULLIF(code_commune, '') IS NOT NULL
                """)

                cur.execute("SELECT count(*) FROM equipements_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "equipements")

                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging "
                        f"{staging_count} "
                        f"vs production {old_count}"
                    )
                    raise RuntimeError(msg)

            conn.commit()  # Phase A commits before Phase B opens

        # Gate: bounds check before swap (D-03 / D-21)
        # Abort if > 5% of staged geometries fall outside France+DOM.
        # With the WGS84 fix applied, this gate should pass cleanly.
        # If it fires, the swap is aborted and production data is preserved.
        if gate_err := check_bounds_gate(
            dsn,
            "equipements_staging",
            "geom",
            "equipements",
            threshold_pct=5.0,
            log=logger,
        ):
            raise RuntimeError(gate_err)

        logger.info("Swap complete: %d rows (was %d)", staging_count, old_count)

        # --- Phase B: atomic swap via base class helper ----------------------
        self.swap_tables(dsn)  # logged guard + DROP IF EXISTS old + RENAME pair

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute("DROP TABLE IF EXISTS _equipements_staging_flat CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                self.rename_partitions_after_swap(cur)
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete")

    # Production index names (migration 034/041 + 042) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_equipements_code_commune", "(code_commune)"),
        ("idx_equipements_type", "(type_equipement)"),
        ("idx_equipements_domaine", "(domaine)"),
        ("idx_equipements_geom", "USING GIST (geom)"),
        ("idx_equipements_dept", "(code_departement)"),
        ("idx_equipements_geom_geog", "USING GIST (geography(geom))"),
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

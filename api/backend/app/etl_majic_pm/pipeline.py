"""MAJIC Personnes Morales concrete ETL pipeline.

Downloads ZIP files of legal entity parcelle ownership from
data.economie.gouv.fr (via data.gouv.fr), extracts per-department CSVs,
transforms them to a unified CSV matching the DB schema, bulk-loads
via COPY into a flat staging table, INSERT SELECTs with type casts
into the partitioned staging table, and swaps atomically.
"""

import asyncio
import csv
import logging
import re
import time
import zipfile
from pathlib import Path

import httpx
import psycopg

from app.etl.base import BasePipeline
from app.etl.download import (
    assert_zip_extraction_safe,
    extract_zip_member_bounded,
    get_download_dir,
    streaming_download,
)
from app.etl_majic_pm.config import (
    DATASET_API_URL,
    DOWNLOAD_TIMEOUT,
    EXPECTED_COLUMNS,
    MAX_RETRIES,
    OUTPUT_COLUMNS,
    PARCELLES_ZIP_URLS_FALLBACK,
    REQUIRED_SOURCE_COLUMNS,
)

logger = logging.getLogger(__name__)

# Regex to extract code prefix from label fields like "P - Propriétaire" -> "P"
_CODE_PREFIX_RE = re.compile(r"^(\S+)")


def _extract_code(value: str) -> str:
    """Extract the code prefix from a 'CODE - Label' field.

    Examples:
        "P - Propriétaire" -> "P"
        "4 - Commune" -> "4"
        "BT - Taillis simples" -> "BT"
        "" -> ""
    """
    if not value:
        return ""
    m = _CODE_PREFIX_RE.match(value)
    return m.group(1) if m else value


class MajicPmPipeline(BasePipeline):
    """Concrete ETL pipeline for MAJIC personnes morales (~17M rows)."""

    # -- abstract properties ---------------------------------------------------

    # Full-France floor; filtered to the scope per departement (D-22, D-23).
    national_min_rows = 16_800_000

    @property
    def dataset_name(self) -> str:
        # Must match dataset_metadata.dataset_name, scheduler config key,
        # and the run_etl CLI arg. The internal module is named majic_pm
        # but the public dataset identifier is foncier_pm everywhere else.
        return "foncier_pm"

    @property
    def expected_columns(self) -> list[str]:
        return EXPECTED_COLUMNS

    @property
    def staging_table_name(self) -> str:
        return "foncier_pm_staging"

    @property
    def production_table_name(self) -> str:
        return "foncier_pm"

    @property
    def flat_staging_table_name(self) -> str:
        return "_foncier_pm_staging_flat"

    @property
    def expected_file_count(self) -> int:
        return 0  # variable (per-department CSVs); dept coverage check handles safety

    @property
    def has_department_partitions(self) -> bool:
        # Data lives in _foncier_pm_staging_flat until swap() runs INSERT SELECT.
        # The runner's pre-swap coverage check queries foncier_pm_staging which
        # is empty at that point, so it always reports 0 departments.
        # The swap() method has its own row-count safety check.
        return False

    @property
    def scope_filter_sql(self) -> dict[str, str]:
        # National file, filtered to the hosted departements (D-22).
        return {"_foncier_pm_staging_flat": "code_departement"}

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~21M actual MAJIC PM rows (2025 dataset, 101 depts)
        # Previous estimate of 30M was inflated — real source is ~21M rows
        return self.scoped_min_rows(self.national_min_rows)

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download the 2 parcelles ZIP files and extract CSVs.

        Resolves URLs via data.gouv.fr API, falls back to hardcoded URLs.
        Returns a list of extracted CSV file paths.
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Async implementation of the download stage."""
        raw_dir = get_download_dir("majic_pm") / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            # Resolve ZIP URLs from data.gouv.fr API
            zip_urls = await self._resolve_zip_urls(client)
            if not zip_urls:
                logger.warning(
                    "Could not resolve ZIP URLs from API, using fallback URLs"
                )
                zip_urls = list(PARCELLES_ZIP_URLS_FALLBACK)

            # Download each ZIP file
            zip_paths: list[Path] = []
            for i, url in enumerate(zip_urls):
                zip_path = raw_dir / f"parcelles_pm_{i}.zip"
                logger.info(
                    "Downloading MAJIC PM ZIP %d/%d: %s",
                    i + 1,
                    len(zip_urls),
                    url,
                )
                ok = await streaming_download(
                    client, url, zip_path, max_retries=MAX_RETRIES
                )
                if ok:
                    zip_paths.append(zip_path)
                    logger.info(
                        "Downloaded %s (%.1f MB)",
                        zip_path.name,
                        zip_path.stat().st_size / 1e6,
                    )
                else:
                    logger.error("Failed to download ZIP from %s", url)

        if not zip_paths:
            logger.error("No ZIP files downloaded")
            return []

        # Extract CSVs from ZIPs
        csv_paths = self._extract_zips(zip_paths, raw_dir)
        logger.info(
            "Extracted %d CSV files from %d ZIPs",
            len(csv_paths),
            len(zip_paths),
        )
        return csv_paths

    async def _resolve_zip_urls(self, client: httpx.AsyncClient) -> list[str]:
        """Resolve the latest parcelles ZIP URLs from data.gouv.fr API.

        Finds resources whose title contains 'parcelles' and 'situation'
        with the most recent year, returning the ZIP URLs.
        """
        try:
            response = await client.get(DATASET_API_URL, follow_redirects=True)
            response.raise_for_status()
            data = response.json()

            # Find all parcelles ZIP resources
            parcelles_resources = []
            for resource in data.get("resources", []):
                title = (resource.get("title") or "").lower()
                url = resource.get("url") or ""
                if "parcelles" in title and "situation" in title and url:
                    # Extract year from title
                    year_match = re.search(r"situation\s+(\d{4})", title)
                    year = int(year_match.group(1)) if year_match else 0
                    parcelles_resources.append((year, title, url))

            if not parcelles_resources:
                logger.warning("No parcelles ZIP resources found in API response")
                return []

            # Get the latest year
            max_year = max(r[0] for r in parcelles_resources)
            latest = [url for year, _, url in parcelles_resources if year == max_year]
            logger.info(
                "Resolved %d parcelles ZIP URLs for year %d", len(latest), max_year
            )
            return sorted(latest)  # deterministic order

        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to resolve ZIP URLs from API: %s", exc)
            return []

    @staticmethod
    def _extract_zips(zip_paths: list[Path], output_dir: Path) -> list[Path]:
        """Extract CSV files from ZIP archives."""
        csv_dir = output_dir / "csvs"
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_paths: list[Path] = []

        for zip_path in zip_paths:
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    # Size/ratio caps before extraction (B-02)
                    assert_zip_extraction_safe(zip_path, zf)
                    for member in zf.infolist():
                        if member.filename.endswith(".csv") and not member.is_dir():
                            # Extract to flat directory with unique name.
                            # Bounded copy: enforces the cap even if the
                            # declared file_size header lies.
                            csv_name = Path(member.filename).name
                            target = csv_dir / csv_name
                            extract_zip_member_bounded(zf, member, target)
                            csv_paths.append(target)
            except zipfile.BadZipFile:
                logger.error("Bad ZIP file: %s", zip_path)  # noqa: TRY400
                continue
            finally:
                # Clean up ZIP to save disk space
                zip_path.unlink(missing_ok=True)

        return sorted(csv_paths)

    def validate_schema(self, file_path: Path) -> None:
        """Validate that the CSV file contains required source columns.

        Reads only the header line. Tolerates extra columns but rejects
        missing required columns.
        """
        with open(file_path, encoding="utf-8") as f:
            header = f.readline().strip()

        actual_cols = set(header.split(";"))
        missing = REQUIRED_SOURCE_COLUMNS - actual_cols
        if missing:
            msg = (
                f"MAJIC PM schema mismatch in {file_path.name}: "
                f"missing required columns {sorted(missing)}"
            )
            raise ValueError(msg)

    def transform(self, file_path: Path) -> Path:  # noqa: C901
        """Transform a single source CSV to clean output CSV for COPY.

        Called once per downloaded CSV (one per department).

        Source CSV: semicolon-delimited, UTF-8, French column names
        Output CSV: comma-delimited, UTF-8, DB column names

        Transformations:
        - Build code_commune = dept + commune (5 chars, zero-padded)
        - Build code_parcelle = code_commune + prefix + section + plan (14 chars)
        - Build adresse from voirie + repetition + nature voie + nom voie
        - Extract code prefixes from label fields (code_droit, nature_culture, etc.)
        - Extract numeric code from groupe_personne
        """
        output_path = file_path.with_name(file_path.stem + "_transformed.csv")

        logger.info("Transforming: %s", file_path.name)
        rows_written = 0

        with open(file_path, encoding="utf-8") as fin:
            # Read header to get column positions
            header_line = fin.readline().strip()
            src_cols = header_line.split(";")

            # Handle duplicate "Contenance" columns by position
            # Index 13 = parcelle contenance, index 16 = SUF contenance
            col_indices: dict[str, int] = {}
            for i, col in enumerate(src_cols):
                if col == "Contenance" and "Contenance" in col_indices:
                    col_indices["Contenance SUF"] = i
                else:
                    col_indices[col] = i

            with open(output_path, "w", encoding="utf-8", newline="") as fout:
                writer = csv.writer(fout)
                writer.writerow(OUTPUT_COLUMNS)

                reader = csv.reader(fin, delimiter=";")
                for row in reader:
                    if len(row) < len(src_cols):
                        continue  # skip malformed rows

                    dept = row[col_indices["Département"]].strip()
                    commune = row[col_indices["Code Commune"]].strip()
                    nom_commune = row[col_indices["Nom de la commune"]].strip()
                    prefix = row[col_indices.get("Préfixe", 4)].strip()
                    section = row[col_indices["Section"]].strip()
                    plan = row[col_indices["N° plan"]].strip()
                    voirie = row[col_indices["N° voirie"]].strip()
                    repetition = row[col_indices["Indice de répétition"]].strip()
                    nature_voie = row[col_indices["Nature voie"]].strip()
                    nom_voie = row[col_indices["Nom voie"]].strip()
                    contenance = row[col_indices["Contenance"]].strip()
                    nature_culture_raw = row[col_indices["Nature culture"]].strip()
                    code_droit_raw = row[col_indices["Code droit - par"]].strip()
                    numero_siren = row[col_indices["N° SIREN - par"]].strip()
                    groupe_raw = row[col_indices["Groupe personne - par"]].strip()
                    forme_abr = row[
                        col_indices["Forme juridique abrégée - par"]
                    ].strip()
                    denomination = row[col_indices["Dénomination - par"]].strip()

                    # Build code_departement (zero-padded to 2 or 3 chars)
                    code_dept = dept.zfill(2)

                    # Build code_commune (always 5 chars = INSEE code)
                    # Metropole: dept(2) + commune(3) -> "57" + "001" = "57001"
                    # DOM: dept(3) + commune[-2:] -> "971" + "01" = "97101"
                    # (DOM CSV commune "101" = dept-last-digit + real-commune "01")
                    if len(code_dept) == 3:
                        # DOM: 3-digit dept, commune has redundant first digit
                        code_commune = code_dept + commune[-2:].zfill(2)
                    else:
                        code_commune = code_dept + commune.zfill(3)
                    if not code_commune:
                        continue  # skip rows without commune

                    # Build code_parcelle (14 chars):
                    # code_commune(5) + prefix(3) + section(2) + plan(4)
                    code_parcelle = (
                        code_commune
                        + prefix.ljust(3, "0")
                        + section.ljust(2, "0")
                        + plan.zfill(4)
                    )

                    # Build adresse from components
                    addr_parts = []
                    if voirie and voirie != "0000":
                        addr_parts.append(voirie.lstrip("0") or "0")
                    if repetition:
                        addr_parts.append(repetition)
                    if nature_voie:
                        addr_parts.append(nature_voie)
                    if nom_voie:
                        addr_parts.append(nom_voie)
                    adresse = " ".join(addr_parts) if addr_parts else ""

                    # Extract code prefixes from label fields
                    nature_culture = _extract_code(nature_culture_raw)
                    code_droit = _extract_code(code_droit_raw)
                    groupe_personne = _extract_code(groupe_raw)

                    # contenance_parcelle: keep as-is (integer string or empty)
                    contenance_parcelle = contenance if contenance else ""

                    writer.writerow(
                        [
                            code_dept,
                            code_commune,
                            nom_commune,
                            adresse,
                            code_parcelle,
                            contenance_parcelle,
                            nature_culture,
                            code_droit,
                            numero_siren,
                            groupe_personne,
                            forme_abr,
                            denomination,
                        ]
                    )
                    rows_written += 1

        logger.info("Transformed %d rows from %s", rows_written, file_path.name)
        return output_path

    def stage(self, file_path: Path) -> int:
        """Bulk-load transformed CSV into _foncier_pm_staging_flat via COPY.

        The flat staging table has all TEXT columns for maximum COPY
        throughput. Type casting happens during swap (INSERT SELECT).
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Idempotent create — base.create_staging_table() already dropped
                # any stale flat table before the per-file loop started.
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _foncier_pm_staging_flat (
                        code_departement TEXT,
                        code_commune TEXT,
                        nom_commune TEXT,
                        adresse TEXT,
                        code_parcelle TEXT,
                        contenance_parcelle TEXT,
                        nature_culture TEXT,
                        code_droit TEXT,
                        numero_siren TEXT,
                        groupe_personne TEXT,
                        forme_juridique_abregee TEXT,
                        denomination TEXT
                    )
                """)

                # Count before COPY to compute delta
                cur.execute("SELECT count(*) FROM _foncier_pm_staging_flat")
                count_before: int = cur.fetchone()[0]  # type: ignore[index]

                # COPY transformed CSV into flat table
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY _foncier_pm_staging_flat ({columns}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))

                cur.execute("SELECT count(*) FROM _foncier_pm_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
                row_count = count_after - count_before

            conn.commit()

        logger.info(
            "Staged %s: flat table now has %d rows (%d new)",
            file_path.name,
            count_after,
            row_count,
        )
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> partitioned staging, rebuild indexes, swap.

        Phase A: INSERT SELECT flat->partitioned + CREATE INDEX (own transaction).
        Phase B: Atomic swap via swap_tables() — logged guard, DROP old, two RENAMEs.
              The pre-swap DROP in swap_tables() eliminates the foncier_pm _old
              collision bug that was manually worked around in Phase 9.
        Phase C: Post-swap cleanup — drop flat staging, rename sequence + partitions.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "foncier_pm"
        old = f"{prod}_old"

        # --- Phase A: long work in its own transaction -----------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                # -- Transfer flat -> partitioned staging ------------------
                logger.info("Transferring flat staging to partitioned staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO foncier_pm_staging
                        (code_departement, code_commune, nom_commune,
                         adresse, code_parcelle, contenance_parcelle,
                         nature_culture, code_droit, numero_siren,
                         groupe_personne, forme_juridique_abregee,
                         denomination)
                    SELECT
                        code_departement, code_commune, nom_commune,
                        adresse, code_parcelle,
                        NULLIF(contenance_parcelle, '')::integer,
                        nature_culture, code_droit, numero_siren,
                        NULLIF(groupe_personne, ''),
                        forme_juridique_abregee, denomination
                    FROM _foncier_pm_staging_flat
                """)

                cur.execute("SELECT count(*) FROM foncier_pm_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
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
                # Same departement scope as staging (D-23).
                old_count = self.count_production_rows(cur, "foncier_pm")

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
        # swap_tables() handles: logged guard, DROP IF EXISTS foncier_pm_old CASCADE,
        # RENAME foncier_pm -> foncier_pm_old, RENAME foncier_pm_staging -> foncier_pm.
        # The pre-swap DROP eliminates the top-level _old collision bug from Phase 9.
        self.swap_tables(dsn)

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                # Drop flat staging
                cur.execute("DROP TABLE IF EXISTS _foncier_pm_staging_flat CASCADE")

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

    @staticmethod
    def _create_staging_indexes(cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        """Create production-equivalent indexes on the staging table."""
        indexes = [
            (
                "idx_foncier_pm_staging_code_commune",
                "CREATE INDEX idx_foncier_pm_staging_code_commune "
                "ON foncier_pm_staging (code_commune)",
            ),
            (
                "idx_foncier_pm_staging_code_parcelle",
                "CREATE INDEX idx_foncier_pm_staging_code_parcelle "
                "ON foncier_pm_staging (code_parcelle)",
            ),
            (
                "idx_foncier_pm_staging_numero_siren",
                "CREATE INDEX idx_foncier_pm_staging_numero_siren "
                "ON foncier_pm_staging (numero_siren)",
            ),
            (
                "idx_foncier_pm_staging_denomination",
                "CREATE INDEX idx_foncier_pm_staging_denomination "
                "ON foncier_pm_staging USING gin (denomination gin_trgm_ops)",
            ),
        ]

        for name, ddl in indexes:
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(ddl)


# -- Registry registration ----------------------------------------------------

from app.etl.registry import registry  # noqa: E402

registry.register(MajicPmPipeline)

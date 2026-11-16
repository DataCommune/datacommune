"""BD TOPO batiments concrete ETL pipeline.

Downloads departement-level .7z archives from data.geopf.fr; the runner
archives them as downloaded (D-124). Each archive is then listed, checked and
extracted by the native ``7z`` one department at a time, its GeoPackage
batiment layer validated, counted and loaded via ogr2ogr with
reprojection from the departement's own projection (Lambert 93, or the
DROM's UTM zone, D-122) to WGS84 into a flat staging table; the extraction and
the .7z are removed once the department is loaded. swap() INSERT SELECTs into
the partitioned production table and swaps atomically.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx
import psycopg

from app.config import settings
from app.etl.base import (
    MILLESIME_DATE,
    BasePipeline,
    check_millesime_label,
    split_dsn_credentials,
)
from app.etl.download import get_download_dir
from app.etl.errors import BdTopoDepartementMissingError
from app.etl.geo_gate import check_bounds_gate
from app.etl.source_archive import SourceReplay
from app.etl_bdtopo.config import (
    COLUMNS_TO_SELECT,
    GPKG_LAYER,
    bdtopo_territory,
    bdtopo_url,
)

logger = logging.getLogger(__name__)

_ARCHIVE_NAME_RE = re.compile(r"^bdtopo_([0-9][0-9AB][0-9]?)\.7z$")
_DEPT_DIR_RE = re.compile(r"^bdtopo_([0-9][0-9AB][0-9]?)$")
_FEATURE_COUNT_RE = re.compile(r"^Feature Count: ([0-9]+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ArchiveMember:
    """One entry of a ``7z l -slt`` listing."""

    path: str
    size: int | None
    directory: bool
    link: bool


def parse_7z_listing(text: str) -> list[ArchiveMember]:
    """Entries of a ``7z l -slt`` listing (7-Zip 25.01, Debian trixie form).

    Blocks after the ``----------`` line, ``Key = value`` lines separated by
    blank lines. ``Attributes`` is ``D drwxr-xr-x`` for a directory and
    ``A lrwxrwxrwx`` for a symbolic link.
    """
    _, separator, body = text.partition("\n----------\n")
    if not separator:
        raise ValueError("BD TOPO: listing 7z illisible (séparateur absent)")
    members: list[ArchiveMember] = []
    for block in re.split(r"\n\s*\n", body):
        fields = dict(
            line.split(" = ", 1) for line in block.splitlines() if " = " in line
        )
        if "Path" not in fields:
            continue
        attributes = fields.get("Attributes", "").split()
        size_text = fields.get("Size", "").strip()
        members.append(
            ArchiveMember(
                path=fields["Path"],
                size=int(size_text) if size_text.isdigit() else None,
                directory=bool(attributes) and "D" in attributes[0],
                link=len(attributes) > 1 and attributes[1].startswith("l"),
            )
        )
    return members


def _unsafe_member_path(path: str, work: Path) -> bool:
    parts = re.split(r"[\\/]", path)
    if (
        not path
        or path.startswith(("/", "\\"))
        or ":" in path
        or PurePosixPath(path).is_absolute()
        or any(part == ".." for part in parts)
    ):
        return True
    return not (work / path).resolve().is_relative_to(work.resolve())


def check_archive_members(
    members: list[ArchiveMember], work: Path, max_bytes: int, name: str
) -> str:
    """Accept a listing before anything is written; return the GPKG member.

    Refused: absolute or climbing paths, paths resolving outside *work*,
    links, an unknown size, a declared total above *max_bytes*, zero or
    several ``.gpkg`` (T-04.1-15, T-04.1-16).
    """
    if not members:
        raise ValueError(f"BD TOPO {name}: archive vide")
    total = 0
    gpkgs: list[str] = []
    for member in members:
        if _unsafe_member_path(member.path, work):
            raise ValueError(f"BD TOPO {name}: chemin interdit {member.path!r}")
        if member.link:
            raise ValueError(f"BD TOPO {name}: lien interdit {member.path!r}")
        if member.directory:
            continue
        if member.size is None:
            raise ValueError(f"BD TOPO {name}: taille inconnue {member.path!r}")
        total += member.size
        if member.path.lower().endswith(".gpkg"):
            gpkgs.append(member.path)
    if total > max_bytes:
        raise ValueError(
            f"BD TOPO {name}: taille déclarée {total} octets, "
            f"au-delà du plafond {max_bytes}"
        )
    if len(gpkgs) != 1:
        raise ValueError(f"BD TOPO {name}: {len(gpkgs)} fichiers .gpkg, un attendu")
    return gpkgs[0]


def _check_extracted_tree(work: Path, gpkg: Path, name: str) -> None:
    """After ``7z x``: no link, and exactly the announced GPKG."""
    for path in work.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"BD TOPO {name}: lien extrait {path.name}")
    found = [p for p in work.rglob("*") if p.suffix.lower() == ".gpkg"]
    if found != [gpkg] or not gpkg.is_file():
        raise ValueError(f"BD TOPO {name}: GPKG extrait inattendu {found}")


def _run_tool(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run the native ``7z``; a missing binary or a failure is an error."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "7z not found. Install 7zip: apt-get install 7zip (Debian)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"7z failed ({cmd[1]}): {exc.stderr}") from exc


class BdTopoPipeline(BasePipeline):
    """Concrete ETL pipeline for BD TOPO batiments (~millions of buildings)."""

    def __init__(self) -> None:
        super().__init__()
        # Feature Count of each loaded producer GPKG, per department.
        self._producer_features: dict[str, int] = {}
        # Extracted GPKG -> (department, .7z input, work directory).
        self._extracted: dict[Path, tuple[str, Path, Path]] = {}

    # Edition date in the URL (plan 02-11, D-27).
    millesime_mechanism = "segment_date"

    # run_etl --from-archive: archived inputs are linked, never copied
    # (plan 04.1-05, Astra review n° 7). A record of .7z archives (since
    # 04.1-05) is extracted one department at a time like a download; an
    # older record of GPKG files is loaded as it is (D-124: kept, not converted).
    archive_replay = True

    def restore_source_inputs(
        self, replay: SourceReplay, directory: Path
    ) -> list[Path]:
        return replay.link(directory)

    def validate_millesime(self, label: str) -> None:
        check_millesime_label(self.dataset_name, label, MILLESIME_DATE)

    # -- abstract properties ---------------------------------------------------

    @property
    def dataset_name(self) -> str:
        return "batiments"

    @property
    def expected_columns(self) -> list[str]:
        return [
            "cleabs",
            "nature",
            "usage_1",
            "nombre_d_etages",
            "hauteur",
            "altitude_minimale_sol",
            "altitude_maximale_toit",
        ]

    @property
    def staging_table_name(self) -> str:
        return "batiments_staging"

    @property
    def production_table_name(self) -> str:
        return "batiments"

    @property
    def flat_staging_table_name(self) -> str:
        return "_batiments_staging_flat"

    @property
    def expected_file_count(self) -> int:
        return len(self.expected_department_coverage)

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def has_department_partitions(self) -> bool:
        # Departement coverage is still checked (supports_department_scope):
        # staging must hold exactly the configured departements. Row-count
        # safety is handled inside swap().
        return False

    @property
    def min_staging_rows(self) -> int:
        # ~80% of ~50M production batiments rows, scaled to the number of
        # departements configured in base.
        return self.scoped_min_rows(40_000_000)

    # -- override: partitioned staging ----------------------------------------

    def create_staging_table(self, dsn: str) -> None:
        """Fresh import: drop flat rows left by a failed run, create the candidate.

        swap() recreates only the candidate (``_create_candidate``): the flat
        table then holds this run's rows. A fresh run must not append to the
        flat rows of an earlier run that failed before its flat drop (04.1-04).
        """
        self.drop_flat_staging(dsn)
        self._create_candidate(dsn)

    def _create_candidate(self, dsn: str) -> None:
        """Create a partitioned staging table mirroring production DDL."""
        staging = self.staging_table_name
        production = self.production_table_name

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
                # Logged parent and partitions (plan 04.1-04): swap_tables()
                # publishes these storage files as they are, without rewrite.
                cur.execute(
                    f"CREATE TABLE {staging} "
                    f"(LIKE {production} INCLUDING ALL EXCLUDING INDEXES) "
                    f"PARTITION BY LIST (code_departement)"
                )
                for dept in self.partition_department_scope:
                    safe = dept.lower()
                    cur.execute(
                        f"CREATE TABLE {staging}_{safe} "
                        f"PARTITION OF {staging} "
                        f"FOR VALUES IN ('{dept}')"
                    )
                cur.execute(
                    f"CREATE TABLE {staging}_default PARTITION OF {staging} DEFAULT"
                )
            conn.commit()

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download the producer's .7z archive of each department, unextracted.

        The archives are the processing units: the runner archives them as
        downloaded (D-124, exact producer bytes) before any extraction, and
        ``transform()`` extracts one department at a time. A hosted
        department not delivered (404, any refused download) raises
        ``BdTopoDepartementMissingError``: the import fails (D-122).
        """
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        """Sequential streamed downloads, one department after the other."""
        raw_dir = get_download_dir(self.dataset_name) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        paths: list[Path] = []
        timeout = httpx.Timeout(60.0, read=600.0)

        departements = self.expected_department_coverage
        async with httpx.AsyncClient(timeout=timeout) as client:
            for i, dept in enumerate(departements):
                # IGN URLs require 3-char zero-padded dept codes (e.g. D075, D02A)
                url = bdtopo_url(dept.zfill(3), self.pinned_millesime)
                archive_path = raw_dir / f"bdtopo_{dept}.7z"

                # D-122: a hosted departement the producer does not deliver
                # ends the import; there is no "n succeeded, m failed" warning.
                try:
                    delivered = await self._download_archive(
                        client, url, archive_path, dept
                    )
                except BdTopoDepartementMissingError:
                    raise
                except Exception as exc:
                    reason = (
                        f"HTTP {exc.response.status_code}"
                        if isinstance(exc, httpx.HTTPStatusError)
                        else type(exc).__name__
                    )
                    raise BdTopoDepartementMissingError(
                        dept, f"téléchargement en échec: {reason}"
                    ) from exc
                if not delivered:
                    raise BdTopoDepartementMissingError(dept, "404 du producteur")
                paths.append(archive_path)
                logger.info("Downloaded %s (%d/%d)", dept, i + 1, len(departements))

        logger.info("Downloaded %d BD TOPO departments total", len(paths))
        return paths

    async def _download_archive(
        self,
        client: httpx.AsyncClient,
        url: str,
        archive_path: Path,
        dept: str,
    ) -> bool:
        """Stream one .7z archive to disk; False when the producer has none (404).

        Streams under the download size cap and fails hard on Content-Length
        mismatch (A-01: no RAM buffering, no truncated archive kept).
        """
        try:
            async with client.stream("GET", url, follow_redirects=True) as response:
                if response.status_code == 404:
                    logger.warning("BD TOPO archive not found for dept %s (404)", dept)
                    return False
                response.raise_for_status()

                expected_size_raw = response.headers.get("content-length")
                max_bytes = settings.etl_max_download_bytes
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                total_bytes = 0
                with open(archive_path, "wb") as f:
                    async for chunk in response.aiter_bytes(65_536):
                        f.write(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            f.close()
                            archive_path.unlink(missing_ok=True)
                            raise RuntimeError(
                                f"BD TOPO dept {dept}: download exceeded "
                                f"{max_bytes} bytes cap (aborting at {total_bytes})"
                            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning("BD TOPO archive not found for dept %s (404)", dept)
                return False
            raise

        # Content-Length verification is blocking here (A-01): a mismatch on
        # a truncated/inflated archive must abort, not warn.
        if expected_size_raw is not None and total_bytes != int(expected_size_raw):
            archive_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"BD TOPO dept {dept}: size mismatch "
                f"(got {total_bytes}, expected {expected_size_raw})"
            )
        return True

    # -- native extraction, one department at a time (plan 04.1-05) -----------

    @property
    def _work_root(self) -> Path:
        return get_download_dir(self.dataset_name) / "work"

    @staticmethod
    def department_of(path: Path) -> str:
        """Department of an input: ``bdtopo_<dept>.7z``, or a GPKG under
        ``bdtopo_<dept>/`` (records archived as GPKG before plan 04.1-05)."""
        if match := _ARCHIVE_NAME_RE.fullmatch(path.name):
            return match.group(1)
        if path.suffix == ".gpkg":
            for parent in path.parents:
                if match := _DEPT_DIR_RE.fullmatch(parent.name):
                    return match.group(1)
        raise ValueError(f"BD TOPO: département illisible dans {path.name}")

    def _clear_work(self) -> None:
        """Remove every extraction: at most one department is ever on disk."""
        self._extracted.clear()
        if self._work_root.exists():
            shutil.rmtree(self._work_root)

    def _prepared_gpkg(self, archive: Path) -> Path:
        """Extracted and schema-checked GPKG of *archive*, extracted once."""
        for gpkg, (_, source, _) in self._extracted.items():
            if source == archive and gpkg.is_file():
                return gpkg
        try:
            gpkg = self._extract(archive)
        except (ValueError, RuntimeError) as exc:
            # Empty, refused or unextractable archive: the departement is not
            # delivered (D-122), the import fails instead of skipping it.
            self._clear_work()
            raise BdTopoDepartementMissingError(
                self.department_of(archive), str(exc)
            ) from exc
        try:
            self._check_gpkg_schema(gpkg)
        except Exception:
            self._clear_work()
            raise
        return gpkg

    def _extract(self, archive: Path) -> Path:
        """List, check, then extract *archive* with the native ``7z``.

        Nothing is written before the listing is accepted: no absolute or
        climbing path, no link, declared total size under
        ``etl_max_decompressed_bytes``, exactly one ``.gpkg``.
        """
        dept = self.department_of(archive)
        work = self._work_root / dept
        listing = _run_tool(["7z", "l", "-slt", str(archive)], timeout=600)
        members = parse_7z_listing(listing.stdout)
        gpkg_member = check_archive_members(
            members, work, settings.etl_max_decompressed_bytes, archive.name
        )
        self._clear_work()
        work.mkdir(parents=True)
        _run_tool(["7z", "x", "-y", "-bd", f"-o{work}", str(archive)], timeout=3600)
        gpkg = work / gpkg_member
        _check_extracted_tree(work, gpkg, archive.name)
        self._extracted[gpkg] = (dept, archive, work)
        logger.info("Extracted GPKG for dept %s: %s", dept, gpkg.name)
        return gpkg

    def _layer_summary(self, gpkg: Path) -> str:
        """``ogrinfo -so`` of the batiment layer (fields and Feature Count)."""
        try:
            result = subprocess.run(
                ["ogrinfo", "-so", str(gpkg), GPKG_LAYER],
                capture_output=True,
                text=True,
                check=True,
                timeout=300,
            )
        except FileNotFoundError as exc:
            msg = (
                "ogrinfo not found. Install GDAL: "
                "apt-get install gdal-bin (Debian/Ubuntu)"
            )
            raise RuntimeError(msg) from exc
        except subprocess.CalledProcessError as exc:
            msg = (
                f"Layer '{GPKG_LAYER}' not found in {gpkg.name}. "
                f"ogrinfo stderr: {exc.stderr}"
            )
            raise ValueError(msg) from exc
        return result.stdout

    def _check_gpkg_schema(self, gpkg: Path) -> None:
        output = self._layer_summary(gpkg)
        for col in self.expected_columns:
            if col not in output:
                msg = f"Column '{col}' not found in {GPKG_LAYER} layer of {gpkg.name}"
                raise ValueError(msg)

    def validate_schema(self, file_path: Path) -> None:
        """Check the batiment layer of the GPKG the input carries.

        A .7z is checked, extracted and its GPKG checked here, before any
        load: a dry run keeps the column check (validation only, one GPKG on
        disk at a time). ``transform()`` reuses the extraction.
        """
        if file_path.suffix == ".7z":
            self._prepared_gpkg(file_path)
        else:
            self._check_gpkg_schema(file_path)

    def transform(self, file_path: Path) -> Path:
        """GPKG to load: extracted from a .7z, or the GPKG itself.

        A GPKG input (archive record written before plan 04.1-05) is loaded
        as it is, without extraction or conversion (D-124).
        """
        if file_path.suffix == ".7z":
            return self._prepared_gpkg(file_path)
        return file_path

    def stage_counters(self) -> dict[str, int]:
        """Feature Count of each producer GPKG (fidelity reference, 04.1-03)."""
        return {
            f"producer_features_{dept}": count
            for dept, count in sorted(self._producer_features.items())
        }

    def _build_ogr_cmd(
        self, source: str, sql_query: str, dsn: str, source_srs: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build the ogr2ogr command loading *source* into the flat staging.

        *source_srs* is the departement's own projection (D-122,
        ``BDTOPO_TERRITORIES``): Lambert 93 in metropolitan France, the
        territory's UTM zone in a DROM. Returns (command, extra_env). The
        DSN password is stripped from argv and carried via PGPASSWORD
        instead (A-02: argv is readable in /proc/<pid>/cmdline for the whole
        load).
        """
        safe_dsn, pg_env = split_dsn_credentials(dsn)
        ogr_cmd = [
            "ogr2ogr",
            "-f",
            "PostgreSQL",
            f"PG:{safe_dsn}",
            source,
            "-sql",
            sql_query,
            "-nln",
            "_batiments_staging_flat",
            "-nlt",
            "MULTIPOLYGON",
            "-s_srs",
            source_srs,
            "-t_srs",
            "EPSG:4326",
            "-lco",
            "GEOMETRY_NAME=geometrie",
            "--config",
            "PG_USE_COPY",
            "YES",
            "-gt",
            "65536",
            "-append",
        ]
        return ogr_cmd, pg_env

    def stage(self, file_path: Path) -> int:
        """Count, load, then free the department's files.

        The Feature Count of the producer GPKG is kept for ``stage_counters``.
        Once loaded, the extraction and the input .7z (already archived by
        the runner) are removed; a replay input is a link, so only the link
        goes, never the archived object.
        """
        extracted = self._extracted.get(file_path)
        if extracted is not None:
            dept, source, work = extracted
        else:
            dept, source, work = self.department_of(file_path), file_path, None
        summary = self._layer_summary(file_path)
        match = _FEATURE_COUNT_RE.search(summary)
        if match is None:
            raise RuntimeError(f"BD TOPO {file_path.name}: Feature Count absent")
        rows = self._load_gpkg(file_path, dept)
        self._producer_features[dept] = int(match.group(1))
        if work is not None:
            self._extracted.pop(file_path, None)
            shutil.rmtree(work)
        self._drop_input(source)
        return rows

    def _drop_input(self, path: Path) -> None:
        """Remove a loaded input of the download cache (link or file).

        Anything outside the cache is left alone: an input is never deleted
        through a resolved path, so an archived object cannot be reached.
        """
        cache = get_download_dir(self.dataset_name)
        if path.parent.resolve().is_relative_to(cache.resolve()):
            path.unlink(missing_ok=True)

    def _load_gpkg(self, file_path: Path, dept_code: str) -> int:
        """Bulk-load a GPKG batiment layer into _batiments_staging_flat via ogr2ogr.

        Uses ogr2ogr with -s_srs <projection of the departement>
        (``bdtopo_territory``: Lambert 93, or the DROM's UTM zone) and
        -t_srs EPSG:4326. Each department's rows are tagged with
        code_departement after loading.
        """
        dsn = self.get_sync_dsn()

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Create flat staging table (idempotent). Unlogged: rebuilt
                # from the sources after a crash, never published (04.1-04).
                cur.execute("""
                    CREATE UNLOGGED TABLE IF NOT EXISTS _batiments_staging_flat (
                        cleabs TEXT,
                        nature VARCHAR(50),
                        usage_1 VARCHAR(50),
                        nombre_d_etages INTEGER,
                        hauteur NUMERIC(6, 2),
                        altitude_minimale_sol NUMERIC(7, 2),
                        altitude_maximale_toit NUMERIC(7, 2),
                        geometrie GEOMETRY(MultiPolygon, 4326),
                        code_departement VARCHAR(3)
                    )
                """)
            conn.commit()

        # Count before ogr2ogr to compute delta
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM _batiments_staging_flat")
            count_before: int = cur.fetchone()[0]  # type: ignore[index]

        # Build ogr2ogr command. Credentials go to PGPASSWORD env,
        # never argv (A-02).
        # GDAL 3.10+ forbids -select with -append; use -sql instead.
        file_posix = str(file_path).replace("\\", "/")
        sql_query = f"SELECT {COLUMNS_TO_SELECT} FROM {GPKG_LAYER}"
        _, source_srs = bdtopo_territory(dept_code)
        ogr_cmd, pg_env = self._build_ogr_cmd(file_posix, sql_query, dsn, source_srs)

        try:
            result = subprocess.run(
                ogr_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=3600,  # 1h timeout for large departments
                env={**os.environ, **pg_env},
            )
            if result.stderr:
                logger.debug("ogr2ogr stderr: %s", result.stderr)
        except FileNotFoundError as exc:
            msg = (
                "ogr2ogr not found. Install GDAL: "
                "apt-get install gdal-bin (Debian/Ubuntu)"
            )
            raise RuntimeError(msg) from exc
        except subprocess.CalledProcessError as exc:
            msg = f"ogr2ogr failed for {file_path.name}: {exc.stderr}"
            raise RuntimeError(msg) from exc

        # Tag rows from this department that have no code_departement yet
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE _batiments_staging_flat "
                    "SET code_departement = %s "
                    "WHERE code_departement IS NULL",
                    (dept_code,),
                )
                cur.execute("SELECT count(*) FROM _batiments_staging_flat")
                count_after: int = cur.fetchone()[0]  # type: ignore[index]
            conn.commit()

        row_count = count_after - count_before
        logger.info(
            "Staged %s (dept %s): %d rows added (flat table total: %d)",
            file_path.name,
            dept_code,
            row_count,
            count_after,
        )
        return row_count

    def swap(self) -> None:
        """Transfer flat staging -> partitioned staging, rebuild indexes, swap.

        Phase A1: create staging + INSERT SELECT + count + DROP of the flat
                  table, one transaction.
        Phase A2: index builds on the candidate, own transaction: the flat table
                  is already gone, so both never share the disk (04.1-04).
        Phase B: Atomic swap via swap_tables().
        Phase C: Post-swap cleanup — idempotent flat drop, rename sequence +
                 partitions.

        Interrupted between A1 and A2, the flat table is missing: the runner's
        resume rebuilds the candidate from the sources.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name  # "batiments"
        old = f"{prod}_old"

        # --- Phase A1: transfer, then free the flat table ---------------------
        # Ensure partitioned staging exists (the flat table stays)
        self._create_candidate(dsn)

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)

                logger.info("Transferring flat staging to partitioned staging")
                t0 = time.perf_counter()

                cur.execute("""
                    INSERT INTO batiments_staging
                        (id, code_departement, cleabs, nature, usage_1,
                         nombre_d_etages, hauteur,
                         altitude_minimale_sol, altitude_maximale_toit,
                         geometrie)
                    SELECT
                        row_number() OVER () AS id,
                        code_departement, cleabs, nature, usage_1,
                        nombre_d_etages, hauteur,
                        altitude_minimale_sol, altitude_maximale_toit,
                        geometrie
                    FROM _batiments_staging_flat
                    WHERE code_departement IS NOT NULL
                """)

                cur.execute("SELECT count(*) FROM batiments_staging")
                staging_count: int = cur.fetchone()[0]  # type: ignore[index]
                logger.info(
                    "Partitioned staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )

                cur.execute("DROP TABLE IF EXISTS _batiments_staging_flat CASCADE")

            conn.commit()  # A1 commits: candidate loaded, flat table freed

        # --- Phase A2: indexes on the candidate alone --------------------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                logger.info("Rebuilding indexes on staging table")
                self._create_staging_indexes(cur)

                logger.info("Running safety check")
                old_count = self.count_production_rows(cur, "batiments")

            conn.commit()  # Phase A2 commits before Phase B opens

        # Gate: bounds check before swap (D-03 / D-21 / FIX-04)
        # Abort if > 5% of staged geometries fall outside France+DOM.
        # Diagnostic: the 15.5% MATRIX-baseline flag is a false positive caused
        # by simplified geo_departements polygons vs precise BDTOPO cadastral
        # boundaries; real out-of-bounds (NOT EXISTS all depts) = 0.027%.
        # This gate catches any future reprojection regression (e.g. ogr2ogr
        # -s_srs/-t_srs removed) which would produce ~100% hors-France.
        if gate_err := check_bounds_gate(
            dsn,
            "batiments_staging",
            "geometrie",
            "batiments",
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
                cur.execute("DROP TABLE IF EXISTS _batiments_staging_flat CASCADE")
                # Sequence rename — IF EXISTS guards safe for pipelines without
                # a staging sequence
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {self.staging_table_name}_id_seq "
                    f"RENAME TO {prod}_id_seq"
                )
                # rename_partitions_after_swap handles the configured partitions.
                self.rename_partitions_after_swap(cur)
            conn.commit()

        # Register _old for deferred TTL cleanup
        self.register_old_table(dsn, old)

        logger.info("Cleanup complete: dropped flat staging, renamed partitions")

    # Production index names (migration 008 + 021) paired with their column
    # spec. Staging twins are derived via BasePipeline.staging_index_name() so
    # the post-swap rename is a pure suffix strip (DB-HYGIENE-01).
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_batiments_geometrie", "USING GIST (geometrie)"),
        ("idx_batiments_geometrie_geog", "USING GIST (geography(geometrie))"),
        ("idx_batiments_cleabs", "(cleabs)"),
        ("idx_batiments_code_departement", "(code_departement)"),
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

registry.register(BdTopoPipeline)

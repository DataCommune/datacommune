"""BDNB building groups per parcel: concrete ETL pipeline (plan 04.7-19).

Downloads one zipped export per departement of the open-data millesime, reads
three of its members as streams (relations, ``ffo_bat`` attributes, version
history), COPYs every record into one flat staging table, then swap() joins
the relations to the attributes into the production-shaped staging table and
swaps atomically.

Feeds the ``construction`` theme of the address report, which reads THIS
table instead of calling api.bdnb.io. The ``/v1/bdnb*`` routes stay a proxy.
"""

import asyncio
import datetime as dt
import logging
import re
import time
from pathlib import Path

import httpx
import psycopg

from app.etl.base import BasePipeline, check_millesime_label
from app.etl.download import get_download_dir, verified_download
from app.etl.errors import FatalInputError
from app.etl.registry import register_pipeline
from app.etl.runner import PipelineRunner
from app.etl.source_archive import SourceReplay
from app.etl_bdnb.config import (
    DATAGOUV_DATASET_URL,
    DATAGOUV_TIMEOUT,
    DOWNLOAD_CONCURRENCY,
    DOWNLOAD_TIMEOUT,
    EXPORT_URL_TEMPLATE,
    MILLESIME_IN_URL_RE,
    OUTPUT_COLUMNS,
    RELATION_COLUMNS,
)
from app.etl_bdnb.transform import (
    LABEL_RE,
    MILLESIME_RE,
    ComptesExport,
    VersionExport,
    departement_du_fichier,
    label_de_millesime,
    read_version,
    transform_export,
    validate_export,
)
from app.scope.floors import producer_departement_floor

logger = logging.getLogger(__name__)

FLAT = "_bdnb_batiment_parcelle_staging_flat"

# Millesime as the URL writes it (``2026-02-b``); the export labels it
# ``2026-02.b`` in its version member.
MILLESIME_BDNB = (MILLESIME_RE, None, "AAAA-MM-x")


class BdnbExportError(FatalInputError):
    """An export of the run the import cannot take: the run fails, nothing is
    published (absent, other millesime, refused member or column)."""

    def __init__(self, departement: str, reason: str) -> None:
        self.departement = departement
        self.reason = reason
        super().__init__(
            f"BDNB: export du département {departement} refusé ({reason}) : "
            "import en échec, rien n'est publié"
        )


async def millesime_courant(client: httpx.AsyncClient) -> str:
    """The newest millesime named by the resources of the data.gouv.fr dataset.

    ``2026-02-b`` (URL form). Refused when no resource names one: never a
    constant written in the code (D6).
    """
    response = await client.get(DATAGOUV_DATASET_URL, follow_redirects=True)
    response.raise_for_status()
    resources = response.json().get("resources") or []
    found = {
        match
        for resource in resources
        if isinstance(resource, dict)
        for match in re.findall(MILLESIME_IN_URL_RE, str(resource.get("url") or ""))
        if MILLESIME_RE.fullmatch(match)
    }
    if not found:
        raise RuntimeError("BDNB: aucun millésime dans les ressources data.gouv.fr")
    return max(found)


@register_pipeline("bdnb_batiment_parcelle")
class BdnbBatimentParcellePipeline(BasePipeline):
    """Concrete ETL pipeline for the BDNB parcel table (~1.4M rows on 44+85)."""

    def __init__(self) -> None:
        super().__init__()
        # Version read in each export of the run, per departement.
        self._versions: dict[str, VersionExport] = {}
        # Transformed CSV -> (departement, input zip), freed once staged.
        self._inputs: dict[Path, tuple[str, Path]] = {}
        self._comptes: dict[str, ComptesExport] = {}
        # Input zip of each departement of the run (download or restore).
        self._sources: dict[str, Path] = {}
        self._swap_counters: dict[str, int] = {}
        # Label every export of the run must carry (``2026-02.b``).
        self._label_cible: str | None = None

    # The millesime is a segment of the export URL (``2026-02-b``).
    millesime_mechanism = "segment_date"

    def validate_millesime(self, label: str) -> None:
        check_millesime_label(self.dataset_name, label, MILLESIME_BDNB)

    # run_etl --from-archive: archived zips are linked, never copied (as BD
    # TOPO); their labels are read back before the runner archives the replay.
    archive_replay = True

    def restore_source_inputs(
        self, replay: SourceReplay, directory: Path
    ) -> list[Path]:
        """Link the archived zips, then read the label of each one.

        The runner archives the replay right after this method and records
        ``source_period_label`` at that moment, before any validation: without
        this read the new record would carry no label. Refused before any
        archiving unless every zip carries the well-formed label of the
        replayed record.
        """
        paths = replay.link(directory)
        attendu = replay.record.get("source_period_label")
        versions = {departement_du_fichier(path): read_version(path) for path in paths}
        labels = sorted({version.label for version in versions.values()})
        if not isinstance(attendu, str) or not LABEL_RE.fullmatch(attendu):
            raise BdnbExportError("archive", f"étiquette d'archive {attendu!r}")
        if labels != [attendu]:
            raise BdnbExportError(
                "archive", f"étiquettes {labels} pour l'archive {attendu}"
            )
        self._versions = versions
        self._sources = {departement_du_fichier(path): path for path in paths}
        self._label_cible = attendu
        return paths

    # -- abstract properties ---------------------------------------------------

    @property
    def dataset_name(self) -> str:
        return "bdnb_batiment_parcelle"

    @property
    def scope_dataset_keys(self) -> tuple[str, ...]:
        # Published under the catalogue key "bdnb", whose API stays proxied.
        return ("bdnb",)

    @property
    def expected_columns(self) -> list[str]:
        # Header validation itself is delegated to validate_export, which must
        # open the members inside the zip.
        return list(RELATION_COLUMNS)

    @property
    def staging_table_name(self) -> str:
        return "bdnb_batiment_parcelle_staging"

    @property
    def production_table_name(self) -> str:
        return "bdnb_batiment_parcelle"

    @property
    def flat_staging_table_name(self) -> str:
        return FLAT

    # -- scope -----------------------------------------------------------------

    @property
    def supports_department_scope(self) -> bool:
        return True

    @property
    def has_department_partitions(self) -> bool:
        # Departement coverage is still checked (supports_department_scope):
        # every expected departement must be present in the flat staging.
        return False

    @property
    def staging_is_partitioned(self) -> bool:
        return False

    @property
    def expected_file_count(self) -> int:
        return len(self.expected_department_coverage)

    @property
    def min_success_ratio(self) -> float:
        # One file per departement, all mandatory: a partial refresh would
        # silently drop a whole departement's buildings.
        return 1.0

    # -- quality gate configuration -------------------------------------------

    @property
    def min_staging_rows(self) -> int:
        # Runner check disabled: total_rows counts flat records of both
        # natures, not the published relations; swap() checks those.
        return 0

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        # Production-shaped staging is only populated inside swap().
        return True

    # -- provenance (PROV-03) --------------------------------------------------

    @property
    def source_period_label(self) -> str | None:
        """Millesime label read in the exports (``2026-02.b``), never the URL form."""
        labels = {version.label for version in self._versions.values()}
        return labels.pop() if len(labels) == 1 else None

    @property
    def source_updated_at(self) -> dt.datetime | None:
        """Publication date of the millesime, UTC-anchored (timestamptz)."""
        dates = {version.date_publication for version in self._versions.values()}
        if len(dates) != 1:
            return None
        return dt.datetime.combine(dates.pop(), dt.time(), tzinfo=dt.UTC)

    # -- stage methods ---------------------------------------------------------

    def download(self) -> list[Path]:
        """Download the zipped export of each expected departement."""
        return asyncio.run(self._async_download())

    async def _async_download(self) -> list[Path]:
        raw_dir = get_download_dir(self.dataset_name) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        self._versions = {}
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        if self.pinned_millesime is not None:
            millesime = self.pinned_millesime
        else:
            async with httpx.AsyncClient(timeout=DATAGOUV_TIMEOUT) as datagouv:
                millesime = await millesime_courant(datagouv)
        self.validate_millesime(millesime)
        self._label_cible = label_de_millesime(millesime)
        logger.info("BDNB millesime %s (%s)", millesime, self._label_cible)

        async def one(
            client: httpx.AsyncClient, dept: str
        ) -> tuple[str, Path, VersionExport]:
            url = EXPORT_URL_TEMPLATE.format(millesime=millesime, dept=dept.lower())
            path = raw_dir / url.rsplit("/", 1)[1]
            async with semaphore:
                # The catalogue says the producer publishes this departement:
                # a missing object (403 on this bucket, or 404) fails the
                # import, never a skipped file.
                try:
                    delivered = await verified_download(client, url, path)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    raise BdnbExportError(dept, f"HTTP {status}") from exc
                if not delivered:
                    raise BdnbExportError(dept, "HTTP 404")
            version = self._checked_version(dept, path)
            logger.info("Downloaded BDNB export %s (%s)", dept, version.label)
            return dept, path, version

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
            results = await asyncio.gather(
                *(one(client, dept) for dept in self.expected_department_coverage)
            )
        self._versions = {dept: version for dept, _path, version in results}
        self._sources = {dept: path for dept, path, _version in results}
        return [path for _dept, path, _version in results]

    def _checked_version(self, dept: str, path: Path) -> VersionExport:
        """The version of *path*, refused unless it is the run's millesime."""
        try:
            version = read_version(path)
        except ValueError as exc:
            raise BdnbExportError(dept, str(exc)) from exc
        if version.label != self._label_cible:
            raise BdnbExportError(
                dept, f"millésime {version.label}, attendu {self._label_cible}"
            )
        return version

    def validate_schema(self, file_path: Path) -> None:
        """Check the label, the three members and their headers inside the zip.

        Same code on a download and on a replay: the label must be the one
        the run targets (download) or the replayed record's (restore).
        """
        dept = departement_du_fichier(file_path)
        try:
            version = validate_export(file_path)
        except ValueError as exc:
            raise BdnbExportError(dept, str(exc)) from exc
        if self._label_cible is not None and version.label != self._label_cible:
            raise BdnbExportError(
                dept, f"millésime {version.label}, attendu {self._label_cible}"
            )
        self._versions[dept] = version

    def transform(self, file_path: Path) -> Path:
        """Stream the export into a COPY-ready CSV of relation and ffo rows."""
        dept = departement_du_fichier(file_path)
        output_path = (
            get_download_dir(self.dataset_name) / "transformed" / f"bdnb-{dept}.csv"
        )
        try:
            self._comptes[dept] = transform_export(file_path, output_path, dept)
        except ValueError as exc:
            raise BdnbExportError(dept, str(exc)) from exc
        self._inputs[output_path] = (dept, file_path)
        return output_path

    def stage_counters(self) -> dict[str, int]:
        """Records of each producer file (fidelity reference, 04.1-03).

        A resume skips the files its checkpoint already loaded: this instance
        never transformed them, so their zip (downloaded or restored again,
        same bytes as the checkpoint's) is counted by the same reading,
        without loading anything. The receipt always names every departement.
        """
        for dept, path in sorted(self._sources.items()):
            if dept in self._comptes:
                continue
            if not path.is_file():
                raise RuntimeError(f"BDNB: export {dept} absent, comptes non établis")
            self._comptes[dept] = transform_export(path, None, dept)
        counters: dict[str, int] = {}
        for dept, comptes in sorted(self._comptes.items()):
            counters[f"producteur_relations_{dept}"] = comptes.relations_lues
            counters[f"producteur_ffo_{dept}"] = comptes.ffo_lues
            counters[f"relations_ecartees_{dept}"] = comptes.relations_ecartees
        return counters

    def stage(self, file_path: Path) -> int:
        """Bulk-load a transformed CSV into the flat staging via COPY."""
        dsn = self.get_sync_dsn()
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Unlogged: rebuilt from the sources after a crash, never
                # published (04.1-04). create_staging_table() dropped any
                # flat table of a former run.
                cur.execute(f"""
                    CREATE UNLOGGED TABLE IF NOT EXISTS {FLAT} (
                        nature VARCHAR(8),
                        batiment_groupe_id TEXT,
                        parcelle_id TEXT,
                        code_departement VARCHAR(3),
                        parcelle_principale BOOLEAN,
                        annee_construction SMALLINT,
                        mat_mur_txt TEXT,
                        mat_toit_txt TEXT
                    )
                """)
                count_before: int = cur.execute(
                    f"SELECT count(*) FROM {FLAT}"
                ).fetchone()[0]  # type: ignore[index]
                columns = ", ".join(OUTPUT_COLUMNS)
                copy_sql = (
                    f"COPY {FLAT} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)"
                )
                with (
                    open(file_path, encoding="utf-8") as f,
                    cur.copy(copy_sql) as copy,
                ):
                    for line in f:
                        copy.write(line.encode("utf-8"))
                count_after: int = cur.execute(
                    f"SELECT count(*) FROM {FLAT}"
                ).fetchone()[0]  # type: ignore[index]
            conn.commit()
        row_count = count_after - count_before
        logger.info(
            "Staged %s: %d rows (flat total %d)", file_path.name, row_count, count_after
        )
        entry = self._inputs.pop(file_path, None)
        self._drop_input(file_path)
        if entry is not None:
            self._drop_input(entry[1])
        return row_count

    def _drop_input(self, path: Path) -> None:
        """Remove a loaded input of the download cache (link or file).

        Anything outside the cache is left alone: an input is never deleted
        through a resolved path, so an archived object cannot be reached.
        """
        cache = get_download_dir(self.dataset_name)
        if path.parent.resolve().is_relative_to(cache.resolve()):
            path.unlink(missing_ok=True)

    def swap(self) -> None:
        """Join flat staging -> production staging, index, swap.

        Phase A1: INSERT SELECT (relations deduplicated, LEFT join on the ffo
                  attributes), counts and guards, DROP of the flat table, one
                  transaction.
        Phase A2: the unique read index on the candidate, own transaction: the
                  flat table is already gone, so both never share the disk.
        Phase B: atomic swap via swap_tables().
        Phase C: sequence rename.
        Phase D: ANALYZE (non-fatal), register the _old table.
        """
        dsn = self.get_sync_dsn()
        prod = self.production_table_name
        staging = self.staging_table_name

        # --- Phase A1: transfer, guards, then free the flat table -----------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                t0 = time.perf_counter()
                cur.execute(_TRANSFER_SQL.format(staging=staging, flat=FLAT))
                par_dept: dict[str, int] = dict(
                    cur.execute(
                        f"SELECT code_departement, count(*) FROM {staging} GROUP BY 1"
                    ).fetchall()
                )
                staging_count = sum(par_dept.values())
                logger.info(
                    "Staging loaded: %d rows in %.1fs",
                    staging_count,
                    time.perf_counter() - t0,
                )
                doublons, divergents = cur.execute(
                    _DUPLICATES_SQL.format(flat=FLAT)
                ).fetchone()  # type: ignore[misc]
                # Set before the guards: a refused candidate still journals
                # the counts that refused it (D13, ffo_divergents).
                self._swap_counters = {
                    **{
                        f"publiees_{code}": count
                        for code, count in sorted(par_dept.items())
                    },
                    "doublons_inter_fichiers": doublons,
                    "ffo_divergents": divergents,
                }
                self._check_departement_floors(par_dept)
                old_count = self.count_production_rows(cur, prod)
                if old_count > 0 and staging_count < old_count * 0.5:
                    msg = (
                        f"Swap aborted: staging has {staging_count} rows "
                        f"but production has {old_count} "
                        f"({staging_count / old_count:.0%} < 50% threshold)"
                    )
                    raise RuntimeError(msg)
                # D13: no rule picks one of two diverging ffo rows of a group;
                # nothing is decided silently, the generation is refused.
                if divergents:
                    raise RuntimeError(
                        f"Swap aborted: {divergents} building groups carry "
                        "different ffo attributes in two rows (ffo_divergents) "
                        "-- no choice rule is decided"
                    )
                cur.execute(f"DROP TABLE IF EXISTS {FLAT} CASCADE")
            conn.commit()

        # --- Phase A2: the read index on the candidate alone ----------------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                self._create_staging_indexes(cur)
            conn.commit()
        PipelineRunner._check_schema_drift_conn(self, dsn, logger)

        # --- Phase B: atomic swap -------------------------------------------
        self.swap_tables(dsn)

        # --- Phase C: post-swap cleanup (non-critical, separate TX) ---------
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                cur.execute(f"DROP TABLE IF EXISTS {FLAT} CASCADE")
                cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
                cur.execute(
                    f"ALTER SEQUENCE IF EXISTS {staging}_id_seq RENAME TO {prod}_id_seq"
                )
            conn.commit()

        # --- Phase D: ANALYZE, for the theme's index path -------------------
        # Non-fatal, as for rnb_liens: the swap succeeded and the data is live;
        # a failed ANALYZE only degrades latency until autovacuum runs.
        try:
            with psycopg.connect(dsn) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    self.set_statement_timeout(cur)
                    cur.execute(f"ANALYZE {prod}")
        except psycopg.Error as exc:
            logger.warning(
                "ANALYZE on %s failed, autovacuum will catch up: %s", prod, exc
            )
        self.register_old_table(dsn, f"{prod}_old")

    def swap_counters(self) -> dict[str, int]:
        return dict(self._swap_counters)

    def _check_departement_floors(self, par_dept: dict[str, int]) -> None:
        """80 % of the producer count of each departement, once counted (T4a).

        One truncated departement is refused even when another one fills the
        total. Inactive while ``app.scope.producer_counts`` has no count.
        """
        for code in self.expected_department_coverage:
            floor = producer_departement_floor(
                self.dataset_name, code, "lignes_stockees"
            )
            if floor is not None and par_dept.get(code, 0) < floor:
                raise RuntimeError(
                    f"Department volume: {code} has {par_dept.get(code, 0):,} "
                    f"rows in {self.staging_table_name} (minimum {floor:,}) "
                    "-- swap aborted"
                )

    # Production index (migration 113) and its column spec; the staging twin is
    # derived by staging_index_name() so the post-swap rename is a suffix strip.
    STAGING_INDEX_SPECS: tuple[tuple[str, str], ...] = (
        ("idx_bdnb_bp_parcelle_groupe", "(parcelle_id, batiment_groupe_id)"),
    )

    def _create_staging_indexes(self, cur: psycopg.Cursor) -> None:  # type: ignore[type-arg]
        for canonical, spec in self.STAGING_INDEX_SPECS:
            name = self.staging_index_name(canonical)
            logger.info("Creating index %s", name)
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            cur.execute(
                f"CREATE UNIQUE INDEX {name} ON {self.staging_table_name} {spec}"
            )


# The rule the API applies when read by parcel (04.7-19-RESEARCH § 3): EVERY
# relation of the parcel, principal or not, LEFT-joined on the ffo attributes
# of its group (a group without ffo row keeps NULL attributes). A relation in
# two files is kept once, in the file of the smallest code (COLLATE "C"), its
# principal flag being the OR of the files; the ffo row of a group comes from
# the smallest code too. Deterministic order, so a replay numbers alike.
_TRANSFER_SQL = """
    INSERT INTO {staging}
        (batiment_groupe_id, parcelle_id, code_departement, parcelle_principale,
         annee_construction, mat_mur_txt, mat_toit_txt)
    WITH relations AS (
        SELECT parcelle_id,
               batiment_groupe_id,
               min(code_departement COLLATE "C") AS code_departement,
               bool_or(parcelle_principale) AS parcelle_principale
        FROM {flat}
        WHERE nature = 'relation'
        GROUP BY parcelle_id, batiment_groupe_id
    ),
    ffo AS (
        SELECT DISTINCT ON (batiment_groupe_id)
               batiment_groupe_id, annee_construction, mat_mur_txt, mat_toit_txt
        FROM {flat}
        WHERE nature = 'ffo'
        ORDER BY batiment_groupe_id, code_departement COLLATE "C",
                 annee_construction, mat_mur_txt, mat_toit_txt
    )
    SELECT r.batiment_groupe_id, r.parcelle_id, r.code_departement,
           r.parcelle_principale,
           f.annee_construction, f.mat_mur_txt, f.mat_toit_txt
    FROM relations r
    LEFT JOIN ffo f USING (batiment_groupe_id)
    ORDER BY r.code_departement COLLATE "C", r.parcelle_id, r.batiment_groupe_id
"""

# Relations present in more than one file, and groups whose ffo rows do not
# all carry the same attributes (two files, or twice in one). ``ROW(...)::text``
# keeps NULL fields apart, where ``count(DISTINCT ROW(...))`` would skip an
# all-NULL row.
_DUPLICATES_SQL = """
    SELECT
        (SELECT count(*) FROM (
            SELECT 1 FROM {flat}
            WHERE nature = 'relation'
            GROUP BY parcelle_id, batiment_groupe_id
            HAVING count(DISTINCT code_departement) > 1
        ) d),
        (SELECT count(*) FROM (
            SELECT 1 FROM {flat}
            WHERE nature = 'ffo'
            GROUP BY batiment_groupe_id
            HAVING count(DISTINCT ROW(
                annee_construction, mat_mur_txt, mat_toit_txt)::text) > 1
        ) g)
"""

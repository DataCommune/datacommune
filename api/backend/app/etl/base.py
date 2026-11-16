"""Abstract base class for ETL pipelines.

Every dataset (BAN, Cadastre, DVF, etc.) implements a concrete pipeline
by subclassing BasePipeline and providing the 5 abstract stage methods.
"""

import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import psycopg

from app.common.source_catalog import DATASET_CATALOG, covered_departements
from app.config import settings

if TYPE_CHECKING:
    from app.etl.source_archive import SourceReplay
    from app.scope.model import InstanceScope

logger = logging.getLogger(__name__)


MILLESIME_DATE = (re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"), "%Y-%m-%d", "AAAA-MM-JJ")
MILLESIME_MONTH = (re.compile(r"[0-9]{4}-[0-9]{2}"), "%Y-%m", "AAAA-MM")
MILLESIME_QUARTER = (re.compile(r"[0-9]{4}_T[1-4]"), None, "AAAA_Tn")


def check_millesime_label(
    dataset: str, label: object, form: tuple[re.Pattern[str], str | None, str]
) -> None:
    """Refuse *label* unless it is exactly *form* (and a real calendar date)."""
    from app.etl.errors import MalformedMillesimeError

    pattern, date_format, expected = form
    if not isinstance(label, str) or not pattern.fullmatch(label):
        raise MalformedMillesimeError(dataset, label, expected)
    if date_format is not None:
        try:
            datetime.strptime(label, date_format)
        except ValueError:
            raise MalformedMillesimeError(dataset, label, expected) from None


def split_dsn_credentials(dsn: str) -> tuple[str, dict[str, str]]:
    """Split a PostgreSQL DSN into a credential-free DSN + subprocess env (A-02).

    Returns ``(dsn_without_password, extra_env)`` where extra_env carries
    ``PGPASSWORD`` so CLI tools (ogr2ogr) never receive the password in
    argv — it would be readable in /proc/<pid>/cmdline for the whole run.
    """
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn, {}
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    userinfo = f"{parts.username}@" if parts.username else ""
    safe_dsn = urlunsplit(
        (
            parts.scheme,
            f"{userinfo}{host}{port}",
            parts.path,
            parts.query,
            parts.fragment,
        )
    )
    return safe_dsn, {"PGPASSWORD": parts.password}


# All 102 departement codes -- shared with migration 001 to guarantee
# staging partitions match production partitions exactly.
DEPARTEMENTS: list[str] = [
    *[f"{i:02d}" for i in range(1, 20)],  # 01-19
    "2A",
    "2B",  # Corse
    *[str(i) for i in range(21, 96)],  # 21-95
    *[str(i) for i in range(971, 977)],  # DOM: 971-976
]


def _advisory_lock_id(table_name: str) -> int:
    """Derive a stable int64 advisory lock ID from a table name."""
    digest = hashlib.md5(table_name.encode()).hexdigest()
    return int(digest[:15], 16)  # 60-bit positive integer


@contextmanager
def advisory_lock(dsn: str, table_name: str):
    """Acquire a PostgreSQL session-level advisory lock for an ETL swap.

    Prevents concurrent pipeline runs from swapping the same table
    simultaneously.  The lock is released when the connection closes.
    """
    lock_id = _advisory_lock_id(table_name)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            acquired = cur.execute(
                "SELECT pg_try_advisory_lock(%s)", (lock_id,)
            ).fetchone()[0]
            if not acquired:
                raise RuntimeError(
                    f"Advisory lock for '{table_name}' already held — "
                    "another ETL run may be in progress"
                )
        try:
            yield conn
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))


# -- staging index hygiene (DB-HYGIENE-01) -----------------------------------
#
# ``ALTER TABLE ... RENAME`` does not cascade to indexes, exactly like it does
# not cascade to partitions.  After a swap, every index built on the staging
# table lands on the production table still carrying a "staging" name.  Left
# alone they are re-copied by the next ``LIKE ... INCLUDING INDEXES`` and the
# set grows monotonically (measured in prod: 647 indexes / 5 112 MB).
STAGING_INDEX_MARKER = "staging"

# Suffix appended to a canonical index name to build its staging twin, so the
# mapping staging name -> production name is a pure suffix strip.
STAGING_INDEX_SUFFIX = f"_{STAGING_INDEX_MARKER}"

# PostgreSQL identifier limit (NAMEDATALEN - 1).
MAX_IDENTIFIER_LENGTH = 63

# Suffix used to park an index name still held by the pre-swap production
# table (now ``{prod}_old``) so the canonical name becomes available again.
# ``{prod}_old`` is dropped right after the publication, taking it with it.
_PARKED_INDEX_SUFFIX = "_swapold"

_INDEX_DEF_NAME_RE = re.compile(r"^CREATE (UNIQUE )?INDEX \S+ ON ", re.IGNORECASE)

# Every index carried by the production table *and* its partitions.
_SWAP_INDEX_INVENTORY_SQL = """
    SELECT i.relname AS index_name,
           t.relname AS table_name,
           pg_get_indexdef(x.indexrelid) AS index_def,
           con.conname AS constraint_name,
           (inh_idx.inhparent IS NOT NULL) AS attached_to_parent_index
    FROM pg_index x
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_class t ON t.oid = x.indrelid
    LEFT JOIN pg_constraint con
           ON con.conindid = x.indexrelid
          AND con.contype IN ('p', 'u', 'x')
    LEFT JOIN pg_inherits inh_idx ON inh_idx.inhrelid = x.indexrelid
    WHERE x.indrelid IN (
        SELECT c.oid FROM pg_class c WHERE c.oid = to_regclass(%(table)s)
        UNION
        SELECT inh.inhrelid FROM pg_inherits inh
        WHERE inh.inhparent = to_regclass(%(table)s)
    )
    ORDER BY t.relname, i.relname
"""

# Which table (if any) currently owns the relation named %s.
# No row  -> the name is free.
# NULL    -> the name is taken by something that is not an index.
_SWAP_INDEX_HOLDER_SQL = """
    SELECT t.relname AS table_name
    FROM pg_class rel
    LEFT JOIN pg_index x ON x.indexrelid = rel.oid
    LEFT JOIN pg_class t ON t.oid = x.indrelid
    WHERE rel.oid = to_regclass(%s)
"""

# Relations of a candidate's partition tree (the table itself when it is not
# partitioned) that a crash would empty. ``pg_partition_tree`` returns no row
# for a table outside any partitioning, hence the explicit ``c.oid = ...``.
_UNLOGGED_CANDIDATE_SQL = """
    SELECT c.relname
    FROM pg_class c
    WHERE c.relpersistence <> 'p'
      AND (
        c.oid = to_regclass(%(table)s)
        OR c.oid IN (SELECT relid FROM pg_partition_tree(to_regclass(%(table)s)))
      )
    ORDER BY c.relname
"""

_SWAP_OLD_PARTITIONS_SQL = """
    SELECT child.relname AS table_name
    FROM pg_inherits inh
    JOIN pg_class child ON child.oid = inh.inhrelid
    WHERE inh.inhparent = to_regclass(%s)
"""


def normalize_index_definition(index_def: str) -> str:
    """Strip the index name from ``pg_get_indexdef`` output.

    Two indexes sharing the normalized definition are exact duplicates:
    same table, same method, same columns/expression, same predicate.
    """
    return _INDEX_DEF_NAME_RE.sub(
        lambda m: f"CREATE {m.group(1) or ''}INDEX ON ", index_def
    )


class BasePipeline(ABC):
    """Abstract ETL pipeline with 5 stages.

    Subclasses must define dataset metadata (properties) and implement
    the five stage methods.  Concrete helpers for staging-table
    lifecycle and DSN derivation are provided.
    """

    # -- abstract properties --------------------------------------------------

    @property
    @abstractmethod
    def dataset_name(self) -> str:
        """Human-readable identifier (e.g. 'ban', 'cadastre')."""

    @property
    @abstractmethod
    def expected_columns(self) -> list[str]:
        """Column names the source CSV/file must contain."""

    @property
    @abstractmethod
    def staging_table_name(self) -> str:
        """Name of the staging table (e.g. 'adresses_staging')."""

    @property
    @abstractmethod
    def production_table_name(self) -> str:
        """Name of the production table (e.g. 'adresses')."""

    @property
    def flat_staging_table_name(self) -> str | None:
        """Name of the flat staging table (e.g. '_ban_staging_flat').

        Override in subclasses that use a flat staging table.
        Returns None if the pipeline does not use flat staging.
        """
        return None

    @property
    def expected_file_count(self) -> int:
        """Expected number of files from download().

        Override in per-department pipelines.  Return 0 to skip
        threshold check (single-file pipelines like DPE, Loyers).
        """
        return 0

    @property
    def has_department_partitions(self) -> bool:
        """Whether the runner should enforce broad department coverage.

        This is a data-quality rule, not a description of the staging table
        topology. Use ``staging_is_partitioned`` for physical table creation.
        """
        return True

    @property
    def staging_is_partitioned(self) -> bool:
        """Whether the production-shaped staging table is partitioned."""
        return True

    @property
    def required_staging_constraints(self) -> tuple[str, ...]:
        """Constraints that must exist even if production already drifted.

        Definitions use PostgreSQL ``ALTER TABLE ... ADD`` syntax, for example
        ``PRIMARY KEY (id)`` or ``UNIQUE (natural_key)``.
        """
        return ()

    @property
    def source_period_label(self) -> str | None:
        """Upstream source period/millesime label, when authentically known.

        Optional contract (PROV-03): override only when the pipeline can read a
        real upstream period from the source. Default: the vintage pinned by
        ``pin_millesime`` (the dated URL actually downloaded), else None so the
        assembly layer yields an explicit ``unknown`` rather than a fabricated
        period. Never derive from an internal import timestamp.
        """
        return self._pinned_millesime

    # -- old-vintage targeting (plan 02-11, D-27) ------------------------------

    #: How the producer serves another vintage than the current one, from a
    #: closed list: ``segment_date`` (dated segment of the URL), ``url_archive``
    #: (the old vintage lives at another address), ``parametre_version`` (query
    #: parameter or version header). None: no vintage can be targeted.
    millesime_mechanism: str | None = None
    _pinned_millesime: str | None = None

    @property
    def supports_millesime_pin(self) -> bool:
        return self.millesime_mechanism is not None

    @property
    def pinned_millesime(self) -> str | None:
        """Vintage label targeted by ``pin_millesime``, None for the current one."""
        return self._pinned_millesime

    def validate_millesime(self, label: str) -> None:
        """Refuse a label outside the producer's format (MalformedMillesimeError).

        Pipelines declaring ``millesime_mechanism`` override this; the label is
        interpolated in a URL, so only the producer's exact format passes.
        """
        from app.etl.errors import MillesimeNotTargetableError

        raise MillesimeNotTargetableError(self.dataset_name)

    def pin_millesime(self, label: str) -> None:
        """Target vintage *label* instead of the producer's current one.

        Refused (MillesimeNotTargetableError) when the pipeline declares no
        mechanism, and (MalformedMillesimeError) when the label is malformed,
        both before any download. ``source_period_label`` then names *label*.
        """
        from app.etl.errors import MillesimeNotTargetableError

        if not self.supports_millesime_pin:
            raise MillesimeNotTargetableError(self.dataset_name)
        self.validate_millesime(label)
        self._pinned_millesime = label

    @property
    def source_updated_at(self) -> datetime | None:
        """Upstream publication date, when the source actually provides it.

        Optional contract (PROV-03): override only when the source exposes a
        genuine publication/update date. Default None; never substitute the
        current time or ``last_updated_at`` (that would be a false millesime).
        """
        return None

    @property
    def supports_department_scope(self) -> bool:
        """Whether this pipeline downloads one file set per configured departement.

        Per-department download pipelines opt in; national files are filtered
        to the scope through ``scope_filter_sql`` instead.
        """
        return False

    # -- instance scope (D-21, D-22) ------------------------------------------

    _bound_scope: "InstanceScope | None" = None

    @property
    def scope_dataset_keys(self) -> tuple[str, ...]:
        """Catalogue keys that must all be hosted before this pipeline runs."""
        return (self.dataset_name,)

    def bind_scope(self, scope: "InstanceScope") -> None:
        """Attach the scope the runner read once at start of this import."""
        self._bound_scope = scope
        self._scope_file_rows = None

    # Rows of each hosted departement in the national file of this import,
    # read by the scope filter (261020-fpm); None until it has run.
    _scope_file_rows: dict[str, int] | None = None

    def record_scope_file_rows(self, rows: dict[str, int]) -> None:
        """Keep the rows the scope filter read per hosted departement."""
        self._scope_file_rows = dict(rows)

    @property
    def national_file_min_rows(self) -> int | None:
        """Rows the whole national file must hold before the scope filter."""
        return self.national_min_rows

    @property
    def scope_file_rows(self) -> dict[str, int] | None:
        return None if self._scope_file_rows is None else dict(self._scope_file_rows)

    @property
    def scope(self) -> "InstanceScope":
        if self._bound_scope is None:
            raise RuntimeError(
                f"No instance scope bound to pipeline {self.dataset_name}"
            )
        return self._bound_scope

    @property
    def scope_filter_sql(self) -> dict[str, str] | None:
        """Staging tables filtered on the scope departements after stage().

        Maps a staging table name to a SQL expression yielding its departement
        code. Both are code constants, never user input. None: no filtering.
        """
        return None

    @property
    def coverage_departement_sql(self) -> dict[str, str] | None:
        """Departement expression of the coverage check, without filtering.

        For a departmental download whose checked staging table has no
        ``code_departement`` column: maps that table to a SQL expression
        yielding its departement code (code constants). Read by the coverage
        check after ``scope_filter_sql``; None keeps ``code_departement``.
        """
        return None

    @property
    def _scope_filtered(self) -> bool:
        return self.scope_filter_sql is not None and self._bound_scope is not None

    @property
    def department_scope(self) -> list[str]:
        """Departements of this import: the instance scope configured in base.

        Read from the scope the runner bound at start (D-22, D-23); a pipeline
        with no bound scope raises instead of falling back to a default list.
        """
        return list(self.scope.departements)

    @property
    def departements_outside_source(self) -> frozenset[str]:
        """Departements none of the pipeline's sources publishes.

        Derived from the catalogue (``source_coverage``), the only territorial
        authority per source; never downloaded nor expected in staging. A key
        outside the catalogue (test doubles) declares no absence.
        """
        keys = [key for key in self.scope_dataset_keys if key in DATASET_CATALOG]
        if not keys:
            return frozenset()
        return frozenset(DEPARTEMENTS) - set(covered_departements(keys, DEPARTEMENTS))

    @property
    def _has_department_dimension(self) -> bool:
        """Departmental download or national file filtered to the scope."""
        return self.supports_department_scope or self.scope_filter_sql is not None

    @property
    def partition_department_scope(self) -> list[str]:
        """Partitions to create/rename for staging.

        The configured departements on a restricted scope; all 102 canonical
        partitions on the full scope.
        """
        if self.is_department_scope_limited:
            return self.department_scope
        return list(DEPARTEMENTS)

    @property
    def is_department_scope_limited(self) -> bool:
        """True when this pipeline follows the scope's departements and that
        scope does not cover the 102 departements.

        Only per-departement downloads and national files filtered to the scope
        follow the departements; a pipeline without a department dimension is
        never limited, whatever the scope.
        """
        if self.supports_department_scope or self._scope_filtered:
            return not self.scope.covers_all_departements
        return False

    @property
    def expected_department_coverage(self) -> list[str]:
        """Configured departements the source covers: all must reach staging."""
        outside = self.departements_outside_source
        return [dept for dept in self.department_scope if dept not in outside]

    def expected_coverage_for_table(self, table: str) -> list[str]:
        """Expected departements of one staging table checked by the guard.

        Default: the pipeline's (union of its sources). A pipeline whose tables
        come from different sources narrows each table to its own source.
        """
        return self.expected_department_coverage

    @property
    def checkpoint_scope_key(self) -> str:
        """Stable key used to invalidate stale resume checkpoints."""
        if not self.is_department_scope_limited:
            return ""
        return ",".join(sorted(set(self.department_scope)))

    def checkpoint_identity(self, scope_key: str) -> str:
        """Compose every resume dimension without changing specialized source keys.

        DPE reads its existing scope_key to restore its validated generation;
        this separate envelope also binds frozen national sources to territory.
        """
        return json.dumps(
            {
                "version": 2,
                "dataset": self.dataset_name,
                "source_key": scope_key,
                "millesime": self.pinned_millesime,
                "departements": sorted(set(self.department_scope))
                if self._has_department_dimension
                else [],
                "staging": self.staging_table_name,
                "flat_staging": self.flat_staging_table_name,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def checkpoint_file_id(self, file_path: Path) -> str:
        """Logical processing unit; the runner refuses duplicate identifiers.

        Basenames preserve the existing DPE manifest/generation contract.
        A pipeline with repeated basenames must supply its source-relative ID.
        """
        return file_path.name

    # Full-France row floor of a national file filtered to the scope (D-22).
    national_min_rows: int | None = None

    # Reimport from the source archive (run_etl --from-archive, plan 02-14):
    # each enabled pipeline proves that restore_source_inputs prepares the
    # same source state as download before resume admission and processing.
    archive_replay: bool = False

    def archive_source_inputs(self, files: list[Path]) -> tuple[Path, list[Path]]:
        """Select the archive root and complete original inputs."""
        from app.etl.download import get_download_dir

        return get_download_dir(self.dataset_name), files

    def restore_source_inputs(
        self, replay: "SourceReplay", directory: Path
    ) -> list[Path]:
        """Restore archived inputs and return only processing units."""
        return replay.materialize(directory)

    def scoped_min_rows(self, full_france_min: int) -> int:
        """Scale a full-France row floor for a local department subset.

        A dataset with producer counts (``app.scope.producer_counts``) sums
        80 % of the count of each departement it imports, whatever the scope.
        A national file filtered to the instance scope (``scope_filter_sql``)
        sums the per-departement floors of ``app.scope.floors``; other
        pipelines keep the linear share.
        """
        # app.scope imports this module: resolve at call time.
        from app.scope.floors import national_floor, producer_floor
        from app.scope.model import scale_floor
        from app.scope.producer_counts import PRODUCER_COUNTS

        # Producer counts first (plan 04.1-17): per departement, every scope.
        if self.dataset_name in PRODUCER_COUNTS:
            return producer_floor(
                self.dataset_name,
                self.expected_department_coverage,
                "lignes_importees",
            )
        if not self.is_department_scope_limited:
            return full_france_min
        coverage = self.expected_department_coverage
        if self._scope_filtered and self._scope_file_rows is not None:
            return sum(self.departement_min_rows(code) for code in coverage)
        if self._scope_filtered:
            return national_floor(self.dataset_name, coverage, full_france_min)
        return scale_floor(full_france_min, len(coverage))

    def check_scope_prerequisites(self, dsn: str) -> None:
        """Refuse a restricted scope the filter cannot apply (before download).

        Raise ``ScopePrerequisiteError``; default: nothing to check.
        """
        return None

    def departement_min_rows(self, departement: str) -> int:
        """Rows one hosted departement needs in the primary staging table.

        Checked departement by departement before swap, so the truncation of
        one departement is refused even when another one fills the total.
        A reference count (legacy prod base) keeps its 80 % floor. Without one, once the
        scope filter has read the departement in the import's own national
        file, its presence is the check: the mean national share refused
        Paris (04.1-21), and the file cannot be its own floor (261020-fpm).
        """
        from app.scope.floors import departement_floor, has_reference_count

        if not self._scope_filtered or self.national_min_rows is None:
            return 1
        if self._scope_file_rows is not None and not has_reference_count(
            self.dataset_name, departement
        ):
            return 1
        return departement_floor(self.dataset_name, departement, self.national_min_rows)

    @property
    def production_departement_sql(self) -> str:
        """SQL expression giving the departement of a published row.

        Code constant, never user input; used to compare at equal scope.
        """
        return "code_departement"

    def count_production_rows(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
        table_name: str,
        departement_sql: str | None = None,
    ) -> int:
        """Count production rows, restricted to the active departement scope."""
        if self.is_department_scope_limited:
            expression = departement_sql or self.production_departement_sql
            row = cur.execute(
                f"SELECT count(*) FROM {table_name} WHERE {expression} = ANY(%s)",
                (self.department_scope,),
            ).fetchone()
        else:
            row = cur.execute(f"SELECT count(*) FROM {table_name}").fetchone()
        return row[0] if row else 0

    @property
    def min_success_ratio(self) -> float:
        """Minimum ratio of successful downloads before swap is allowed.

        Default 0.9 (90%) for per-department pipelines.
        Override per-pipeline if needed.  Return 1.0 for single-file
        pipelines where partial success makes no sense.
        """
        from app.config import settings  # lazy import to avoid circular ref

        return settings.etl_min_success_ratio

    @property
    def min_staging_rows(self) -> int:
        """Minimum rows required in staging before swap is allowed.

        Default (GATE-B'): a low global fallback floor
        (settings.etl_min_staging_rows, scaled to the active department
        scope) so a quasi-empty staging can never swap even on a new or
        unknown pipeline with no baseline yet.  Override in each concrete
        pipeline with ~80% of the last known production count.

        Return 0 to disable the runner-level check explicitly.  Flat-staging
        pipelines (DPE, BAN, Monuments) must return 0 so the runner-level
        check is skipped; those pipelines enforce the floor inside their
        swap() via min_staging_rows_final instead.
        """
        return self.scoped_min_rows(settings.etl_min_staging_rows)

    @property
    def min_staging_rows_final(self) -> int:
        """Minimum rows required in the final (partitioned) staging table.

        Used only by flat-staging pipelines (DPE, BAN, Monuments) that
        perform the INSERT SELECT inside swap().  The runner-level check
        (min_staging_rows) is skipped for those pipelines; they call this
        property after the INSERT SELECT and raise RuntimeError if below.

        Default: delegates to min_staging_rows (works for non-flat pipelines
        that never call this property directly).
        """
        return self.min_staging_rows

    @property
    def schema_drift_mode(self) -> str:
        """How to handle schema drift between staging and production.

        'warn'  — log a WARNING and emit an alert; swap proceeds.
        'abort' — block the swap and return EtlResult(success=False).

        Default is 'warn' for all pipelines.  Override to 'abort' for
        datasets where a column mismatch would break the production API
        (e.g. DVF, DPE whose column names are used in API queries).
        """
        return "warn"

    @property
    def skip_runner_schema_drift_check(self) -> bool:
        """Whether to skip the runner-level schema drift check.

        Set to True on flat-staging pipelines (DPE, BAN, Monuments) where
        the prod-shaped staging table is only populated inside swap(), so
        the runner cannot check it before calling pipeline.swap().  Those
        pipelines call PipelineRunner._check_schema_drift_conn() directly
        inside their swap() after the INSERT SELECT.
        """
        return False

    # -- abstract stage methods -----------------------------------------------

    @abstractmethod
    def download(self) -> list[Path]:
        """Download source files and return local paths."""

    @abstractmethod
    def validate_schema(self, file_path: Path) -> None:
        """Raise ValueError if *file_path* does not match expected_columns."""

    @abstractmethod
    def transform(self, file_path: Path) -> Path:
        """Clean / reshape *file_path* and return the transformed path."""

    @abstractmethod
    def stage(self, file_path: Path) -> int:
        """Bulk-load *file_path* into the staging table. Return row count."""

    @abstractmethod
    def swap(self) -> None:
        """Atomically replace production data with staging data."""

    # -- optional validation ----------------------------------------------------

    def validate_data(self, dsn: str) -> list[str]:
        """Run data quality checks on the staging table.

        Override in subclasses to add dataset-specific checks.
        Return a list of warning strings (logged but do not block swap).
        Raise RuntimeError to block the swap explicitly.
        """
        return []

    def stage_counters(self) -> dict[str, int]:
        """Named counts journaled as steps of the run (``count:<name>``).

        Called once all files are staged, before the publication gates.
        """
        return {}

    def swap_counters(self) -> dict[str, int]:
        """Named counts computed by ``swap()`` on the candidate (``count:<name>``).

        Read once the swap stage succeeded, journaled like :meth:`stage_counters`.
        """
        return {}

    # -- concrete helpers -----------------------------------------------------

    @staticmethod
    def get_sync_dsn() -> str:
        """Derive a sync psycopg DSN from the async SQLAlchemy URL.

        The application uses ``postgresql+asyncpg://`` for FastAPI, but ETL
        batch operations use synchronous ``psycopg`` (no event-loop overhead).
        """
        return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @staticmethod
    def set_statement_timeout(
        cur: psycopg.Cursor,  # type: ignore[type-arg]
        seconds: int | None = None,
    ) -> None:
        """Set statement_timeout on the current cursor/connection."""
        timeout = settings.etl_statement_timeout_s if seconds is None else seconds
        cur.execute(f"SET statement_timeout = '{timeout}s'")

    @property
    def published_old_tables(self) -> tuple[str, ...]:
        """Previous generations this pipeline's swap renames to ``*_old``.

        The runner drops them right after a successful publication, in the
        same transaction as the repair of the dependent materialized views
        (plan 04.1-04). A rollback replays the archived sources instead.
        """
        return (f"{self.production_table_name}_old",)

    def create_staging_table(self, dsn: str) -> None:
        """Create a production-shaped staging table.

        Secondary indexes are excluded for load performance. PK/UNIQUE
        constraints are restored immediately before the atomic swap.
        """
        staging = self.staging_table_name
        production = self.production_table_name

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Clean slate — drop both staging and flat staging
                cur.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
                if self.flat_staging_table_name:
                    cur.execute(
                        f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                    )
                # Parent: copy DDL + constraints from production.
                # Logged from creation, like its partitions (plan 04.1-04): the
                # swap publishes these very storage files and production is
                # always logged (crash-safe). Converting an unlogged candidate
                # at the swap rewrote the whole table, doubling the disk peak;
                # swap_tables() now refuses an unlogged candidate instead.
                # EXCLUDING INDEXES: indexes are created AFTER data load
                # to avoid slow inserts on indexed tables.
                partition_clause = (
                    " PARTITION BY LIST (code_departement)"
                    if self.staging_is_partitioned
                    else ""
                )
                cur.execute(
                    f"CREATE TABLE {staging} "
                    f"(LIKE {production} INCLUDING ALL EXCLUDING INDEXES)"
                    f"{partition_clause}"
                )

                # BIGSERIAL fix: give staging its own id sequence BEFORE
                # creating partitions so they inherit the correct DEFAULT.
                # LIKE copies IDENTITY or DEFAULT from production — drop
                # IDENTITY first (can't SET DEFAULT on identity columns),
                # then attach our own sequence.
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

                if self.staging_is_partitioned:
                    # Full-France remains the default; local scoped runs create
                    # only requested partitions plus DEFAULT.
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

    def ensure_staging_constraints(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
        production: str | None = None,
        staging: str | None = None,
    ) -> None:
        """Restore PK/UNIQUE constraints after the staging load.

        ``LIKE ... EXCLUDING INDEXES`` intentionally omits indexes and therefore
        also omits PK/UNIQUE constraints. Copy their definitions from production,
        then add any pipeline-declared invariant needed to recover from an
        already-drifted production table.

        The table pair defaults to this pipeline's own. Pipelines publishing a
        second table (transport/gares) pass it explicitly; pipeline-declared
        invariants stay scoped to the default staging table.
        """
        default_pair = staging is None
        production = production or self.production_table_name
        staging = staging or self.staging_table_name

        source_rows = cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = %s::regclass
              AND contype IN ('p', 'u')
            ORDER BY contype, conname
            """,
            (production,),
        ).fetchall()
        existing_rows = cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = %s::regclass
              AND contype IN ('p', 'u')
            """,
            (staging,),
        ).fetchall()

        required = {row[0] for row in source_rows}
        if default_pair:
            required.update(self.required_staging_constraints)
        existing = {row[0] for row in existing_rows}

        for definition in sorted(required - existing):
            cur.execute(f"ALTER TABLE {staging} ADD {definition}")
            logger.info("Restored staging constraint on %s: %s", staging, definition)

    def prepare_staging_constraints(self, dsn: str) -> None:
        """Build staging PK/UNIQUE constraints before the atomic swap."""
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                self.ensure_staging_constraints(cur)
            conn.commit()

    def drop_staging_table(self, dsn: str) -> None:
        """Drop the staging table and all its partitions."""
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.staging_table_name} CASCADE")
                if self.flat_staging_table_name:
                    cur.execute(
                        f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                    )
            conn.commit()

    def drop_flat_staging(self, dsn: str) -> None:
        """Drop only the flat staging table (cleanup after swap)."""
        if not self.flat_staging_table_name:
            return
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DROP TABLE IF EXISTS {self.flat_staging_table_name} CASCADE"
                )
            conn.commit()
        logger.info("Dropped flat staging table %s", self.flat_staging_table_name)

    def register_old_table(self, dsn: str, old_table_name: str) -> None:
        """Record that an _old table exists (trace until its drop).

        The runner drops ``published_old_tables`` and their rows right after
        the publication; a row left here (drop refused, run interrupted) is
        purged by ``cleanup_old_tables`` or ``purge-old-tables``.
        """
        try:
            with psycopg.connect(dsn) as conn:
                conn.execute(
                    "INSERT INTO etl_old_tables (table_name, dataset_name) "
                    "VALUES (%s, %s) "
                    "ON CONFLICT (table_name) DO UPDATE SET created_at = NOW()",
                    (old_table_name, self.dataset_name),
                )
                conn.commit()
            logger.info("Registered old table %s for deferred cleanup", old_table_name)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to register old table %s (table may not exist yet)",
                old_table_name,
                exc_info=True,
            )

    def drop_previous_old_table(self, dsn: str, old_table_name: str) -> None:
        """Drop a previous _old table if it exists (before creating a new one)."""
        try:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {old_table_name} CASCADE")
                conn.execute(
                    "DELETE FROM etl_old_tables WHERE table_name = %s",
                    (old_table_name,),
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to drop previous old table %s (may not exist)",
                old_table_name,
                exc_info=True,
            )

    @staticmethod
    def assert_staging_logged(
        cur: psycopg.Cursor,  # type: ignore[type-arg]
        table: str,
    ) -> None:
        """Refuse to publish *table* if it or one of its partitions is unlogged.

        Raises ``StagingNotLoggedError`` before any rename: production must
        survive a crash, and the swap no longer rewrites the candidate.
        """
        from app.etl.errors import StagingNotLoggedError

        unlogged = [
            row[0]
            for row in cur.execute(_UNLOGGED_CANDIDATE_SQL, {"table": table}).fetchall()
        ]
        if unlogged:
            raise StagingNotLoggedError(table, unlogged)

    def swap_tables(self, dsn: str) -> None:
        """Atomic two-RENAME swap in a minimal transaction (SWAP-01).

        Guarantees:
          - Publishes the candidate's own storage files: it was created logged
            and is never converted here (plan 04.1-04); an unlogged candidate
            or partition raises ``StagingNotLoggedError`` before any rename.
          - Drops any stale {prod}_old table first (prevents RENAME collision
            on retry — fixes the foncier_pm top-level _old bug from Phase 9).
          - The two RENAMEs run in a single transaction: either both commit
            atomically or neither does (SIGKILL-safe).
          - Strips staging names from the swapped-in indexes in that same
            transaction (DB-HYGIENE-01), so production never carries
            ``*_staging_*`` index names and the next ``LIKE`` has nothing
            to re-copy.

        Must be called AFTER the long INSERT SELECT + index builds have
        committed in a separate transaction (Phase A). This helper is
        Phase B of the refactored swap protocol.
        """
        prod = self.production_table_name
        staging = self.staging_table_name
        old = f"{prod}_old"

        # Building a PK/UNIQUE index can be expensive on large datasets. Keep
        # that work outside the short transaction that renames production.
        self.prepare_staging_constraints(dsn)

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                self.set_statement_timeout(cur)
                # Production must be crash-safe; no rewrite to make it so.
                self.assert_staging_logged(cur, staging)
                # Pre-swap guard: drop stale _old (prevents collision on retry).
                # CASCADE drops any orphaned partitions still attached.
                cur.execute(f"DROP TABLE IF EXISTS {old} CASCADE")
                # Atomic rename pair — single transaction.
                cur.execute(f"ALTER TABLE {prod} RENAME TO {old}")
                cur.execute(f"ALTER TABLE {staging} RENAME TO {prod}")
                # RENAME does not cascade to indexes: strip the staging names
                # inside the same transaction so production is never published
                # carrying them (DB-HYGIENE-01). Catalog-only work on relations
                # this transaction already holds ACCESS EXCLUSIVE on.
                self.rename_indexes_after_swap(cur)
            conn.commit()
        logger.info(
            "Atomic swap committed: %s -> %s, %s -> %s",
            prod,
            old,
            staging,
            prod,
        )

    def rename_partitions_after_swap(
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
    ) -> None:
        """Rename staging-named partitions to production names after swap.

        PostgreSQL's ALTER TABLE RENAME does not cascade to partitions,
        so after renaming staging -> production the partitions still carry
        staging names (e.g. ``foo_staging_01``).  This causes conflicts on
        the next ETL run.  Rename them to ``production_XX`` so the staging
        namespace is free for re-use.

        Old production partitions (e.g. ``foo_01`` still attached to
        ``foo_old`` after the parent rename) are dropped first so their
        names are free for the staging rename.  They are redundant because
        the entire ``foo_old`` parent table is dropped after the publication
        (DROP TABLE foo_old CASCADE would drop them anyway).
        """
        staging = self.staging_table_name
        production = self.production_table_name

        for dept in self.partition_department_scope:
            safe = dept.lower()
            # Step 1: drop old production partition (still attached to foo_old,
            # freed immediately since the parent is dropped after publication).
            cur.execute(f"DROP TABLE IF EXISTS {production}_{safe}")
            # Step 2: rename staging partition to production name
            old_name = f"{staging}_{safe}"
            new_name = f"{production}_{safe}"
            cur.execute(f"ALTER TABLE IF EXISTS {old_name} RENAME TO {new_name}")
        # Default partition: drop old then rename staging
        cur.execute(f"DROP TABLE IF EXISTS {production}_default")
        cur.execute(
            f"ALTER TABLE IF EXISTS {staging}_default RENAME TO {production}_default"
        )

    # -- staging index hygiene (DB-HYGIENE-01) --------------------------------

    @staticmethod
    def staging_index_name(canonical_name: str) -> str:
        """Return the staging twin of a production index name.

        The staging name is the production name plus a ``_staging`` suffix, so
        ``rename_indexes_after_swap`` recovers the production name by a pure
        suffix strip.  Never hand-write staging index names: derive them here.
        """
        name = f"{canonical_name}{STAGING_INDEX_SUFFIX}"
        if len(name) > MAX_IDENTIFIER_LENGTH:
            raise ValueError(
                f"Staging index name {name!r} exceeds "
                f"{MAX_IDENTIFIER_LENGTH} chars — shorten {canonical_name!r}"
            )
        return name

    def canonical_index_name(self, index_name: str) -> str:
        """Map a staging-tainted index name back to its production name.

        Handles, in order:
          1. the canonical contract ``idx_foo_bar_staging`` -> ``idx_foo_bar``;
          2. constraint indexes auto-named by PostgreSQL,
             ``foo_staging_pkey`` -> ``foo_pkey``;
          3. legacy hand-written names, ``idx_foo_staging_bar``
             -> ``idx_foo_bar``.
        """
        staging = self.staging_table_name
        production = self.production_table_name
        if index_name.endswith(STAGING_INDEX_SUFFIX):
            return index_name[: -len(STAGING_INDEX_SUFFIX)]
        if staging in index_name:
            return index_name.replace(staging, production, 1)
        if f"_{STAGING_INDEX_MARKER}_" in index_name:
            return index_name.replace(f"_{STAGING_INDEX_MARKER}_", "_", 1)
        return index_name.replace(STAGING_INDEX_MARKER, "").replace("__", "_")

    def rename_indexes_after_swap(  # noqa: C901
        self,
        cur: psycopg.Cursor,  # type: ignore[type-arg]
        production: str | None = None,
    ) -> None:
        """Strip staging names from the freshly swapped production table.

        ``production`` defaults to this pipeline's production table. Pipelines
        publishing a second table outside ``swap_tables`` (transport/gares) pass
        it explicitly so that table gets the same cleanup (DB-HYGIENE-01).

        ``ALTER TABLE ... RENAME`` does not cascade to indexes (same PostgreSQL
        limitation ``rename_partitions_after_swap`` works around for
        partitions), so after the swap every index built on staging sits on the
        production table with a "staging" name.  Left alone, the next
        ``CREATE TABLE ... (LIKE prod INCLUDING INDEXES)`` copies them under
        fresh auto-generated names and the set grows on every single run.

        Two passes:
          1. drop exact duplicates (identical normalized ``pg_get_indexdef``),
             keeping the constraint-backed index first, then a clean name, then
             a staging name — neither a constraint index nor a partition child
             index is ever dropped (PostgreSQL refuses both: something
             "requires it"); duplicate partition children go away on their own
             when the duplicate parent index is dropped;
          2. rename every surviving staging-named index to its canonical name,
             parking any homonym still attached to ``{prod}_old`` (that table is
             dropped after the publication and takes the parked index with
             it).

        Idempotent: a second call on an already-clean table emits nothing.
        """
        production = production or self.production_table_name
        old = f"{production}_old"

        rows = cur.execute(_SWAP_INDEX_INVENTORY_SQL, {"table": production}).fetchall()

        def keep_priority(row: tuple) -> int:  # type: ignore[type-arg]
            index_name, _table, _definition, constraint_name, attached = row
            if constraint_name is not None or attached:
                # A constraint or a partitioned parent index requires it:
                # PostgreSQL refuses the DROP ("... requires it").
                return 0
            if STAGING_INDEX_MARKER not in index_name:
                return 1  # already canonical — it owns its shape
            return 2

        winners: dict[tuple[str, str], str] = {}
        tainted: list[str] = []
        for index_name, table_name, definition, constraint_name, attached in sorted(
            rows, key=keep_priority
        ):
            shape = (table_name, normalize_index_definition(definition))
            winner = winners.get(shape)
            if winner is None:
                winners[shape] = index_name
            elif constraint_name is None and not attached:
                logger.info(
                    "Dropping duplicate index %s on %s (same shape as %s)",
                    index_name,
                    table_name,
                    winner,
                )
                cur.execute(f"DROP INDEX IF EXISTS {index_name}")
                continue
            if STAGING_INDEX_MARKER in index_name:
                tainted.append(index_name)

        if not tainted:
            return

        parkable = {old}
        parkable.update(
            row[0] for row in cur.execute(_SWAP_OLD_PARTITIONS_SQL, (old,)).fetchall()
        )

        for index_name in sorted(tainted):
            target = self.canonical_index_name(index_name)
            if not target or target == index_name:
                continue
            holder = cur.execute(_SWAP_INDEX_HOLDER_SQL, (target,)).fetchone()
            if holder is not None:
                holder_table = holder[0]
                if holder_table not in parkable:
                    logger.warning(
                        "Cannot reclaim index name %s for %s: still held by %s",
                        target,
                        index_name,
                        holder_table or "a non-index relation",
                    )
                    continue
                keep = MAX_IDENTIFIER_LENGTH - len(_PARKED_INDEX_SUFFIX)
                parked = f"{target[:keep]}{_PARKED_INDEX_SUFFIX}"
                cur.execute(f"ALTER INDEX IF EXISTS {target} RENAME TO {parked}")
            cur.execute(f"ALTER INDEX IF EXISTS {index_name} RENAME TO {target}")
            logger.info("Renamed index %s -> %s", index_name, target)

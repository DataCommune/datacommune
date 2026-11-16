"""Durable publication journal; committed swaps cannot be rolled back here."""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from app.commune.dependencies import COMMUNE_SOURCES, PUBLICATION_LOCK_ID
from app.scheduler.locks import release_lock, try_acquire_lock

log = logging.getLogger(__name__)


def requires_fresh_start(dsn: str, dataset: str) -> bool:
    """An incomplete publication cannot reuse a pre-publication checkpoint."""
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT status FROM etl_publications WHERE dataset_name=%s "
            "ORDER BY id DESC LIMIT 1",
            (dataset,),
        ).fetchone()
    return row is not None and row[0] != "published"


@contextmanager
def dataset_execution_lock(dsn: str, dataset: str):
    acquired, connection = try_acquire_lock(dsn, dataset)
    if not acquired:
        raise RuntimeError("Advisory lock already held for dataset execution")
    try:
        yield
    finally:
        release_lock(connection)


@contextmanager
def maintenance_connection(dsn: str):
    """Exclude publishers/readers through DDL and failure metadata commits.

    Admission happens before yielding, so a rejected maintenance operation
    cannot mark an active publisher's metadata failed. The session lock survives
    transaction rollback and is released only when this connection closes.
    Call only outside Publication: required DDL uses its owner's existing
    connection and lock. Dataset owners acquire this lock last.
    """
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute("SET lock_timeout = '250ms'")
        conn.execute("SELECT pg_advisory_lock(%s)", (PUBLICATION_LOCK_ID,))
        conn.execute("SET lock_timeout = '0'")
        yield conn


@dataclass(frozen=True)
class ObservedCandidate:
    publication_id: int
    dataset_name: str
    table_name: str
    generation: UUID
    relation_oids: tuple[tuple[str, int], ...]
    row_count: int


class PublicationWork:
    """In-process capability, valid only while its issuing owner is active."""

    def __init__(self, publication):
        self._publication = publication
        self._connection = publication.connection
        self._observed = ObservedCandidate(
            publication.id,
            publication.pipeline.dataset_name,
            publication.pipeline.production_table_name,
            publication.generation,
            tuple(sorted(publication.relation_oids.items())),
            publication.row_count,
        )

    @property
    def connection(self):
        return self._connection

    def assert_current(self, conn) -> ObservedCandidate:
        return self._assert_state(conn, "pending")

    def _assert_state(self, conn, status) -> ObservedCandidate:
        owner = self._publication
        if (
            owner._active_work is not self
            or conn is not self._connection
            or conn is not owner.connection
            or conn.closed
            or conn.autocommit
            or conn.info.transaction_status != TransactionStatus.INTRANS
        ):
            raise RuntimeError("Inactive publication work or wrong owner connection")
        owner._assert_owner()
        observed = self._observed
        with conn.cursor(row_factory=tuple_row) as cursor:
            row = cursor.execute(
                "SELECT id,generation,table_name,status,relation_oids,row_count "
                "FROM etl_publications WHERE dataset_name=%s ORDER BY id DESC LIMIT 1",
                (observed.dataset_name,),
            ).fetchone()
            expected = (
                observed.publication_id,
                observed.generation,
                observed.table_name,
                status,
                dict(observed.relation_oids),
                observed.row_count,
            )
            if row != expected:
                raise RuntimeError("Publication candidate identity changed")
            for name, oid in observed.relation_oids:
                if cursor.execute(
                    "SELECT to_regclass(%s)::oid", (name,)
                ).fetchone() != (oid,):
                    raise RuntimeError("Publication candidate source OID changed")
        return observed


class Publication:
    """Hold exclusion until views, validation and success metadata are committed."""

    def __init__(self, dsn: str, pipeline, pre_pending_check=None):
        self.dsn = dsn
        self.pipeline = pipeline
        self.generation = uuid4()
        self.connection = None
        self.id = None
        self.row_count = None
        self.relation_oids = {}
        self._owner_identity = None
        self._active_work = None
        self._pre_pending_check = pre_pending_check

    def __enter__(self):
        self.connection = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
        try:
            # Queue the writer so new shared readers cannot indefinitely overtake
            # it. PostgreSQL's typed 55P03 timeout is an OperationalError, already
            # retried by COR19; admission failure precedes any durable pending row.
            self.connection.execute("SET lock_timeout = '250ms'")
            self.connection.execute(
                "SELECT pg_advisory_lock(%s)", (PUBLICATION_LOCK_ID,)
            )
            self.connection.execute("SET lock_timeout = '0'")
            self._owner_identity = self.connection.execute(
                "SELECT pid,backend_start,datid FROM pg_stat_activity "
                "WHERE pid=pg_backend_pid()"
            ).fetchone()
            if self._pre_pending_check is not None:
                self._pre_pending_check(self.connection)
            self.id = self.connection.execute(
                "INSERT INTO etl_publications(generation,dataset_name,table_name,st"
                "atus) "
                "VALUES(%s,%s,%s,'pending') RETURNING id",
                (
                    self.generation,
                    self.pipeline.dataset_name,
                    self.pipeline.production_table_name,
                ),
            ).fetchone()[0]
            return self
        except BaseException:
            self.connection.close()
            raise

    def observe_stock(self) -> int:
        """Measure the whole published stock, including rows kept by scoped imports."""
        table = self.pipeline.production_table_name
        tables = {table, COMMUNE_SOURCES.get(self.pipeline.dataset_name, table)}
        self.relation_oids = {}
        for name in sorted(tables):
            oid = self.connection.execute(
                "SELECT to_regclass(%s)::oid", (name,)
            ).fetchone()[0]
            if oid is None:
                raise RuntimeError("Published relation missing")
            self.relation_oids[name] = int(oid)
        self.row_count = int(
            self.connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
        )
        self.connection.execute(
            "UPDATE etl_publications SET relation_oids=%s,row_count=%s WHERE id=%s",
            (Jsonb(self.relation_oids), self.row_count, self.id),
        )
        return self.row_count

    def _assert_owner(self) -> None:
        if (
            self.connection is None
            or self.connection.closed
            or self._owner_identity is None
        ):
            raise RuntimeError("Publication owner connection unavailable")
        with self.connection.cursor(row_factory=tuple_row) as cursor:
            identity = cursor.execute(
                "SELECT pid,backend_start,datid FROM pg_stat_activity "
                "WHERE pid=pg_backend_pid()"
            ).fetchone()
            held = cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                "AND pid=pg_backend_pid() AND mode='ExclusiveLock' AND granted "
                "AND database=(SELECT oid FROM pg_database "
                "WHERE datname=current_database()) "
                "AND classid=%s::oid AND objid=%s::oid AND objsubid=1)",
                (PUBLICATION_LOCK_ID >> 32, PUBLICATION_LOCK_ID & 0xFFFFFFFF),
            ).fetchone()
        if identity != self._owner_identity or held != (True,):
            raise RuntimeError("Publication owner session or exclusive lock changed")

    @contextmanager
    def required_work(self):
        """Commit required DDL and publication together on the lock-owning session."""
        conn = self.connection
        if (
            conn is None
            or conn.closed
            or not conn.autocommit
            or conn.info.transaction_status != TransactionStatus.IDLE
            or self._active_work is not None
            or self.row_count is None
            or not self.relation_oids
        ):
            raise RuntimeError("Publication work requires an observed idle owner")
        self._assert_owner()
        work = PublicationWork(self)
        conn.autocommit = False
        self._active_work = work
        try:
            with conn.transaction():
                work.assert_current(conn)
                yield work
                work._assert_state(conn, "published")
        finally:
            self._active_work = None
            if (
                not conn.closed
                and conn.info.transaction_status == TransactionStatus.IDLE
            ):
                conn.autocommit = True

    def publish(self, update_metadata) -> None:
        if self.row_count is None:
            raise RuntimeError("Publication stock has not been observed")
        if self._active_work is not None:
            self._active_work.assert_current(self.connection)
        validated_stock = self.row_count, dict(self.relation_oids)
        self.observe_stock()
        if validated_stock != (self.row_count, self.relation_oids):
            raise RuntimeError("Publication stock changed during required work")
        with self.connection.transaction():
            update_metadata(self.connection)
            changed = self.connection.execute(
                "UPDATE etl_publications SET status='published',completed_at=NOW(), "
                "relation_oids=%s,row_count=%s WHERE id=%s AND status='pending'",
                (Jsonb(self.relation_oids), self.row_count, self.id),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Publication no longer pending")

    def record_required_failure(self, writer) -> None:
        """Called after rollback; preserve exclusion without opening a new session."""
        self._assert_owner()
        if (
            not self.connection.autocommit
            or self.connection.info.transaction_status != TransactionStatus.IDLE
        ):
            raise RuntimeError("Required failure trace must follow rollback")
        with self.connection.transaction():
            writer(self.connection)

    def __exit__(self, exc_type, _exc, _tb):
        try:
            if exc_type is not None and self.id is not None:
                try:
                    self.connection.execute(
                        "UPDATE etl_publications SET status='failed',completed_at=N"
                        "OW(), "
                        "failure_type=%s WHERE id=%s AND status='pending'",
                        (exc_type.__name__, self.id),
                    )
                except Exception:  # noqa: BLE001
                    # The already committed pending row remains a reader barrier.
                    log.error(  # noqa: TRY400
                        "Publication failure could not be recorded; pending remains"
                    )
        finally:
            self.connection.close()

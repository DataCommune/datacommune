"""PostgreSQL advisory lock helpers for pipeline exclusion.

Session-level locks persist until the connection is closed, which is ideal
for ETL pipelines that span multiple transactions (create staging, COPY, swap).
"""

from __future__ import annotations

import hashlib
import logging

import psycopg

log = logging.getLogger(__name__)


def _dataset_lock_id(dataset_name: str) -> int:
    """Derive a stable integer lock ID from dataset name.

    Uses first 8 bytes of SHA-256 as a signed 64-bit int.
    PostgreSQL advisory locks accept bigint keys.
    """
    h = hashlib.sha256(dataset_name.encode()).digest()
    return int.from_bytes(h[:8], "big", signed=True)


def try_acquire_lock(
    dsn: str, dataset_name: str
) -> tuple[bool, psycopg.Connection | None]:
    """Try to acquire an advisory lock for *dataset_name*.

    Returns ``(acquired, connection)``.  When acquired is True the caller
    **must** eventually call :func:`release_lock` (or close the connection)
    to release the session-level lock.

    The connection is configured with ``tcp_keepalives_idle=60`` so that
    PostgreSQL detects dead clients quickly instead of holding the lock
    for the default 2 h keepalive timeout.
    """
    lock_id = _dataset_lock_id(dataset_name)
    conn = psycopg.connect(dsn, options="-c tcp_keepalives_idle=60")
    try:
        result = conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,)).fetchone()
    except Exception:
        conn.close()
        raise
    acquired = result[0] if result else False
    if not acquired:
        conn.close()
        return False, None
    log.debug("Advisory lock acquired for %s (id=%d)", dataset_name, lock_id)
    return True, conn


def release_lock(conn: psycopg.Connection | None) -> None:
    """Release the advisory lock by closing the connection.

    Safe to call with ``None`` (no-op).
    """
    if conn is not None:
        conn.close()

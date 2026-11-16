"""Explicit guards for readers of the thirteen journalled commune sources."""

import asyncio
import functools
import logging
import re
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.error_redaction import exception_chain, qualified_type
from app.commune.dependencies import COMMUNE_SOURCES, PUBLICATION_LOCK_ID
from app.database import get_db

logger = logging.getLogger(__name__)

PUBLICATION_DATASETS = frozenset(COMMUNE_SOURCES)
PUBLICATION_ROUTE_DATASETS = {
    "/v1/" + dataset.replace("_", "-"): dataset for dataset in PUBLICATION_DATASETS
}

PUBLICATION_NOT_VALIDATED = (
    "Données temporairement indisponibles : publication non validée."
)
PUBLICATION_LOCKED = "Publication temporairement indisponible."
ADMISSION_UNAVAILABLE = "Admission de publication indisponible."
PUBLICATION_UNCONFIGURED = "Configuration de publication indisponible."
# The only HTTPException details a failure log may quote: constants, never a
# detail built elsewhere from a request.
_LOGGABLE_DETAILS = frozenset(
    {
        PUBLICATION_NOT_VALIDATED,
        PUBLICATION_LOCKED,
        ADMISSION_UNAVAILABLE,
        PUBLICATION_UNCONFIGURED,
    }
)
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")

# Contention with a writer: both reader try-locks and the readiness statement.
ADMISSION_TIMEOUT_S = 0.25
# Checkout of the admission connection (pool wait, pre-ping, reconnection) is
# not contention with a writer and stays outside the budget above. It is still
# bounded, well under pool_timeout: every waiter already holds its main
# connection, so waiters could otherwise hold the whole pool (hold-and-wait).
ADMISSION_CHECKOUT_TIMEOUT_S = 2.0
# How long a request waits for the admission rollback (never cancelled): a
# slow one finishes in the background instead of delaying the answer.
ADMISSION_CLOSE_WAIT_S = 0.05
# Lifespan shutdown: ceiling on waiting for background admission tasks.
ADMISSION_SHUTDOWN_TIMEOUT_S = 5.0
# Background admission tasks (abandoned checkouts, closings), referenced until
# they finish; their failures are retrieved and logged by _settle.
_ADMISSION_TASKS: set[asyncio.Future] = set()
# Cleared by shutdown_admissions: no new admission once the app stops.
_admissions_open = True


async def guard_publication(session: AsyncSession, datasets: list[str]) -> None:
    """Hold a transaction-level shared lock through the subsequent data reads."""
    if not datasets or set(datasets) - PUBLICATION_DATASETS:
        raise ValueError("Unknown publication reader dataset")
    await acquire_publication_read_lock(session)
    try:
        ready = (
            await session.execute(
                text("""
            WITH reader_lock AS MATERIALIZED (
                SELECT pg_try_advisory_xact_lock_shared(:lock_id) AS acquired
            ), latest AS (
                SELECT DISTINCT ON (dataset_name) * FROM etl_publications
                WHERE dataset_name=ANY(:datasets) ORDER BY dataset_name,id DESC
            )
            SELECT (SELECT acquired FROM reader_lock) AND NOT EXISTS (
                SELECT 1 FROM latest p
                WHERE p.status <> 'published' OR EXISTS (
                    SELECT 1 FROM jsonb_each_text(p.relation_oids) rel(name,oid)
                    WHERE to_regclass(rel.name)::oid IS DISTINCT FROM rel.oid::oid
                )
            )
        """),
                {"lock_id": PUBLICATION_LOCK_ID, "datasets": datasets},
            )
        ).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in {"42P01", "57014"}:
            raise
        ready = False
    if not ready:
        raise HTTPException(503, PUBLICATION_NOT_VALIDATED)


async def require_publication(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Dependency installed only on explicitly mapped source routers."""
    if request.url.path == "/v1/zone/stats":
        await guard_publication(session, ["dvf", "dpe"])
        return
    if request.url.path == "/v1/zone/resolve":
        dataset = request.query_params.get("datasets", "").strip().lower()
        if dataset not in {"dvf", "dpe"}:
            raise HTTPException(422, "Dataset invalide")
        await guard_publication(session, [dataset])
        return
    if request.url.path in {
        "/v1/bien/logements",
        "/v1/bien/dpe/inventaire",
        "/v1/bien/dpe/document",
        "/v1/bien/dpe/comparaison",
    }:
        # The housing context also reads parcel sales; documentary routes do not.
        datasets = (
            ["dpe", "dvf"] if request.url.path == "/v1/bien/logements" else ["dpe"]
        )
        try:
            await guard_publication(session, datasets)
        except HTTPException as exc:
            # Dependency failures occur before the documentary endpoint can
            # attach its no-store header; keep the same public failure state.
            exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
            raise
        return
    if request.url.path.startswith("/v1/dpe/"):
        # The public document by its number (04-04): same no-store failure as
        # the documentary routes above, the dependency failing before the
        # endpoint can attach its header.
        try:
            await guard_publication(session, ["dpe"])
        except HTTPException as exc:
            exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
            raise
        return
    root = "/".join(request.url.path.split("/")[:3])
    if root == "/v1/logements":
        await guard_publication(session, ["dpe"])
        return
    dataset = PUBLICATION_ROUTE_DATASETS.get(root)
    if dataset is None:
        raise HTTPException(503, PUBLICATION_UNCONFIGURED)
    await guard_publication(session, [dataset])


async def acquire_publication_read_lock(
    session: AsyncSession | AsyncConnection,
) -> None:
    """Acquire before the readiness statement takes its READ COMMITTED snapshot."""
    try:
        acquired = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock_shared(:lock_id)"),
                {"lock_id": PUBLICATION_LOCK_ID},
            )
        ).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "57014":
            raise
        acquired = False
    if not acquired:
        raise HTTPException(503, PUBLICATION_LOCKED)


async def guard_snapshot_publication(
    session: AsyncSession, datasets: list[str]
) -> None:
    """Prevent a writer between creating a repeatable snapshot and taking its lock.

    The temporary connection holds exclusion while the main transaction acquires
    its own lock and first snapshot. The main lock then outlives admission.

    Refusal contract (261013-adm, contract A): once the admission connection is
    obtained, a writer holding the publication lock is refused at once by the
    reader try-lock (503, PUBLICATION_LOCKED). Only the try-locks and the
    readiness statement run under the 250 ms budget; the checkout (pool wait,
    pre-ping, reconnection) has its own 2 s bound. A refusal therefore takes at
    most 2 s + 250 ms, and can be slower than the budget alone when the checkout
    is slow: accepted, in exchange for no false 503 on a slow checkout. An
    expired checkout bound refuses too (503, ADMISSION_UNAVAILABLE): the read is
    never admitted without both locks.

    The admission rollback never delays the answer past the contract: it runs
    in its own supervised task, never cancelled, and the request waits for it
    at most ADMISSION_CLOSE_WAIT_S, within the time left of the 2 s + 250 ms.
    """
    if not _admissions_open:
        raise HTTPException(503, ADMISSION_UNAVAILABLE)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ADMISSION_CHECKOUT_TIMEOUT_S + ADMISSION_TIMEOUT_S
    try:
        admission = await _admission_connection(session.bind)
        try:
            async with asyncio.timeout(ADMISSION_TIMEOUT_S):
                await acquire_publication_read_lock(admission)
                await guard_publication(session, datasets)
        finally:
            # Releases the admission lock; outside both budgets.
            closing = _supervise(asyncio.ensure_future(admission.close()))
            wait = min(ADMISSION_CLOSE_WAIT_S, max(0.0, deadline - loop.time()))
            # asyncio.wait never cancels the closing, even if this request is.
            await asyncio.wait({closing}, timeout=wait)
    except (TimeoutError, PoolTimeoutError) as exc:
        # Chained for the failure log, which renders types only (C60).
        raise HTTPException(503, ADMISSION_UNAVAILABLE) from exc


async def _admission_connection(
    bind: AsyncEngine | AsyncConnection,
) -> AsyncConnection:
    """Check out the admission connection without ever cancelling the pool.

    A checkout cancelled while the pool invalidates a connection whose pre-ping
    failed loses that pool slot for good (SQLAlchemy 2.0.46 with asyncpg: the
    cancellation lands in the terminate of ``_ConnectionRecord.invalidate``).
    The checkout runs in its own shielded task: past the bound the request is
    refused, and the task, left to finish, returns its connection to the pool.
    """
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    connection = engine.connect()
    checkout = asyncio.ensure_future(connection.start())
    try:
        async with asyncio.timeout(ADMISSION_CHECKOUT_TIMEOUT_S):
            await asyncio.shield(checkout)
    except BaseException:
        # Expired bound, failed checkout or cancelled request alike.
        if not checkout.done():
            _supervise(checkout)
            checkout.add_done_callback(functools.partial(_return_abandoned, connection))
        elif not checkout.cancelled() and checkout.exception() is None:
            # Checked out just as the bound expired: give it back.
            _supervise(asyncio.ensure_future(connection.close()))
        # A checkout that failed in time raises to its request: not logged twice.
        raise
    return connection


def _supervise(task: asyncio.Future) -> asyncio.Future:
    """Keep a background admission task referenced until it settles."""
    _ADMISSION_TASKS.add(task)
    task.add_done_callback(_settle)
    return task


def _settle(task: asyncio.Future) -> None:
    """Forget a settled task, retrieving and logging its failure (C60)."""
    _ADMISSION_TASKS.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning(
            "admission: tâche de fond en échec (%s)", describe_read_failure(error)
        )


def _return_abandoned(connection: AsyncConnection, checkout: asyncio.Future) -> None:
    """Give an abandoned checkout's connection back once it completes."""
    # A failed checkout's error is retrieved and logged by _settle.
    if checkout.cancelled() or checkout.exception() is not None:
        return
    _supervise(asyncio.ensure_future(connection.close()))


async def drain_admission_tasks(timeout: float) -> int:
    """Wait up to *timeout* for background admission tasks, never cancelling.

    Tasks spawned meanwhile (the closing of a checkout that just completed) are
    waited for too. Returns how many are still running at the deadline.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while _ADMISSION_TASKS:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.wait(set(_ADMISSION_TASKS), timeout=remaining)
        # Let the done callbacks run (they discard, and may spawn a closing).
        await asyncio.sleep(0)
    return len(_ADMISSION_TASKS)


async def shutdown_admissions(timeout: float = ADMISSION_SHUTDOWN_TIMEOUT_S) -> int:
    """Lifespan shutdown: refuse new admissions, then drain before dispose.

    Past *timeout* the remaining tasks are left to the engine disposal and
    their count is logged; nothing is cancelled.
    """
    global _admissions_open
    _admissions_open = False
    remaining = await drain_admission_tasks(timeout)
    if remaining:
        logger.warning(
            "admission: %d tâche(s) de fond encore active(s) à l'arrêt", remaining
        )
    return remaining


def describe_read_failure(exc: BaseException) -> str:
    """Message-free description of a swallowed read failure, for its log (C60).

    Qualified types of the failure and of its direct cause (the root of an
    expired ``asyncio.timeout`` is its internal CancelledError), the detail of a
    publication refusal (constants only) and the SQLSTATE of a database error.
    Never an exception message: it can quote a parameter the visitor typed.
    """
    chain = exception_chain(exc)
    fields = [f"error_type={qualified_type(exc)}"]
    if len(chain) > 1:
        fields.append(f"cause_type={qualified_type(chain[1])}")
    detail = getattr(exc, "detail", None)
    if (
        isinstance(exc, StarletteHTTPException)
        and isinstance(detail, str)
        and detail in _LOGGABLE_DETAILS
    ):
        fields.append(f"detail={detail!r}")
    for error in chain:
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        if (
            isinstance(error, DBAPIError)
            and isinstance(sqlstate, str)
            and _SQLSTATE.fullmatch(sqlstate)
        ):
            fields.append(f"sqlstate={sqlstate}")
            break
    return " ".join(fields)

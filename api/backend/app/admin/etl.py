"""Admin — ETL ops router (/v1/admin/etl).

Three endpoints:
  - GET  /v1/admin/etl                          — per-pipeline summary list
  - GET  /v1/admin/etl/{pipeline_name}          — paginated run history
  - POST /v1/admin/etl/{pipeline_name}/trigger  — enqueue manual refresh (audit-logged)

All endpoints gated by AdminUser (403 non-admin, 429 >50 req/min).

Trigger pattern — fire-and-forget (research Open Question 3):
  1. Validate pipeline_name against PIPELINE_MAP (404 if unknown)
  2. log_admin_action + await db.commit() FIRST (audit trail is durable
     even if the executor fails to schedule)
  3. asyncio.create_task(_run_pipeline_in_executor(name)) — returns 202
  4. run_scheduled_pipeline internally acquires a session-level advisory
     lock via scheduler.locks.try_acquire_lock; if already held, silently
     skips. That is why the response is 202 Accepted, not 200 — we cannot
     guarantee the pipeline will start (it may be already running).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminUser
from app.admin.queries import (
    list_etl_pipeline_summary,
    list_etl_runs_for_pipeline,
)
from app.admin.schemas import (
    EtlPipelineSummaryListResponse,
    EtlRunHistoryResponse,
    TriggerEtlRequest,
)
from app.admin.service import log_admin_action
from app.common.exceptions import ErreurApiError
from app.common.source_catalog import covered_departements
from app.database import get_db
from app.scheduler.jobs import PIPELINE_MAP, run_scheduled_pipeline
from app.scope.store import load_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/etl", tags=["admin"])


# ---------------------------------------------------------------------------
# Fire-and-forget helper (wraps the sync pipeline runner in an executor)
# ---------------------------------------------------------------------------


async def _run_pipeline_in_executor(pipeline_name: str) -> None:
    """Offload the synchronous pipeline runner to a thread so the FastAPI
    event loop is not blocked. `run_scheduled_pipeline` uses psycopg sync
    under the hood — calling it directly in an async endpoint would stall
    uvicorn for the whole pipeline duration (hours).

    The advisory lock lives inside `run_scheduled_pipeline` — no need to
    guard it here.
    """
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, run_scheduled_pipeline, pipeline_name)
    except Exception:  # final boundary — log then swallow
        logger.exception(
            "admin_etl_trigger_executor_failed",
            extra={"pipeline": pipeline_name},
        )


# ---------------------------------------------------------------------------
# GET /v1/admin/etl
# ---------------------------------------------------------------------------


@router.get("", response_model=EtlPipelineSummaryListResponse)
async def list_pipelines_summary(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EtlPipelineSummaryListResponse:
    """Resume par pipeline (last_success, dernier run, duration, rows, erreur)."""
    rows, total = await list_etl_pipeline_summary(db)
    return EtlPipelineSummaryListResponse(rows=rows, total=total)


# ---------------------------------------------------------------------------
# GET /v1/admin/etl/{pipeline_name}
# ---------------------------------------------------------------------------


@router.get("/{pipeline_name}", response_model=EtlRunHistoryResponse)
async def get_pipeline_history(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    pipeline_name: Annotated[str, Path(min_length=1, max_length=50)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> EtlRunHistoryResponse:
    """Historique des runs pour un pipeline precis (pagine)."""
    if pipeline_name not in PIPELINE_MAP:
        raise ErreurApiError(
            404,
            "Introuvable",
            f"Pipeline '{pipeline_name}' inconnu",
        )
    rows, total = await list_etl_runs_for_pipeline(
        db=db,
        pipeline_name=pipeline_name,
        page=page,
        page_size=page_size,
    )
    return EtlRunHistoryResponse(
        pipeline=pipeline_name,
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/etl/{pipeline_name}/trigger
# ---------------------------------------------------------------------------


@router.post(
    "/{pipeline_name}/trigger",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_pipeline(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    pipeline_name: Annotated[str, Path(min_length=1, max_length=50)],
    body: TriggerEtlRequest,
) -> dict:
    """Declenche un run manuel.

    Retour: 202 Accepted (enqueue). Si une execution est deja en cours,
    le pipeline sera silencieusement ignore par l'advisory lock dans
    scheduler.jobs.run_scheduled_pipeline. Le frontend affiche un toast
    en consequence.

    404 si pipeline_name inconnu.
    409 si le dataset n'est pas hébergé : une clé du pipeline est absente du
    périmètre configuré en base (``python -m app.scope``), ou ce périmètre
    est vide ; 409 aussi quand la source ne publie aucun département hébergé
    (couverture de source du catalogue). Le runner relit le périmètre et
    refuse aussi en seconde ligne.
    """
    if pipeline_name not in PIPELINE_MAP:
        raise ErreurApiError(
            404,
            "Introuvable",
            f"Pipeline '{pipeline_name}' inconnu",
        )
    # D-23: the database scope is the only scope source. Refuse before the
    # audit row so a non-hosted dataset never looks like an enqueued run.
    scope = await load_scope(db)
    keys = PIPELINE_MAP[pipeline_name]().scope_dataset_keys
    missing = [key for key in keys if scope.is_empty or not scope.hosts(key)]
    if missing:
        raise ErreurApiError(
            409,
            "Dataset non hébergé",
            f"Dataset non hébergé par cette instance : {', '.join(missing)}. "
            "Le périmètre se configure en base avec "
            "`python -m app.scope set --datasets … --departements …`.",
        )
    # Expected departements = scope ∩ source coverage (catalogue): nothing to
    # import when empty. Same refusal as run_etl, before the audit (WR-03).
    if not covered_departements(keys, scope.departements):
        raise ErreurApiError(
            409,
            "Source sans couverture",
            f"source sans couverture sur le périmètre: {pipeline_name}. "
            "Aucun département hébergé n'est publié par cette source "
            "(couverture de source du catalogue).",
        )

    # Audit FIRST, then commit, then enqueue. If the task schedule fails
    # the audit row is still durable.
    await log_admin_action(
        db=db,
        admin=admin,
        action="trigger_etl",
        target_type="etl_pipeline",
        target_id=pipeline_name,
        before=None,
        after={"pipeline": pipeline_name},
        reason=body.reason,
        request=request,
    )
    await db.commit()

    # Fire-and-forget. Exceptions inside the coroutine are logged in
    # _run_pipeline_in_executor, never raised back to this handler.
    asyncio.create_task(_run_pipeline_in_executor(pipeline_name))  # noqa: RUF006

    return {
        "status": "enqueued",
        "pipeline": pipeline_name,
        "note": "ignoré si une exécution est déjà en cours",
    }

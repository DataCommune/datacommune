"""Health check endpoint for monitoring API and database status.

Returns "degradee" when any dataset exceeds its per-dataset freshness
threshold or when a materialized view is stale, so Uptime Kuma can detect
stale data automatically.

Review #1 finding #14: matviews block now reads from matview_metadata table
(written by the scheduler) instead of pg_matviews (which has no timestamps).
Review #1 finding #13: dataset health entries now carry etat_pipeline derived
from etl_runs last_success_at + last run status.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.email import email_failures_count
from app.common.migration_check import _load_script_directory
from app.config import settings
from app.database import get_db
from app.datasets.freshness import compute_freshness, manual_update_threshold
from app.health.schemas import (
    DatasetHealthStatus,
    DeploymentVersion,
    MatviewStatus,
    ReponseHealth,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["sante"])


@router.get(
    "/health/live",
    summary="Liveness probe",
    description=(
        "Probe de vivacite pour les orchestrateurs (Kubernetes, Docker). "
        "Retourne 200 si le processus est vivant."
    ),
)
async def verifier_liveness() -> dict[str, str]:
    """Liveness probe: process is up and can serve HTTP."""
    return {"statut": "ok"}


@router.get("/health/version", response_model=DeploymentVersion)
async def deployment_version(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeploymentVersion:
    """Expose non-secret release and schema markers for deployment qualification."""
    result = await db.execute(text("SELECT version_num FROM alembic_version"))
    script = _load_script_directory()
    return DeploymentVersion(
        environment=settings.app_environment,
        deployment_sha=settings.deployment_sha,
        schema_actual=result.scalar_one_or_none(),
        schema_expected=",".join(sorted(script.get_heads())),
    )


@router.get(
    "/health",
    response_model=ReponseHealth,
    summary="Etat de sante complet",
    description=(
        "Verification de l'API et de la connectivite base de donnees. "
        "Retourne le statut, la version et le temps de reponse DB."
    ),
)
async def verifier_sante(
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> ReponseHealth:
    """Verifie l'etat de sante de l'API et de la base de donnees.

    Retourne le statut de connexion a PostgreSQL, la version majeure de
    PostGIS (S5 : jamais la version exacte ni les flags de build), et la
    fraicheur de chaque dataset. Si un dataset a un etat_pipeline
    degrade ou echec, ou si une matview depasse son seuil, l'etat editorial
    passe a "degraded" (statut "degradee") mais la reponse reste HTTP 200 :
    seule une indisponibilite technique reelle (DB/PostGIS) renvoie 503.
    """
    try:
        result = await db.execute(text("SELECT PostGIS_Version()"))
        # The query doubles as the DB connectivity probe (a failure drives the
        # 503 below), so it stays — only its value is trimmed. Audit S5: this
        # endpoint is public and PostGIS_Version() returns the exact version
        # plus the build flags ("3.5 USE_GEOS=1 USE_PROJ=1"), which is the one
        # piece of reconnaissance here worth withholding. The field stays in the
        # contract (backward compat) carrying the major version only.
        raw_version = result.scalar()
        version_postgis = raw_version.split(".")[0].strip() if raw_version else None

        # Freshness + etat_pipeline for each dataset with a configured threshold.
        # Uses LATERAL JOIN to get last_success_at and last run status from etl_runs.
        rows = await db.execute(
            text(
                "SELECT dm.dataset_name, dm.last_updated_at, "
                "dm.freshness_threshold_days, dm.cron_schedule, "
                "lr.status AS last_run_status, "
                "lr.started_at AS last_run_started_at, "
                "ls.last_success_at "
                "FROM dataset_metadata dm "
                "LEFT JOIN LATERAL ("
                "    SELECT status, started_at FROM etl_runs "
                "    WHERE dataset_name = dm.dataset_name "
                "    ORDER BY started_at DESC LIMIT 1"
                ") lr ON TRUE "
                "LEFT JOIN LATERAL ("
                "    SELECT completed_at AS last_success_at FROM etl_runs "
                "    WHERE dataset_name = dm.dataset_name AND status = 'success' "
                "    ORDER BY completed_at DESC LIMIT 1"
                ") ls ON TRUE "
                "WHERE dm.freshness_threshold_days IS NOT NULL "
                "ORDER BY dm.dataset_name"
            )
        )

        datasets_status: list[DatasetHealthStatus] = []
        has_degraded = False

        for row in rows:
            mapping = row._mapping
            threshold = manual_update_threshold(
                mapping["dataset_name"], mapping["freshness_threshold_days"]
            )
            if threshold is None:
                continue  # quarantined: no freshness promise (D-40)
            # D-12: updates are manual and no scheduler runs, so a cron date that
            # passed says nothing about the data; only the threshold applies.
            fraicheur, etat_pipeline = compute_freshness(
                mapping.get("last_success_at"),
                threshold,
                cron_schedule=None,
                last_run_status=mapping.get("last_run_status"),
                last_run_started_at=mapping.get("last_run_started_at"),
            )
            if etat_pipeline in ("degrade", "echec"):
                has_degraded = True
            datasets_status.append(
                DatasetHealthStatus(
                    nom=mapping["dataset_name"],
                    fraicheur=fraicheur or "fresh",
                    derniere_maj=mapping["last_updated_at"],
                    etat_pipeline=etat_pipeline,
                )
            )

        # Matview freshness: read from matview_metadata (written by scheduler).
        # Returns real last_refresh_at timestamps; degrades global status when
        # a matview exceeds its freshness_threshold_days or last refresh failed.
        mv_rows = await db.execute(
            text(
                "SELECT matview_name, last_refresh_at, last_refresh_status, "
                "last_error, freshness_threshold_days "
                "FROM matview_metadata"
            )
        )
        matviews: list[MatviewStatus] = []
        for mv in mv_rows:
            m = mv._mapping
            mv_fraicheur, mv_etat = compute_freshness(
                m.get("last_refresh_at"),
                m.get("freshness_threshold_days"),
                cron_schedule=None,  # matviews use pure day-threshold
                last_run_status=m.get("last_refresh_status"),
                last_run_started_at=m.get("last_refresh_at"),
            )
            if mv_etat in ("degrade", "echec") or mv_fraicheur == "outdated":
                has_degraded = True
            # Also degrade if last refresh explicitly failed
            if m.get("last_refresh_status") == "failed":
                has_degraded = True
            matviews.append(
                MatviewStatus(
                    nom=m["matview_name"],
                    derniere_maj=m.get("last_refresh_at"),
                )
            )

        # D-07/D-08: the DB answered, so the service is technically available.
        # An editorial degradation (stale source, failed ETL, aged matview)
        # keeps HTTP 200 — it never drives the technical availability decision.
        editorial_status = "degraded" if has_degraded else "current"
        statut = "degradee" if has_degraded else "ok"

        return ReponseHealth(
            statut=statut,
            technical_status="available",
            editorial_status=editorial_status,
            base_de_donnees="connectee",
            version_postgis=version_postgis,
            datasets_status=datasets_status or None,
            matviews=matviews or None,
            email_failures=email_failures_count(),
            environment=settings.app_environment,
            deployment_sha=settings.deployment_sha,
        )
    except Exception:
        # Only a real technical unavailability (API/DB) returns 503.
        logger.exception("health_check_db_error")
        response.status_code = 503
        return ReponseHealth(
            statut="degradee",
            technical_status="unavailable",
            editorial_status="unknown",
            base_de_donnees="deconnectee",
            erreur="database unavailable",
            environment=settings.app_environment,
            deployment_sha=settings.deployment_sha,
        )

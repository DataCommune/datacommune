"""Dataset metadata endpoint with freshness and ETL run history."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas import DatasetHosting, DatasetProvenance, ReponseApi
from app.common.source_catalog import DATASET_CATALOG
from app.database import get_db
from app.datasets import freshness
from app.datasets.freshness import compute_freshness, manual_update_threshold
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.datasets.queries import get_all_datasets
from app.datasets.schemas import (
    DatasetInfo,
    DerniereRun,
    LimitesSource,
    ReutilisationSource,
)
from app.scope.coverage import hosting_for
from app.scope.dependencies import get_instance_scope
from app.scope.model import SCOPE_EXCLUDED_KEYS, InstanceScope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["datasets"])


def _compute_next_run(cron_schedule: str | None) -> str | None:
    """Compute next scheduled run from cron expression.

    Uses APScheduler's CronTrigger if available, otherwise falls back
    to returning the raw cron expression.
    """
    if not cron_schedule:
        return None
    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(cron_schedule)
        next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
        if next_fire:
            return next_fire.isoformat()
    except Exception:  # noqa: BLE001
        logger.debug("CronTrigger unavailable, returning raw cron schedule")
    return cron_schedule


def _build_derniere_run(row: dict) -> DerniereRun | None:
    """Build DerniereRun from the lateral join columns, or None."""
    if row.get("last_run_status") is None:
        return None
    return DerniereRun(
        statut=row["last_run_status"],
        debut=row.get("last_run_started_at"),
        fin=row.get("last_run_completed_at"),
        duree_secondes=row.get("last_run_duration"),
        lignes_chargees=row.get("last_run_rows"),
        erreur=row.get("last_run_error"),
    )


def _build_provenance(row: dict, now: datetime) -> DatasetProvenance | None:
    """Assemble the provenance contract for a row, or None if off-catalog.

    Pure: reuses the batch-loaded facts, no extra DB call. Unknown dataset
    names (not in the canonical catalog) yield None rather than a 500.
    """
    facts = ProvenanceFacts(
        last_success_at=row.get("last_success_at"),
        source_period_label=row.get("source_period_label"),
        source_updated_at=row.get("source_updated_at"),
        effective_departments=row.get("effective_departments"),
    )
    try:
        return assemble_provenance(row["dataset_name"], facts, now=now)
    except KeyError:
        logger.debug(
            "dataset %s absent du catalogue canonique — provenance omise",
            row.get("dataset_name"),
        )
        return None


def _catalogue_facts(key: str) -> dict:
    """Reuse terms, quarantine and source limits declared by the catalogue.

    Read from :data:`DATASET_CATALOG` only (LIC-01, D-40): the page that
    publishes them never holds a copy. An off-catalogue name gets none.
    """
    entry = DATASET_CATALOG.get(key)
    if entry is None:
        return {}
    limites = entry.limites_source
    return {
        "reutilisation": ReutilisationSource(
            licence=entry.licence,
            licence_url=entry.licence_url,
            conditions=entry.conditions_redistribution,
            restrictions=list(entry.restrictions),
            verifiee_le=entry.licence_verifiee_le,
        ),
        "quarantaine": entry.quarantaine,
        "limites_source": LimitesSource(
            chapeau=limites.chapeau, puces=list(limites.puces)
        )
        if limites is not None
        else None,
    }


def _is_quarantined(key: str) -> bool:
    entry = DATASET_CATALOG.get(key)
    return entry is not None and entry.quarantaine is not None


def _served_status(row: dict) -> str:
    """Status of a metadata row in the closed vocabulary of ``/v1/datasets``.

    Migrations seed ``dataset_metadata`` with ``'unknown'`` (004, 018),
    ``'proxy'`` (039) and ``'disponible'`` for datasets no run ever loaded
    (039). A catalogue quarantine wins; a proxied source is ``proxy_amont``;
    a dataset is ``disponible`` only with a successful run in ``etl_runs``,
    otherwise ``non_importe`` — a seeded row is never presented as imported.
    """
    if _is_quarantined(row["dataset_name"]):
        return "quarantined"
    status = row["status"]
    if status == "proxy":
        return "proxy_amont"
    if status == "unknown" or row.get("last_success_at") is None:
        return "non_importe"
    return status


def build_dataset_info(row: dict, now: datetime | None = None) -> DatasetInfo:
    """Assemble one DatasetInfo from a batch-loaded row (no I/O).

    Combines the legacy internal pipeline freshness (compute_freshness) with the
    structured source provenance (assemble_provenance). Extracted from the
    endpoint so the router only loads rows and serializes, and so the assembly
    is unit-tested directly.

    ``now`` defaults to the freshness ``_now()`` seam so a single clock drives
    both the legacy freshness and the source editorial state, and so existing
    tests that patch ``app.datasets.freshness._now`` keep working.

    A dataset the catalogue quarantines is served ``quarantined``, without any
    freshness or schedule promise, whatever its metadata row says (D-40).
    """
    if now is None:
        now = freshness._now()
    # Same policy as /v1/health: manual updates (D-12), no cron adherence.
    fraicheur, etat_pipeline = compute_freshness(
        row.get("last_success_at"),
        manual_update_threshold(
            row["dataset_name"], row.get("freshness_threshold_days")
        ),
        cron_schedule=None,
        last_run_status=row.get("last_run_status"),
        last_run_started_at=row.get("last_run_started_at"),
        now=now,
    )
    key = row["dataset_name"]
    quarantined = _is_quarantined(key)
    return DatasetInfo(
        nom=key,
        statut=_served_status(row),
        derniere_maj=row["last_updated_at"],
        nombre_lignes=row["row_count"],
        duree_import_secondes=row["last_import_duration_seconds"],
        cron_schedule=None if quarantined else row.get("cron_schedule"),
        fraicheur=None if quarantined else fraicheur,
        etat_pipeline="degrade" if quarantined else etat_pipeline,
        prochaine_maj_prevue=None
        if quarantined
        else _compute_next_run(row.get("cron_schedule")),
        derniere_run=_build_derniere_run(row),
        provenance=_build_provenance(row, now),
        **_catalogue_facts(key),
    )


@router.get(
    "/datasets",
    response_model=ReponseApi[list[DatasetInfo]],
    summary="Metadonnees des datasets",
    description=(
        "Liste les datasets ingeres avec leur statut, nombre de lignes, "
        "date de derniere mise a jour et source, plus chaque cle du catalogue "
        "sans import ; `hebergement` dit si l'instance heberge le dataset ou "
        "le declare hors couverture, avec son motif ; `reutilisation` donne la "
        "licence et les restrictions relevees chez le producteur ; un jeu que "
        "le catalogue met en quarantaine est servi `quarantined` avec son motif "
        "(`quarantaine`)."
    ),
)
async def lister_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    scope: Annotated[InstanceScope, Depends(get_instance_scope)],
) -> ReponseApi[list[DatasetInfo]]:
    """Liste les datasets ingeres avec statut, fraicheur et derniere run."""
    rows = await get_all_datasets(db)
    now = freshness._now()
    return ReponseApi(donnees=list_dataset_infos(rows, scope, now))


def _hosting(key: str, scope: InstanceScope) -> DatasetHosting | None:
    if key in SCOPE_EXCLUDED_KEYS or key not in DATASET_CATALOG:
        return None
    return hosting_for(key, scope, None)


def _unloaded_dataset_info(
    key: str, hosting: DatasetHosting, now: datetime
) -> DatasetInfo:
    """Catalogue key without a ``dataset_metadata`` row: declared, not hidden.

    A catalogue quarantine wins over every other status: the key is never
    presented as merely not imported or proxied (D-40).
    """
    if _is_quarantined(key):
        statut = "quarantined"
    elif hosting.etat == "hors_couverture":
        statut = "hors_couverture"
    elif DATASET_CATALOG[key].coverage_policy == "upstream":
        statut = "proxy_amont"
    else:
        statut = "non_importe"
    return DatasetInfo(
        nom=key,
        statut=statut,
        provenance=assemble_provenance(key, ProvenanceFacts(), now=now),
        hebergement=hosting,
        **_catalogue_facts(key),
    )


def list_dataset_infos(
    rows: list[dict], scope: InstanceScope, now: datetime
) -> list[DatasetInfo]:
    """Every metadata row, plus every catalogue key (but ``commune``) without one.

    Existing rows keep their fields; each gains ``hebergement`` (DATA-03).
    """
    infos = []
    for row in rows:
        info = build_dataset_info(row, now=now)
        info.hebergement = _hosting(row["dataset_name"], scope)
        infos.append(info)
    loaded = {row["dataset_name"] for row in rows}
    for key in sorted(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS - loaded):
        infos.append(_unloaded_dataset_info(key, hosting_for(key, scope, None), now))  # noqa: PERF401
    return sorted(infos, key=lambda info: info.nom)

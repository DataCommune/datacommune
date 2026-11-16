"""Pydantic schemas for admin users endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AdminUserRow(BaseModel):
    """One row in the admin users table listing."""

    id: int
    email: str
    name: str | None
    is_admin: bool
    email_verified: bool
    created_at: datetime
    last_login: datetime | None
    plan: str
    quota_used: int
    quota_monthly: int
    api_keys_count: int


class AdminUsersListResponse(BaseModel):
    """Paginated list response for admin users table."""

    rows: list[AdminUserRow]
    total: int
    page: int
    page_size: int


class AdminApiKeyInfo(BaseModel):
    """API key detail as shown in the user detail side panel."""

    id: int
    prefix: str | None
    label: str | None
    quota_used: int
    quota_monthly: int
    last_used_at: datetime | None
    revoked: bool
    is_default: bool
    created_at: datetime


class AdminSubscriptionInfo(BaseModel):
    """Active subscription detail for a user."""

    plan_name: str
    status: str
    current_period_end: datetime | None
    source: str | None
    stripe_subscription_id: str


class AdminRecentActivity(BaseModel):
    """One entry in the user's recent activity timeline (from admin_audit_log)."""

    id: int
    action: str
    reason: str
    created_at: datetime


class AdminUserDetailResponse(AdminUserRow):
    """Full user detail response for the admin side panel.

    Extends AdminUserRow (all user columns at root level) and adds
    the nested relations fetched in the detail endpoint.
    """

    api_keys: list[AdminApiKeyInfo]
    subscription: AdminSubscriptionInfo | None
    recent_activity: list[AdminRecentActivity]


class GrantPlanRequest(BaseModel):
    """Request body for POST /admin/users/{id}/grant-plan.

    `force_downgrade` must be set to True when the requested plan is lower
    than the user's current plan. The backend rejects implicit downgrades
    to prevent accidental plan regression from the admin modal.
    """

    plan: Literal["free", "pro", "business", "enterprise"]
    reason: str = Field(..., min_length=10)
    end_date: datetime | None = None
    force_downgrade: bool = False


class ToggleAdminRequest(BaseModel):
    """Request body for PATCH /admin/users/{id}/admin."""

    is_admin: bool
    reason: str = Field(..., min_length=10)


class ResetQuotaRequest(BaseModel):
    """Request body for POST /admin/users/{id}/reset-quota."""

    reason: str = Field(..., min_length=10)


class RevokeKeysRequest(BaseModel):
    """Request body for POST /admin/users/{id}/revoke-keys.

    Revokes ALL api_keys of the target user (including is_default). Admin
    action is intentional — confirmation + reason enforced on the frontend.
    """

    reason: str = Field(..., min_length=10)


class ResendVerifRequest(BaseModel):
    """Request body for POST /admin/users/{id}/resend-verification."""

    reason: str = Field(..., min_length=10)


class BulkActionRequest(BaseModel):
    """Request body for POST /admin/users/bulk.

    `action` — which admin operation to apply to every user in `user_ids`.
    `user_ids` — 1..100 target user ids (Pydantic enforces bounds, endpoint
    additionally rejects the caller's own id up front).
    `reason` — shared reason string propagated to every per-user audit row.
    """

    action: Literal["reset_quota", "revoke_keys", "resend_verification"]
    user_ids: list[int] = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=10)


class BulkActionResult(BaseModel):
    """Per-user outcome in a bulk action response."""

    user_id: int
    status: Literal["ok", "error"]
    detail: str | None = None


class BulkActionResponse(BaseModel):
    """Aggregate bulk action response — frontend shows partial-success UX."""

    results: list[BulkActionResult]
    total: int
    failed: int


# --- Audit log schemas ---


class AdminAuditEntry(BaseModel):
    """One row from admin_audit_log joined with users.email."""

    id: int
    admin_user_id: int
    admin_email: str | None  # joined from users
    action: str  # e.g. 'grant_plan', 'toggle_is_admin'
    target_type: str  # e.g. 'user'
    target_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str
    ip: str | None
    user_agent: str | None
    created_at: datetime


class AdminAuditListResponse(BaseModel):
    """Paginated audit log response."""

    rows: list[AdminAuditEntry]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Phase 17 — Admin Observability
# ---------------------------------------------------------------------------

# ----- /admin/requests -----


class AdminRequestEntry(BaseModel):
    """Une ligne de api_request_log enrichie avec user_email via JOIN api_keys."""

    id: int
    ts: datetime
    method: str
    path: str
    status_code: int | None
    duration_ms: float | None
    client_ip: str | None  # HOST(r.client_ip) in SQL — INET -> dotted string
    api_key_id: int | None
    user_id: int | None
    user_email: str | None


class AdminRequestsListResponse(BaseModel):
    """Réponse paginée de /admin/requests."""

    rows: list[AdminRequestEntry]
    total: int
    page: int
    page_size: int


# ----- /admin/api-keys -----


class AdminApiKeyRow(BaseModel):
    """Ligne cross-user sur /admin/api-keys.

    last_used_at dérivé de api_request_log via LATERAL MAX(ts). Retention 7
    jours sur api_request_log — donc NULL si pas d'activité dans les 7
    derniers jours (documenté dans l'UI).
    """

    id: int
    prefix: str | None
    label: str | None
    user_id: int | None
    owner_email: str | None
    quota_used: int
    quota_monthly: int
    revoked: bool
    is_default: bool
    created_at: datetime
    last_used_at: datetime | None


class AdminApiKeysListResponse(BaseModel):
    """Réponse paginée de /admin/api-keys."""

    rows: list[AdminApiKeyRow]
    total: int
    page: int
    page_size: int


class RevokeKeyRequest(BaseModel):
    """Body de POST /admin/api-keys/{key_id}/revoke."""

    reason: str = Field(..., min_length=10)


class UpdateKeyQuotaRequest(BaseModel):
    """Body de PATCH /admin/api-keys/{key_id}/quota.

    quota_monthly borné [0, 10 000 000] pour éviter les valeurs absurdes
    qui casseraient l'arithmétique d'enforcement.
    """

    quota_monthly: int = Field(..., ge=0, le=10_000_000)
    reason: str = Field(..., min_length=10)


# ----- /admin/overview + heatmap -----


class AdminOverviewResponse(BaseModel):
    """Compteurs affichés sur la page /admin (racine).

    Le MRR est indisponible : les montants, devises et cadences facturés ne
    sont pas persistés. Aucun prix catalogue ni droit gratuit n'est additionné.
    Ce contrat ne mesure pas les encaissements ; un stock vide ne prouve pas 0 €.
    """

    users_count: int
    active_keys_count: int
    mrr_eur_estimated: None = Field(
        description="Toujours null : données de facturation insuffisantes pour le MRR."
    )
    mrr_status: Literal["unavailable"]
    mrr_reason: Literal["billing_amounts_not_persisted"]
    requests_24h: int


class HeatmapDay(BaseModel):
    """Un jour de la grille heatmap (date ISO YYYY-MM-DD + compteur req)."""

    day: str  # ISO date — Pydantic ne sérialise pas date -> str auto côté API
    count: int


class AdminUserHeatmapResponse(BaseModel):
    """Réponse de /admin/users/{user_id}/heatmap.

    retention_days indique la fenêtre réellement couverte (7 jours côté
    api_request_log). L'UI grise les cellules hors fenêtre et affiche une
    note explicite.
    """

    days: list[HeatmapDay]
    retention_days: int


# ---------------------------------------------------------------------------
# Phase 18 -- Billing Hardening (/admin/subscriptions)
# ---------------------------------------------------------------------------


class AdminSubscriptionRow(BaseModel):
    """Une ligne de /admin/subscriptions (subscriptions + users join).

    source vaut 'stripe' ou 'manual_grant'. Les rows anterieures a
    la migration 062 ont ete backfillees -- aucune ligne n'a `source=NULL`
    en production apres ce plan.
    """

    id: int
    user_id: int
    user_email: str | None
    plan_name: str
    status: str
    source: str | None  # 'stripe' | 'manual_grant' | None (defensive)
    stripe_subscription_id: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime


class AdminSubscriptionsListResponse(BaseModel):
    """Reponse paginee de /admin/subscriptions."""

    rows: list[AdminSubscriptionRow]
    total: int
    page: int
    page_size: int


class SyncSubscriptionRequest(BaseModel):
    """Body de POST /admin/subscriptions/{sub_id}/sync.

    Force-sync se declenche sur demande explicite de l'admin (support
    client, dispute, webhook manque). reason obligatoire pour traca
    identique aux autres mutations admin (min_length=10).
    """

    reason: str = Field(..., min_length=10)


# ---------------------------------------------------------------------------
# Phase 19 — Admin Ops (/admin/etl)
# ---------------------------------------------------------------------------


class EtlPipelineSummary(BaseModel):
    """Per-pipeline aggregate row for the /admin/etl list view.

    One row per pipeline name registered in scheduler.jobs.PIPELINE_MAP.
    last_run_* fields reflect the most recent run regardless of status.
    last_success_at reflects the most recent successful run (may be NULL
    if the pipeline has never succeeded)."""

    pipeline: str
    last_success_at: datetime | None
    last_run_status: str | None  # 'running'|'success'|'failed'|None (never ran)
    last_run_started_at: datetime | None
    last_run_duration_seconds: float | None
    last_run_rows_loaded: int | None
    last_run_error_message: str | None


class EtlPipelineSummaryListResponse(BaseModel):
    rows: list[EtlPipelineSummary]
    total: int


class EtlRunRow(BaseModel):
    """Une ligne d'historique pour un pipeline precis (drill-down)."""

    id: int
    started_at: datetime
    completed_at: datetime | None
    status: str
    total_rows_loaded: int | None
    total_duration_seconds: float | None
    error_message: str | None
    stages: dict[str, Any] | list[Any] | None  # JSONB


class EtlRunHistoryResponse(BaseModel):
    pipeline: str
    rows: list[EtlRunRow]
    total: int
    page: int
    page_size: int


class TriggerEtlRequest(BaseModel):
    """Body for POST /admin/etl/{pipeline_name}/trigger.

    reason is mandatory for audit trail (min_length=10) — mirrors
    Phase 18 SyncSubscriptionRequest."""

    reason: str = Field(..., min_length=10)


# ---------------------------------------------------------------------------
# Phase 19 — Admin Ops (/admin/system)
# ---------------------------------------------------------------------------


class RateLimitBucket(BaseModel):
    bucket: str  # 'ip:1.2.3.4' | 'key:prefix16' | 'admin:42'
    count: int  # requests in the last _WINDOW_SECONDS


class DiskUsageRow(BaseModel):
    mount: str  # '/etl_tmp' or '/'
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    percent_used: float | None
    status: str  # 'ok' | 'unavailable'
    detail: str | None  # populated on 'unavailable'


class ServiceHealth(BaseModel):
    name: str  # 'api' | 'db' | 'scheduler'
    status: str  # 'up' | 'down' | 'stale' | 'unknown'
    detail: str | None


class DbHealth(BaseModel):
    status: str  # 'up' | 'down'
    connections_active: int | None
    connections_idle: int | None
    connections_total: int | None
    db_size_pretty: str | None
    db_size_bytes: int | None
    error: str | None


class SystemStatus(BaseModel):
    """Composite response for GET /v1/admin/system."""

    db: DbHealth
    # {active_buckets: list[RateLimitBucket], total_buckets: int, window_seconds: int}
    rate_limit: dict[str, Any]
    disk: list[DiskUsageRow]
    services: list[ServiceHealth]
    generated_at: datetime

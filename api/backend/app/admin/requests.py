# ruff: noqa: E501
"""Admin — API request log viewer (/admin/requests).

Read-only endpoints over `api_request_log` with typed filters. Requires
AdminUser (50 req/min + is_admin). No audit log write (reads only, per
Phase 15 convention).

Indexes used (added in migration 061):
- idx_api_log_key_ts: supports user_id filter via JOIN on api_keys
- idx_api_log_status_ts: supports status_code filter
- idx_api_log_path_ts: supports path LIKE 'prefix%' (requires ts range)
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.audit import _end_of_day_if_midnight
from app.admin.dependencies import AdminUser
from app.admin.schemas import AdminRequestEntry, AdminRequestsListResponse
from app.common.csv_escape import neutralize_csv_value
from app.common.exceptions import ErreurApiError
from app.common.sql_escape import escape_like
from app.database import get_db

router = APIRouter(prefix="/v1/admin/requests", tags=["admin"])

_CSV_BATCH_SIZE = 500
_CSV_MAX_ROWS = 50_000  # Hard cap to prevent OOM / runaway exports (research pitfall 6)
_CSV_FIELDNAMES = [
    "ts",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "client_ip",
    "user_id",
    "user_email",
    "api_key_id",
    "id",
]


@router.get("", response_model=AdminRequestsListResponse)
async def list_requests(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int | None, Query(ge=1)] = None,
    endpoint: Annotated[str | None, Query(max_length=200)] = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminRequestsListResponse:
    """Liste paginée de api_request_log avec filtres cumulables."""

    # Pitfall 7 from research: endpoint filter requires date_from to stay sargable
    # (path_ts index scan is efficient only when ts range bounds the partition).
    if endpoint is not None and date_from is None:
        raise ErreurApiError(
            422,
            "Filtre endpoint requiert date_from",
            "Préciser date_from pour éviter un scan complet du log",
        )

    date_to_norm = _end_of_day_if_midnight(date_to) if date_to else None
    offset = (page - 1) * page_size

    params = {
        "user_id": user_id,
        # Audit AD-03: escape LIKE wildcards so the endpoint prefix matches
        # literally (paired with ESCAPE '\' in the SQL below).
        "endpoint": escape_like(endpoint) if endpoint is not None else None,
        "status_code": status_code,
        "date_from": date_from,
        "date_to": date_to_norm,
        "offset": offset,
        "limit": page_size,
    }

    data_sql = text(
        """
        SELECT
            r.id,
            r.ts,
            r.method,
            r.path,
            r.status_code,
            r.duration_ms,
            HOST(r.client_ip) AS client_ip,
            r.api_key_id,
            k.user_id,
            u.email AS user_email
        FROM api_request_log r
        LEFT JOIN api_keys k ON k.id = r.api_key_id
        LEFT JOIN users   u ON u.id = k.user_id
        WHERE
            (CAST(:user_id AS INTEGER) IS NULL OR k.user_id = CAST(:user_id AS INTEGER))
            AND (CAST(:endpoint AS TEXT) IS NULL OR r.path LIKE CAST(:endpoint AS TEXT) || '%' ESCAPE '\\')
            AND (CAST(:status_code AS INTEGER) IS NULL OR r.status_code = CAST(:status_code AS INTEGER))
            AND (CAST(:date_from AS TIMESTAMPTZ) IS NULL OR r.ts >= CAST(:date_from AS TIMESTAMPTZ))
            AND (CAST(:date_to AS TIMESTAMPTZ) IS NULL OR r.ts <= CAST(:date_to AS TIMESTAMPTZ))
        ORDER BY r.ts DESC
        OFFSET :offset LIMIT :limit
        """
    )
    count_sql = text(
        """
        SELECT COUNT(*) FROM api_request_log r
        LEFT JOIN api_keys k ON k.id = r.api_key_id
        WHERE
            (CAST(:user_id AS INTEGER) IS NULL OR k.user_id = CAST(:user_id AS INTEGER))
            AND (CAST(:endpoint AS TEXT) IS NULL OR r.path LIKE CAST(:endpoint AS TEXT) || '%' ESCAPE '\\')
            AND (CAST(:status_code AS INTEGER) IS NULL OR r.status_code = CAST(:status_code AS INTEGER))
            AND (CAST(:date_from AS TIMESTAMPTZ) IS NULL OR r.ts >= CAST(:date_from AS TIMESTAMPTZ))
            AND (CAST(:date_to AS TIMESTAMPTZ) IS NULL OR r.ts <= CAST(:date_to AS TIMESTAMPTZ))
        """
    )

    rows_result = await db.execute(data_sql, params)
    rows = [AdminRequestEntry(**dict(r)) for r in rows_result.mappings().all()]

    total_result = await db.execute(count_sql, params)
    total = int(total_result.scalar_one() or 0)

    return AdminRequestsListResponse(
        rows=rows, total=total, page=page, page_size=page_size
    )


async def _fetch_batch(
    db: AsyncSession,
    cursor_id: int | None,
    batch_size: int,
    *,
    user_id: int | None,
    endpoint: str | None,
    status_code: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[dict]:
    """Fetch one batch using keyset pagination on r.id DESC.

    cursor_id: id of the last row emitted in the previous batch (None for first).
    """
    sql = text(
        """
        SELECT
            r.id, r.ts, r.method, r.path, r.status_code, r.duration_ms,
            HOST(r.client_ip) AS client_ip, r.api_key_id,
            k.user_id, u.email AS user_email
        FROM api_request_log r
        LEFT JOIN api_keys k ON k.id = r.api_key_id
        LEFT JOIN users   u ON u.id = k.user_id
        WHERE
            (CAST(:cursor_id AS BIGINT) IS NULL OR r.id < CAST(:cursor_id AS BIGINT))
            AND             (CAST(:user_id AS INTEGER) IS NULL OR k.user_id = CAST(:user_id AS INTEGER))
            AND (CAST(:endpoint AS TEXT) IS NULL OR r.path LIKE CAST(:endpoint AS TEXT) || '%' ESCAPE '\\')
            AND (CAST(:status_code AS INTEGER) IS NULL OR r.status_code = CAST(:status_code AS INTEGER))
            AND (CAST(:date_from AS TIMESTAMPTZ) IS NULL OR r.ts >= CAST(:date_from AS TIMESTAMPTZ))
            AND (CAST(:date_to AS TIMESTAMPTZ) IS NULL OR r.ts <= CAST(:date_to AS TIMESTAMPTZ))
        ORDER BY r.id DESC
        LIMIT :limit
        """
    )
    result = await db.execute(
        sql,
        {
            "cursor_id": cursor_id,
            "user_id": user_id,
            "endpoint": endpoint,
            "status_code": status_code,
            "date_from": date_from,
            "date_to": date_to,
            "limit": batch_size,
        },
    )
    return [dict(r) for r in result.mappings().all()]


async def _stream_requests_csv(
    db: AsyncSession,
    filters: dict,
) -> AsyncGenerator[str, None]:
    """Yield CSV rows in ~500-row batches with UTF-8 BOM and truncation marker."""
    # Initial chunk: BOM + header
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM so Excel recognises the encoding
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue()

    rows_emitted = 0
    cursor_id: int | None = None

    while rows_emitted < _CSV_MAX_ROWS:
        batch = await _fetch_batch(db, cursor_id, _CSV_BATCH_SIZE, **filters)
        if not batch:
            break

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore")
        for row in batch:
            # Normalize None -> "" and datetime -> iso string, then neutralize
            # spreadsheet formulas (audit S13/FA-02): `path` and `user_email`
            # are user-fed, so a leading `=` would execute on open in Excel.
            out = {
                k: neutralize_csv_value(
                    ""
                    if v is None
                    else (v.isoformat() if isinstance(v, datetime) else v)
                )
                for k, v in row.items()
            }
            writer.writerow(out)
            rows_emitted += 1
            if rows_emitted >= _CSV_MAX_ROWS:
                break
        yield buf.getvalue()
        cursor_id = batch[-1]["id"]

    # Truncation marker row if we hit the cap
    if rows_emitted >= _CSV_MAX_ROWS:
        tail = io.StringIO()
        tail_writer = csv.DictWriter(
            tail, fieldnames=_CSV_FIELDNAMES, extrasaction="ignore"
        )
        tail_writer.writerow(
            {
                "ts": "",
                "method": "TRUNCATED",
                "path": f"{_CSV_MAX_ROWS} rows max — ajoutez des filtres pour réduire",
                "status_code": "",
                "duration_ms": "",
                "client_ip": "",
                "user_id": "",
                "user_email": "",
                "api_key_id": "",
                "id": "",
            }
        )
        yield tail.getvalue()


@router.get("/export.csv")
async def export_csv(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int | None, Query(ge=1)] = None,
    endpoint: Annotated[str | None, Query(max_length=200)] = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
) -> StreamingResponse:
    """Export CSV du log filtré. Streamé par batches de 500, cappé à 50 000 rangs."""

    # Validate BEFORE returning the generator: once the streaming response has
    # committed its headers, an HTTPException raised by the generator is swallowed
    # and the client receives a truncated 200 instead of the error.
    if endpoint is not None and date_from is None:
        raise ErreurApiError(
            422,
            "Filtre endpoint requiert date_from",
            "Préciser date_from pour éviter un scan complet du log",
        )

    date_to_norm = _end_of_day_if_midnight(date_to) if date_to else None
    filters = {
        "user_id": user_id,
        # Audit AD-03: same escaping as list_requests (consumed by _fetch_batch).
        "endpoint": escape_like(endpoint) if endpoint is not None else None,
        "status_code": status_code,
        "date_from": date_from,
        "date_to": date_to_norm,
    }

    # X-Truncated header flipped to "true" only if we hit the cap — but we can't know
    # that until the generator finishes, AND FastAPI commits headers before the body.
    # So we set it optimistically to "false" and callers reading the CSV can check the
    # TRUNCATED marker row as a fallback.
    headers = {
        "Content-Disposition": 'attachment; filename="admin_requests.csv"',
        "X-Truncated": "false",
    }
    return StreamingResponse(
        _stream_requests_csv(db, filters),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )

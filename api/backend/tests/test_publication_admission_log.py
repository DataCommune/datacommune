"""A DPE read refused by publication admission says why, without a message (C60).

Every failure is injected into the admission itself
(``guard_snapshot_publication``): a slow reader try-lock trips the real
``asyncio.timeout`` budget, or the try-lock raises a ``DBAPIError`` whose
statement, parameters and driver message quote a visitor value. The log of
every call site that swallows the failure must carry the qualified types, the
constant refusal detail and the SQLSTATE, and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from app.adresse import documents
from app.common import publication_readiness as readiness
from app.logements import router as logements_router
from app.resolve import multi
from tests.scope_doubles import route_hosting

pytestmark = pytest.mark.smoke

_VISITOR = "12 rue sentinelle admission 44999"
_BAN = "44109_7777_00042"
_SQLSTATE = "57P01"


class _Admission:
    """Admission connection double: no pool, the try-lock is patched."""

    async def close(self) -> None:
        return None


async def _checked_out(_bind: object) -> _Admission:
    return _Admission()


class _DriverError(Exception):
    sqlstate = _SQLSTATE


async def _slow_lock(_session: object) -> None:
    await asyncio.sleep(5)


def _db_error() -> DBAPIError:
    return DBAPIError(
        f"SELECT pg_try_advisory_xact_lock_shared(:lock_id) -- {_VISITOR}",
        {"lock_id": 1, "q": _VISITOR},
        _DriverError(f"terminating connection while reading {_VISITOR}"),
    )


def _inject(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    monkeypatch.setattr(readiness, "_admission_connection", _checked_out)
    lock = _slow_lock if failure == "timeout" else AsyncMock(side_effect=_db_error())
    monkeypatch.setattr(readiness, "acquire_publication_read_lock", lock)


async def _admit(*_args: object, **_kwargs: object) -> None:
    await readiness.guard_snapshot_publication(SimpleNamespace(bind=None), ["dpe"])


_EXPECTED = {
    "timeout": (
        "error_type=fastapi.exceptions.HTTPException",
        "cause_type=builtins.TimeoutError",
        "detail='Admission de publication indisponible.'",
    ),
    "dbapi": (
        "error_type=sqlalchemy.exc.DBAPIError",
        f"sqlstate={_SQLSTATE}",
    ),
}


def _app_blob(records: list[logging.LogRecord]) -> str:
    return "\n".join(
        f"{record.getMessage()} {record.args!r} {record.__dict__!r} "
        f"{record.exc_info!r} {record.exc_text!r}"
        for record in records
        if record.name.startswith("app.")
    )


def _assert_logged(records: list[logging.LogRecord], marker: str, failure: str) -> None:
    messages = [r.getMessage() for r in records if marker in r.getMessage()]
    assert len(messages) == 1, messages
    for field in _EXPECTED[failure]:
        assert field in messages[0], messages[0]
    blob = _app_blob(records)
    assert _VISITOR not in blob
    assert "terminating connection" not in blob


@pytest.mark.parametrize("failure", ["timeout", "dbapi"])
async def test_inventory_route_logs_the_admission_failure_without_message(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _inject(monkeypatch, failure)
    monkeypatch.setattr(logements_router, "load_dpe_inventory", _admit)

    response = await client.get(
        "/v1/bien/dpe/inventaire", params={"contexte": _VISITOR}
    )

    # Public contract unchanged: constant 503 Problem Details, no-store.
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "Données DPE momentanément indisponibles" in response.text
    assert _VISITOR not in response.text
    _assert_logged(caplog.records, "lecture documentaire DPE indisponible", failure)


@pytest.mark.parametrize("failure", ["timeout", "dbapi"])
async def test_logements_inventory_block_logs_the_admission_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _inject(monkeypatch, failure)
    monkeypatch.setattr(logements_router, "load_dpe_inventory_result", _admit)

    result = await logements_router._charge_inventaire(_VISITOR)

    assert result.page.state == "unavailable"
    _assert_logged(caplog.records, "inventaire DPE indisponible", failure)


@pytest.mark.parametrize("failure", ["timeout", "dbapi"])
async def test_resolve_dpe_block_logs_the_admission_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _inject(monkeypatch, failure)
    monkeypatch.setattr(multi, "load_dpe_inventory_result", _admit)

    result = await multi._load_dpe_result(
        AsyncMock(), _VISITOR, _VISITOR, correlation_id=None
    )

    assert result.inventory.state == "unavailable"
    _assert_logged(caplog.records, "resolve: lecture DPE indisponible", failure)


@pytest.mark.parametrize("failure", ["timeout", "dbapi"])
async def test_address_documents_log_the_admission_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    _inject(monkeypatch, failure)
    monkeypatch.setattr(documents, "_demarrer_instantane", _admit)

    with pytest.raises(documents.DocumentsIndisponiblesError):
        await documents.lire_documents_adresse(
            AsyncMock(),
            _BAN,
            1,
            None,
            route_hosting("ban", "dpe"),
            date(2026, 10, 13),
        )

    _assert_logged(caplog.records, "documents DPE indisponibles", failure)


def test_only_the_constant_refusal_details_are_described() -> None:
    reflective = HTTPException(503, f"Indisponible pour {_VISITOR}")
    structured = HTTPException(503, {"q": _VISITOR})

    assert _VISITOR not in readiness.describe_read_failure(reflective)
    assert "detail=" not in readiness.describe_read_failure(structured)
    assert readiness.describe_read_failure(ValueError(_VISITOR)) == (
        "error_type=builtins.ValueError"
    )

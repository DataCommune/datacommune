"""Unhandled errors: no visitor input in the log, no internal detail in the reply.

A database driver message can quote a SQL parameter, hence an address or an
identifier the visitor typed. Outside DEBUG such an error is logged with its
type, route template, request id and frames, never its message.
"""

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError

from app.common.exceptions import (
    ErreurApiError,
    ErrorHandlingMiddleware,
    register_exception_handlers,
)
from app.config import settings

pytestmark = pytest.mark.unit

_SENTINEL = "12 rue Sentinelle"
_LOGGER = "app.common.exceptions"
_EVENT = "unhandled_exception"


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(ErrorHandlingMiddleware)

    @app.get("/boom/{ident}")
    async def boom(ident: str):
        try:
            raise ValueError(f"driver rejected {_SENTINEL}")
        except ValueError as driver_error:
            raise DBAPIError(
                "SELECT * FROM adresses WHERE label = $1",
                (_SENTINEL,),
                driver_error,
            ) from driver_error

    @app.get("/plain/{ident}")
    async def plain(ident: str):
        raise RuntimeError("plain failure")

    @app.get("/metier")
    async def metier():
        raise ErreurApiError(404, "Adresse introuvable", "Aucune adresse BAN.")

    return app


async def _get(path: str):
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def _record(caplog) -> logging.LogRecord:
    records = [r for r in caplog.records if r.msg.startswith(_EVENT)]
    assert len(records) == 1
    return records[0]


def _whole(record: logging.LogRecord) -> str:
    return " ".join(
        [
            record.getMessage(),
            repr(record.args),
            repr(record.__dict__),
            repr(record.exc_info),
            repr(record.exc_text),
        ]
    )


async def test_database_error_is_logged_without_its_message(caplog, monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    caplog.set_level(logging.ERROR, logger=_LOGGER)

    response = await _get("/boom/abc123")

    record = _record(caplog)
    assert _SENTINEL not in _whole(record)
    assert "/boom/abc123" not in _whole(record)
    assert record.exc_info is None
    assert record.error_type == "sqlalchemy.exc.DBAPIError"
    assert record.route == "/boom/{ident}"
    assert record.method == "GET"
    assert record.request_id == response.headers["X-Request-ID"]
    # The rendered line carries them too: the formatter drops stdlib `extra`.
    assert f"request_id={record.request_id}" in record.getMessage()
    assert "route=/boom/{ident}" in record.getMessage()
    assert "<message redacted>" in record.getMessage()
    # Frames stay, each chained exception is closed by its type only.
    assert "in boom" in record.traceback
    assert "builtins.ValueError: <message redacted>" in record.traceback
    assert "sqlalchemy.exc.DBAPIError: <message redacted>" in record.traceback


async def test_reply_to_the_visitor_carries_no_internal_detail(caplog, monkeypatch):
    monkeypatch.setattr(settings, "debug", False)

    response = await _get("/boom/abc123")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "about:blank",
        "title": "Erreur interne du serveur",
        "status": 500,
        # Route template, never the raw path: it would quote the visitor value
        # (261012-lgx I2).
        "instance": "/boom/{ident}",
    }
    assert "abc123" not in response.text
    for internal in (_SENTINEL, "SELECT", "DBAPIError", "Traceback"):
        assert internal not in response.text


async def test_debug_keeps_the_full_database_message(caplog, monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    caplog.set_level(logging.ERROR, logger=_LOGGER)

    await _get("/boom/abc123")

    record = _record(caplog)
    assert record.exc_info is not None
    assert _SENTINEL in str(record.exc_info[1])
    assert record.route == "/boom/{ident}"


async def test_other_errors_keep_their_frames_without_their_message(
    caplog, monkeypatch
):
    # C60: outside DEBUG no exception message of any type is logged, frames and
    # types only (the message can quote a cursor or an address).
    monkeypatch.setattr(settings, "debug", False)
    caplog.set_level(logging.ERROR, logger=_LOGGER)

    response = await _get("/plain/abc123")

    record = _record(caplog)
    assert response.status_code == 500
    assert record.error_type == "builtins.RuntimeError"
    assert "RuntimeError" in record.traceback
    # The frame shows the source line (a literal here), never the runtime message.
    assert "RuntimeError: <message redacted>" in record.traceback
    assert "RuntimeError: plain failure" not in _whole(record)
    assert record.route == "/plain/{ident}"
    assert "/plain/abc123" not in record.getMessage()


async def test_business_errors_keep_their_format(caplog):
    caplog.set_level(logging.ERROR, logger=_LOGGER)

    response = await _get("/metier")

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Adresse introuvable",
        "status": 404,
        "detail": "Aucune adresse BAN.",
        "instance": "/metier",
    }
    assert "X-Request-ID" not in response.headers
    assert not [r for r in caplog.records if r.msg.startswith(_EVENT)]

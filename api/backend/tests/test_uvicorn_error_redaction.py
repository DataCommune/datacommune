"""Exceptions that escape the application: no visitor input in the server log.

ErrorHandlingMiddleware logs a 500 without any exception message (C60,
261012-jrn). An exception that escapes it (an outer middleware, a failure after
the response) is logged by uvicorn ("Exception in ASGI application") or by
asyncio ("Task exception was never retrieved", "Exception in callback"), with
its message and the message of its causes, which can quote an address or a SQL
parameter. Outside DEBUG those loggers keep the frames and types only.

The output checked is what the configured handlers write: with the uvicorn
CLI, uvicorn configures its own handler first (Config.__init__), then the
application lifespan calls configure_logging; uvicorn.error is emitted by that
uvicorn handler, asyncio by the root handler of configure_logging. Each test
starts from the server loggers of a fresh process and points the handlers it
built at its own buffer: a handler keeps the sys.stderr of its creation, so
another test's uvicorn Config (test_golden_preview) would otherwise leave a
handler writing outside any capture.
"""

import asyncio
import copy
import io
import logging
import logging.config

import pytest
import structlog
from fastapi import FastAPI
from uvicorn.config import LOGGING_CONFIG, Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from app.common.exceptions import ErrorHandlingMiddleware, register_exception_handlers
from app.config import settings
from app.logging_config import configure_logging

pytestmark = pytest.mark.unit

_INPUT = "12 rue Sentinelle"
_CAUSE_INPUT = "44000 Nantes-Sentinelle"
_REDACTED = "<message redacted>"
_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "uvicorn.asgi",
    "uvicorn.unlisted",
    "asyncio",
    "httpx",
    "httpcore",
)


@pytest.fixture
def isolated_logging():
    """Server loggers of a fresh process; global logging state restored after.

    An earlier uvicorn dictConfig in the suite leaves a handler on "uvicorn"
    (propagate False): without this reset the "app_only" topology would not be
    the one under test.
    """
    root = logging.getLogger()
    saved_root = (list(root.handlers), list(root.filters), root.level)
    saved = {
        name: (
            list(logger.handlers),
            list(logger.filters),
            logger.level,
            logger.propagate,
            logger.disabled,
        )
        for name in _LOGGERS
        for logger in [logging.getLogger(name)]
    }
    # A previous test can leave uvicorn's handler bound to the stream that was
    # active when Config was created. Start every topology from a neutral
    # server-logger state so app_only exercises the root handler it configures.
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.filters.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        logger.disabled = False
    yield
    root.handlers[:], root.filters[:], level = saved_root
    root.setLevel(level)
    for name, (handlers, filters, level, propagate, disabled) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled
    structlog.reset_defaults()


def _uvicorn_logging() -> None:
    logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))


def _capture_handler_streams() -> io.StringIO:
    """Point every stream handler of the root and server loggers at one buffer.

    Only the handlers built by the configuration under test are there (the
    fixture empties the server loggers, dictConfig replaces the root ones).
    """
    stream = io.StringIO()
    for logger in [logging.getLogger(), *map(logging.getLogger, _LOGGERS)]:
        for handler in logger.handlers:
            if type(handler) is logging.StreamHandler:
                handler.setStream(stream)
    return stream


def _configure(topology: str, json_output: bool) -> io.StringIO:
    if topology == "uvicorn_cli":
        # Production order: uvicorn's dictConfig, then the lifespan's.
        _uvicorn_logging()
        configure_logging(json_output=json_output)
    elif topology == "app_only":
        configure_logging(json_output=json_output)
    else:  # uvicorn_after: a later uvicorn dictConfig must not undo the filter
        configure_logging(json_output=json_output)
        _uvicorn_logging()
    return _capture_handler_streams()


def _escaped_error() -> RuntimeError:
    """An error whose message and whose cause's message quote visitor input."""
    try:
        try:
            raise ValueError(f"driver rejected {_CAUSE_INPUT}")
        except ValueError as cause:
            raise RuntimeError(f"outer middleware rejected {_INPUT}") from cause
    except RuntimeError as exc:
        return exc


def _uvicorn_exception() -> str:
    try:
        raise _escaped_error()
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application\n")
    return "Exception in ASGI application"


def _uvicorn_child_exception() -> str:
    try:
        raise _escaped_error()
    except RuntimeError:
        logging.getLogger("uvicorn.asgi").exception("ASGI callable failed")
    return "ASGI callable failed"


def _uvicorn_exception_as_argument() -> str:
    logging.getLogger("uvicorn.error").error("Server failure: %s", _escaped_error())
    return "Server failure:"


def _asyncio_never_retrieved() -> str:
    # asyncio's own handler: the message quotes repr(future), hence the error.
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        future.set_exception(_escaped_error())
        loop.default_exception_handler(
            {
                "message": "Task exception was never retrieved",
                "exception": future.exception(),
                "future": future,
            }
        )
    finally:
        loop.close()
    return "Task exception was never retrieved"


def _asyncio_callback() -> str:
    def failing_callback() -> None:
        raise _escaped_error()

    loop = asyncio.new_event_loop()
    try:
        loop.call_soon(failing_callback)
        loop.run_until_complete(asyncio.sleep(0.01))
    finally:
        loop.close()
    return "Exception in callback"


EMITTERS = [
    pytest.param(_uvicorn_exception, True, id="uvicorn.error-exception"),
    pytest.param(_uvicorn_child_exception, True, id="uvicorn.asgi-exception"),
    pytest.param(_uvicorn_exception_as_argument, False, id="uvicorn.error-argument"),
    pytest.param(_asyncio_never_retrieved, True, id="asyncio-never-retrieved"),
    pytest.param(_asyncio_callback, True, id="asyncio-callback"),
]
TOPOLOGIES = ["uvicorn_cli", "app_only", "uvicorn_after"]


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "console"])
@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize(("emit", "with_traceback"), EMITTERS)
def test_escaped_exception_logs_no_input(
    isolated_logging, monkeypatch, emit, with_traceback, topology, json_output
):
    monkeypatch.setattr(settings, "debug", False)
    stream = _configure(topology, json_output)

    marker = emit()

    output = stream.getvalue()
    assert marker in output  # the capture holds the emitting handler
    assert _INPUT not in output
    assert _CAUSE_INPUT not in output
    assert _REDACTED in output
    if with_traceback:
        # Frames and types of the whole chain stay.
        assert "in _escaped_error" in output
        assert f"builtins.ValueError: {_REDACTED}" in output
        assert f"builtins.RuntimeError: {_REDACTED}" in output


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "console"])
@pytest.mark.parametrize("topology", ["uvicorn_cli", "app_only"])
def test_an_unlisted_server_logger_is_held_by_the_handlers(
    isolated_logging, monkeypatch, topology, json_output
):
    # No filter on this logger itself: the emitting handler (uvicorn's under
    # the CLI, the root one otherwise) holds it.
    monkeypatch.setattr(settings, "debug", False)
    stream = _configure(topology, json_output)

    try:
        raise _escaped_error()
    except RuntimeError:
        logging.getLogger("uvicorn.unlisted").exception("Unlisted server failure")

    output = stream.getvalue()
    assert "Unlisted server failure" in output
    assert _INPUT not in output
    assert _CAUSE_INPUT not in output
    assert f"builtins.ValueError: {_REDACTED}" in output


@pytest.mark.parametrize("topology", TOPOLOGIES)
@pytest.mark.parametrize(("emit", "with_traceback"), EMITTERS)
def test_debug_keeps_the_exception_messages(
    isolated_logging, monkeypatch, emit, with_traceback, topology
):
    monkeypatch.setattr(settings, "debug", True)
    stream = _configure(topology, json_output=True)

    emit()

    output = stream.getvalue()
    assert _INPUT in output
    if with_traceback:
        assert _CAUSE_INPUT in output


# --- End to end: uvicorn's own error path ----------------------------------


class _RaisingOuterMiddleware:
    """Pure ASGI middleware outside ErrorHandlingMiddleware, failing after the
    response (as a failing close would)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        await self.app(scope, receive, send)
        if scope["type"] == "http":
            raise _escaped_error()


def _asgi_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(_RaisingOuterMiddleware)  # last added = outermost

    @app.get("/adresse/{ident}")
    async def adresse(ident: str):
        return {"ok": True}

    return app


class _Transport(asyncio.Transport):
    def __init__(self) -> None:
        super().__init__()
        self.buffer = b""
        self.closed = False

    def get_extra_info(self, name, default=None):
        extra = {
            "sockname": ("127.0.0.1", 8000),
            "peername": ("127.0.0.1", 50000),
            "sslcontext": False,
        }
        return extra.get(name, default)

    def write(self, data: bytes) -> None:
        self.buffer += data

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    def pause_reading(self) -> None:
        pass

    def resume_reading(self) -> None:
        pass

    def set_protocol(self, protocol) -> None:
        pass


async def _serve_one_request() -> bytes:
    # Config.__init__ runs uvicorn's dictConfig, as the CLI does before the
    # lifespan; configure_logging then runs as in app.main.lifespan.
    config = Config(app=_asgi_app(), lifespan="off", log_config=LOGGING_CONFIG)
    config.load()
    server_state = ServerState()
    protocol = H11Protocol(
        config=config,
        server_state=server_state,
        app_state={},
        _loop=asyncio.get_running_loop(),
    )
    transport = _Transport()
    protocol.connection_made(transport)
    protocol.data_received(
        b"GET /adresse/12-rue-Sentinelle HTTP/1.1\r\nHost: test\r\n\r\n"
    )
    for _ in range(500):
        await asyncio.sleep(0.01)
        if not server_state.tasks:
            break
    protocol.connection_lost(None)
    return transport.buffer


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "console"])
async def test_uvicorn_logs_an_escaped_exception_without_input(
    isolated_logging, monkeypatch, json_output
):
    monkeypatch.setattr(settings, "debug", False)
    original_configure = Config.configure_logging

    captured: list[io.StringIO] = []

    def configure_like_production(self) -> None:
        original_configure(self)
        configure_logging(json_output=json_output)
        captured.append(_capture_handler_streams())

    monkeypatch.setattr(Config, "configure_logging", configure_like_production)

    response = await _serve_one_request()

    output = captured[0].getvalue()
    assert response.startswith(b"HTTP/1.1 200")
    assert "Exception in ASGI application" in output
    assert "in _escaped_error" in output
    assert _INPUT not in output
    assert _CAUSE_INPUT not in output
    assert f"builtins.RuntimeError: {_REDACTED}" in output


async def test_uvicorn_debug_keeps_the_escaped_exception(isolated_logging, monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    original_configure = Config.configure_logging

    captured: list[io.StringIO] = []

    def configure_like_production(self) -> None:
        original_configure(self)
        configure_logging(json_output=True)
        captured.append(_capture_handler_streams())

    monkeypatch.setattr(Config, "configure_logging", configure_like_production)

    await _serve_one_request()

    output = captured[0].getvalue()
    assert "Exception in ASGI application" in output
    assert _INPUT in output
    assert _CAUSE_INPUT in output

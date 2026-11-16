"""Structured logging configuration using structlog + stdlib.

Uses ProcessorFormatter to wrap stdlib logging, so ALL existing
logging.getLogger() calls automatically produce structured output
(JSON in production, colored console in dev). No per-file migration needed.
"""

import logging
import logging.config
from collections.abc import Mapping

import structlog

from app.common.error_redaction import (
    REDACTED,
    exception_chain,
    qualified_type,
    traceback_without_messages,
)
from app.config import settings

# Loggers that report an exception escaping the application, outside
# ErrorHandlingMiddleware (an outer middleware, a failure after the response, a
# background task): uvicorn ("Exception in ASGI application") and asyncio
# ("Task exception was never retrieved", "Exception in callback").
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.asgi", "asyncio")
_SERVER_LOGGER_ROOTS = ("uvicorn", "asyncio")
# A shorter str(exc) is not searched in the message: it would garble the
# message for a negligible leak (repr(exc), always searched, holds the type).
_MIN_SCRUBBED_STR = 4


def _placeholder(exc: BaseException) -> str:
    return f"{qualified_type(exc)}: {REDACTED}"


def _renderings(exc: BaseException) -> list[str]:
    renderings = []
    for render, minimum in ((repr, 1), (str, _MIN_SCRUBBED_STR)):
        try:
            text = render(exc)
        except Exception:  # noqa: BLE001 - a broken __repr__ must not break logging
            text = ""
        if len(text) >= minimum:
            renderings.append(text)
    return renderings


_Args = tuple[object, ...] | Mapping[str, object]


def _without_exceptions(args: _Args | None) -> _Args | None:
    if isinstance(args, Mapping):
        return {
            key: _placeholder(value) if isinstance(value, BaseException) else value
            for key, value in args.items()
        }
    if isinstance(args, tuple):
        return tuple(
            _placeholder(value) if isinstance(value, BaseException) else value
            for value in args
        )
    return args


def _redact_record(record: logging.LogRecord) -> None:
    if isinstance(record.msg, BaseException):
        record.msg = _placeholder(record.msg)
    record.args = _without_exceptions(record.args)
    exc = record.exc_info[1] if record.exc_info else None
    if exc is None:
        return
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string must not break logging
        message = str(record.msg)
    for error in exception_chain(exc):
        for rendering in _renderings(error):
            message = message.replace(rendering, _placeholder(error))
    # Every formatter renders the message (uvicorn's, structlog's): the frames
    # travel in it, exc_info is dropped so no formatter prints the messages.
    record.msg = (
        message.rstrip("\n") + "\n" + traceback_without_messages(exc).rstrip("\n")
    )
    record.args = ()
    record.exc_info = None
    record.exc_text = None


class ServerExceptionRedactionFilter(logging.Filter):
    """Outside DEBUG, a server record keeps exception frames and types only (C60).

    The exception and its causes can quote visitor input (a SQL parameter, an
    address): exc_info becomes the message-less traceback of the whole chain,
    appended to the message; an exception passed as an argument or quoted by
    the message (asyncio quotes repr(future)) is replaced by its type. Records
    of other loggers, and every record with DEBUG, are untouched.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if settings.debug or isinstance(record.msg, dict):  # dict: structlog's own
            return True
        if any(
            record.name == root or record.name.startswith(root + ".")
            for root in _SERVER_LOGGER_ROOTS
        ):
            _redact_record(record)
        return True


# One instance: configure_logging can run again without stacking filters.
_SERVER_EXCEPTION_FILTER = ServerExceptionRedactionFilter()


def _filter_server_loggers() -> None:
    """Hold the filter on the server loggers and on the handlers they own.

    Under the uvicorn CLI, uvicorn's dictConfig runs first (Config.__init__)
    and its "uvicorn" handler, which emits uvicorn.error, survives the
    application's dictConfig. A logger filter also survives a later uvicorn
    dictConfig, which replaces the handlers but keeps the logger filters.
    """
    for name in _SERVER_LOGGERS:
        logger = logging.getLogger(name)
        logger.addFilter(_SERVER_EXCEPTION_FILTER)
        for handler in logger.handlers:
            handler.addFilter(_SERVER_EXCEPTION_FILTER)


def configure_logging(*, json_output: bool = False, log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging.

    Args:
        json_output: True for JSON (production), False for console (dev).
        log_level: Root log level.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structured": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        renderer,
                    ],
                    "foreign_pre_chain": shared_processors,
                },
            },
            # The root handler emits asyncio and, without uvicorn's own
            # logging config, uvicorn.error.
            "filters": {
                "server_exceptions": {"()": ServerExceptionRedactionFilter},
            },
            "handlers": {
                "console": {
                    "level": log_level,
                    "class": "logging.StreamHandler",
                    "formatter": "structured",
                    "filters": ["server_exceptions"],
                },
            },
            "root": {
                "handlers": ["console"],
                "level": log_level,
            },
            # Uvicorn access logs and HTTPX/HTTPCore request logs include raw
            # URLs. Those URLs can carry addresses, coordinates and parcel IDs.
            # Application code emits explicit metadata-only events instead.
            "loggers": {
                "uvicorn.access": {
                    "handlers": [],
                    "level": "CRITICAL",
                    "propagate": False,
                },
                "httpx": {
                    "handlers": [],
                    "level": "CRITICAL",
                    "propagate": False,
                },
                "httpcore": {
                    "handlers": [],
                    "level": "CRITICAL",
                    "propagate": False,
                },
            },
        }
    )
    _filter_server_loggers()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

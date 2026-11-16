"""Render an exception without its message (C60).

A message can quote a value the visitor typed (an address, a key, a SQL
parameter), whichever library wrote it: outside DEBUG only frames and types
are logged. Pure helpers, shared by the 500 handler (app.common.exceptions)
and the server loggers' filter (app.logging_config).
"""

import traceback

REDACTED = "<message redacted>"


def exception_chain(exc: BaseException) -> list[BaseException]:
    """The exception and its causes/contexts, outermost first, cycle-safe."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return chain


def qualified_type(exc: BaseException) -> str:
    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}"


def traceback_without_messages(exc: BaseException) -> str:
    """Frames of every chained exception, each closed by its type only.

    A frame is its file, line, function and source line (code); never its
    locals. Notes and the members of an exception group are never printed.
    """
    lines: list[str] = []
    for error in reversed(exception_chain(exc)):
        lines.append("Traceback (most recent call last):\n")
        lines.extend(traceback.format_tb(error.__traceback__))
        lines.append(f"{qualified_type(error)}: {REDACTED}\n")
    return "".join(lines)

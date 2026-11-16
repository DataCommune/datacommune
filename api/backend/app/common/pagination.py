"""Cursor-based pagination helpers for phases 5-7."""

from __future__ import annotations

import binascii
from base64 import urlsafe_b64decode, urlsafe_b64encode

from pydantic import BaseModel

from .exceptions import ErreurApiError
from .schemas import PaginationMeta

# Hard ceiling on decoded offsets: cursors are opaque server-generated tokens,
# any value outside [0, OFFSET_MAX] means a forged or corrupted cursor.
# Prevents negative-offset PostgreSQL errors and timeout-inducing deep scans.
OFFSET_MAX = 1_000_000


def encode_cursor(value: int) -> str:
    """Encode an integer offset into an opaque cursor string."""
    return urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    """Decode an opaque cursor string back to an integer offset.

    Raises ErreurApiError(400) if the cursor is malformed, decodes to a
    negative offset, or exceeds OFFSET_MAX.
    """
    padded = cursor + "=" * (4 - len(cursor) % 4)
    # The error is raised after the try, detached from the decoding error,
    # whose message quotes the decoded cursor, hence any text a visitor
    # encoded in it (C60): never ``from exc``.
    value: int | None
    try:
        value = int(urlsafe_b64decode(padded.encode()).decode())
    except (binascii.Error, UnicodeDecodeError, ValueError):
        value = None
    if value is None or value < 0 or value > OFFSET_MAX:
        raise ErreurApiError(400, "Curseur invalide")
    return value


def decode_pagination(curseur: str | None) -> int:
    """Decode a pagination cursor to an offset, defaulting to 0."""
    return decode_cursor(curseur) if curseur else 0


def encode_cursor_str(value: str) -> str:
    """Encode a string value into an opaque cursor token (base64url, no padding)."""
    return urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_cursor_str(cursor: str | None) -> str | None:
    """Decode an opaque cursor token back to the original string value.

    Returns None if cursor is None or empty.
    Raises ErreurApiError(400) if the cursor is malformed.
    """
    if not cursor:
        return None
    padded = cursor + "=" * (4 - len(cursor) % 4)
    # Raised detached from the decoding error, as decode_cursor (C60).
    try:
        return urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError):
        pass
    raise ErreurApiError(400, "Curseur invalide")


def build_page[T: BaseModel](
    rows: list[dict],
    schema: type[T],
    limite: int,
    offset: int,
) -> tuple[list[T], PaginationMeta]:
    """Build a paginated page from raw rows.

    Caller MUST fetch limite + 1 rows from DB.
    The extra row is used to detect whether a next page exists.
    """
    has_next = len(rows) > limite
    if has_next:
        rows = rows[:limite]

    items = [schema(**row) for row in rows]
    curseur_suivant = encode_cursor(offset + limite) if has_next else None
    pagination = PaginationMeta(
        curseur_suivant=curseur_suivant,
        taille=len(items),
        limite=limite,
    )
    return items, pagination

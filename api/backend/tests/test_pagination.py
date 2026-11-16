"""Unit tests for common/pagination.py -- cursor encode/decode + page helpers."""

from base64 import urlsafe_b64encode

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.unit

from app.common.exceptions import ErreurApiError
from app.common.pagination import (
    OFFSET_MAX,
    build_page,
    decode_cursor,
    decode_cursor_str,
    decode_pagination,
    encode_cursor,
)


class _DummySchema(BaseModel):
    id: int
    name: str


class TestCursorRoundTrip:
    """Verify encode/decode is a perfect round-trip."""

    @pytest.mark.parametrize("value", [0, 1, 42, 100, 9999, 1000000])
    def test_roundtrip(self, value: int):
        cursor = encode_cursor(value)
        assert decode_cursor(cursor) == value

    def test_cursor_is_url_safe(self):
        cursor = encode_cursor(12345)
        assert all(c.isalnum() or c in "-_" for c in cursor)

    @pytest.mark.parametrize(
        "bad",
        ["not-a-valid-cursor!!!", "bogus", "@@@@", "!!!", "zzzzzzzzz"],
    )
    def test_decode_invalid_raises_400(self, bad: str):
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor(bad)
        assert exc_info.value.code == 400
        assert exc_info.value.message == "Curseur invalide"

    @pytest.mark.parametrize("bad", ["bogus", "not-a-valid-cursor!!!", "zzzzzzzzz"])
    def test_decode_cursor_str_invalid_raises_400(self, bad: str):
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor_str(bad)
        assert exc_info.value.code == 400

    def test_decode_cursor_str_none_returns_none(self):
        assert decode_cursor_str(None) is None
        assert decode_cursor_str("") is None


class TestCursorBounds:
    """decode_cursor: offset must be within [0, OFFSET_MAX] (audit L2 findings)."""

    def test_negative_offset_rejected(self):
        cursor = encode_cursor(-1)
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor(cursor)
        assert exc_info.value.code == 400
        assert "Curseur invalide" in exc_info.value.message

    @pytest.mark.parametrize("value", [-1000000, -42])
    def test_negative_offset_rejected_large(self, value: int):
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor(encode_cursor(value))
        assert exc_info.value.code == 400

    def test_offset_beyond_plafond_rejected(self):
        cursor = encode_cursor(OFFSET_MAX + 1)
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor(cursor)
        assert exc_info.value.code == 400

    def test_offset_at_plafond_accepted(self):
        cursor = encode_cursor(OFFSET_MAX)
        assert decode_cursor(cursor) == OFFSET_MAX

    def test_corrupted_cursor_bytes_rejected(self):
        # Valid base64url but decodes to non-UTF8 garbage
        raw = urlsafe_b64encode(b"\xff\xfe\x00garbage").decode().rstrip("=")
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor(raw)
        assert exc_info.value.code == 400

    def test_corrupted_cursor_non_integer_rejected(self):
        # Valid base64url, valid UTF-8, but not an integer
        raw = urlsafe_b64encode(b"not-an-int").decode().rstrip("=")
        with pytest.raises(ErreurApiError) as exc_info:
            decode_cursor(raw)
        assert exc_info.value.code == 400

    def test_decode_pagination_beyond_plafond_rejected(self):
        cursor = encode_cursor(OFFSET_MAX * 10)
        with pytest.raises(ErreurApiError):
            decode_pagination(cursor)


class TestDecodePagination:
    """decode_pagination: cursor string | None -> int offset."""

    def test_none_returns_zero(self):
        assert decode_pagination(None) == 0

    def test_valid_cursor(self):
        cursor = encode_cursor(42)
        assert decode_pagination(cursor) == 42

    def test_zero_cursor(self):
        cursor = encode_cursor(0)
        assert decode_pagination(cursor) == 0


class TestBuildPage:
    """build_page: slice rows, build PaginationMeta."""

    def _make_rows(self, n: int) -> list[dict]:
        return [{"id": i, "name": f"item-{i}"} for i in range(n)]

    def test_full_page_has_next(self):
        rows = self._make_rows(6)  # limite+1
        items, pagination = build_page(rows, _DummySchema, limite=5, offset=0)
        assert len(items) == 5
        assert pagination.curseur_suivant is not None
        assert pagination.taille == 5

    def test_partial_page_no_next(self):
        rows = self._make_rows(3)
        items, pagination = build_page(rows, _DummySchema, limite=5, offset=0)
        assert len(items) == 3
        assert pagination.curseur_suivant is None
        assert pagination.taille == 3

    def test_empty_rows(self):
        items, pagination = build_page([], _DummySchema, limite=5, offset=0)
        assert len(items) == 0
        assert pagination.curseur_suivant is None
        assert pagination.taille == 0

    def test_exact_limit_no_next(self):
        rows = self._make_rows(5)
        items, pagination = build_page(rows, _DummySchema, limite=5, offset=0)
        assert len(items) == 5
        assert pagination.curseur_suivant is None

    def test_schema_instantiation(self):
        rows = [{"id": 1, "name": "hello"}]
        items, _ = build_page(rows, _DummySchema, limite=5, offset=0)
        assert isinstance(items[0], _DummySchema)
        assert items[0].id == 1
        assert items[0].name == "hello"

    def test_offset_propagated(self):
        rows = self._make_rows(6)  # has next
        _, pagination = build_page(rows, _DummySchema, limite=5, offset=10)
        assert pagination.curseur_suivant is not None
        assert decode_cursor(pagination.curseur_suivant) == 15

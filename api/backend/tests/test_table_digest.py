"""Pure part of scripts/table_digest.py: row encoding, digest, column checks."""

import hashlib

import pytest

from scripts.table_digest import digest_rows, encode_row, select_columns

ROWS = [
    ("44001", 2026, None, "12.50"),
    ("44002", 2026, 7, "0.00"),
    ("85001", 2026, 3, None),
]


def test_same_rows_give_the_same_digest():
    assert digest_rows(iter(ROWS)) == digest_rows(list(ROWS))
    count, sha = digest_rows(ROWS)
    assert count == 3
    assert len(sha) == 64 and int(sha, 16) >= 0


def test_row_order_changes_the_digest():
    assert digest_rows(ROWS)[1] != digest_rows(list(reversed(ROWS)))[1]


def test_one_changed_value_changes_the_digest():
    changed = [ROWS[0], ("44002", 2026, 8, "0.00"), ROWS[2]]
    assert digest_rows(ROWS)[1] != digest_rows(changed)[1]


def test_null_is_not_the_empty_string_nor_the_text_none():
    assert encode_row((None,)) != encode_row(("",))
    assert encode_row((None,)) != encode_row(("None",))


def test_values_are_separated_unambiguously():
    assert encode_row(("a\tb", "c")) != encode_row(("a", "b\tc"))
    assert encode_row(("a\nb",)) != encode_row(("a", "b"))


def test_empty_table_digest_is_the_empty_sha256():
    assert digest_rows([]) == (0, hashlib.sha256(b"").hexdigest())


def test_unknown_order_column_is_refused():
    with pytest.raises(ValueError, match="unknown column"):
        select_columns(["id", "code_commune", "annee"], ["code_commune", "x"], [])


def test_unknown_excluded_column_is_refused():
    with pytest.raises(ValueError, match="unknown column"):
        select_columns(["id", "code_commune"], ["code_commune"], ["idx"])


def test_order_column_cannot_be_excluded():
    with pytest.raises(ValueError, match="excluded"):
        select_columns(["id", "code_commune"], ["code_commune"], ["code_commune"])


def test_missing_table_is_refused():
    with pytest.raises(ValueError, match="no column"):
        select_columns([], ["code_commune"], [])


def test_selected_columns_keep_table_order_minus_exclusions():
    assert select_columns(
        ["id", "code_commune", "annee", "taux"], ["code_commune", "annee"], ["id"]
    ) == ["code_commune", "annee", "taux"]

"""Audit S13 / FA-02 — CSV formula injection neutralization.

A cell whose first character is one of `= + - @ TAB CR` is interpreted as a
formula by Excel / LibreOffice / Google Sheets and can execute on open. Every
export that produces a spreadsheet-consumable file must run its cell values
through neutralize_csv_value() first.
"""

import pytest

from app.common.csv_escape import neutralize_csv_value


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r"])
def test_leading_trigger_is_prefixed_with_apostrophe(trigger):
    payload = f"{trigger}cmd|' /C calc'!A0"
    assert neutralize_csv_value(payload) == f"'{payload}"


def test_plain_value_is_untouched():
    assert neutralize_csv_value("/v1/dpe") == "/v1/dpe"
    assert neutralize_csv_value("user@example.com") == "user@example.com"


def test_trigger_not_in_first_position_is_untouched():
    assert neutralize_csv_value("a=b") == "a=b"


def test_none_becomes_empty_string():
    assert neutralize_csv_value(None) == ""


def test_non_string_values_pass_through_unchanged():
    """Numbers and datetimes are not attacker-controlled text — keep their type."""
    assert neutralize_csv_value(42) == 42
    assert neutralize_csv_value(12.5) == 12.5


def test_empty_string_is_untouched():
    assert neutralize_csv_value("") == ""

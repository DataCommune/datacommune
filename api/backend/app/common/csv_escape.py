"""Shared CSV formula-injection neutralization (audit S13 / FA-02).

A spreadsheet (Excel, LibreOffice, Google Sheets) treats a cell whose first
character is `=`, `+`, `-`, `@`, TAB or CR as a formula and may execute it when
the file is opened. Any export fed by user-controlled text must run its cell
values through neutralize_csv_value() before writing them.

Mirrors `frontend/src/lib/utils/csv-escape.ts` — keep the trigger set in sync.
"""

from typing import Any

# OWASP CSV injection trigger set: leading TAB and CR also start a formula in
# some spreadsheet importers, not just the four operator characters.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_value(value: Any) -> Any:
    """Prefix an apostrophe when a text cell would be parsed as a formula.

    None becomes the empty string. Non-string values (numbers, datetimes) are
    returned unchanged: they carry no attacker-controlled text and keeping
    their type lets the caller format them as it already does.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if value.startswith(FORMULA_TRIGGERS):
        return f"'{value}"
    return value

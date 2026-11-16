"""Field-by-field comparison logic with type-aware tolerances."""

from __future__ import annotations

import re
import unicodedata
from enum import Enum


class FieldType(Enum):
    EXACT = "exact"
    TEXT_NORM = "text_norm"
    NUMERIC = "numeric"
    COORD = "coord"
    DATE = "date"
    SKIP = "skip"


def normalize_text(value: str | None) -> str:
    """Lower + strip + remove accents + collapse whitespace."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def compare_field(  # noqa: C901
    db_val: object,
    gov_val: object,
    field_type: FieldType,
    tolerance: float = 0.0,
) -> tuple[bool, str]:
    """Compare a single field. Returns (match, description)."""
    if field_type is FieldType.SKIP:
        return True, "skipped"

    if db_val is None and gov_val is None:
        return True, "both null"
    if db_val is None or gov_val is None:
        return False, f"db={db_val!r} vs gov={gov_val!r} (one is null)"

    if field_type is FieldType.EXACT:
        match = str(db_val) == str(gov_val)
        desc = "ok" if match else f"db={db_val!r} != gov={gov_val!r}"
        return match, desc

    if field_type is FieldType.TEXT_NORM:
        db_norm = normalize_text(str(db_val))
        gov_norm = normalize_text(str(gov_val))
        match = db_norm == gov_norm
        desc = "ok" if match else f"db={db_norm!r} != gov={gov_norm!r}"
        return match, desc

    if field_type is FieldType.NUMERIC:
        try:
            db_f = float(db_val)
            gov_f = float(gov_val)
        except (ValueError, TypeError):
            return False, f"non-numeric: db={db_val!r}, gov={gov_val!r}"
        match = db_f == 0 if gov_f == 0 else abs(db_f - gov_f) / abs(gov_f) <= tolerance
        desc = "ok" if match else f"db={db_f} vs gov={gov_f} (tol={tolerance})"
        return match, desc

    if field_type is FieldType.COORD:
        try:
            db_f = float(db_val)
            gov_f = float(gov_val)
        except (ValueError, TypeError):
            return False, f"non-numeric coord: db={db_val!r}, gov={gov_val!r}"
        match = abs(db_f - gov_f) <= tolerance
        desc = "ok" if match else f"db={db_f} vs gov={gov_f} (±{tolerance})"
        return match, desc

    if field_type is FieldType.DATE:
        match = str(db_val).strip() == str(gov_val).strip()
        desc = "ok" if match else f"db={db_val!r} != gov={gov_val!r}"
        return match, desc

    return False, f"unknown type {field_type}"

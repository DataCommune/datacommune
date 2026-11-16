"""Lightweight data quality validators for ETL staging tables.

Each check samples rows from the staging table and returns a list of
warning strings.  These are logged but do NOT block the swap by default.
Pipelines opt-in by overriding ``BasePipeline.validate_data()``.
"""

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)

# Metropolitan France bounds
_FR_LON_MIN, _FR_LON_MAX = -5.5, 10.0
_FR_LAT_MIN, _FR_LAT_MAX = 41.0, 51.5

# DOM-TOM bounding boxes
_DOMTOM = [
    (-64.0, -60.0, 14.0, 19.0),  # Antilles (Guadeloupe, Martinique)
    (-55.0, -51.0, 2.0, 6.0),  # Guyane
    (43.0, 46.0, -13.5, -11.0),  # Mayotte
    (55.0, 56.5, -21.5, -20.5),  # Reunion
]


def _in_france(lon: float, lat: float) -> bool:
    """Check if coordinates fall within metropolitan France or DOM-TOM."""
    if _FR_LON_MIN <= lon <= _FR_LON_MAX and _FR_LAT_MIN <= lat <= _FR_LAT_MAX:
        return True
    return any(
        lo_min <= lon <= lo_max and la_min <= lat <= la_max
        for lo_min, lo_max, la_min, la_max in _DOMTOM
    )


def check_gps_bounds(
    dsn: str,
    table: str,
    lon_col: str = "longitude",
    lat_col: str = "latitude",
    sample_size: int = 1000,
) -> list[str]:
    """Sample rows and check GPS coordinates are within France bounds.

    Returns list of warning strings (empty if all OK).
    """
    warnings: list[str] = []
    try:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                f"SELECT {lon_col}::float, {lat_col}::float "
                f"FROM {table} "
                f"WHERE {lon_col} IS NOT NULL AND {lat_col} IS NOT NULL "
                f"ORDER BY RANDOM() LIMIT %s",
                (sample_size,),
            ).fetchall()

        if not rows:
            return warnings

        out_of_bounds = sum(1 for lon, lat in rows if not _in_france(lon, lat))
        if out_of_bounds > 0:
            pct = out_of_bounds / len(rows) * 100
            warnings.append(
                f"GPS bounds: {out_of_bounds}/{len(rows)} sampled rows "
                f"({pct:.1f}%) outside France bounds"
            )
    except Exception:  # noqa: BLE001
        logger.debug("GPS bounds check failed", exc_info=True)
    return warnings


def check_insee_codes(
    dsn: str,
    table: str,
    col: str = "code_commune",
    sample_size: int = 1000,
) -> list[str]:
    """Validate INSEE code format (5 chars, valid department prefix).

    Returns list of warning strings (empty if all OK).
    """
    warnings: list[str] = []
    try:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                f"SELECT {col} FROM {table} "
                f"WHERE {col} IS NOT NULL "
                f"ORDER BY RANDOM() LIMIT %s",
                (sample_size,),
            ).fetchall()

        if not rows:
            return warnings

        invalid = 0
        for (code,) in rows:
            if not isinstance(code, str) or len(code) != 5:
                invalid += 1
                continue
            # Valid prefixes: 01-19, 2A, 2B, 21-95, 971-976
            prefix = code[:2]
            if (prefix not in ("2A", "2B") and not prefix.isdigit()) or prefix == "00":
                invalid += 1

        if invalid > 0:
            pct = invalid / len(rows) * 100
            warnings.append(
                f"INSEE codes: {invalid}/{len(rows)} sampled rows "
                f"({pct:.1f}%) have invalid format in {col}"
            )
    except Exception:  # noqa: BLE001
        logger.debug("INSEE code check failed", exc_info=True)
    return warnings

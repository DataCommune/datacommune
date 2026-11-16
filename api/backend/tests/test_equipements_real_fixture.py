"""Real BPE fixture test for equipements ETL transform() (D-04).

This test exercises transform() directly on a real BPE CSV sample with the
actual column names and values from the INSEE BPE 2024 source.  It catches
the column-name bug that the synthetic fixture in test_equipements_reprojection.py
missed: the real CSV provides LATITUDE/LONGITUDE WGS84 columns that override
LAMBERT_X/Y, and the swap must NOT re-project those WGS84 values as Lambert-93.

Fixture: tests/fixtures/bpe_sample.csv (~30 rows, dept 85 + 44 + 971, EPSG 2154/5490)
"""

from pathlib import Path

import pandas as pd
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "bpe_sample.csv"

# WGS84 bounds for France métropolitaine + DOM (coarse)
LAT_MIN, LAT_MAX = 14.0, 52.0  # includes DOM islands
LON_MIN, LON_MAX = -62.0, 12.0  # includes Guadeloupe/Martinique


def _run_transform() -> pd.DataFrame:
    """Run EquipementsPipeline.transform() on the real BPE fixture.

    Returns the output CSV as a DataFrame.
    """
    from app.etl_equipements.pipeline import EquipementsPipeline

    p = EquipementsPipeline()
    out_path = p.transform(FIXTURE)
    df = pd.read_csv(out_path)
    return df


def test_transform_real_bpe_fixture_produces_wgs84_coords() -> None:
    """transform() on real BPE CSV must produce WGS84 France bounds (D-04 / FIX-01).

    The real BPE source provides LATITUDE/LONGITUDE WGS84 that override LAMBERT_X/Y.
    After transform(), lat and lon must be in WGS84 range — NOT Lambert-93 values
    (Lambert X~300000-900000, Y~6000000-7200000 would fail this assertion).
    """
    df = _run_transform()

    valid = df[df["latitude"].notna() & df["longitude"].notna()]
    assert len(valid) > 0, "No rows with valid lat/lon after transform()"

    # France métro rows: latitude must be in [41, 51] (not Lambert Y ~6590000)
    # code_departement may be int or str depending on pandas inference — cast to str
    metro = valid[valid["code_departement"].astype(str).isin(["85", "44"])]
    assert len(metro) > 0, "No France metro rows in fixture"

    lat_series = pd.to_numeric(metro["latitude"], errors="coerce")
    lon_series = pd.to_numeric(metro["longitude"], errors="coerce")

    assert lat_series.between(41, 51).all(), (
        f"latitude NOT in WGS84 France bounds [41, 51] for metro rows: "
        f"min={lat_series.min():.6f}, max={lat_series.max():.6f}. "
        "This is the Lambert-93 override bug (LATITUDE WGS84 overwrites LAMBERT_Y "
        "but swap() re-projects WGS84 as Lambert-93 — fix: use ST_SetSRID(..., 4326))."
    )
    assert lon_series.between(-5, 10).all(), (
        f"longitude NOT in WGS84 France bounds [-5, 10] for metro rows: "
        f"min={lon_series.min():.6f}, max={lon_series.max():.6f}."
    )


def test_transform_real_bpe_fixture_no_lambert_values_in_output() -> None:
    """WGS84 override must fully replace Lambert-93 values (no large-magnitude coords).

    Lambert-93 Y values are ~6000000-7200000.  If LATITUDE column is missing or
    the override does not apply, these Lambert magnitudes would appear in lat/lon.
    """
    df = _run_transform()

    valid = df[df["latitude"].notna() & df["longitude"].notna()]
    lat_series = pd.to_numeric(valid["latitude"], errors="coerce")

    # No Lambert-93 Y magnitudes should appear in latitude
    assert (lat_series.abs() < 1000).all(), (
        "latitude contains Lambert-93 magnitude values "
        f"(expected |lat| < 1000 for WGS84, got max={lat_series.abs().max():.0f}).  "
        "LATITUDE WGS84 column did not override LAMBERT_Y."
    )


def test_transform_real_bpe_dom_rows_have_valid_coords() -> None:
    """DOM rows (dept 971) must also have valid WGS84 coords after transform().

    The BPE fixture includes Guadeloupe rows (EPSG:5490, lat~16, lon~-61).
    These should pass through the LATITUDE/LONGITUDE WGS84 path correctly.
    """
    df = _run_transform()

    dom = df[df["code_departement"].astype(str) == "971"]
    if len(dom) == 0:
        pytest.skip("No DOM rows in fixture")

    lat_series = pd.to_numeric(dom["latitude"], errors="coerce")
    lon_series = pd.to_numeric(dom["longitude"], errors="coerce")

    # Guadeloupe is at ~lat 16, lon -61
    assert lat_series.between(14, 20).all(), (
        f"DOM latitude not in expected Guadeloupe range [14, 20]: {lat_series.tolist()}"
    )
    assert lon_series.between(-63, -60).all(), (
        "DOM longitude not in expected Guadeloupe range [-63, -60]: "
        f"{lon_series.tolist()}"
    )

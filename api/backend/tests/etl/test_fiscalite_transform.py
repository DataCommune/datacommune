"""Offline unit tests for FiscalitePipeline.transform() (Phase 43, Plans 02+04).

Covers transform() mechanics: output schema shape, the code_insee validity
filter, D-04 measured-zero preservation, and (Plan 43-04, D-01) that the
four rate columns stay in percentage points -- source 40.01 stays 40.01,
never 0.4001. The DGFiP source is already expressed in points; the
pre-43-04 pipeline divided by 100 to compensate for the legacy fraction
storage, which is exactly the semantic bug DATA-01 exists to fix.
"""

from pathlib import Path

import pandas as pd
import pytest

from app.etl_fiscalite.config import OUTPUT_COLUMNS
from app.etl_fiscalite.pipeline import FiscalitePipeline


@pytest.fixture
def pipeline() -> FiscalitePipeline:
    return FiscalitePipeline()


@pytest.fixture
def source_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a synthetic source CSV mirroring the DGFiP schema.

    Row 2 has a deliberately invalid (4-char) code_insee — a synthetic
    invalid fixture proving the transform's length filter still catches
    malformed commune codes.
    """
    df = pd.DataFrame(
        [
            {
                "INSEE COM": "44109",
                "LIBCOM": "Nantes",
                "DEP": "44",
                "LIBDEP": "Loire-Atlantique",
                "LIBREG": "Pays de la Loire",
                "Q03": "Nantes Metropole",
                "Taux_Global_TFB": "40,01",
                "Taux_Global_TFNB": "551,16",
                "Taux_Global_TH": "0",
                "Taux_Plein_TEOM": "9,87",
                "EXERCICE": "2024",
                "IND_MAJOTHRS": "NON",
                "THSURTAXRSTAU": "",
            },
            {
                "INSEE COM": "4410",
                "LIBCOM": "Commune invalide",
                "DEP": "44",
                "LIBDEP": "Loire-Atlantique",
                "LIBREG": "Pays de la Loire",
                "Q03": "",
                "Taux_Global_TFB": "10,00",
                "Taux_Global_TFNB": "10,00",
                "Taux_Global_TH": "10,00",
                "Taux_Plein_TEOM": "10,00",
                "EXERCICE": "2024",
                "IND_MAJOTHRS": "NON",
                "THSURTAXRSTAU": "",
            },
        ]
    )

    raw_dir = tmp_path / "fiscalite" / "raw"
    raw_dir.mkdir(parents=True)
    out = raw_dir / "fiscalite_particuliers.csv"
    df.to_csv(out, index=False, sep=";", encoding="utf-8-sig")

    from app.etl_fiscalite import pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "get_download_dir",
        lambda _: tmp_path / "fiscalite",
    )
    return out


def test_transform_produces_all_output_columns(
    pipeline: FiscalitePipeline, source_csv: Path
) -> None:
    out_path = pipeline.transform(source_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    assert list(df.columns) == OUTPUT_COLUMNS


def test_transform_filters_rows_with_invalid_code_insee_length(
    pipeline: FiscalitePipeline, source_csv: Path
) -> None:
    """Synthetic invalid fixture: a 4-char code_insee must be dropped.

    If the code_insee length filter in pipeline.py regresses, this test
    turns red — proving the harness catches malformed commune codes.
    """
    out_path = pipeline.transform(source_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    assert list(df["code_insee"]) == ["44109"]


def test_transform_preserves_zero_rate_as_zero_not_absent(
    pipeline: FiscalitePipeline, source_csv: Path
) -> None:
    """D-04: a measured zero rate must remain 0, never become NULL/absent."""
    out_path = pipeline.transform(source_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    nantes = df[df["code_insee"] == "44109"].iloc[0]
    assert nantes["taux_th_rs"] != ""


def test_transform_keeps_rates_as_percentage_points_not_fraction(
    pipeline: FiscalitePipeline, source_csv: Path
) -> None:
    """D-01: source "40,01" (already percentage points) must stay 40.01 in
    the transformed output -- never divided into a 0-1 fraction (0.4001).

    contracts/fiche-fields.v1.json documents fiscalite.taux_* as
    unit=percent_point. The high TFNB sentinel proves a legitimate rate over
    100 points is never mistaken for an already-converted legacy value.
    """
    out_path = pipeline.transform(source_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    nantes = df[df["code_insee"] == "44109"].iloc[0]
    assert float(nantes["taux_tfpb"]) == pytest.approx(40.01)
    assert float(nantes["taux_tfpnb"]) == pytest.approx(551.16)
    assert float(nantes["taux_teom"]) == pytest.approx(9.87)

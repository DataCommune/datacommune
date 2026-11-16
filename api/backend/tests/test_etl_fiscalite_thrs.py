"""Unit tests for the fiscalite THRS (second-home surtax) columns, plan 04.7-08."""

import inspect

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

from app.etl_fiscalite.config import OUTPUT_COLUMNS, SOURCE_COLUMNS
from app.etl_fiscalite.pipeline import FiscalitePipeline

SOURCE_HEADER = [
    "EXERCICE",
    "DEP",
    "LIBDEP",
    "LIBREG",
    "LIBCOM",
    "Q03",
    "INSEE COM",
    "Taux_Global_TFB",
    "Taux_Global_TFNB",
    "Taux_Global_TH",
    "Taux_Plein_TEOM",
    "IND_MAJOTHRS",
    "THSURTAXRSTAU",
]


def _row(ind: str, taux: str) -> dict:
    return {
        "EXERCICE": "2024",
        "DEP": "85",
        "LIBDEP": "Vendee",
        "LIBREG": "Pays de la Loire",
        "LIBCOM": "Challans",
        "Q03": "CC Challans-Gois",
        "INSEE COM": "85047",
        "Taux_Global_TFB": "40.5",
        "Taux_Global_TFNB": "60.1",
        "Taux_Global_TH": "20.2",
        "Taux_Plein_TEOM": "9.9",
        "IND_MAJOTHRS": ind,
        "THSURTAXRSTAU": taux,
    }


def _transform(tmp_path, monkeypatch, rows: list[dict]) -> pd.DataFrame:
    monkeypatch.setattr(
        "app.etl_fiscalite.pipeline.get_download_dir", lambda name: tmp_path
    )
    source = tmp_path / "fiscalite.csv"
    pd.DataFrame(rows, columns=SOURCE_HEADER).to_csv(
        source, sep=";", index=False, encoding="utf-8"
    )
    out = FiscalitePipeline().transform(source)
    return pd.read_csv(out, dtype=str).fillna("")


class TestColumnLists:
    def test_source_columns_declare_the_thrs_headers(self):
        assert "IND_MAJOTHRS" in SOURCE_COLUMNS
        assert "THSURTAXRSTAU" in SOURCE_COLUMNS

    def test_output_columns_end_with_the_thrs_columns(self):
        # Positional with the flat staging table + COPY: new columns go last.
        assert OUTPUT_COLUMNS[-2:] == ["majoration_thrs", "taux_majoration_thrs"]

    def test_flat_staging_and_insert_carry_the_thrs_columns(self):
        for fn in (FiscalitePipeline.stage, FiscalitePipeline.swap):
            source = inspect.getsource(fn)
            assert "majoration_thrs" in source
            assert "taux_majoration_thrs" in source


class TestTransformThrs:
    def test_oui_becomes_true_with_rate_kept(self, tmp_path, monkeypatch):
        out = _transform(tmp_path, monkeypatch, [_row("OUI", "60.0")])
        assert out.loc[0, "majoration_thrs"] == "true"
        assert float(out.loc[0, "taux_majoration_thrs"]) == 60.0

    def test_non_becomes_false_and_empty_rate_stays_null(self, tmp_path, monkeypatch):
        out = _transform(tmp_path, monkeypatch, [_row("NON", "")])
        assert out.loc[0, "majoration_thrs"] == "false"
        assert out.loc[0, "taux_majoration_thrs"] == ""

    def test_source_zero_rate_is_kept_as_is(self, tmp_path, monkeypatch):
        out = _transform(tmp_path, monkeypatch, [_row("NON", "0")])
        assert float(out.loc[0, "taux_majoration_thrs"]) == 0.0

    def test_decimal_comma_is_accepted(self, tmp_path, monkeypatch):
        out = _transform(tmp_path, monkeypatch, [_row("OUI", "12,5")])
        assert float(out.loc[0, "taux_majoration_thrs"]) == 12.5

    @pytest.mark.parametrize("bad", ["soixante", "60 %", "n.c.", "N/A", "NULL", "NaN"])
    def test_an_unreadable_rate_fails_validation(self, tmp_path, monkeypatch, bad):
        with pytest.raises(ValueError, match="THSURTAXRSTAU"):
            _transform(tmp_path, monkeypatch, [_row("OUI", bad)])

    @pytest.mark.parametrize("bad", ["oui", "Oui", "O", "1", "TRUE", ""])
    def test_any_other_indicator_fails_validation(self, tmp_path, monkeypatch, bad):
        with pytest.raises(ValueError, match="IND_MAJOTHRS"):
            _transform(tmp_path, monkeypatch, [_row("OUI", "20"), _row(bad, "")])

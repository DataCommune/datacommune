"""LOVAC rates distinguish a measured zero from unavailable denominators."""

import csv
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.etl_vacance.pipeline import VacancePipeline

pytestmark = pytest.mark.unit

_RATES = ("taux_vacance", "taux_vacance_moins_2ans", "taux_vacance_plus_2ans")
_COLUMN_PROOF = (
    "Logements vacants : 2026 via pp_vacant_26 ; parc privé : 2025 via ff_pp_total_25"
)


def _write_source(
    path,
    *,
    year="25",
    total_column="ff_pp_total_24",
    total="100",
    vacant="0",
    long_vacant="0",
):
    row = {
        f"CODGEO_{year}": "44001",
        f"LIBGEO_{year}": "Commune test",
        f"pp_vacant_{year}": vacant,
        f"pp_vacant_plus_2ans_{year}": long_vacant,
    }
    if total_column is not None:
        row[total_column] = total
    with path.open("w", encoding="latin-1", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=row, delimiter=";")
        writer.writeheader()
        writer.writerow(row)
    return path


def _transform(tmp_path, monkeypatch, *, total, vacant="0", long_vacant="0"):
    monkeypatch.setattr("app.etl_vacance.pipeline.get_download_dir", lambda _: tmp_path)
    path = tmp_path / "source.csv"
    _write_source(
        path,
        total_column="ff_pp_total_24" if total is not None else None,
        total=total,
        vacant=vacant,
        long_vacant=long_vacant,
    )
    pipeline = VacancePipeline()
    pipeline._async_download = AsyncMock(return_value=[path])
    pipeline.download()
    output = pipeline.transform(path)
    with output.open(encoding="utf-8", newline="") as handle:
        return next(csv.DictReader(handle))


def test_download_prepares_exact_columns_and_source_period(tmp_path):
    path = _write_source(
        tmp_path / "lovac.csv", year="26", total_column="ff_pp_total_25"
    )
    pipeline = VacancePipeline()
    pipeline._async_download = AsyncMock(return_value=[path])

    assert pipeline.download() == [path]
    assert pipeline.source_period_label == (
        "Logements vacants : 2026 via pp_vacant_26 ; "
        "parc privé : 2025 via ff_pp_total_25"
    )


def test_download_records_same_year_denominator_fallback(tmp_path):
    path = _write_source(
        tmp_path / "lovac.csv", year="26", total_column="ff_pp_total_26"
    )
    pipeline = VacancePipeline()
    pipeline._async_download = AsyncMock(return_value=[path])

    pipeline.download()

    assert pipeline.source_period_label == (
        "Logements vacants : 2026 via pp_vacant_26 ; "
        "parc privé : 2026 via ff_pp_total_26"
    )


@pytest.mark.parametrize(
    ("archived_label", "expected_replay_label"),
    [
        (
            "LOVAC 2026",
            f"Libellé archivé : LOVAC 2026 ; {_COLUMN_PROOF}",
        ),
        (
            f"Libellé archivé : LOVAC 2026 ; {_COLUMN_PROOF}",
            f"Libellé archivé : LOVAC 2026 ; {_COLUMN_PROOF}",
        ),
    ],
    ids=("historical-label", "already-composed-label"),
)
def test_download_does_not_reuse_a_previous_replay_label(
    tmp_path, archived_label, expected_replay_label
):
    path = _write_source(
        tmp_path / "lovac.csv", year="26", total_column="ff_pp_total_25"
    )
    replay = SimpleNamespace(
        record={"source_period_label": archived_label},
        materialize=lambda _directory: [path],
    )
    pipeline = VacancePipeline()

    pipeline.restore_source_inputs(replay, tmp_path)
    assert pipeline.source_period_label == expected_replay_label

    pipeline._async_download = AsyncMock(return_value=[path])
    pipeline.download()
    assert pipeline.source_period_label == _COLUMN_PROOF


def test_transform_rejects_columns_different_from_prepared_pair(tmp_path, monkeypatch):
    monkeypatch.setattr("app.etl_vacance.pipeline.get_download_dir", lambda _: tmp_path)
    path = _write_source(
        tmp_path / "lovac.csv", year="26", total_column="ff_pp_total_25"
    )
    pipeline = VacancePipeline()
    pipeline._async_download = AsyncMock(return_value=[path])
    pipeline.download()
    _write_source(path, year="26", total_column="ff_pp_total_26")

    with pytest.raises(ValueError, match="prepared column pair"):
        pipeline.transform(path)


@pytest.mark.parametrize("total", ["", "0", "non disponible", "-5"])
def test_unavailable_denominator_never_produces_a_zero_rate(
    tmp_path, monkeypatch, total
):
    row = _transform(tmp_path, monkeypatch, total=total)
    assert [row[name] for name in _RATES] == ["", "", ""]


def test_missing_denominator_column_still_rejects_an_unknown_schema(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="LOVAC schema mismatch"):
        _transform(tmp_path, monkeypatch, total=None)


def test_measured_zero_remains_zero(tmp_path, monkeypatch):
    row = _transform(tmp_path, monkeypatch, total="100")
    assert [float(row[name]) for name in _RATES] == [0, 0, 0]


def test_rates_keep_their_denominator_and_source_year(tmp_path, monkeypatch):
    row = _transform(tmp_path, monkeypatch, total="80", vacant="12", long_vacant="4")
    assert [float(row[name]) for name in _RATES] == [15, 10, 5]
    assert row["annee"] == "2025"
    assert float(row["nb_logements_prives"]) == 80


def test_missing_numerator_remains_unavailable(tmp_path, monkeypatch):
    row = _transform(tmp_path, monkeypatch, total="100", vacant="", long_vacant="")
    assert [row[name] for name in _RATES] == ["", "", ""]


@pytest.mark.parametrize(
    ("vacant", "long_vacant", "expected"),
    [
        ("-1", "0", ["", "", "0.0"]),
        ("2", "5", ["2.0", "", "5.0"]),
        ("2", "-5", ["2.0", "", ""]),
    ],
)
def test_negative_numerator_cannot_produce_a_rate(
    tmp_path, monkeypatch, vacant, long_vacant, expected
):
    row = _transform(
        tmp_path, monkeypatch, total="100", vacant=vacant, long_vacant=long_vacant
    )
    assert [row[name] for name in _RATES] == expected

"""Independent official 2025 CSV witnesses for COR-06 rental semantics."""

import csv
import json
from pathlib import Path

import pytest

from app.config import settings
from app.etl_loyers.pipeline import LoyersPipeline

FIXTURES = Path(__file__).parent / "fixtures" / "loyers"
OFFICIAL = json.loads((FIXTURES / "official-2025.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", ["appartement", "maison", "1-2_pieces", "3+_pieces"])
def test_official_source_preserves_prediction_scope_and_quality(
    tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    pipeline = LoyersPipeline()
    source = FIXTURES / f"{kind}.csv"
    pipeline.validate_schema(source)
    with pipeline.transform(source).open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    expected = [r["values"] for r in OFFICIAL if r["type_habitat"] == kind]
    surfaces = {
        "appartement": (52, 22.2),
        "maison": (92, 22.4),
        "1-2_pieces": (37, 22.9),
        "3+_pieces": (72, 21.3),
    }
    for actual, oracle in zip(rows, expected, strict=True):
        assert actual["id_zone"] == oracle["id_zone"]
        assert actual["type_prediction"] == oracle["TYPPRED"]
        assert int(actual["nb_observations_commune"]) == int(oracle["nbobs_com"])
        assert int(actual["nb_observations_maille"]) == int(oracle["nbobs_mail"])
        assert float(actual["r2_ajuste"]) == pytest.approx(
            float(oracle["R2_adj"].replace(",", "."))
        )
        assert actual["trimestre_reference"] == "3"
        assert actual["charges_comprises"] == "true"
        assert actual["meuble"] == "false"
        assert actual["annee"] == "2025"
        assert (
            float(actual["surface_reference_m2"]),
            float(actual["surface_piece_reference_m2"]),
        ) == surfaces[kind]


def test_source_type_is_not_guessed_for_unknown_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    source = tmp_path / "unknown.csv"
    source.write_bytes((FIXTURES / "maison.csv").read_bytes())
    with pytest.raises(ValueError, match="type"):
        LoyersPipeline().transform(source)


def test_future_vintage_requires_its_own_reference_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    monkeypatch.setattr("app.etl_loyers.pipeline.ANNEE", 2026)
    with pytest.raises(ValueError, match="vintage"):
        LoyersPipeline().transform(FIXTURES / "appartement.csv")


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("epci", "epci"),
        ("EPCI", "epci"),
        ("commune", "commune"),
        ("maille", "maille"),
        ("maile", "maile"),
        ("unverified", None),
        ("EPCI-inconnu", None),
        ("EPCI ", None),
        ("ePcI", None),
        ("", None),
    ],
)
def test_synthetic_scope_is_preserved_or_rejected(
    tmp_path, monkeypatch, scope, expected
):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    path = tmp_path / "appartement.csv"
    path.write_text(
        (FIXTURES / "appartement.csv")
        .read_text(encoding="latin-1")
        .replace(";maille;", f";{scope};"),
        encoding="latin-1",
    )
    if expected is None:
        with pytest.raises(ValueError, match="scope"):
            LoyersPipeline().transform(path)
    else:
        with LoyersPipeline().transform(path).open(encoding="utf-8") as stream:
            assert next(csv.DictReader(stream))["type_prediction"] == expected

"""Unit tests for DemographiePipeline.transform — merge + column mapping.

These tests exercise the transform step against a synthetic merged CSV and
verify that the 3-source merge (pop legales + base logement + base pop struct)
produces all the expected OUTPUT_COLUMNS with correct numeric coercion.
"""

from pathlib import Path

import pandas as pd
import pytest

from app.etl_demographie.config import OUTPUT_COLUMNS
from app.etl_demographie.pipeline import (
    MERGED_CSV_NAME,
    DemographiePipeline,
)


@pytest.fixture
def pipeline() -> DemographiePipeline:
    return DemographiePipeline()


@pytest.fixture
def merged_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a synthetic merged CSV mirroring the download() output schema."""
    df = pd.DataFrame(
        [
            {
                "code_insee": "75101",
                "Commune": "Paris 1er Arrondissement",
                "DEP": "75",
                "REG": "11",
                "PMUN": "16888",
                "P22_POPH": "7800",
                "P22_POPF": "9088",
                "P22_MEN": "10000",
                "P22_LOG": "15000",
                "P22_RP": "10000",
                "P22_LOGVAC": "1200",
                "P22_RSECOCC": "3800",
            },
            {
                "code_insee": "44109",
                "Commune": "Nantes",
                "DEP": "44",
                "REG": "52",
                "PMUN": "320732",
                "P22_POPH": "150000",
                "P22_POPF": "170732",
                "P22_MEN": "170000",
                "P22_LOG": "185000",
                "P22_RP": "170000",
                "P22_LOGVAC": "10000",
                "P22_RSECOCC": "5000",
            },
            {
                # Commune only in pop_legales — base logement left-join produced NaN.
                "code_insee": "97123",
                "Commune": "Petite commune DOM",
                "DEP": "971",
                "REG": "01",
                "PMUN": "500",
                "P22_POPH": "",
                "P22_POPF": "",
                "P22_MEN": "",
                "P22_LOG": "",
                "P22_RP": "",
                "P22_LOGVAC": "",
                "P22_RSECOCC": "",
            },
        ]
    )

    # Read the path back into get_download_dir so transform() writes next to it.
    raw_dir = tmp_path / "demographie" / "raw"
    raw_dir.mkdir(parents=True)
    out = raw_dir / MERGED_CSV_NAME
    df.to_csv(out, index=False, sep=";", encoding="utf-8")

    # Redirect get_download_dir so transform writes under tmp_path too.
    from app.etl_demographie import pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "get_download_dir",
        lambda _: tmp_path / "demographie",
    )
    return out


def test_transform_produces_all_output_columns(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    assert list(df.columns) == OUTPUT_COLUMNS


def test_transform_maps_sex_split_correctly(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    paris1 = df[df["code_insee"] == "75101"].iloc[0]
    assert paris1["population"] == "16888"
    assert paris1["population_hommes"] == "7800"
    assert paris1["population_femmes"] == "9088"


def test_transform_maps_housing_counts(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    nantes = df[df["code_insee"] == "44109"].iloc[0]
    assert nantes["nb_menages"] == "170000"
    assert nantes["nb_logements"] == "185000"
    assert nantes["nb_residences_principales"] == "170000"
    assert nantes["nb_logements_vacants"] == "10000"
    assert nantes["nb_residences_secondaires"] == "5000"


def test_transform_handles_missing_supplementary_data(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    """Communes present in pop_legales but missing from base logement/pop_struct
    must still get a row with NULL supplementary fields, not be dropped."""
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    dom = df[df["code_insee"] == "97123"]
    assert len(dom) == 1
    assert dom.iloc[0]["population"] == "500"
    # Empty strings in the merged CSV → empty in output (will become NULL in SQL).
    assert dom.iloc[0]["population_hommes"] == ""
    assert dom.iloc[0]["nb_menages"] == ""


def test_transform_sets_dom_departement_to_3_chars(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    dom = df[df["code_insee"] == "97123"].iloc[0]
    assert dom["code_departement"] == "971"


def test_transform_sets_metropole_departement_to_2_chars(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)

    nantes = df[df["code_insee"] == "44109"].iloc[0]
    assert nantes["code_departement"] == "44"


def test_transform_sets_annee_2022(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    assert (df["annee"] == "2022").all()


def test_transform_leaves_superficie_and_densite_empty(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    """superficie + densite are recomputed post-swap from cadastre parcelles —
    the transform must emit empty strings, not numeric zeros."""
    out_path = pipeline.transform(merged_csv)
    df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    assert (df["superficie"] == "").all()
    assert (df["densite"] == "").all()


def test_validate_schema_accepts_merged_csv(
    pipeline: DemographiePipeline, merged_csv: Path
) -> None:
    pipeline.validate_schema(merged_csv)


def test_validate_schema_rejects_missing_column(
    pipeline: DemographiePipeline, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "code_insee;Commune;PMUN\n75101;Paris;1000\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing columns"):
        pipeline.validate_schema(bad)


def test_merge_sources_left_joins_preserve_pop_legales(
    pipeline: DemographiePipeline, tmp_path: Path
) -> None:
    """Simulate _merge_sources with a commune missing from logement/struct —
    output must preserve the row with NaN on supplementary fields."""
    pop_csv = tmp_path / "pop.csv"
    log_csv = tmp_path / "log.csv"
    struct_csv = tmp_path / "struct.csv"

    pop_csv.write_text(
        "COM;Commune;PMUN;DEP;REG\n"
        "75101;Paris 1;16888;75;11\n"
        "99999;Ghost commune;100;99;99\n",
        encoding="utf-8",
    )
    log_csv.write_text(
        "CODGEO;P22_MEN;P22_LOG;P22_RP;P22_LOGVAC;P22_RSECOCC\n"
        "75101;10000;15000;10000;1200;3800\n",
        encoding="utf-8",
    )
    struct_csv.write_text(
        "CODGEO;P22_POPH;P22_POPF\n75101;7800;9088\n",
        encoding="utf-8",
    )

    merged_path = tmp_path / "merged.csv"
    pipeline._merge_sources(pop_csv, log_csv, struct_csv, merged_path)

    df = pd.read_csv(merged_path, dtype=str, keep_default_na=False, sep=";")
    assert set(df["code_insee"]) == {"75101", "99999"}

    paris = df[df["code_insee"] == "75101"].iloc[0]
    assert paris["P22_MEN"] == "10000"
    assert paris["P22_POPH"] == "7800"

    ghost = df[df["code_insee"] == "99999"].iloc[0]
    # Left-joined NaN becomes empty string in the written CSV.
    assert ghost["P22_MEN"] == ""
    assert ghost["P22_POPH"] == ""

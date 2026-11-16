# Header of tests/fixtures/merimee_sample.csv copied from the producer file
# https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv as read on
# 2026-09-25 (78 columns, "|" separator, mixed-case names). The four data rows
# are built for the test in the shape of real rows (public heritage data only).
"""Monuments source (plan 02-20): Mérimée CSV published on data.gouv.fr.

The data.culture.gouv.fr Opendatasoft export was retired (301 to a home page);
the Ministry of Culture publishes the same base on data.gouv.fr with a "|"
separator and mixed-case column names. These tests pin the reader to that
format and keep the refusal of a header that lacks a required column.
"""

from pathlib import Path

import pandas as pd
import pytest

from app.config import settings
from app.etl_monuments import config as monuments_config
from app.etl_monuments.pipeline import MonumentsPipeline

FIXTURE = Path(__file__).parent.parent / "fixtures" / "merimee_sample.csv"
AUTHORIZED_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MonumentsPipeline:
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    return MonumentsPipeline()


def _header_and_body() -> tuple[str, str]:
    text = FIXTURE.read_text(encoding="utf-8")
    header, _, body = text.partition("\n")
    return header, body


def test_validate_schema_accepts_the_published_merimee_header(
    pipeline: MonumentsPipeline,
) -> None:
    pipeline.validate_schema(FIXTURE)


def test_validate_schema_refuses_a_missing_required_column(
    pipeline: MonumentsPipeline, tmp_path: Path
) -> None:
    header, _ = _header_and_body()
    columns = header.split("|")
    idx = columns.index("Reference")
    kept = [c for i, c in enumerate(columns) if i != idx]
    # Header-only copy: the refusal happens on the header, before any row.
    copy = tmp_path / "merimee_no_reference.csv"
    copy.write_text("|".join(kept) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reference"):
        pipeline.validate_schema(copy)


def test_validate_schema_refuses_the_old_semicolon_format(
    pipeline: MonumentsPipeline, tmp_path: Path
) -> None:
    header, _ = _header_and_body()
    copy = tmp_path / "merimee_semicolon.csv"
    copy.write_text(header.replace("|", ";") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns"):
        pipeline.validate_schema(copy)


def test_transform_reads_the_published_format(pipeline: MonumentsPipeline) -> None:
    out = pipeline.transform(FIXTURE)
    df = pd.read_csv(out, dtype=str)

    # The row without coordinates is dropped; the three others are kept.
    assert len(df) == 3
    assert list(df.columns) == monuments_config.OUTPUT_COLUMNS

    lat = pd.to_numeric(df["latitude"])
    lon = pd.to_numeric(df["longitude"])
    assert lat.between(41, 52).all()
    assert lon.between(-5.5, 10).all()

    assert set(df["code_departement"]) == {"44", "85"}
    assert df["code_insee"].str.fullmatch(r"\d{5}").all()
    multi = df[df["reference"] == "PA00990004"]
    assert multi["code_insee"].tolist() == ["85194"]


def test_transform_keeps_the_first_departement_of_a_multi_valued_field(
    pipeline: MonumentsPipeline, tmp_path: Path
) -> None:
    # Shapes read in the archived producer file of 2026-10-29: a monument on
    # two departements (PA00095650 "12;81", PA00116558 "01;71"), whose first
    # INSEE code belongs to the first departement; Corsica (2A, INSEE code
    # with a letter); Saint-Pierre-et-Miquelon (975, never hosted: D-123).
    header, body = _header_and_body()
    columns = header.split("|")
    template = body.splitlines()[0].split("|")
    lower = [c.strip().lower() for c in columns]

    def row(reference: str, departement: str, insee: str) -> str:
        values = list(template)
        values[lower.index("reference")] = reference
        values[lower.index("departement_format_numerique")] = departement
        values[lower.index("cog_insee_lors_de_la_protection")] = insee
        return "|".join(values)

    source = tmp_path / "merimee_multi.csv"
    source.write_text(
        "\n".join(
            [
                header,
                row("PA00095650", "12;81", "12276;81292"),
                row("PA00116558", "01;71", "01370;71270"),
                row("PA2A000001", "2A", "2A004"),
                row("PA97500001", "975", "97502"),
                row("PA00990001", "44", "44109"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    df = pd.read_csv(pipeline.transform(source), dtype=str)
    departements = dict(zip(df["reference"], df["code_departement"], strict=True))
    assert departements == {
        "PA00095650": "12",
        "PA00116558": "01",
        "PA2A000001": "2A",
        # Kept as published: the scope filter removes it (never hosted).
        "PA97500001": "975",
        "PA00990001": "44",
    }


def test_source_configuration_is_the_authorized_merimee_file() -> None:
    assert monuments_config.MONUMENTS_CSV_URL == AUTHORIZED_URL
    assert monuments_config.CSV_SEPARATOR == "|"

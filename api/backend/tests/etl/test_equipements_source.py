"""Equipements source (plan 02-20): BPE25, vintage pinned next to the URL.

INSEE withdrew BPE24.zip (HTTP 500 since 2026-09-24) and only publishes BPE25.
The vintage used to be a string hidden in transform() ("2024"), which produced
a 2024 label under a URL that could no longer serve 2024. These tests keep the
configured vintage and the file named by DOWNLOAD_URL together, and bound the
read of a 1.5 GB, 96-column CSV to the columns transform() consumes.
"""

import re
from pathlib import Path

import pandas as pd
import pytest

from app.config import settings
from app.etl_equipements import config as equipements_config
from app.etl_equipements import pipeline as equipements_pipeline
from app.etl_equipements.pipeline import EquipementsPipeline

FIXTURE = Path(__file__).parent.parent / "fixtures" / "bpe_sample.csv"


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EquipementsPipeline:
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl_tmp"))
    return EquipementsPipeline()


def test_source_vintage_matches_the_file_named_by_the_url() -> None:
    m = re.search(r"BPE(\d\d)\.zip$", equipements_config.DOWNLOAD_URL)
    assert m, equipements_config.DOWNLOAD_URL
    expected = "20" + m.group(1)
    assert expected == equipements_config.SOURCE_VINTAGE


def test_transform_writes_the_configured_vintage(
    pipeline: EquipementsPipeline,
) -> None:
    # The fixture is a real BPE 2024 sample: its AN column says 2024 but is not
    # read. The vintage comes from the configuration, pinned to the URL.
    out = pipeline.transform(FIXTURE)
    df = pd.read_csv(out, dtype=str)
    assert len(df) > 0
    assert (df["annee"] == equipements_config.SOURCE_VINTAGE).all()


def test_local_archive_name_derives_from_the_url() -> None:
    name = equipements_pipeline.archive_file_name(equipements_config.DOWNLOAD_URL)
    assert name == "bpe25.zip"


def test_read_is_bounded_to_the_columns_transform_uses(
    pipeline: EquipementsPipeline,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_columns = equipements_config.READ_COLUMNS
    assert set(equipements_config.SOURCE_COLUMNS) <= read_columns

    # Fixture copy with a column the producer ships but transform() never reads.
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    enriched = [lines[0] + ';"QP2024"'] + [line + ';"QN04401M"' for line in lines[1:]]
    copy = tmp_path / "bpe_enriched.csv"
    copy.write_text("\n".join(enriched) + "\n", encoding="utf-8")

    real_read_csv = pd.read_csv
    loaded: list[list[str]] = []

    def spy(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        df = real_read_csv(path, *args, **kwargs)
        if Path(path) == copy:
            loaded.append(list(df.columns))
        return df

    monkeypatch.setattr(equipements_pipeline.pd, "read_csv", spy)
    pipeline.transform(copy)

    assert len(loaded) == 1
    header = [c.strip('"') for c in enriched[0].split(";")]
    assert set(loaded[0]) == set(header) & read_columns
    assert "QP2024" not in loaded[0]
    assert "AN" not in loaded[0]

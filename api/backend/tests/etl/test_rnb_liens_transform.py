"""Unit tests for the RNB link export parser (Lot B, PLAN-RATTACHEMENT-DPE).

Fixtures are verbatim rows from ``RNB_44.csv.zip`` (published 2026-08-01):
the whole point of this pipeline is reproducing the live RNB call's grouping,
so the parser is tested against the real embedded-JSON shape, not a guess.
"""

import csv
import io
import zipfile
from pathlib import Path

import pytest

from app.etl_rnb_liens.config import CSV_SEPARATOR, OUTPUT_COLUMNS
from app.etl_rnb_liens.pipeline import RnbLiensPipeline
from app.etl_rnb_liens.transform import (
    iter_link_rows,
    parse_cles_ban,
    parse_parcelles,
    pg_text_array,
    transform_rnb_export,
    validate_export_header,
)

pytestmark = pytest.mark.unit

HEADER = "rnb_id;point;shape;status;ext_ids;addresses;plots;validated_by"

# Real dept 44 row: one address, two parcels with very different cover ratios.
ROW_PORNICHET = (
    'AKDK5JSSD9MA;"SRID=4326;POINT(-2.31 47.24)";"SRID=4326;MULTIPOLYGON('
    '((-2.31 47.24,-2.32 47.24,-2.31 47.25,-2.31 47.24)))";constructed;'
    '"[{""id"": ""bdnb-bc-SDH5-N55D-8CD5"", ""source"": ""bdnb""}]";'
    '"[{""cle_interop_ban"" : ""44132_1490_00026"", ""street_number"" : ""26"", '
    '""street_rep"" : null, ""street"" : ""Avenue de Villès Babin"", '
    '""city_zipcode"" : ""44380"", ""city_name"" : ""Pornichet""}]";'
    '"[{""id"" : ""44132000AT0544"", ""bdg_cover_ratio"" : 0.017525587996804367}, '
    '{""id"" : ""44132000AT0543"", ""bdg_cover_ratio"" : 0.9824744120092228}]";[]'
)

# Corner building: two entrances, two BAN keys, one parcel. The case R4-02
# (and therefore this whole table) exists for.
ROW_ANGLE = (
    "BATANGLE0001;;;constructed;[];"
    '"[{""cle_interop_ban"" : ""44109_1234_00002""}, '
    '{""cle_interop_ban"" : ""44109_5678_00012""}]";'
    '"[{""id"" : ""44109000HN0129"", ""bdg_cover_ratio"" : 1.0}]";[]'
)

# 13,8 % of the export: a building RNB knows with no BAN address at all.
ROW_SANS_ADRESSE = (
    "BATNOADDR001;;;constructed;[];[];"
    '"[{""id"" : ""44109000HN0130"", ""bdg_cover_ratio"" : 1.0}]";[]'
)


def _read_rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text), delimiter=CSV_SEPARATOR))


def _make_export(tmp_path: Path, rows: list[str], name: str = "RNB_44") -> Path:
    zip_path = tmp_path / f"{name}.csv.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}.csv", "\n".join([HEADER, *rows]) + "\n")
    return zip_path


# -- pg_text_array ------------------------------------------------------------


def test_pg_text_array_empty_is_castable_literal():
    """Empty must be '{}': '' does not cast to text[] and would fail the swap."""
    assert pg_text_array([]) == "{}"


def test_pg_text_array_quotes_each_element():
    assert pg_text_array(["44132000AT0544", "44132000AT0543"]) == (
        '{"44132000AT0544","44132000AT0543"}'
    )


def test_pg_text_array_escapes_quotes_and_backslashes():
    assert pg_text_array(['a"b', "c\\d"]) == '{"a\\"b","c\\\\d"}'


# -- parse_cles_ban -----------------------------------------------------------


def test_parse_cles_ban_reads_embedded_json():
    row = _read_rows("\n".join([HEADER, ROW_PORNICHET]))[0]
    assert parse_cles_ban(row["addresses"]) == ["44132_1490_00026"]


def test_parse_cles_ban_keeps_every_entrance_of_a_corner_building():
    row = _read_rows("\n".join([HEADER, ROW_ANGLE]))[0]
    assert parse_cles_ban(row["addresses"]) == ["44109_1234_00002", "44109_5678_00012"]


def test_parse_cles_ban_dedups_preserving_order():
    raw = (
        '[{"cle_interop_ban": "B"}, {"cle_interop_ban": "A"}, {"cle_interop_ban": "B"}]'
    )
    assert parse_cles_ban(raw) == ["B", "A"]


@pytest.mark.parametrize("raw", ["", "[]", "not json", '{"not": "a list"}'])
def test_parse_cles_ban_degrades_to_empty_never_raises(raw):
    assert parse_cles_ban(raw) == []


def test_parse_cles_ban_skips_null_keys():
    assert parse_cles_ban('[{"cle_interop_ban": null}, {"cle_interop_ban": ""}]') == []


# -- parse_parcelles ----------------------------------------------------------


def test_parse_parcelles_orders_by_descending_cover_ratio():
    """parcelles[0] must be the principal parcel, not the source order."""
    row = _read_rows("\n".join([HEADER, ROW_PORNICHET]))[0]
    assert parse_parcelles(row["plots"]) == ["44132000AT0543", "44132000AT0544"]


def test_parse_parcelles_keeps_parcel_with_unreadable_ratio_but_sorts_it_last():
    raw = '[{"id": "P1"}, {"id": "P2", "bdg_cover_ratio": 0.5}]'
    assert parse_parcelles(raw) == ["P2", "P1"]


@pytest.mark.parametrize("raw", ["", "[]", "broken"])
def test_parse_parcelles_degrades_to_empty(raw):
    assert parse_parcelles(raw) == []


# -- iter_link_rows -----------------------------------------------------------


def test_iter_link_rows_explodes_one_row_per_ban_key():
    rows = _read_rows("\n".join([HEADER, ROW_ANGLE]))
    out = list(iter_link_rows(rows, "44", "2026-08-01"))
    assert [r[0] for r in out] == ["BATANGLE0001", "BATANGLE0001"]
    assert [r[1] for r in out] == ["44109_1234_00002", "44109_5678_00012"]


def test_iter_link_rows_stamps_department_and_millesime():
    rows = _read_rows("\n".join([HEADER, ROW_PORNICHET]))
    (row,) = list(iter_link_rows(rows, "44", "2026-08-01"))
    assert row[2] == "44"
    assert row[3] == "constructed"
    assert row[4] == '{"44132000AT0543","44132000AT0544"}'
    assert row[5] == "2026-08-01"


def test_iter_link_rows_drops_buildings_without_a_ban_key():
    """A keyless building can neither carry nor widen: no key, no link, no row."""
    rows = _read_rows("\n".join([HEADER, ROW_SANS_ADRESSE]))
    assert list(iter_link_rows(rows, "44", "")) == []


def test_iter_link_rows_leaves_millesime_empty_when_unknown():
    """Unknown publication date stays empty -> NULL, never an import time."""
    rows = _read_rows("\n".join([HEADER, ROW_PORNICHET]))
    (row,) = list(iter_link_rows(rows, "44", ""))
    assert row[5] == ""


def test_iter_link_rows_keeps_demolished_buildings():
    """Status is ingested unfiltered: the live call does not filter either."""
    demolished = ROW_ANGLE.replace(";constructed;", ";demolished;")
    rows = _read_rows("\n".join([HEADER, demolished]))
    assert {row[3] for row in iter_link_rows(rows, "44", "")} == {"demolished"}


# -- zip streaming ------------------------------------------------------------


def test_validate_export_header_accepts_the_real_header(tmp_path):
    validate_export_header(_make_export(tmp_path, [ROW_PORNICHET]))


def test_validate_export_header_rejects_a_missing_ingested_column(tmp_path):
    zip_path = tmp_path / "RNB_44.csv.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("RNB_44.csv", "rnb_id;point;shape;status\n")
    with pytest.raises(ValueError, match="missing required columns"):
        validate_export_header(zip_path)


def test_transform_rnb_export_writes_copy_ready_csv(tmp_path):
    zip_path = _make_export(tmp_path, [ROW_PORNICHET, ROW_ANGLE, ROW_SANS_ADRESSE])
    out = tmp_path / "out.csv"

    written = transform_rnb_export(zip_path, out, "44", "2026-08-01")

    assert written == 3  # 1 (Pornichet) + 2 (corner) + 0 (no address)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == OUTPUT_COLUMNS
    assert len(rows) == 4


def test_transform_rejects_an_archive_without_a_single_csv(tmp_path):
    zip_path = tmp_path / "RNB_44.csv.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("readme.txt", "nope")
    with pytest.raises(ValueError, match="Expected exactly one CSV"):
        validate_export_header(zip_path)


# -- pipeline wiring ----------------------------------------------------------


def test_dept_is_read_from_the_export_filename():
    assert RnbLiensPipeline._dept_from_path(Path("RNB_85.csv.zip")) == "85"


def test_flat_staging_gate_is_deferred_to_swap(bind_departements):
    """min_staging_rows must be 0 so the runner does not gate on flat rows."""
    bind_departements(("44", "85"), RnbLiensPipeline)
    pipeline = RnbLiensPipeline()
    assert pipeline.min_staging_rows == 0
    assert pipeline.min_staging_rows_final > 0
    assert pipeline.skip_runner_schema_drift_check is True


def test_national_coverage_floor_does_not_apply():
    """44/85 only: the 96-department runner floor must stay off."""
    pipeline = RnbLiensPipeline()
    assert pipeline.has_department_partitions is False
    assert pipeline.staging_is_partitioned is False


def test_every_download_must_succeed(bind_departements):
    bind_departements(("44", "85"), RnbLiensPipeline)
    pipeline = RnbLiensPipeline()
    assert pipeline.min_success_ratio == 1.0
    assert pipeline.expected_file_count == len(pipeline.department_scope) == 2


def test_millesime_is_none_until_a_download_reports_one():
    """PROV-03: no upstream date read -> no fabricated period."""
    pipeline = RnbLiensPipeline()
    assert pipeline.source_updated_at is None
    assert pipeline.source_period_label is None

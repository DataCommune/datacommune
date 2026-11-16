"""Pure parts of the fidelity measurement and extraction scripts (plan 02-01)."""

import hashlib
import json
from datetime import date
from decimal import Decimal

import pytest

from scripts.extract_fidelity_reference import (
    REFERENCE_TABLES,
    departement_from_insee,
    dumps_sorted,
    parse_panel,
    to_jsonable,
    write_manifest,
)
from scripts.measure_db_sizes import DEPARTEMENTS_102, department_of_partition


@pytest.mark.parametrize(
    ("relname", "expected"),
    [
        ("adresses_44", "44"),
        ("adresses_2a", "2A"),
        ("mutations_grain_974", "974"),
        ("adresses_default", None),
        ("adresses", None),
        ("geo_communes", None),
    ],
)
def test_department_of_partition(relname, expected):
    assert department_of_partition(relname) == expected


def test_departement_copy_matches_application_list():
    # The script carries a copy so it can run without the app package; the
    # copy must not drift from the source of truth.
    from app.etl.base import DEPARTEMENTS

    assert list(DEPARTEMENTS_102) == DEPARTEMENTS


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("44109", "44"),
        ("2A004", "2A"),
        ("97411", "974"),
        ("85047", "85"),
        (None, None),
        ("441", None),
    ],
)
def test_departement_from_insee(code, expected):
    assert departement_from_insee(code) == expected


def test_serialization_is_order_independent():
    a = {"b": 1, "a": {"y": Decimal("1.50"), "x": date(2024, 1, 2)}}
    b = {"a": {"x": date(2024, 1, 2), "y": Decimal("1.50")}, "b": 1}
    assert (
        dumps_sorted(to_jsonable(a)).encode() == dumps_sorted(to_jsonable(b)).encode()
    )


def test_decimal_and_null_are_preserved_exactly():
    assert to_jsonable(Decimal("12.30")) == "12.30"
    assert to_jsonable(None) is None
    assert to_jsonable(float("nan")) == "nan"


def test_write_manifest_hashes_files(tmp_path):
    payload = b'{"k": 1}\n'
    (tmp_path / "level1_counts.json").write_bytes(payload)
    # A stray file in the output directory must never enter the manifest.
    (tmp_path / "sizes.json").write_bytes(b"{}\n")
    history = tmp_path / "hist"
    history.mkdir()
    extra = history / "mutations_44_85_before_2021-01-01.csv.gz"
    extra.write_bytes(b"gz")

    path = write_manifest(
        tmp_path,
        source_label="unit",
        alembic_version="096",
        methods={"vacance": {"status": "ok"}},
        extracted_at="2026-09-21T00:00:00Z",
        script_sha256="0" * 64,
        extra_files=[extra],
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    names = [f["name"] for f in manifest["files"]]
    assert "hist" not in names and "manifest.json" not in names
    assert "sizes.json" not in names
    (entry,) = manifest["files"]
    assert entry == {
        "name": "level1_counts.json",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert manifest["dvf_history"][0]["sha256"] == hashlib.sha256(b"gz").hexdigest()
    assert manifest["departements"] == ["44", "85"]
    assert manifest["source"] == {"label": "unit", "alembic_version": "096"}


def test_write_manifest_hashes_panel_input(tmp_path):
    # The hand-written panel.json sits with the references and must be hashed.
    panel = b'{"codes": ["44109"], "selection": {"44109": "golden"}}\n'
    (tmp_path / "panel.json").write_bytes(panel)
    (tmp_path / "level1_counts.json").write_bytes(b"{}\n")
    path = write_manifest(
        tmp_path,
        source_label="unit",
        alembic_version=None,
        methods={},
        extracted_at="2026-09-21T00:00:00Z",
        script_sha256="0" * 64,
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = {f["name"]: f for f in manifest["files"]}
    assert set(files) == {"level1_counts.json", "panel.json"}
    assert files["panel.json"]["sha256"] == hashlib.sha256(panel).hexdigest()


def test_parse_panel_accepts_list_and_file(tmp_path):
    assert parse_panel("85047, 44109,44109") == ["44109", "85047"]
    panel = tmp_path / "panel.json"
    panel.write_text(json.dumps({"codes": ["2a004", "44001"]}), encoding="utf-8")
    assert parse_panel(str(panel)) == ["2A004", "44001"]
    with pytest.raises(SystemExit):
        parse_panel("44109;DROP")


def test_reference_tables_are_unique_identifiers():
    assert len(REFERENCE_TABLES) == len(set(REFERENCE_TABLES)) == 30
    assert all(t.replace("_", "").isalnum() and t.islower() for t in REFERENCE_TABLES)

"""Floors per departement from the producer counts (plan 04.1-17)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.data_quality.profiles import PROFILES, effective_volume_floor
from app.etl_arcep.pipeline import ArcepPipeline
from app.scope import floors
from app.scope.floors import (
    REFERENCE_FLOOR_RATIO,
    ProducerCountMissingError,
    producer_departement_floor,
    producer_floor,
)
from app.scope.model import DEPARTEMENTS_HEBERGEABLES
from app.scope.producer_counts import PRODUCER_COUNTS

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
# Producer counts of plan 04.1-03 (2026-10-13), inputs of app/scope/producer_counts.py.
PRODUCTEURS = BACKEND / "tests" / "fixtures" / "producteurs"
TEMOIN = ("75", "67", "971")
# Served stock of 44/85 measured on the work base (plan 04.1-02): the 44/85
# floors must never refuse it.
SERVI_44_85 = {
    ("connectivite", "lignes_stockees"): 1_018_201,  # buildings (plan 16)
    ("connectivite", "lignes_importees"): 13_294_348,  # offer rows
}


def _floor(dataset: str, codes, unite: str) -> int:
    return sum(
        int(PRODUCER_COUNTS[dataset][unite][code] * REFERENCE_FLOOR_RATIO)
        for code in codes
    )


def _fidelite():
    spec = importlib.util.spec_from_file_location(
        "fidelite_nationale_floors", BACKEND / "scripts" / "fidelite_nationale.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_temoin_floor_is_the_sum_of_its_departements() -> None:
    for unite in ("lignes_importees", "lignes_stockees"):
        assert producer_floor("connectivite", TEMOIN, unite) == _floor(
            "connectivite", TEMOIN, unite
        )


def test_44_85_floors_never_refuse_the_served_stock() -> None:
    for (dataset, unite), servi in SERVI_44_85.items():
        floor = producer_floor(dataset, ("44", "85"), unite)
        assert floor is not None and floor <= servi


def test_national_floor_sums_every_hosted_departement_the_source_publishes() -> None:
    national = producer_floor(
        "connectivite", DEPARTEMENTS_HEBERGEABLES, "lignes_stockees"
    )
    assert national == _floor(
        "connectivite", DEPARTEMENTS_HEBERGEABLES, "lignes_stockees"
    )
    # No extrapolation of 44/85 any more: the producer says ~25.8 M buildings.
    assert 20_000_000 < national < 21_000_000


def test_a_departement_the_source_does_not_publish_has_no_floor() -> None:
    # DVF: Alsace-Moselle and Mayotte are outside the source (catalogue).
    assert producer_departement_floor("dvf", "67", "lignes_stockees") is None
    assert producer_floor("dvf", ("67", "44"), "lignes_stockees") == _floor(
        "dvf", ("44",), "lignes_stockees"
    )
    # 975 cannot be hosted (D-123): no floor, never zero expected.
    assert producer_departement_floor("connectivite", "975", "lignes_stockees") is None


def test_a_dataset_without_counts_keeps_its_former_floor() -> None:
    assert producer_floor("loyers", ("44",), "lignes_stockees") is None


def test_a_missing_count_is_an_error_never_a_zero(monkeypatch) -> None:
    sans_44 = {
        unite: {k: v for k, v in comptes.items() if k != "44"}
        for unite, comptes in PRODUCER_COUNTS["ban"].items()
    }
    monkeypatch.setitem(floors.PRODUCER_COUNTS, "ban", sans_44)
    with pytest.raises(ProducerCountMissingError, match="ban in departement 44"):
        producer_floor("ban", ("44", "85"), "lignes_stockees")


def test_import_and_quality_use_the_same_rule() -> None:
    pipeline = ArcepPipeline()
    pipeline._bound_scope = SimpleNamespace(
        departements=TEMOIN, covers_all_departements=False
    )
    assert pipeline.min_staging_rows == _floor(
        "connectivite", TEMOIN, "lignes_importees"
    )
    scope = SimpleNamespace(departements=TEMOIN, covers_all_departements=False)
    volume = PROFILES["connectivite"].volume
    assert effective_volume_floor(volume, scope, "connectivite") == _floor(
        "connectivite", TEMOIN, "lignes_stockees"
    )


def test_the_versioned_module_is_the_generator_output(tmp_path) -> None:
    out = tmp_path / "producer_counts.py"
    assert (
        _fidelite().main(
            ["floors", "--producteurs", str(PRODUCTEURS), "--out", str(out)]
        )
        == 0
    )
    versioned = BACKEND / "app" / "scope" / "producer_counts.py"
    assert out.read_bytes() == versioned.read_bytes().replace(b"\r\n", b"\n")

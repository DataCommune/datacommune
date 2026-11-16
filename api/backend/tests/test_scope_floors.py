"""Per-departement floors of national files: calibration locked on the reference."""

import json
from pathlib import Path

import pytest

import run_etl
from app.etl.base import DEPARTEMENTS
from app.etl_encadrement_loyers.pipeline import EncadrementLoyersPipeline
from app.scope.floors import (
    FALLBACK_MARGINS,
    REFERENCE_COUNTS,
    REFERENCE_FLOOR_RATIO,
    departement_floor,
    national_floor,
)

pytestmark = pytest.mark.unit

LEVEL1 = Path(__file__).parent / "fixtures" / "fidelity_ct275" / "level1_counts.json"
# Pipeline dataset -> table counted in the reference extraction.
REFERENCE_TABLE = {
    "antennes": "antennes",
    "copropriete": "copropriete",
    "demographie": "demographie",
    "equipements": "equipements",
    "fiscalite": "fiscalite_locale",
    "foncier_pm": "foncier_pm",
    "loyers": "indicateurs_loyers",
    "monuments": "monuments_historiques",
    "permis": "permis",
    "revenus": "revenus",
    "vacance": "vacance",
    "zonage_abc": "zonage_abc",
}


def test_reference_counts_equal_the_ct275_extraction():
    tables = json.loads(LEVEL1.read_text(encoding="utf-8"))["tables"]
    for dataset, table in REFERENCE_TABLE.items():
        counts = tables[table]["counts"]
        assert REFERENCE_COUNTS[dataset] == {"44": counts["44"], "85": counts["85"]}
    # The reference attributes these by a column that cannot hold 44/85
    # (carte_scolaire: "044"; gares: CODE_INSEE removed upstream) or not at
    # all (arrets, encadrement): scolaire is measured on the source instead,
    # the others have no reference and use the fallback margin.
    assert tables["carte_scolaire"]["counts"]["44"] == 0
    assert tables["gares"]["counts"]["44"] == 0
    assert set(REFERENCE_COUNTS) == set(REFERENCE_TABLE) | {"scolaire"}


def test_every_national_dataset_has_a_documented_margin():
    national = {
        cls().dataset_name
        for cls in run_etl.ALL_PIPELINES
        if cls().scope_filter_sql is not None
    } | {EncadrementLoyersPipeline.__new__(EncadrementLoyersPipeline).dataset_name}
    assert set(FALLBACK_MARGINS) == national
    assert all(0 < margin <= 1 for margin in FALLBACK_MARGINS.values())


def test_departement_floor_is_80_percent_of_reference_else_margin():
    assert REFERENCE_FLOOR_RATIO == 0.8
    assert departement_floor("loyers", "44", 100_000) == int(828 * 0.8)
    assert departement_floor("loyers", "01", 100_000) == int(100_000 / 102 * 0.5)
    assert departement_floor("transports", "44", 360_000) == int(360_000 / 102 * 0.5)
    assert national_floor("loyers", ("44", "85"), 100_000) == 662 + 809
    assert national_floor("loyers", DEPARTEMENTS, 100_000) == 100_000

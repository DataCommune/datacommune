"""Catalogue parity of GET /v1/datasets: reuse terms, quarantine, limits (D-40, LIC-01).

« Données & méthode » publishes the licence, the reuse restrictions and the
quarantine of every dataset from ``/v1/datasets`` only: nothing is copied in
the frontend. These tests pin the served fields to the canonical catalogue
(``app.common.source_catalog``), with and without a ``dataset_metadata`` row,
and pin the frontend fixture of the response to the same catalogue so the
page tests never run on a stale copy.

Pure tests: no DB, no network.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.common.schemas import ReponseApi
from app.common.source_catalog import DATASET_CATALOG
from app.datasets.router import build_dataset_info, list_dataset_infos
from app.datasets.schemas import DatasetInfo
from app.scope.model import SCOPE_EXCLUDED_KEYS
from tests.scope_doubles import SCOPE_44_85

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
_QUARANTINED = sorted(k for k, e in DATASET_CATALOG.items() if e.quarantaine)
_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "data"
    / "__fixtures__"
    / "datasets-api.json"
)


def _row(name: str, status: str = "disponible") -> dict:
    return {
        "dataset_name": name,
        "status": status,
        "last_updated_at": _NOW,
        "row_count": 10,
        "last_import_duration_seconds": 1.0,
        "cron_schedule": "0 2 * * 0",
        "freshness_threshold_days": 7,
        "last_success_at": _NOW,
        "last_run_status": "success",
        "last_run_started_at": _NOW,
    }


def _expected_reutilisation(key: str) -> dict:
    entry = DATASET_CATALOG[key]
    return {
        "licence": entry.licence,
        "licence_url": entry.licence_url,
        "conditions": entry.conditions_redistribution,
        "restrictions": list(entry.restrictions),
        "verifiee_le": date.fromisoformat(entry.licence_verifiee_le),
    }


def _expected_limites(key: str) -> dict | None:
    limites = DATASET_CATALOG[key].limites_source
    if limites is None:
        return None
    return {"chapeau": limites.chapeau, "puces": list(limites.puces)}


def test_two_datasets_are_quarantined_by_the_catalogue() -> None:
    # COR-07 (permis) and COR-09/COR-13 (encadrement des loyers): the page
    # shows both as « Non qualifié ». A third one is a decision, not a drift.
    assert _QUARANTINED == ["encadrement_loyers", "permis"]


@pytest.mark.parametrize("key", sorted(DATASET_CATALOG))
def test_row_serves_the_catalogue_reuse_terms(key: str) -> None:
    info = build_dataset_info(_row(key), now=_NOW)
    assert info.reutilisation is not None
    assert info.reutilisation.model_dump() == _expected_reutilisation(key)
    assert info.quarantaine == DATASET_CATALOG[key].quarantaine
    served_limites = info.limites_source.model_dump() if info.limites_source else None
    assert served_limites == _expected_limites(key)


@pytest.mark.parametrize("key", sorted(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS))
def test_key_without_row_serves_the_catalogue_reuse_terms(key: str) -> None:
    infos = {info.nom: info for info in list_dataset_infos([], SCOPE_44_85, _NOW)}
    info = infos[key]
    assert info.reutilisation is not None
    assert info.reutilisation.model_dump() == _expected_reutilisation(key)
    assert info.quarantaine == DATASET_CATALOG[key].quarantaine


@pytest.mark.parametrize("key", _QUARANTINED)
def test_quarantined_row_is_served_quarantined_without_freshness(key: str) -> None:
    info = build_dataset_info(_row(key, status="available"), now=_NOW)
    assert info.statut == "quarantined"
    assert info.quarantaine
    assert info.cron_schedule is None
    assert info.fraicheur is None
    assert info.prochaine_maj_prevue is None
    assert info.etat_pipeline == "degrade"


@pytest.mark.parametrize("key", _QUARANTINED)
def test_quarantined_key_without_row_is_served_quarantined(key: str) -> None:
    infos = {info.nom: info for info in list_dataset_infos([], SCOPE_44_85, _NOW)}
    assert infos[key].statut == "quarantined"
    assert infos[key].quarantaine == DATASET_CATALOG[key].quarantaine
    # The quarantine never hides the hosting: permis stays « source sans
    # couverture » on 44/85, with its reason.
    assert infos[key].hebergement is not None


def test_a_row_the_catalogue_does_not_quarantine_keeps_its_status() -> None:
    info = build_dataset_info(_row("dvf"), now=_NOW)
    assert info.statut == "disponible"
    assert info.quarantaine is None
    assert info.cron_schedule == "0 2 * * 0"


def test_off_catalogue_row_gets_no_reuse_terms() -> None:
    info = build_dataset_info(_row("table_hors_catalogue"), now=_NOW)
    assert info.reutilisation is None
    assert info.quarantaine is None
    assert info.limites_source is None
    assert info.provenance is None


def test_dvf_restrictions_and_limits_are_served_apart() -> None:
    info = build_dataset_info(_row("dvf"), now=_NOW)
    restrictions = " ".join(info.reutilisation.restrictions)
    assert "réidentification" in restrictions
    assert "indexation" in restrictions
    assert info.limites_source is not None
    limites = " ".join([info.limites_source.chapeau, *info.limites_source.puces])
    # Each territory is stated once, in the limits, never in the restrictions
    # (dossier 57-COPIE-DVF, rule 57-15; relecture D-15 of 02-02).
    for territoire in ("Bas-Rhin", "Mayotte"):
        assert territoire in limites
        assert territoire not in restrictions


# -- Frontend fixture of the response ------------------------------------------


def _fixture_infos() -> dict[str, DatasetInfo]:
    body = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    response = ReponseApi[list[DatasetInfo]].model_validate(body)
    return {info.nom: info for info in response.donnees}


def test_fixture_is_a_valid_datasets_response() -> None:
    infos = _fixture_infos()
    statuts = {info.statut for info in infos.values()}
    # Hosted, out of coverage, not imported, proxied and quarantined keys.
    expected = {"disponible", "hors_couverture", "non_importe", "proxy_amont"}
    assert expected | {"quarantined"} <= statuts


def test_fixture_carries_the_catalogue_fields_of_every_key() -> None:
    """The page tests read this fixture: it must say what the API serves.

    On a catalogue change, regenerate the fixture from the backend
    (``list_dataset_infos`` on the same rows) rather than editing it by hand.
    """
    infos = _fixture_infos()
    for key, info in infos.items():
        assert key in DATASET_CATALOG, f"{key}: absent du catalogue"
        assert info.reutilisation is not None, key
        assert info.reutilisation.model_dump() == _expected_reutilisation(key), key
        assert info.quarantaine == DATASET_CATALOG[key].quarantaine, key
        served = info.limites_source.model_dump() if info.limites_source else None
        assert served == _expected_limites(key), key
        assert info.statut == "quarantined" or key not in _QUARANTINED, key
        assert info.provenance is not None
        assert info.provenance.nom == DATASET_CATALOG[key].nom, key
        assert info.provenance.producteur == DATASET_CATALOG[key].producteur, key


# -- Seeded statuses of dataset_metadata (relecture D-15 of 03-11) -------------
#
# Migrations seed rows the API serves as they are: 'unknown' (004, 018),
# 'disponible' without any import (039) and 'proxy' (039). The public page
# words a closed vocabulary only: a seeded row is never « imported ».

_SERVED_STATUSES = {
    "disponible",
    "non_importe",
    "proxy_amont",
    "hors_couverture",
    "quarantined",
}


def _seeded(name: str, status: str, *, imported: bool) -> dict:
    row = _row(name, status=status)
    if not imported:
        row["last_success_at"] = None
        row["last_run_status"] = None
        row["last_run_started_at"] = None
    return row


@pytest.mark.parametrize(
    ("name", "status", "imported", "served"),
    [
        ("ban", "unknown", False, "non_importe"),  # 004 / 018
        ("dvf", "unknown", False, "non_importe"),  # 018
        ("dvf", "unknown", True, "non_importe"),  # never written by a success
        ("fiscalite", "disponible", False, "non_importe"),  # 039, no import
        ("bdnb", "proxy", False, "proxy_amont"),  # 039
        ("sirene", "proxy", False, "proxy_amont"),
        ("scoring", "proxy", False, "proxy_amont"),  # off-catalogue row of 039
        ("dvf", "disponible", True, "disponible"),  # written by a success
        ("permis", "disponible", False, "quarantined"),  # quarantine first
    ],
)
def test_seeded_status_is_served_in_the_closed_vocabulary(
    name: str, status: str, imported: bool, served: str
) -> None:
    info = build_dataset_info(_seeded(name, status, imported=imported), now=_NOW)
    assert info.statut == served


def test_no_seeded_row_without_an_import_is_served_as_imported() -> None:
    seeded = [
        _seeded(name, status, imported=False)
        for name, status in (
            ("ban", "unknown"),
            ("cadastre", "unknown"),
            ("antennes", "disponible"),
            ("vacance", "disponible"),
            ("georisques", "proxy"),
            ("foncier_pm", "disponible"),
        )
    ]
    infos = list_dataset_infos(seeded, SCOPE_44_85, _NOW)
    assert {info.statut for info in infos} <= _SERVED_STATUSES
    assert not [info.nom for info in infos if info.statut == "disponible"]

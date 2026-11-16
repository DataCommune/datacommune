"""Licence completeness of the canonical source catalog (LIC-01).

Every catalog entry must carry the licence read on the producer's own pages:
licence label, licence URL, redistribution terms, restrictions and the date
the page was consulted. DVF additionally carries the reuse restrictions of the
DGFiP (article R*112 A-3 LPF) and its territorial exclusions.

« Données & méthode » displays what ``GET /v1/datasets`` serves (D-40): the
served licence, conditions, restrictions and quarantine must be the
catalogue's, key by key (the page keeps no copy of its own).

Pure tests: no DB (the metadata query is patched), no network.
"""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.common.source_catalog import DATASET_CATALOG, RESOLVER_SOURCE_MAP
from app.etl_dvf.pipeline import DVF_NO_DATA_DEPARTEMENTS

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("key", sorted(DATASET_CATALOG))
def test_entry_carries_a_complete_verified_licence(key: str) -> None:
    entry = DATASET_CATALOG[key]
    assert entry.licence.strip(), f"{key}: empty licence"
    assert entry.licence_url.startswith("https://"), (
        f"{key}: licence_url must be an https URL, got {entry.licence_url!r}"
    )
    assert entry.conditions_redistribution.strip(), (
        f"{key}: empty conditions_redistribution"
    )
    assert isinstance(entry.restrictions, tuple), f"{key}: restrictions not a tuple"
    assert all(r.strip() for r in entry.restrictions), f"{key}: blank restriction"
    verified_on = date.fromisoformat(entry.licence_verifiee_le)
    assert verified_on <= date.today(), f"{key}: licence_verifiee_le in the future"


def test_catalog_has_forty_one_entries() -> None:
    # 04.7-04: altimetrie (IGN) and geologie (BRGM), read live by the report;
    # 04.7-05: axes_transport (IGN BD TOPO) and peb (DGAC); 04.7-06:
    # delinquance (SSMSI), eau_potable (Hub'Eau), annuaire_education;
    # 04.7-07: qualite_air (Atmo France), filosofi_carreaux, iris (INSEE).
    # 04.7-08: zone_tendue (DILA), encadrement_communes (Naistor).
    assert len(DATASET_CATALOG) == 41


@pytest.mark.parametrize(
    "key", sorted(k for k, e in DATASET_CATALOG.items() if "ODbL" in e.licence)
)
def test_odbl_entries_state_the_share_alike_condition(key: str) -> None:
    assert "partage à l'identique" in DATASET_CATALOG[key].conditions_redistribution


def test_dvf_carries_the_dgfip_reuse_restrictions() -> None:
    restrictions = DATASET_CATALOG["dvf"].restrictions
    assert len(restrictions) >= 2
    joined = " ".join(restrictions).lower()
    assert "réidentification" in joined
    assert "indexation" in joined


# Names under which the arbitrated DVF limits state the departments the
# producer does not publish (dossier 57-COPIE-DVF, family 5).
_DEPARTEMENT_NAMES = {
    "57": "Moselle",
    "67": "Bas-Rhin",
    "68": "Haut-Rhin",
    "976": "Mayotte",
}


def test_dvf_territorial_exclusions_cover_the_pipeline_no_data_departments() -> None:
    # A coverage fact, stated once to the visitor in the source limits served
    # with the dataset, never a second time in the reuse restrictions (57-15).
    limites = DATASET_CATALOG["dvf"].limites_source
    assert limites is not None, "dvf must state its territorial exclusions"
    text = " ".join([limites.chapeau, *limites.puces])
    missing = sorted(
        code
        for code in DVF_NO_DATA_DEPARTEMENTS
        if code not in _DEPARTEMENT_NAMES or _DEPARTEMENT_NAMES[code] not in text
    )
    assert not missing, f"dvf limits do not name departments {missing}"
    for restriction in DATASET_CATALOG["dvf"].restrictions:
        for name in _DEPARTEMENT_NAMES.values():
            assert name not in restriction, f"{name} stated twice (restrictions)"


def test_commune_carries_the_most_restrictive_conditions_of_its_sources() -> None:
    commune = DATASET_CATALOG["commune"]
    for source in RESOLVER_SOURCE_MAP["commune"]:
        for restriction in DATASET_CATALOG[source].restrictions:
            assert restriction in commune.restrictions, (
                f"commune drops a restriction of {source}: {restriction[:60]}"
            )


def _served_row(name: str) -> dict:
    return {
        "dataset_name": name,
        "status": "disponible",
        "last_updated_at": None,
        "row_count": 1,
        "last_import_duration_seconds": None,
        "cron_schedule": None,
        "freshness_threshold_days": None,
    }


@pytest.mark.asyncio
async def test_every_served_dataset_displays_the_catalog_licence(client) -> None:
    # « Données & méthode » renders the licence served by /v1/datasets and
    # nothing else (D-40): the served JSON must be the catalogue, key by key,
    # for an imported key as for a key without an import.
    loaded = ["dvf", "permis", "encadrement_loyers", "commune"]
    rows = AsyncMock(return_value=[_served_row(name) for name in loaded])
    with patch("app.datasets.router.get_all_datasets", new=rows):
        response = await client.get("/v1/datasets")
    assert response.status_code == 200, response.text
    served = {entry["nom"]: entry for entry in response.json()["donnees"]}
    assert set(served) == set(DATASET_CATALOG)
    divergences = []
    for key, entry in DATASET_CATALOG.items():
        reutilisation = served[key]["reutilisation"]
        expected = {
            "licence": entry.licence,
            "licence_url": entry.licence_url,
            "conditions": entry.conditions_redistribution,
            "restrictions": list(entry.restrictions),
            "verifiee_le": entry.licence_verifiee_le,
        }
        if reutilisation != expected:
            divergences.append(f"{key}: served {reutilisation!r}")
        if served[key]["quarantaine"] != entry.quarantaine:
            divergences.append(f"{key}: quarantaine {served[key]['quarantaine']!r}")
        if (served[key]["statut"] == "quarantined") != (entry.quarantaine is not None):
            divergences.append(f"{key}: statut {served[key]['statut']!r}")
    assert not divergences, "\n  ".join(["licence drift:", *divergences])

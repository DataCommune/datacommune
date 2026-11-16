"""Pure part of scripts/semantic_live_demo.py: what a route served for a key."""

import pytest

from scripts.semantic_live_demo import observe

pytestmark = pytest.mark.unit

OUT = {"etat": "hors_couverture", "motif": "dataset_non_heberge", "departements": []}


def _meta(key):
    return {"source": {}, "hebergement": {key: OUT}}


def test_list_route_out_of_scope_serves_an_empty_list_with_its_reason():
    obs = observe(
        "/v1/antennes", "antennes", 200, {"donnees": [], "meta": _meta("antennes")}
    )
    assert obs == {
        "statut_http": 200,
        "forme": "liste_vide",
        "etat": "hors_couverture",
        "motif": "dataset_non_heberge",
        "nb": 0,
        "donnees": [],
    }


def test_list_route_with_rows_is_not_an_empty_list():
    obs = observe("/v1/antennes", "antennes", 200, {"donnees": [{}, {}], "meta": {}})
    assert obs["nb"] == 2 and "donnees" not in obs and obs["etat"] == "heberge"


def test_zone_stats_keeps_null_indicators_never_zero():
    body = {"donnees": {"dpe_distribution": None}, "meta": _meta("dpe")}
    obs = observe("/v1/zone/stats", "dpe", 200, body)
    assert obs["indicateurs"] == {"dpe_distribution": None}
    assert obs["nb"] == 0 and obs["motif"] == "dataset_non_heberge"
    body = {"donnees": {"dpe_distribution": {"A": 2, "B": 0}}, "meta": {}}
    assert observe("/v1/zone/stats", "dpe", 200, body)["nb"] == 2


def test_zone_resolve_empty_envelope():
    body = {
        "donnees": {"results": [], "total_count": 0, "has_next": False},
        "meta": _meta("dpe"),
    }
    obs = observe("/v1/zone/resolve", "dpe", 200, body)
    assert obs["results"] == [] and obs["total_count"] == 0 and obs["has_next"] is False


def test_documentary_refusal_reads_the_reason_of_the_key_in_the_body():
    body = {"detail": "Hors couverture — dpe: dataset_non_heberge", "status": 409}
    obs = observe("/v1/bien/dpe/inventaire", "dpe", 409, body)
    assert obs["forme"] == "refus_409" and obs["motif"] == "dataset_non_heberge"
    other = {"detail": "Hors couverture — ban: dataset_non_heberge"}
    assert observe("/v1/bien/dpe/document", "dpe", 409, other)["motif"] is None


def test_datasets_entry_is_kept_with_its_hosting_state():
    body = {"donnees": [{"nom": "dpe", "hebergement": OUT, "nombre_lignes": 5}]}
    obs = observe("/v1/datasets", "dpe", 200, body)
    assert obs["entree_presente"] is True and obs["etat"] == "hors_couverture"
    assert obs["nb"] == 0
    hosted = {
        "donnees": [
            {"nom": "dpe", "hebergement": {"etat": "heberge"}, "nombre_lignes": 5}
        ]
    }
    assert observe("/v1/datasets", "dpe", 200, hosted)["nb"] == 5


def test_resolve_state_out_of_coverage_is_read_from_dataset_states():
    state = {
        "presence": "absent",
        "absence_reason": "out_of_coverage",
        "motif": "dataset_non_heberge",
    }
    body = {"donnees": [{"dataset_states": {"dpe": state}, "dpe_inventory": None}]}
    obs = observe("/v1/resolve", "dpe", 200, body)
    assert obs["etat"] == "hors_couverture" and obs["entree_presente"] is True
    assert obs["nb"] == 0


def test_logements_serving_dpe_documents_is_not_an_empty_form():
    body = {
        "donnees": {"dpe_inventory": {"items": [{}], "total": 1}},
        "meta": _meta("dpe"),
    }
    obs = observe("/v1/bien/logements", "dpe", 200, body)
    assert obs["forme"] == "contenu_dpe_servi" and obs["nb"] == 1


def test_an_unexpected_status_is_an_error_form():
    assert observe("/v1/dpe", "dpe", 500, None)["forme"] == "erreur"

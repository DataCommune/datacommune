"""Drift guard: the frontend comparison fixture is a response the API can serve.

``frontend/src/lib/data/__fixtures__/commune-comparaison.json`` holds frozen
harness responses of ``/v1/communes/comparaison`` (written by
``tests/integration/test_commune_comparison_contract.py`` through
``COMPARAISON_API_FIXTURE_OUTPUT``). The frontend composition and the 03-10
specs replay them: if the published schema or the semantic contract moves,
this test fails until the fixture is regenerated.
"""

import json
from pathlib import Path

import pytest

from app.common.semantic_audit import audit_commune_payload
from app.commune.schemas import ResultatComparaisonCommunes

pytestmark = pytest.mark.unit

# tests/test_comparison_fixture_contract.py -> parents[1] is api/backend,
# parents[2] is api/, parents[3] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = _REPO_ROOT / "frontend/src/lib/data/__fixtures__/commune-comparaison.json"


def _responses() -> dict[str, dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_responses()))
def test_the_fixture_is_valid_for_the_published_schema(name):
    body = _responses()[name]
    assert body["statut"] == "ok"
    result = ResultatComparaisonCommunes.model_validate(body["donnees"])
    assert 2 <= len(result.communes) + len(result.codes_inconnus) <= 4
    assert result.criteres
    for critere in result.criteres:
        assert [c.code_insee for c in critere.cellules] == [
            c.code_insee for c in result.communes
        ]


@pytest.mark.parametrize("name", sorted(_responses()))
def test_every_synthesis_of_the_fixture_passes_the_semantic_audit(name):
    body = _responses()[name]
    syntheses = [c["synthese"] for c in body["donnees"]["communes"] if c["synthese"]]
    assert syntheses
    for synthese in syntheses:
        payload = {"donnees": [synthese], "meta": body["meta"]}
        assert audit_commune_payload(payload) == []


def test_the_fixture_covers_the_cases_the_frontend_relies_on():
    responses = _responses()
    three = responses["trois_communes_dont_hors_couverture"]["donnees"]
    assert three["codes_inconnus"]
    assert any(c["synthese"] is None for c in three["communes"])
    served = [
        cellule
        for response in responses.values()
        for critere in response["donnees"]["criteres"]
        for cellule in critere["cellules"][1:]
    ]
    assert any(cellule["ecart"] is not None for cellule in served)
    motifs = {cellule["motif_non_calcul"] for cellule in served}
    assert {
        "periodes_differentes",
        "donnee_absente",
        "commune_hors_couverture",
    } <= motifs
    assert any(
        critere["identiques"]
        for critere in responses["deux_communes_hebergees"]["donnees"]["criteres"]
    )

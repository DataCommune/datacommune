"""The six MCP tools against the application (plans 04.12-02 and 04.12-03).

Each tool replays its /v1 route; the reads behind the route are doubles (same
method as tests/test_commune_plm.py). What is checked: the /v1 answer reaches
the agent projected, every value with its state, source, date and scale, and a
/v1 refusal is a tool error, never an empty result.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.adresse import router as adresse_router
from app.adresse.schemas import ComparaisonAdresses, RechercheAdresses
from app.adresse.service import AdresseInconnueError
from app.commune import router as commune_router
from app.datasets import router as datasets_router
from tests.mcp_client import application_ouverte, client_mcp

pytestmark = pytest.mark.unit

CHAMP_MAISON = "adresse.reference_commune.prix_median_maison"
SOURCE_DVF = {
    "cle": "dvf",
    "nom": "DVF",
    "producteur": "DGFiP",
    "url": "https://www.data.gouv.fr/",
}


@pytest.fixture
def ouverte(monkeypatch):
    with application_ouverte(monkeypatch):
        yield


async def _appeler(outil: str, arguments: dict):
    async with client_mcp() as session:
        return await session.call_tool(outil, arguments)


# --- Address tools ------------------------------------------------------------


async def test_chercher_adresse_returns_ban_keys_with_their_source(
    monkeypatch, ouverte
):
    recherche = RechercheAdresses.model_validate(
        {
            "ordre_types": ["adresse", "commune"],
            "resultats": [
                {
                    "type_resultat": "adresse",
                    "ban_id": "44109_1234_00012",
                    "numero": "12",
                    "nom_voie": "Rue d'essai",
                    "code_postal": "44000",
                    "code_commune": "44109",
                    "nom_commune": "Commune d'essai",
                }
            ],
            "hebergement": {"ban": {"etat": "heberge", "departements": ["44"]}},
        }
    )
    servir = AsyncMock(return_value=recherche)
    monkeypatch.setattr(adresse_router, "servir_recherche", servir)

    resultat = await _appeler("chercher_adresse", {"saisie": "12 rue d'essai"})

    assert resultat.isError is False, resultat.content
    sortie = resultat.structuredContent
    assert sortie["etat_recherche"] == "aboutie"
    assert sortie["resultats"][0]["ban_id"] == "44109_1234_00012"
    assert sortie["source"]["cle"] == "ban"
    assert sortie["couverture_ban"]["etat"] == "heberge"
    assert servir.await_args.args[1] == "12 rue d'essai"


async def test_an_unknown_address_is_a_tool_error_not_an_empty_page(
    monkeypatch, ouverte
):
    monkeypatch.setattr(
        adresse_router,
        "lire_fiche_adresse",
        AsyncMock(side_effect=AdresseInconnueError()),
    )
    resultat = await _appeler("lire_adresse", {"cle_ban": "44109_1234_00099"})

    assert resultat.isError is True
    assert "404" in resultat.content[0].text


async def test_comparer_adresses_keeps_gap_reason_and_absence(monkeypatch, ouverte):
    colonne = {
        "commune": {"code_insee": "44109", "nom": "Commune d'essai"},
        "departement": {"code": "44"},
        "couverture": {"etat": "heberge"},
    }
    comparaison = ComparaisonAdresses.model_validate(
        {
            "colonnes": [
                {**colonne, "ban_id": "44109_1234_00012"},
                {**colonne, "ban_id": "44109_1234_00014"},
            ],
            "groupes": [
                {
                    "cle": "commune_reference",
                    "titre": "Commune de l'adresse (référence)",
                    "lignes": [
                        {
                            "cle": "reference_commune.maisons",
                            "field_id": CHAMP_MAISON,
                            "identiques": False,
                            "cellules": [
                                {
                                    "ban_id": "44109_1234_00012",
                                    "field_id": CHAMP_MAISON,
                                    "etat": "value",
                                    "valeur": 3000,
                                    "periode": "2025",
                                    "source": SOURCE_DVF,
                                    "rattachement": "commune_reference",
                                },
                                {
                                    "ban_id": "44109_1234_00014",
                                    "field_id": CHAMP_MAISON,
                                    "etat": "insufficient_sample",
                                    "motif": "4 ventes",
                                    "source": SOURCE_DVF,
                                    "rattachement": "commune_reference",
                                    "motif_non_calcul": "donnee_absente",
                                    "identique_a_la_reference": False,
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    monkeypatch.setattr(
        adresse_router, "comparer_adresses", AsyncMock(return_value=comparaison)
    )

    resultat = await _appeler(
        "comparer_adresses", {"cles_ban": ["44109_1234_00012", "44109_1234_00014"]}
    )

    assert resultat.isError is False, resultat.content
    (ligne,) = resultat.structuredContent["lignes"]
    reference, comparee = ligne["cellules"]
    assert (reference["etat"], reference["valeur"], reference["date"]) == (
        "servi",
        3000,
        "2025",
    )
    assert (comparee["etat"], comparee["motif"], comparee["motif_non_calcul"]) == (
        "manquant",
        "effectif_insuffisant",
        "donnee_absente",
    )
    assert "valeur" not in comparee and "ecart" not in comparee


# --- Commune tools ------------------------------------------------------------


def _doubler_synthese(monkeypatch, lignes: dict[str, dict]) -> None:
    async def lire(_db, codes, _sources):
        return [dict(lignes[code]) for code in codes if code in lignes]

    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", lire)
    doubles = {
        "synthese_autre_maille": [],
        "fenetre_mediane_commune": None,
        "tendances_prix_par_codes": {},
        "get_provenance_facts": {},
    }
    for nom, retour in doubles.items():
        monkeypatch.setattr(commune_router, nom, AsyncMock(return_value=retour))


async def test_comparer_communes_serves_gaps_and_absences(monkeypatch, ouverte):
    _doubler_synthese(
        monkeypatch,
        {
            "44109": {
                "code_insee": "44109",
                "population": 320000,
                "annee_demographie": 2022,
            },
            "85191": {
                "code_insee": "85191",
                "population": None,
                "annee_demographie": 2022,
            },
        },
    )
    identites = {
        "44109": {"nom": "Commune A", "departement": "44"},
        "85191": {"nom": "Commune B", "departement": "85"},
    }
    monkeypatch.setattr(
        commune_router, "_commune_identities", AsyncMock(return_value=identites)
    )

    resultat = await _appeler("comparer_communes", {"codes_insee": ["44109", "85191"]})

    assert resultat.isError is False, resultat.content
    sortie = resultat.structuredContent
    assert [c["nom"] for c in sortie["communes"]] == ["Commune A", "Commune B"]
    (population,) = [c for c in sortie["criteres"] if c["cle"] == "population"]
    reference, comparee = population["cellules"]
    assert (reference["etat"], reference["valeur"], reference["date"]) == (
        "servi",
        320000,
        "2022",
    )
    assert reference["source"]["cle"] == "demographie"
    assert comparee["etat"] == "manquant"
    assert "valeur" not in comparee
    assert comparee["motif_non_calcul"] == "donnee_absente"


async def test_a_commune_out_of_coverage_is_said_so(monkeypatch, ouverte):
    from app.main import app
    from app.scope.dependencies import get_instance_scope
    from tests.scope_doubles import SCOPE_44_85

    app.dependency_overrides[get_instance_scope] = lambda: SCOPE_44_85
    _doubler_synthese(monkeypatch, {})

    resultat = await _appeler("lire_commune", {"code_insee": "75056"})

    assert resultat.isError is False, resultat.content
    sortie = resultat.structuredContent
    assert sortie["etat"] == "hors_couverture"
    assert sortie["mesures"] == []


async def test_lister_jeux_de_donnees_names_every_catalogue_dataset(
    monkeypatch, ouverte
):
    monkeypatch.setattr(datasets_router, "get_all_datasets", AsyncMock(return_value=[]))

    resultat = await _appeler("lister_jeux_de_donnees", {})

    assert resultat.isError is False, resultat.content
    jeux = {jeu["cle"]: jeu for jeu in resultat.structuredContent["jeux"]}
    assert {"dvf", "dpe", "ban"} <= set(jeux)
    assert jeux["dvf"]["hebergement"]["etat"] == "heberge"

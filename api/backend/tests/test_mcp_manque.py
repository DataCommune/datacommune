"""A missing value stays missing through the MCP server (D-143, D-09).

The pivot of the phase: a value /v1 serves as an absence (null with its state)
must reach an agent as ``manquant``, ``hors_couverture`` or ``panne``, without
any ``valeur`` key, never as 0; a measured zero stays ``zero`` (the five states
of invariant I-4, Ianis 2026-11-15). Played with the
official MCP client against the real application (middlewares, route,
coverage); only the database reads are replaced by doubles.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.adresse import router as adresse_router
from app.adresse.schemas import FicheAdresse
from app.commune import router as commune_router
from tests.mcp_client import application_ouverte, client_mcp

pytestmark = pytest.mark.unit

# A commune whose median is withheld (3 sales, under the publication threshold),
# whose count is a measured 3, and whose local tax rate is a measured 0.
LIGNE_COMMUNE = {
    "code_insee": "44109",
    "nom_commune": "Commune d'essai",
    "code_departement": "44",
    "population": 320000,
    "annee_demographie": 2022,
    "prix_m2_median": None,
    "nb_mutations_12m": 3,
    "taux_th_rs": 0.0,
    "annee_fiscalite": 2024,
}


@pytest.fixture
def application_sans_base(monkeypatch):
    with application_ouverte(monkeypatch):
        yield


@pytest.fixture
def synthese_double(monkeypatch, application_sans_base):
    async def lignes(_db, codes, _sources):
        return [dict(LIGNE_COMMUNE) for code in codes if code == "44109"]

    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", lignes)
    monkeypatch.setattr(
        commune_router, "synthese_autre_maille", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        commune_router, "fenetre_mediane_commune", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        commune_router, "get_provenance_facts", AsyncMock(return_value={})
    )


def _mesure(mesures: list[dict], champ: str) -> dict:
    (trouvee,) = [m for m in mesures if m["champ"] == champ]
    return trouvee


async def test_a_withheld_commune_median_reaches_the_agent_as_missing(
    synthese_double,
):
    async with client_mcp() as session:
        resultat = await session.call_tool("lire_commune", {"code_insee": "44109"})

    assert resultat.isError is False, resultat.content
    mesures = resultat.structuredContent["mesures"]
    mediane = _mesure(mesures, "dvf.prix_m2_median")
    assert mediane["etat"] == "manquant"
    assert mediane["motif"] == "effectif_insuffisant"
    assert "valeur" not in mediane
    assert mediane["echelle"] == "commune"
    assert mediane["source"]["cle"] == "dvf"
    # The text an agent reads carries the same absence, never a 0 or a null.
    texte = json.loads(resultat.content[0].text)
    assert "valeur" not in _mesure(texte["mesures"], "dvf.prix_m2_median")

    effectif = _mesure(mesures, "dvf.nb_mutations_12m")
    assert (effectif["etat"], effectif["valeur"]) == ("servi", 3)
    zero = _mesure(mesures, "fiscalite.taux_th_rs")
    assert (zero["etat"], zero["valeur"], zero["date"]) == ("zero", 0, "2024")


def _fiche_adresse() -> FicheAdresse:
    """An address page: no sale registered, clay read failed, no DPE declared."""
    source_dvf = {
        "cle": "dvf",
        "nom": "DVF",
        "producteur": "DGFiP",
        "url": "https://www.data.gouv.fr/",
    }
    source_ademe = {**source_dvf, "cle": "dpe", "nom": "DPE", "producteur": "ADEME"}
    source_georisques = {**source_dvf, "cle": "georisques", "nom": "Géorisques"}
    return FicheAdresse.model_validate(
        {
            "identite": {
                "ban_id": "44109_1234_00012",
                "libelle": "12 rue d'essai 44000 Commune d'essai",
                "commune": {"code_insee": "44109", "nom": "Commune d'essai"},
                "departement": {"code": "44", "nom": "Loire-Atlantique"},
            },
            "couverture": {"etat": "heberge"},
            "reperes": {
                "dpe": {
                    "field_id": "adresse.dpe.nombre_declares",
                    "etat": "value",
                    "valeur": 0,
                    "source": source_ademe,
                    "rattachement": "adresse_saisie_diagnostiqueur",
                },
                "ventes": {
                    "nombre": {
                        "field_id": "adresse.dvf.ventes_parcelle.nombre",
                        "etat": "not_reported",
                        "motif": "Aucune vente publiée sur la parcelle.",
                        "source": source_dvf,
                        "rattachement": "parcelle_du_point",
                    },
                    "derniere": {
                        "field_id": "adresse.dvf.ventes_parcelle.derniere",
                        "etat": "out_of_coverage",
                        "motif": "dataset_non_heberge",
                        "source": source_dvf,
                        "rattachement": "parcelle_du_point",
                    },
                },
                "argiles": {
                    "field_id": "adresse.risques.argiles_au_point",
                    "etat": "temporary_error",
                    "motif": "Géorisques ne répond pas.",
                    "source": source_georisques,
                    "rattachement": "au_point",
                },
                "reference_commune": {
                    "maisons": {
                        "field_id": "adresse.reference_commune.prix_median_maison",
                        "etat": "value",
                        "valeur": 3120,
                        "periode": "2025",
                        "source": source_dvf,
                        "rattachement": "commune_reference",
                    },
                    "appartements": {
                        "field_id": "adresse.reference_commune.prix_median_appartement",
                        "etat": "insufficient_sample",
                        "motif": "4 ventes",
                        "source": source_dvf,
                        "rattachement": "commune_reference",
                    },
                },
            },
        }
    )


async def test_absences_of_an_address_page_reach_the_agent_as_absences(
    monkeypatch, application_sans_base
):
    monkeypatch.setattr(
        adresse_router,
        "lire_fiche_adresse",
        AsyncMock(return_value=_fiche_adresse()),
    )
    async with client_mcp() as session:
        resultat = await session.call_tool(
            "lire_adresse", {"cle_ban": "44109_1234_00012"}
        )

    assert resultat.isError is False, resultat.content
    mesures = resultat.structuredContent["mesures"]
    ventes = _mesure(mesures, "adresse.dvf.ventes_parcelle.nombre")
    assert (ventes["etat"], ventes["motif"], ventes["echelle"]) == (
        "manquant",
        "non_renseigne",
        "parcelle",
    )
    assert "valeur" not in ventes
    derniere = _mesure(mesures, "adresse.dvf.ventes_parcelle.derniere")
    assert (derniere["etat"], derniere["motif"]) == (
        "hors_couverture",
        "dataset_non_heberge",
    )
    assert "valeur" not in derniere
    # An outage stays an outage (I-4, Ianis 2026-11-15): neither a lack nor a 0.
    argiles = _mesure(mesures, "adresse.risques.argiles_au_point")
    assert (argiles["etat"], argiles["motif"], argiles["precision"]) == (
        "panne",
        "source_indisponible",
        "Géorisques ne répond pas.",
    )
    assert "valeur" not in argiles
    dpe = _mesure(mesures, "adresse.dpe.nombre_declares")
    assert (dpe["etat"], dpe["valeur"], dpe["echelle"]) == ("zero", 0, "adresse")
    reference = _mesure(mesures, "adresse.reference_commune.prix_median_maison")
    assert (reference["valeur"], reference["date"], reference["echelle"]) == (
        3120,
        "2025",
        "commune",
    )
    appartements = _mesure(mesures, "adresse.reference_commune.prix_median_appartement")
    assert (appartements["etat"], appartements["precision"]) == (
        "manquant",
        "4 ventes",
    )
    assert "valeur" not in appartements

"""Pydantic schemas for Recensement demographie endpoints.

Source: INSEE — Recensement de la population.
"""

from pydantic import BaseModel, ConfigDict


class ResultatDemographie(BaseModel):
    """Population and housing indicators for a commune."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "75056",
                    "nom_commune": "Paris",
                    "population": 2133111,
                    "nb_menages": 1111111,
                    "nb_logements_vacants": 120000,
                    "annee": 2022,
                }
            ]
        }
    )

    code_insee: str
    nom_commune: str | None = None
    code_departement: str | None = None
    code_region: str | None = None
    population: int | None = None
    population_hommes: int | None = None
    population_femmes: int | None = None
    nb_menages: int | None = None
    nb_logements: int | None = None
    nb_residences_principales: int | None = None
    nb_logements_vacants: int | None = None
    nb_residences_secondaires: int | None = None
    superficie: float | None = None
    densite: float | None = None
    annee: int

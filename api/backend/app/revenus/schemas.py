"""Pydantic schemas for Filosofi revenus endpoints.

Source: INSEE — Filosofi (revenus medians, pauvrete par commune).
"""

from pydantic import BaseModel, ConfigDict, Field


class ResultatRevenus(BaseModel):
    """Household income indicators for a commune."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "75056",
                    "nom_commune": None,
                    "revenu_median": 31670,
                    "taux_pauvrete": 16.0,
                    "indice_gini": 0.526,
                    "annee": 2021,
                }
            ]
        }
    )

    code_insee: str
    nom_commune: str | None = None
    code_departement: str | None = None
    nom_departement: str | None = None
    nom_region: str | None = None
    nb_menages_fiscaux: int | None = Field(
        default=None,
        description="Nombre de ménages fiscaux de la série déclarée Filosofi (DEC).",
    )
    revenu_median: int | None = Field(
        default=None,
        description=(
            "Revenu déclaré médian annuel par unité de consommation, en "
            "euros, avant redistribution (Filosofi DEC, Q221). Ce revenu "
            "ne mesure pas le niveau de vie après impôts et prestations."
        ),
    )
    revenu_q1: int | None = Field(
        default=None,
        description=(
            "Premier quartile du revenu déclaré annuel par unité de "
            "consommation, en euros (Filosofi DEC)."
        ),
    )
    revenu_q3: int | None = Field(
        default=None,
        description=(
            "Troisième quartile du revenu déclaré annuel par unité de "
            "consommation, en euros (Filosofi DEC)."
        ),
    )
    revenu_d1: int | None = Field(
        default=None,
        description=(
            "Premier décile du revenu déclaré annuel par unité de "
            "consommation, en euros (Filosofi DEC)."
        ),
    )
    revenu_d9: int | None = Field(
        default=None,
        description=(
            "Neuvième décile du revenu déclaré annuel par unité de "
            "consommation, en euros (Filosofi DEC)."
        ),
    )
    indice_gini: float | None = Field(
        default=None,
        description=(
            "Indice de Gini du revenu déclaré par unité de consommation "
            "(Filosofi DEC), entre 0 et 1."
        ),
    )
    taux_pauvrete: float | None = Field(
        default=None,
        description=(
            "Taux de pauvreté au seuil de 60 % du niveau de vie médian "
            "métropolitain, en points de pourcentage. Calculé sur le "
            "revenu disponible après redistribution (Filosofi "
            "DISP_PAUVRES, TP6021), distinct du revenu déclaré."
        ),
    )
    part_imposee: float | None = Field(
        default=None,
        description=(
            "Part des ménages fiscaux imposés, en points de pourcentage "
            "(Filosofi DEC, PMIMP21)."
        ),
    )
    annee: int

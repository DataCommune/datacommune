"""Pydantic schemas for Loyers rental price indicator endpoints.

Source: Estimations ANIL, a partir des donnees du Groupe SeLoger
et de leboncoin.
"""

from pydantic import BaseModel, ConfigDict, Field


class ResultatLoyer(BaseModel):
    """Rental price indicator for a commune and housing type."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "75056",
                    "nom_commune": "Paris",
                    "type_habitat": "appartement",
                    "loyer_m2": 28.5,
                    "loyer_m2_bas": 19.8,
                    "loyer_m2_haut": 37.2,
                    "r2": 0.82,
                    "nb_observations": 12450,
                    "annee": 2025,
                }
            ]
        }
    )

    code_insee: str
    nom_commune: str | None = None
    type_habitat: str
    loyer_m2: float | None = Field(
        default=None,
        description=(
            "Loyer d'annonce prédit en €/m²/mois. Les métadonnées confirment "
            "le bien et la période de référence."
        ),
    )
    loyer_m2_bas: float | None = Field(
        default=None,
        description=(
            "Borne basse de l'intervalle de prédiction à 95 %, "
            "en €/m²/mois ; pas un quartile."
        ),
    )
    loyer_m2_haut: float | None = Field(
        default=None,
        description=(
            "Borne haute de l'intervalle de prédiction à 95 %, "
            "en €/m²/mois ; pas un quartile."
        ),
    )
    r2: float | None = None
    nb_observations: int | None = None
    annee: int
    id_zone: str | None = Field(
        default=None,
        description=(
            "Identifiant source de la maille d'estimation ; "
            "NULL si non conservé à l'import."
        ),
    )
    type_prediction: str | None = Field(
        default=None,
        description=(
            "TYPPRED source : commune, epci ou maille. La commune servie "
            "n'est pas nécessairement l'échelle d'estimation."
        ),
    )
    nb_observations_commune: int | None = None
    nb_observations_maille: int | None = None
    r2_ajuste: float | None = Field(
        default=None,
        description=(
            "R² ajusté du modèle (R2_adj source), "
            "distinct d'un intervalle de prédiction."
        ),
    )
    trimestre_reference: int | None = None
    charges_comprises: bool | None = None
    meuble: bool | None = None
    surface_reference_m2: float | None = None
    surface_piece_reference_m2: float | None = None

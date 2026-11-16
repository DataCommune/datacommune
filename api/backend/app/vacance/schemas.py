"""Pydantic schemas for LOVAC vacance endpoints.

Source: CEREMA — LOVAC logements vacants.
"""

from pydantic import BaseModel, ConfigDict, Field


class ResultatVacance(BaseModel):
    """Vacant housing indicators for a commune."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_commune": "44999",
                    "nom_commune": "Commune synthétique",
                    "nb_logements_prives": 100,
                    "nb_logements_vacants": 0,
                    "taux_vacance": 0,
                    "annee": 2025,
                }
            ]
        }
    )

    code_commune: str
    nom_commune: str | None = None
    code_departement: str | None = None
    nom_departement: str | None = None
    nom_region: str | None = None
    nb_logements_prives: int | None = None
    nb_logements_vacants: int | None = None
    nb_vacants_moins_2ans: int | None = None
    nb_vacants_plus_2ans: int | None = None
    taux_vacance: float | None = Field(
        default=None,
        description=(
            "Taux de vacance du parc privé, en %. "
            "Indisponible si le numérateur est inconnu ou négatif, "
            "ou si le parc privé est inconnu ou non positif. "
            "Un zéro mesuré est conservé."
        ),
    )
    taux_vacance_moins_2ans: float | None = Field(
        default=None,
        description=(
            "Taux de vacance de moins de deux ans du parc privé, en %. "
            "Indisponible si le numérateur est inconnu ou négatif, "
            "ou si le parc privé est inconnu ou non positif. "
            "Un zéro mesuré est conservé."
        ),
    )
    taux_vacance_plus_2ans: float | None = Field(
        default=None,
        description=(
            "Taux de vacance de plus de deux ans du parc privé, en %. "
            "Indisponible si le numérateur est inconnu ou négatif, "
            "ou si le parc privé est inconnu ou non positif. "
            "Un zéro mesuré est conservé."
        ),
    )
    annee: int

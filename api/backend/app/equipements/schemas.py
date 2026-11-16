"""Pydantic schemas for BPE equipements endpoints.

Source: INSEE --- Base Permanente des Equipements.
"""

from pydantic import BaseModel, ConfigDict


class ResultatEquipement(BaseModel):
    """Permanent facility entry."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type_equipement": "B203",
                    "code_commune": "75056",
                    "domaine": "commerces",
                    "latitude": 48.8566,
                    "longitude": 2.3522,
                    "annee": 2024,
                }
            ]
        }
    )

    type_equipement: str
    code_commune: str
    code_departement: str | None = None
    code_iris: str | None = None
    domaine: str | None = None
    sous_domaine: str | None = None
    nb_equipements: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    annee: int

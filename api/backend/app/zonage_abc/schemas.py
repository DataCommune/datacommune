"""Pydantic schemas for Zonage ABC housing tension zone endpoints.

Source: Ministere de la Transition Ecologique, arrete du 1er aout 2014
modifie. Zones: Abis (Paris+), A, B1, B2, C (decroissant en tension).
"""

from pydantic import BaseModel, ConfigDict


class ResultatZonageAbc(BaseModel):
    """Housing tension zone classification for a commune."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "75056",
                    "nom_commune": "Paris",
                    "code_departement": "75",
                    "zone": "Abis",
                    "reclassement": False,
                    "date_arrete": "2025-09-05",
                }
            ]
        }
    )

    code_insee: str
    nom_commune: str | None = None
    code_departement: str | None = None
    zone: str
    reclassement: bool | None = None
    date_arrete: str | None = None

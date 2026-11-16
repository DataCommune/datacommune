"""Pydantic schemas for ANFR antennes endpoints.

Source: ANFR — Observatoire 2G 3G 4G 5G.
"""

from pydantic import BaseModel, ConfigDict


class ResultatAntenne(BaseModel):
    """Mobile network antenna record."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "operateur": "ORANGE",
                    "generation": "5G",
                    "bande_frequence": "3500 MHz",
                    "code_insee": "75056",
                    "latitude": 48.8566,
                    "longitude": 2.3522,
                }
            ]
        }
    )

    operateur: str | None = None
    generation: str | None = None
    bande_frequence: str | None = None
    numero_station: str | None = None
    numero_support: str | None = None
    code_insee: str | None = None
    adresse: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    date_mise_en_service: str | None = None
    statut: str | None = None

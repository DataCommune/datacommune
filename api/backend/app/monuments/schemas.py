"""Pydantic schemas for Monuments Historiques endpoints."""

from pydantic import BaseModel, ConfigDict


class ResultatMonument(BaseModel):
    """Monument historique result with protection status and location."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "reference": "PA00086250",
                    "denomination": "Cathedrale Notre-Dame",
                    "adresse": "6 place du Parvis Notre-Dame",
                    "commune": "Paris",
                    "code_insee": "75056",
                    "protection": "classe",
                    "date_protection": "1862",
                    "latitude": 48.8530,
                    "longitude": 2.3499,
                }
            ]
        }
    )

    reference: str
    denomination: str | None = None
    adresse: str | None = None
    commune: str | None = None
    code_insee: str | None = None
    protection: str | None = None
    date_protection: str | None = None
    latitude: float
    longitude: float

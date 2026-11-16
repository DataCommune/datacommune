"""Pydantic schemas for BD TOPO batiments endpoints."""

from pydantic import BaseModel, ConfigDict


class ResultatBatiment(BaseModel):
    """BD TOPO building result with height, floors, and usage data."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "cleabs": "BATIMENT0000000123456789",
                    "nature": "Indifferenciee",
                    "usage_1": "Residentiel",
                    "nombre_d_etages": 5,
                    "hauteur": 15.50,
                    "altitude_minimale_sol": 35.20,
                    "altitude_maximale_toit": 50.70,
                }
            ]
        }
    )

    cleabs: str
    nature: str | None = None
    usage_1: str | None = None
    nombre_d_etages: int | None = None
    hauteur: float | None = None
    altitude_minimale_sol: float | None = None
    altitude_maximale_toit: float | None = None

"""Pydantic schemas for Transport endpoints."""

from pydantic import BaseModel, ConfigDict


class ResultatArret(BaseModel):
    """Transport stop or train station result with distance info."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "stop_name": "Gare de Lyon",
                    "latitude": 48.8443,
                    "longitude": 2.3735,
                    "distance_m": 142.5,
                    "location_type": 1,
                    "agency_name": "RATP",
                    "type_transport": "arret",
                }
            ]
        }
    )

    stop_name: str
    latitude: float
    longitude: float
    distance_m: float
    location_type: int | None = None
    agency_name: str | None = None
    type_transport: str = "arret"

"""Pydantic schemas for geocoding endpoints (field names in French)."""

from pydantic import BaseModel, ConfigDict


class ResultatAdresse(BaseModel):
    """Base address result with common fields from the adresses table."""

    ban_id: str | None = None
    numero: str | None = None
    rep: str | None = None
    nom_voie: str | None = None
    code_postal: str | None = None
    code_commune: str | None = None
    nom_commune: str | None = None
    longitude: float
    latitude: float


class ResultatGeocodage(ResultatAdresse):
    """Geocoding result with trigram similarity score (0-1)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ban_id": "75056_7560_00001",
                    "numero": "1",
                    "rep": None,
                    "nom_voie": "Rue de Rivoli",
                    "code_postal": "75001",
                    "code_commune": "75101",
                    "nom_commune": "Paris 1er Arrondissement",
                    "longitude": 2.3411,
                    "latitude": 48.8606,
                    "score": 0.92,
                }
            ]
        }
    )

    score: float


class ResultatReverseGeocodage(ResultatAdresse):
    """Reverse geocoding result with distance in meters to query point."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ban_id": "75056_7560_00001",
                    "numero": "1",
                    "rep": None,
                    "nom_voie": "Rue de Rivoli",
                    "code_postal": "75001",
                    "code_commune": "75101",
                    "nom_commune": "Paris 1er Arrondissement",
                    "longitude": 2.3411,
                    "latitude": 48.8606,
                    "distance_m": 12.5,
                }
            ]
        }
    )

    distance_m: float

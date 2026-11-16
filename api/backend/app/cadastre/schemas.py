"""Pydantic schemas for cadastre endpoints (field names in French)."""

from pydantic import BaseModel, ConfigDict


class ResultatParcelle(BaseModel):
    """Resultat d'une requete cadastrale."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id_parcelle": "75101000AB0001",
                    "commune": "PARIS 1ER ARRONDISSEMENT",
                    "prefixe": "000",
                    "section": "AB",
                    "numero": "0001",
                    "contenance": 12345,
                    "centroide_lat": 48.8606,
                    "centroide_lon": 2.3411,
                    "geometrie_geojson": (
                        '{"type":"MultiPolygon","coordinates":[[[[2.34,48.86]]]]}'
                    ),
                }
            ]
        }
    )

    id_parcelle: str
    commune: str | None = None
    prefixe: str | None = None
    section: str | None = None
    numero: str | None = None
    contenance: int | None = None
    centroide_lat: float
    centroide_lon: float
    geometrie_geojson: str

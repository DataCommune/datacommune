"""Pydantic schemas for renovation scoring endpoints."""

from datetime import date

from pydantic import BaseModel, ConfigDict

CSV_COLUMNS: list[str] = [
    "numero_dpe",
    "date_dpe",
    "annee_construction",
    "surface_habitable",
    "etiquette_dpe",
    "type_chauffage",
    "code_postal",
    "code_departement",
    "commune",
    "id_parcelle",
    "contenance",
    "date_derniere_vente",
    "prix_derniere_vente",
    "adresse",
    "longitude",
    "latitude",
]


class ScoringRenovTarget(BaseModel):
    """Scoring renovation: DPE passoire thermique croisee cadastre + DVF."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "numero_dpe": "2331E0987654321B",
                    "date_dpe": "2023-06-12",
                    "annee_construction": 1972,
                    "surface_habitable": 85.0,
                    "etiquette_dpe": "F",
                    "type_chauffage": "chaudiere fioul",
                    "code_postal": "44000",
                    "code_departement": "44",
                    "commune": "Nantes",
                    "id_parcelle": "44109000AB0123",
                    "contenance": 450,
                    "date_derniere_vente": "2019-03-22",
                    "prix_derniere_vente": 180000.0,
                    "adresse": "12 rue des Olivettes",
                    "longitude": -1.5536,
                    "latitude": 47.2136,
                }
            ]
        }
    )

    numero_dpe: str
    date_dpe: date | None = None
    annee_construction: int | None = None
    surface_habitable: float | None = None
    etiquette_dpe: str | None = None
    type_chauffage: str | None = None
    code_postal: str | None = None
    code_departement: str | None = None
    commune: str | None = None
    id_parcelle: str | None = None
    contenance: int | None = None
    date_derniere_vente: date | None = None
    prix_derniere_vente: float | None = None
    adresse: str | None = None
    longitude: float
    latitude: float

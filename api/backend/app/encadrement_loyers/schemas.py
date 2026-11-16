"""Pydantic schemas for Encadrement des loyers endpoints.

Source: DRIHL — loyers de reference par quartier, nb pieces,
epoque de construction et type de location.
"""

from pydantic import BaseModel, ConfigDict


class ResultatEncadrementLoyer(BaseModel):
    """Rent control reference price for a quartier."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "annee": 2024,
                    "nom_quartier": "Halles",
                    "nb_pieces": "2",
                    "epoque_construction": "Apres 1990",
                    "type_location": "non meublé",
                    "loyer_reference": 26.7,
                    "loyer_reference_majore": 32.0,
                    "loyer_reference_minore": 18.7,
                    "ville": "PARIS",
                }
            ]
        }
    )

    annee: int
    secteur: int | None = None
    numero_quartier: int | None = None
    nom_quartier: str | None = None
    nb_pieces: str
    epoque_construction: str
    type_location: str
    loyer_reference: float | None = None
    loyer_reference_majore: float | None = None
    loyer_reference_minore: float | None = None
    ville: str
    code_insee_quartier: str | None = None

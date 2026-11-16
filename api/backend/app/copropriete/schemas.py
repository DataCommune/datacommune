"""Pydantic schemas for RNIC copropriete endpoints.

Source: ANAH — Registre National des Coproprietes.
"""

from pydantic import BaseModel, ConfigDict


class ResultatCopropriete(BaseModel):
    """Copropriete registry entry."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "numero_immatriculation": "AA-0001-AX",
                    "nom_copropriete": "Residence Les Lilas",
                    "code_commune": "75115",
                    "nb_lots_total": 120,
                    "type_syndic": "professionnel",
                }
            ]
        }
    )

    numero_immatriculation: str
    nom_copropriete: str | None = None
    adresse: str | None = None
    code_commune: str | None = None
    code_postal: str | None = None
    nb_lots_total: int | None = None
    nb_lots_habitation: int | None = None
    nb_lots_commerciaux: int | None = None
    nb_lots_stationnement: int | None = None
    nom_syndic: str | None = None
    type_syndic: str | None = None
    periode_construction: str | None = None
    annee_reglement: int | None = None

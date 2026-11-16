"""Pydantic schemas for MAJIC personnes morales (legal entity parcels)."""

from pydantic import BaseModel, ConfigDict


class ResultatFoncierPm(BaseModel):
    """Legal entity property ownership record from MAJIC PM data."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_parcelle": "75101000AB0001",
                    "code_commune": "75056",
                    "nom_commune": "Paris",
                    "adresse": "12 Rue de Rivoli",
                    "contenance_parcelle": 2500,
                    "nature_culture": "S",
                    "code_droit": "P",
                    "numero_siren": "552032534",
                    "groupe_personne": 1,
                    "forme_juridique_abregee": "SA",
                    "denomination": "SOCIETE EXEMPLE SAS",
                }
            ]
        }
    )

    code_parcelle: str
    code_commune: str
    nom_commune: str | None = None
    adresse: str | None = None
    contenance_parcelle: int | None = None  # centiares (1 ca = 1 m2)
    nature_culture: str | None = None
    code_droit: str | None = None  # P=pleine prop, U=usufruit, N=nu-prop
    numero_siren: str | None = None
    groupe_personne: int | None = None
    forme_juridique_abregee: str | None = None
    denomination: str | None = None

"""Pydantic schemas for Carte Scolaire endpoints."""

from pydantic import BaseModel, ConfigDict


class ResultatScolaire(BaseModel):
    """Sector college result with establishment code and address match info."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_rne": "0750001A",
                    "code_insee": "75056",
                    "libelle_commune": "PARIS",
                    "type_et_libelle": "RUE VICTOR HUGO",
                    "n_de_voie_debut": 1.0,
                    "n_de_voie_fin": 15.0,
                    "parite": "I",
                    "secteur_unique": None,
                }
            ]
        }
    )

    code_rne: str
    code_insee: str | None = None
    libelle_commune: str | None = None
    type_et_libelle: str | None = None
    n_de_voie_debut: float | None = None
    n_de_voie_fin: float | None = None
    parite: str | None = None
    secteur_unique: str | None = None

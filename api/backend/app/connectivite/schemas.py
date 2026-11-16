"""Pydantic schemas for broadband eligibility endpoints.

Source: Arcep Ma Connexion Internet (base_imb + eligibilite par immeuble).
"""

from pydantic import BaseModel, ConfigDict


class ResultatEligibilite(BaseModel):
    """Single broadband eligibility record for a building."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id_imb": "0750100001A",
                    "code_insee": "75101",
                    "voie": "RUE DE RIVOLI",
                    "numero": "1",
                    "technologie": "FTTH",
                    "debit_max": "THD1G",
                    "operateur": "ORA",
                }
            ]
        }
    )

    id_imb: str
    code_insee: str
    voie: str | None = None
    numero: str | None = None
    technologie: str
    debit_max: str | None = None
    operateur: str | None = None

"""Compatibility schema for the quarantined local permits resource."""

from pydantic import BaseModel, ConfigDict, Field

from app.common.schemas import ReponseApi
from app.permis.availability import QUARANTINE_REASON


class ResultatPermis(BaseModel):
    """Building permit entry."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "numero_dossier": "COR07-SYNTHETIC",
                    "type_autorisation": "PC",
                    "code_commune": "93999",
                    "nb_logements_crees": 12,
                    "surface_plancher": 850.5,
                    "annee": 2024,
                }
            ]
        }
    )
    numero_dossier: str | None = None
    type_autorisation: str | None = None
    date_depot: str | None = Field(
        default=None,
        description="Non fournie ; jamais deduite du commencement.",
    )
    date_commencement: str | None = None
    date_decision: str | None = None
    code_commune: str | None = None
    nom_commune: str | None = None
    code_departement: str | None = None
    nature_projet: str | None = None
    nb_logements_crees: int | None = None
    surface_plancher: float | None = None
    type_construction: str | None = None
    destination: str | None = None
    annee: int


class ReponsePermis(ReponseApi[list[ResultatPermis]]):
    """Compatibility envelope with an explicit quarantine, never a proven absence."""

    statut: str = "indisponible"
    raison: str = QUARANTINE_REASON

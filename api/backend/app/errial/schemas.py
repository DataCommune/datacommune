"""Pydantic response models for ERRIAL proxy endpoint."""

import logging

from pydantic import BaseModel, ConfigDict, model_validator

from app.common.alerting import alert_upstream_unknown_fields
from app.common.proxy_coverage import ProxySource

logger = logging.getLogger(__name__)


class CommuneErrial(BaseModel):
    """Commune info from ERRIAL risk assessment."""

    code_insee: str | None = None
    nom: str | None = None
    code_postal: str | None = None
    code_departement: str | None = None


class PPR(BaseModel):
    """Plan de Prevention des Risques."""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _warn_unknown_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            unknown = sorted(set(data) - cls.model_fields.keys())
            if unknown:
                logger.warning(
                    "upstream_unknown_fields_dropped",
                    extra={"schema_name": cls.__name__, "unknown_fields": unknown},
                )
                alert_upstream_unknown_fields(cls.__name__, unknown)
        return data

    id_gaspar: str | None = None
    libelle: str | None = None
    date_approbation: str | None = None
    date_prescription: str | None = None


class AZI(BaseModel):
    """Atlas des Zones Inondables entry."""

    code: str | None = None
    libelle: str | None = None


class PollutionCounts(BaseModel):
    """Pollution site counts by proximity level."""

    basias_sur_parcelle: int | None = None
    basias_proximite: int | None = None
    basol_sur_parcelle: int | None = None
    basol_proximite: int | None = None
    sis_sur_parcelle: int | None = None
    sis_proximite: int | None = None
    icpe_sur_parcelle: int | None = None
    icpe_proximite: int | None = None


class ResultatErrial(BaseModel):
    """Response model for GET /v1/errial."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "adresse": "1 Rue de la Paix, Paris",
                    "code_parcelle": "000-AB-0069@75102",
                    "commune": {
                        "code_insee": "75102",
                        "nom": "Paris 2e Arrondissement",
                        "code_postal": "75002",
                        "code_departement": "75",
                    },
                    "sismicite": "1",
                    "radon": "1",
                    "argile": 0,
                    "ppr": [],
                    "azi": [],
                    "pollution": {
                        "basias_sur_parcelle": 0,
                        "basias_proximite": 2,
                        "basol_sur_parcelle": 0,
                        "basol_proximite": 0,
                        "sis_sur_parcelle": 0,
                        "sis_proximite": 0,
                        "icpe_sur_parcelle": 0,
                        "icpe_proximite": 1,
                    },
                    "nucleaire": False,
                    "recul_trait_cote": False,
                    "obligation_debroussaillement": False,
                    "zone_urbaine": True,
                    "plan_exposition_bruit": None,
                    "canalisations": 0,
                }
            ]
        }
    )

    adresse: str | None = None
    code_parcelle: str | None = None
    commune: CommuneErrial | None = None
    sismicite: str | None = None
    radon: str | None = None
    argile: int | None = None
    ppr: list[PPR] | None = None
    azi: list[AZI] | None = None
    pollution: PollutionCounts | None = None
    nucleaire: bool | None = None
    recul_trait_cote: bool | None = None
    obligation_debroussaillement: bool | None = None
    zone_urbaine: bool | None = None
    plan_exposition_bruit: str | None = None
    canalisations: int | None = None
    sources: dict[str, ProxySource] = {}

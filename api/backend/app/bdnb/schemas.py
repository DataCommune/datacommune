"""Pydantic response models for BDNB proxy endpoint.

Upstream-facing models inherit ProxyModel (A6-04): unknown upstream fields
are logged as a structured warning then dropped from the published contract,
while a new field added by the BDNB API never breaks the proxy.
"""

import logging

from pydantic import BaseModel, ConfigDict, model_validator

from app.common.alerting import alert_upstream_unknown_fields
from app.common.proxy_coverage import ProxySource

logger = logging.getLogger(__name__)


class ProxyModel(BaseModel):
    """Allowlist base for upstream-facing proxy models.

    extra="ignore" keeps the proxy resilient: a NEW field added upstream is
    never a 5xx for clients. The explicit allowlist is the set of declared
    fields; anything else is warned about (contract-drift signal) and dropped.
    """

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


class BatimentBDNB(ProxyModel):
    """Single building record from BDNB batiment_groupe_complet."""

    batiment_groupe_id: str | None = None
    code_commune_insee: str | None = None
    code_departement_insee: str | None = None
    libelle_adr_principale_ban: str | None = None
    annee_construction: int | None = None
    hauteur_mean: int | None = None
    s_geom_groupe: int | None = None
    classe_bilan_dpe: str | None = None
    conso_5_usages_ep_m2: float | None = None
    emission_ges_5_usages_m2: float | None = None
    mat_mur_txt: str | None = None
    mat_toit_txt: str | None = None
    l_parcelle_id: list[str] | None = None
    l_siren: list[str] | None = None
    l_denomination_proprietaire: list[str] | None = None
    distance_monument_historique: int | None = None
    altitude_sol_mean: int | None = None


class ResultatBDNB(BaseModel):
    """Response model for GET /v1/bdnb."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "batiments": [
                        {
                            "batiment_groupe_id": "bdnb-bc96e...",
                            "code_commune_insee": "75102",
                            "annee_construction": 1850,
                            "classe_bilan_dpe": "E",
                            "conso_5_usages_ep_m2": 320.5,
                            "l_parcelle_id": ["75102000AB0001"],
                        }
                    ],
                    "total": None,
                    "nombre_retournes": 1,
                }
            ]
        }
    )

    batiments: list[BatimentBDNB]
    total: int | None = None
    nombre_retournes: int = 0
    couverture: ProxySource | None = None

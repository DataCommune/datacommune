"""Pydantic response models for risques naturels and pollutions endpoints.

Upstream-facing models inherit ProxyModel (LB-04): unknown upstream fields
are logged as a structured warning then dropped from the published contract,
while a new field added by GASPAR / BASIAS / BASOL never breaks the proxy.
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


class RisqueDetail(ProxyModel):
    libelle_risque_long: str | None = None
    code_risque: str | None = None


class RisqueNaturel(ProxyModel):
    """Single natural risk record from GASPAR."""

    libelle_risque: str | None = None
    code_risque: str | None = None
    zone_sismicite: str | None = None
    risques_detail: list[RisqueDetail] | None = None


class ExpositionArgiles(ProxyModel):
    """RGA clay shrink-swell exposure level."""

    niveau: str | None = None
    exposition: str | None = None


class PotentielRadon(ProxyModel):
    """Radon potential class for a commune."""

    classe_potentiel: str | None = None


class InstallationClassee(ProxyModel):
    """ICPE classified installation record."""

    nom: str | None = None
    regime: str | None = None
    seveso: str | None = None


class ResultatRisques(BaseModel):
    """Unified response model for GET /v1/risques."""

    sources: dict[str, ProxySource] = {}

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "risques_naturels": [
                        {
                            "libelle_risque": "Inondation",
                            "code_risque": "11",
                        }
                    ],
                    "argiles": {"niveau": "moyen"},
                    "radon": [{"classe_potentiel": "2"}],
                    "icpe": [
                        {
                            "nom": "Usine XYZ",
                            "regime": "Autorisation",
                            "seveso": "Non Seveso",
                        }
                    ],
                }
            ]
        }
    )

    risques_naturels: list[RisqueNaturel] | None = None
    argiles: ExpositionArgiles | None = None
    radon: list[PotentielRadon] | None = None
    icpe: list[InstallationClassee] | None = None


class SitePollue(ProxyModel):
    """Single polluted site record."""

    nom: str | None = None
    adresse: str | None = None


class ResultatPollutions(BaseModel):
    """Unified response model for GET /v1/pollutions."""

    sources: dict[str, ProxySource] = {}

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "casias": [{"nom": "Ancien site industriel"}],
                    "instructions": [{"nom": "Site BASOL"}],
                    "conclusions_sis": [],
                    "conclusions_sup": [],
                }
            ]
        }
    )

    casias: list | None = None
    instructions: list | None = None
    conclusions_sis: list | None = None
    conclusions_sup: list | None = None

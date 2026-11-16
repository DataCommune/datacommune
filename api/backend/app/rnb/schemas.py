"""Pydantic response models for RNB proxy endpoint.

All upstream-facing models inherit ProxyModel (LB-04): unknown upstream
fields are logged as a structured warning then dropped from the published
contract, while a new field added upstream never breaks the proxy.
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


class AdresseRNB(ProxyModel):
    """Address attached to a RNB building."""

    street_number: str | None = None
    street_rep: str | None = None
    street: str | None = None
    city_name: str | None = None
    city_zipcode: str | None = None
    city_insee_code: str | None = None


class ExternalId(ProxyModel):
    """External identifier linking RNB to other databases."""

    id: str | None = None
    source: str | None = None


class PlotInfo(ProxyModel):
    """Cadastral plot intersection info."""

    id: str | None = None
    bdg_cover_ratio: float | None = None


class BatimentRNB(ProxyModel):
    """Single building record from RNB."""

    rnb_id: str | None = None
    status: str | None = None
    is_active: bool | None = None
    addresses: list[AdresseRNB] = []
    ext_ids: list[ExternalId] = []
    plots: list[PlotInfo] = []
    distance: float | None = None


class ResultatRNB(BaseModel):
    """Response model for GET /v1/rnb."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "batiments": [
                        {
                            "rnb_id": "ABCD1234",
                            "status": "constructed",
                            "is_active": True,
                            "addresses": [
                                {
                                    "street_number": "1",
                                    "street": "Rue de la Paix",
                                    "city_name": "Paris",
                                    "city_zipcode": "75002",
                                    "city_insee_code": "75102",
                                }
                            ],
                            "ext_ids": [{"id": "BATIMENT00001", "source": "bdtopo"}],
                            "plots": [
                                {"id": "75102000AB0069", "bdg_cover_ratio": 0.95}
                            ],
                        }
                    ],
                    "total": None,
                    "nombre_retournes": 1,
                }
            ]
        }
    )

    batiments: list[BatimentRNB]
    total: int | None = None
    nombre_retournes: int = 0
    couverture: ProxySource | None = None

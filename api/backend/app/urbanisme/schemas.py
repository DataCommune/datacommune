"""Pydantic response models for urbanisme zones PLU and prescriptions.

Upstream-facing models inherit ProxyModel (LB-04): unknown upstream fields
are logged as a structured warning then dropped from the published contract,
while a new field added by API Carto GPU never breaks the proxy.
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


class ZonePlu(ProxyModel):
    """Single PLU zone from API Carto GPU zone-urba endpoint."""

    libelle: str | None = None
    libelong: str | None = None
    typezone: str | None = None
    destdomi: str | None = None
    nomfic: str | None = None
    partition: str | None = None


class Prescription(ProxyModel):
    """Single surface prescription from API Carto GPU prescription-surf."""

    libelle: str | None = None
    txt: str | None = None
    typepsc: str | None = None


class ResultatUrbanisme(BaseModel):
    """Unified response model for GET /v1/urbanisme."""

    sources: dict[str, ProxySource] = {}

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "zones_plu": [
                        {
                            "libelle": "Ua",
                            "libelong": "Zone urbaine centre",
                            "typezone": "U",
                            "destdomi": "Habitat",
                        }
                    ],
                    "prescriptions": [
                        {
                            "libelle": "Espace boise classe",
                            "txt": "Art. L.130-1",
                            "typepsc": "01",
                        }
                    ],
                }
            ]
        }
    )

    zones_plu: list[ZonePlu] | None = None
    prescriptions: list[Prescription] | None = None

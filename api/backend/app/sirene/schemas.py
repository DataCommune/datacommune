"""Pydantic schemas for Sirene entreprises proxy endpoint.

Upstream-facing models inherit ProxyModel (LB-04): unknown upstream fields
are logged as a structured warning then dropped from the published contract,
while a new field added by the API Recherche d'Entreprises never breaks the
proxy.
"""

import logging

from pydantic import BaseModel, ConfigDict, model_validator

from app.common.alerting import alert_upstream_unknown_fields

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


class ResultatEntreprise(ProxyModel):
    """Single entreprise record from API Recherche d'Entreprises."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "siren": "443061841",
                    "nom_complet": "GOOGLE FRANCE",
                    "nom_raison_sociale": "GOOGLE FRANCE",
                    "siret": "44306184100047",
                    "activite_principale": "62.01Z",
                    "tranche_effectif_salarie": "51",
                    "latitude": 48.8566,
                    "longitude": 2.3522,
                    "distance": 0.5,
                }
            ]
        },
    )

    siren: str | None = None
    nom_complet: str | None = None
    nom_raison_sociale: str | None = None
    siret: str | None = None
    activite_principale: str | None = None
    tranche_effectif_salarie: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance: float | None = None

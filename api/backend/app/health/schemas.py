"""Health endpoint schemas (all fields in French)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DatasetHealthStatus(BaseModel):
    """Per-dataset freshness indicator for health endpoint."""

    nom: str
    fraicheur: str  # "fresh", "stale", "outdated"
    derniere_maj: datetime | None = None
    etat_pipeline: Literal["sain", "degrade", "echec"] = Field(
        default="sain",
        description="Synthèse fraîcheur + statut dernière run",
    )


class MatviewStatus(BaseModel):
    """Materialized view refresh status."""

    nom: str
    derniere_maj: datetime | None = None


class DeploymentVersion(BaseModel):
    environment: str
    deployment_sha: str
    schema_actual: str | None
    schema_expected: str


class ReponseHealth(BaseModel):
    """Reponse du endpoint de sante de l'API.

    ``technical_status`` and ``editorial_status`` are two independent aggregates
    (D-07/D-08): only a real technical unavailability (API/DB) drives HTTP 503,
    while an editorial delay (stale source, failed ETL, aged matview) stays
    HTTP 200 with a structured degraded editorial state. ``statut`` and the
    historical fields are preserved for backward compatibility.
    """

    statut: str  # "ok", "degradee", "deconnectee"
    technical_status: Literal["available", "unavailable"] = "available"
    editorial_status: Literal["current", "degraded", "unknown"] = "current"
    base_de_donnees: str
    version_postgis: str | None = None  # Major version only (audit S5)
    erreur: str | None = None
    datasets_status: list[DatasetHealthStatus] | None = None
    matviews: list[MatviewStatus] | None = None
    email_failures: int | None = None
    environment: str = "production"
    deployment_sha: str = "unknown"

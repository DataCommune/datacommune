"""Pydantic schemas for the dataset metadata endpoint."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.common.schemas import DatasetHosting, DatasetProvenance


class DerniereRun(BaseModel):
    """Details of the latest ETL run for a dataset."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "statut": "success",
                    "debut": "2026-02-09T14:00:00Z",
                    "fin": "2026-02-09T14:05:42Z",
                    "duree_secondes": 342.5,
                    "lignes_chargees": 26000000,
                    "erreur": None,
                }
            ]
        }
    )

    statut: str  # "success", "failed", "running"
    debut: datetime | None = None
    fin: datetime | None = None
    duree_secondes: float | None = None
    lignes_chargees: int | None = None
    erreur: str | None = None


class ReutilisationSource(BaseModel):
    """Reuse terms of a source, read on the producer's own pages (LIC-01).

    Copied from the canonical catalogue, never written by a client: the
    licence as published, its URL, the redistribution conditions, the reuse
    restrictions of the producer and the date the pages were consulted.
    """

    licence: str
    licence_url: str
    conditions: str
    restrictions: list[str] = Field(
        default_factory=list,
        description=(
            "Restrictions de réutilisation posées par le producteur (ex. DVF, "
            "article R*112 A-3 du LPF) ; vide si le producteur n'en pose pas."
        ),
    )
    verifiee_le: date = Field(
        description="Date de consultation des pages du producteur."
    )


class LimitesSource(BaseModel):
    """Limits of the source itself (coverage, market, cadence), arbitrated copy."""

    chapeau: str
    puces: list[str]


class DatasetInfo(BaseModel):
    """Metadata for an ingested dataset with freshness and run history."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "nom": "ban",
                    "statut": "disponible",
                    "derniere_maj": "2026-02-09T14:30:00Z",
                    "nombre_lignes": 26000000,
                    "duree_import_secondes": 342.5,
                    "cron_schedule": "0 2 * * 0",
                    "fraicheur": "fresh",
                    "prochaine_maj_prevue": None,
                    "derniere_run": {
                        "statut": "success",
                        "debut": "2026-02-09T14:00:00Z",
                        "fin": "2026-02-09T14:05:42Z",
                        "duree_secondes": 342.5,
                        "lignes_chargees": 26000000,
                        "erreur": None,
                    },
                }
            ]
        }
    )

    nom: str
    statut: str = Field(
        description=(
            "Vocabulaire fermé : « disponible » (un import a réussi), "
            "« non_importe » (aucun import réussi), « proxy_amont » (source lue "
            "en direct), « hors_couverture » (clé non hébergée, sans ligne) ou "
            "« quarantined » (quarantaine déclarée au catalogue)."
        )
    )
    derniere_maj: datetime | None = None
    nombre_lignes: int | None = None
    duree_import_secondes: float | None = None
    # cron_schedule and fraicheur describe DataCommune's INTERNAL import
    # planning and pipeline health — NOT the upstream source. They are distinct
    # from provenance.cadence (official source cadence) and provenance.etat
    # (editorial source freshness). Do not conflate the two (D-05).
    cron_schedule: str | None = None
    fraicheur: str | None = None  # internal pipeline state: fresh/stale/outdated
    etat_pipeline: Literal["sain", "degrade", "echec"] = Field(
        default="sain",
        description="Synthèse fraîcheur + statut dernière run (état interne pipeline)",
    )
    prochaine_maj_prevue: str | None = None  # human-readable or cron fallback
    derniere_run: DerniereRun | None = None
    provenance: DatasetProvenance | None = Field(
        default=None,
        description=(
            "Contrat de provenance structuré (attribution, période source, "
            "couverture, cadence officielle, état éditorial). None si le "
            "dataset n'est pas au catalogue canonique."
        ),
    )
    hebergement: DatasetHosting | None = Field(
        default=None,
        description=(
            "Hébergement par cette instance, lu dans le périmètre en base : "
            "« heberge » ou « hors_couverture » avec son motif (D-24). None "
            "pour une clé hors catalogue ou la synthèse commune."
        ),
    )
    reutilisation: ReutilisationSource | None = Field(
        default=None,
        description=(
            "Licence, conditions et restrictions de réutilisation relevées chez "
            "le producteur, lues au catalogue canonique (LIC-01). None pour une "
            "clé hors catalogue."
        ),
    )
    quarantaine: str | None = Field(
        default=None,
        description=(
            "Motif publié de la quarantaine quand le catalogue en déclare une : "
            "le statut est alors « quarantined » et les valeurs du jeu ne sont "
            "pas présentées comme fiables (D-40). None sinon."
        ),
    )
    limites_source: LimitesSource | None = Field(
        default=None,
        description=(
            "Limites propres à la source (territoire, marché, cadence), "
            "distinctes des restrictions de réutilisation. None si le "
            "catalogue n'en déclare pas."
        ),
    )

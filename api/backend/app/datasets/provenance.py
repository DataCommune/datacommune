"""Pure provenance assembly: static catalog + authentic dynamic facts.

This service (D-03) combines the canonical static catalog
(:data:`app.common.source_catalog.DATASET_CATALOG` — attribution, coverage
policy, official cadence) with the authentic dynamic facts persisted per
dataset (last successful import, upstream period/date, effective coverage) and
derives a structured :class:`DatasetProvenance`.

It performs no I/O: no DB session, no HTTP, no scheduler. The router loads rows
in one batch query and calls :func:`assemble_provenance` per row, so the
editorial rules live here once instead of being copied into the router.

Honesty rules (PROV-03, D-06):
- ``last_success_at`` proves a DataCommune import succeeded, never that the
  source published a new millesime — it is exposed only as
  ``derniere_importation_reussie``, never as a period;
- a period is ``connue`` only with a real upstream label or date, ``glissante``
  only for a live upstream proxy, otherwise ``inconnue``;
- an editorial state is ``actuel``/``retarde`` (the structured ``source_delayed``
  signal) only for an official cadence with a real upstream date; an irregular
  cadence or a missing upstream date stays ``inconnu``.

Legacy pipeline/import freshness (:func:`app.datasets.freshness.compute_freshness`)
stays a separate concern: it describes DataCommune's own ETL health, not the
source's editorial freshness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.common.schemas import (
    DatasetCoverage,
    DatasetProvenance,
    EditorialState,
    PublicationCadence,
    SourcePeriod,
)
from app.common.source_catalog import DATASET_CATALOG, DatasetCatalogEntry

# Editorial grace applied on top of the official cadence interval before a
# source is flagged ``retarde`` (source_delayed). Distinct from the ETL cron
# operational grace (D-05): this measures the source's own publication delay.
_EDITORIAL_GRACE = timedelta(days=2)


@dataclass(frozen=True)
class ProvenanceFacts:
    """Authentic dynamic facts for one dataset, read from the DB.

    All fields are optional: a pipeline that never declared an upstream period
    or date leaves them ``None`` so the assembly yields ``inconnue``/``inconnu``
    rather than an invented value.
    """

    last_success_at: datetime | None = None
    source_period_label: str | None = None
    source_updated_at: datetime | None = None
    effective_departments: list[str] | None = None


def jour_d_import(faits: ProvenanceFacts | None) -> date | None:
    """Day (Paris) of the last successful import; None if unknown."""
    if faits is None or faits.last_success_at is None:
        return None
    return faits.last_success_at.astimezone(ZoneInfo("Europe/Paris")).date()


def periode_de_source(faits: ProvenanceFacts | None) -> str | None:
    """Period of a value read from an imported table (decision C14, SEM-02).

    The millésime the import declared, else the upstream publication day (as
    ``/v1/datasets`` labels it), else the day of the last successful import:
    Ianis serves the import date as the period of a source that names no
    millésime (C14; extended to the commune comparator by C28). None when
    nothing is known: a period is never invented. Unlike the provenance block
    (PROV-03), where an import never becomes a period, this is the period of a
    served value.
    """
    if faits is None:
        return None
    if faits.source_period_label:
        return faits.source_period_label
    if faits.source_updated_at is not None:
        return faits.source_updated_at.date().isoformat()
    jour = jour_d_import(faits)
    return jour.isoformat() if jour is not None else None


def assemble_provenance(
    dataset_key: str,
    facts: ProvenanceFacts,
    now: datetime,
) -> DatasetProvenance:
    """Assemble a :class:`DatasetProvenance` from the catalog and dynamic facts.

    Args:
        dataset_key: canonical catalog key (e.g. ``"ban"``).
        facts: authentic dynamic facts for this dataset.
        now: reference time (injected for date-independent behavior/tests).

    Raises:
        KeyError: if ``dataset_key`` is not a canonical catalog entry.
    """
    entry = DATASET_CATALOG[dataset_key]
    return DatasetProvenance(
        nom=entry.nom,
        licence=entry.licence,
        url=entry.url,
        producteur=entry.producteur,
        dataset=entry.dataset,
        periode=_assemble_period(entry, facts),
        derniere_importation_reussie=facts.last_success_at,
        couverture=_assemble_coverage(entry, facts),
        cadence=entry.cadence,
        etat=_assemble_editorial_state(entry.cadence, facts, now),
    )


def _assemble_period(
    entry: DatasetCatalogEntry,
    facts: ProvenanceFacts,
) -> SourcePeriod:
    """Derive the source period without fabricating a millesime.

    ``connue`` only when a real upstream label or date exists; ``glissante`` for
    a live upstream proxy (its data always reflects the current upstream state);
    otherwise ``inconnue``.
    """
    if facts.source_period_label or facts.source_updated_at is not None:
        label = facts.source_period_label
        if label is None and facts.source_updated_at is not None:
            # Honest label derived from the real upstream date, not an import.
            label = facts.source_updated_at.date().isoformat()
        return SourcePeriod(statut="connue", label=label)
    if entry.coverage_policy == "upstream":
        return SourcePeriod(statut="glissante")
    return SourcePeriod(statut="inconnue")


def _assemble_coverage(
    entry: DatasetCatalogEntry,
    facts: ProvenanceFacts,
) -> DatasetCoverage:
    """Derive effective coverage from facts first, then catalog policy.

    Effective departments win when present. Otherwise a ``national``/``upstream``
    policy maps to ``nationale``/``amont``; an ``instance`` policy without an
    effective department fact stays ``inconnue``: the hosted scope configured in
    the database says what the instance hosts, not what a table covers.
    """
    if entry.dataset == "permis":
        # Historic row counts/departments cannot establish source coverage.
        return DatasetCoverage(type="inconnue")
    if facts.effective_departments:
        return DatasetCoverage(
            type="departements",
            departements=list(facts.effective_departments),
        )
    if entry.coverage_policy == "national":
        return DatasetCoverage(type="nationale")
    if entry.coverage_policy == "upstream":
        return DatasetCoverage(type="amont")
    return DatasetCoverage(type="inconnue")


def _assemble_editorial_state(
    cadence: PublicationCadence,
    facts: ProvenanceFacts,
    now: datetime,
) -> EditorialState:
    """Derive the editorial state from cadence + upstream date only.

    ``inconnu`` when the cadence is not officially published or the upstream
    date is unknown; ``actuel`` when an official cadence is respected;
    ``retarde`` (source_delayed) only once the official interval + grace is
    exceeded. An irregular cadence can never be ``retarde``.
    """
    if cadence.statut != "official" or cadence.intervalle_jours is None:
        return EditorialState.INCONNU
    if facts.source_updated_at is None:
        return EditorialState.INCONNU

    interval = timedelta(days=cadence.intervalle_jours)
    age = now - facts.source_updated_at
    if age <= interval + _EDITORIAL_GRACE:
        return EditorialState.ACTUEL
    return EditorialState.RETARDE

"""Departement pages: import dates, states and blocks (plan 04.7-10, D2 and D4).

Pure functions over the scope, the import journal and one row of
``v_departement_synthese``: no I/O. The routes of ``app.zone.router`` read,
these decide.

Dates (decision 4). A dataset is imported for departement D when its
``effective_departments`` (what its last publication actually covered)
contains D; its date is then the day, in Paris, of
``dataset_metadata.last_updated_at``: the runner writes both in one statement
of the publication transaction, so perimeter and date are one generation
(review Codex 04.7-10 n. 3). The run is marked ``success`` in ``etl_runs``
only after the cleanup that follows, so ``jour_d_import`` (provenance, read
in ``etl_runs``) may differ from it by a few minutes, and an interrupted run
may leave an older success beside a newer perimeter. A pipeline without a
departement perimeter (``effective_departments`` null) is read through the
``scope_snapshot`` and the ``completed_at`` of its last successful run, one
row hence one generation. A page's date is the latest such day among its
hosted and imported datasets, None when there is none: the import changes the
served data, never the response or the period.

States (decision 2), the vocabulary of ``/v1/commune``: a dataset out of
coverage is ``out_of_coverage`` with its D-24 reason; hosted but not imported
for D, or without any retained row, ``not_reported``; a median under the
threshold ``insufficient_sample`` with its real count; an F/G share without a
known class ``zero_denominator``. A block is never a fabricated 0.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.common.schemas import DatasetHosting
from app.commune.absences import EtatColonne
from app.commune.schemas import hosted_commune_sources
from app.datasets.provenance import ProvenanceFacts, jour_d_import
from app.dvf.couverture import SEUIL_ND_COMMUNE
from app.scope.coverage import hosting_for
from app.scope.model import InstanceScope
from app.zone.schemas import (
    ClassesDpe,
    DpeDepartement,
    FenetreDvf,
    PopulationDepartement,
    PrixDepartement,
    PrixTypeDepartement,
)

# The three sources of the departement page, in the order of its blocks.
PAGE_SOURCES: tuple[str, ...] = ("dvf", "dpe", "demographie")

_NOT_IMPORTED = (
    "Jeu hébergé pour le département, mais aucun import réussi ne l'a encore couvert."
)
_NO_DOCUMENT = "Aucun diagnostic DPE retenu pour le département."
_NO_KNOWN_CLASS = "Aucune classe DPE connue parmi les diagnostics du département."
_NO_POPULATION = "Aucune population communale chargée pour le département."
_KINDS = {"maison": "maisons", "appartement": "appartements"}


@dataclass(frozen=True)
class ImportFacts:
    """The last successful import of one dataset, as the journal records it."""

    last_success_at: datetime | None = None
    effective_departments: tuple[str, ...] | None = None
    # ``departements`` of the scope snapshot of that same run.
    scope_departements: tuple[str, ...] | None = None
    # ``dataset_metadata.last_updated_at``, written with
    # ``effective_departments`` by the same publication.
    metadata_updated_at: datetime | None = None


def _generation(facts: ImportFacts) -> tuple[tuple[str, ...] | None, datetime | None]:
    """Perimeter and moment of the last import, read from one generation.

    The declared perimeter with the publication that wrote it; without one,
    the scope snapshot with the run that recorded it.
    """
    if facts.effective_departments is not None:
        return facts.effective_departments, facts.metadata_updated_at
    return facts.scope_departements, facts.last_success_at


def imported_for(facts: ImportFacts | None, departement: str) -> bool:
    """Whether the last import of a dataset covered *departement*."""
    if facts is None:
        return False
    covered, moment = _generation(facts)
    return moment is not None and covered is not None and departement in covered


def import_day(
    keys: Iterable[str],
    scope: InstanceScope,
    facts: Mapping[str, ImportFacts],
    departement: str,
) -> date | None:
    """Latest import day (Paris) of the *keys* hosted and imported for D."""
    days = [
        jour_d_import(ProvenanceFacts(last_success_at=_generation(facts[key])[1]))
        for key in keys
        if hosting_for(key, scope, departement).etat == "heberge"
        and imported_for(facts.get(key), departement)
    ]
    return max((day for day in days if day is not None), default=None)


def page_day(
    scope: InstanceScope, facts: Mapping[str, ImportFacts], departement: str
) -> date | None:
    """Date of the departement page: its three sources."""
    return import_day(PAGE_SOURCES, scope, facts, departement)


def fiches_day(
    scope: InstanceScope, facts: Mapping[str, ImportFacts], departement: str
) -> date | None:
    """Date of the commune pages of D: the hosted sources of the commune page."""
    return import_day(
        hosted_commune_sources(scope, departement), scope, facts, departement
    )


def indexable(
    scope: InstanceScope, facts: Mapping[str, ImportFacts], departement: str
) -> bool:
    """DVF hosted AND imported for D: its pages enter the sitemap."""
    return hosting_for("dvf", scope, departement).etat == "heberge" and imported_for(
        facts.get("dvf"), departement
    )


def block_state(hosting: DatasetHosting, *, imported: bool) -> EtatColonne | None:
    """State a whole block takes from its source, or None when it is read."""
    if hosting.etat != "heberge":
        assert hosting.motif is not None  # DatasetHosting enforces it
        return EtatColonne(etat="out_of_coverage", motif=hosting.motif.value)
    if not imported:
        return EtatColonne(etat="not_reported", motif=_NOT_IMPORTED)
    return None


def _prix_type(
    row: Mapping[str, Any] | None, kind: str, state: EtatColonne | None
) -> PrixTypeDepartement:
    if state is not None:
        return PrixTypeDepartement(mediane_m2=None, effectif=None, etat=state)
    count = (row or {}).get(f"nb_mutations_12m_{kind}")
    median = (row or {}).get(f"prix_m2_median_{kind}")
    observations = _KINDS[kind]
    if not count:
        return PrixTypeDepartement(
            mediane_m2=None,
            effectif=None,
            etat=EtatColonne(
                etat="not_reported",
                motif=(
                    f"Aucune vente de {observations} retenue dans le département "
                    "sur la fenêtre des 12 derniers mois."
                ),
            ),
        )
    if median is None and count < SEUIL_ND_COMMUNE:
        return PrixTypeDepartement(
            mediane_m2=None,
            effectif=count,
            etat=EtatColonne(
                etat="insufficient_sample",
                motif=(
                    f"{count} ventes de {observations} retenues sur 12 mois, sous "
                    f"le seuil de publication de {SEUIL_ND_COMMUNE}."
                ),
            ),
        )
    # A null median from 5 sales is a defect of the view: the model refuses
    # it rather than inventing a state (as the directory, D-47).
    return PrixTypeDepartement(mediane_m2=median, effectif=count, etat=None)


def prix_block(
    row: Mapping[str, Any] | None, state: EtatColonne | None
) -> PrixDepartement:
    """DVF block of a view row (None: no retained row for the departement)."""
    maison = _prix_type(row, "maison", state)
    appartement = _prix_type(row, "appartement", state)
    counted = maison.effectif is not None or appartement.effectif is not None
    return PrixDepartement(
        fenetre=(
            FenetreDvf(debut=row["fenetre_debut"], fin=row["fenetre_fin"])
            if counted and row is not None
            else None
        ),
        maison=maison,
        appartement=appartement,
    )


def _absent_dpe(state: EtatColonne) -> DpeDepartement:
    return DpeDepartement(
        classes=None,
        inconnue=None,
        total=None,
        classes_connues=None,
        part_fg_pct=None,
        date_min=None,
        date_max=None,
        etat=state,
        etat_part_fg=state,
    )


def dpe_block(
    row: Mapping[str, Any] | None, state: EtatColonne | None
) -> DpeDepartement:
    """DPE block of a view row: counts per class, share of F and G, dates."""
    if state is not None:
        return _absent_dpe(state)
    if row is None or not row.get("dpe_total"):
        return _absent_dpe(EtatColonne(etat="not_reported", motif=_NO_DOCUMENT))
    known = row["dpe_classes_connues"]
    share = row["dpe_part_fg_pct"]
    return DpeDepartement(
        classes=ClassesDpe(**{c: row[f"dpe_{c.lower()}"] for c in "ABCDEFG"}),
        inconnue=row["dpe_inconnue"],
        total=row["dpe_total"],
        classes_connues=known,
        part_fg_pct=None if share is None else float(share),
        date_min=row["dpe_date_min"],
        date_max=row["dpe_date_max"],
        etat=None,
        etat_part_fg=(
            EtatColonne(etat="zero_denominator", motif=_NO_KNOWN_CLASS)
            if known == 0
            else None
        ),
    )


def population_block(
    row: Mapping[str, Any] | None, state: EtatColonne | None
) -> PopulationDepartement:
    """Sum of the communes' populations, with its base and its vintage."""
    if state is None and (row is None or row.get("population") is None):
        state = EtatColonne(etat="not_reported", motif=_NO_POPULATION)
    if state is not None:
        return PopulationDepartement(
            total=None, communes_comptees=None, annee=None, etat=state
        )
    assert row is not None
    return PopulationDepartement(
        total=row["population"],
        communes_comptees=row["population_communes"],
        annee=row["annee_population"],
        etat=None,
    )

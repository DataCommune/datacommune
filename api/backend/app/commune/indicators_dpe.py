"""Pure documentary calculations; neither a source adapter nor publication proof.

Callers supply one canonical identity and already established global graph facts.
No graph is rebuilt from a commune subset. The aggregate deliberately has no
generation, provenance or publication envelope. Only unverified revision evidence
is supported, matching the current document reader.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.commune.indicator_schemas import (
    CLASSES,
    DistributionDpeIndicateur,
    EnergyClass,
    IndicateurAbsent,
    IndicateurNombre,
    PopulationDpeIndicateur,
    ResultatDpeIndicateur,
)
from app.dpe.lifecycle import GRAPH_ISSUES, validity_at
from app.dpe.schemas import DpeCalendarDate, DpeLifecycleValidity

SourceState = Literal[
    "present", "absent_from_complete_scope", "explicitly_deactivated", "unknown"
]
ReplacementState = Literal["not_checked", "no_successor_known", "replaced", "ambiguous"]
GraphIssue = Literal["cycle", "fork", "missing_target", "truncated", "ambiguous_target"]
DateIssue = Literal["invalid_establishment_date", "invalid_end_date"]
Regime = Literal["existant", "neuf"]
BuildingType = Literal["maison", "appartement", "immeuble", "autre"]
BUILDING_MARGINS: tuple[Literal["maison", "appartement", "autre", "inconnu"], ...] = (
    "maison",
    "appartement",
    "autre",
    "inconnu",
)
REGIME_MARGINS: tuple[Literal["existant", "neuf", "inconnu"], ...] = (
    "existant",
    "neuf",
    "inconnu",
)
REMOVED_STATES = frozenset({"absent_from_complete_scope", "explicitly_deactivated"})
VALIDITIES: tuple[DpeLifecycleValidity, ...] = (
    "unknown",
    "expired",
    "not_expired_by_date",
    "inconsistent_dates",
    "future_establishment",
)
REPLACEMENTS: tuple[ReplacementState, ...] = (
    "not_checked",
    "no_successor_known",
    "replaced",
    "ambiguous",
)
SOURCE_STATES: tuple[SourceState, ...] = (
    "present",
    "absent_from_complete_scope",
    "explicitly_deactivated",
    "unknown",
)


class CanonicalDpeDocument(BaseModel):
    """Validated calculation projection, not evidence of source qualification.

    Regimes are the union already established for this canonical document, not
    individual observations to merge here. Missing classes/types use None;
    missing source/graph knowledge must be explicitly marked unknown/not_checked.
    Identity is private, excluded from serialization and rendered repr/errors.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    document_key: Annotated[
        str, Field(strict=True, min_length=1, repr=False, exclude=True)
    ]
    energy_class: EnergyClass | None
    building_type: BuildingType | None
    source_state: SourceState
    regimes: frozenset[Regime]
    is_tombstone: Annotated[bool, Field(strict=True)]
    established_at: DpeCalendarDate | None
    expires_at: DpeCalendarDate | None
    construction_year: Annotated[int, Field(strict=True, ge=1, le=9999)] | None
    replacement_state: ReplacementState
    graph_issues: frozenset[GraphIssue]
    date_issues: frozenset[DateIssue]
    revision_status: Literal["unverified"]

    @model_validator(mode="after")
    def coherent_projection(self) -> Self:
        if self.document_key != self.document_key.strip():
            raise ValueError("Canonical document identity must be nonblank and trimmed")
        if (
            "invalid_establishment_date" in self.date_issues
            and self.established_at is not None
        ) or ("invalid_end_date" in self.date_issues and self.expires_at is not None):
            raise ValueError("Invalid source date facts require an absent parsed date")
        return self


@dataclass(frozen=True)
class DpeDocumentaryCalculation:
    """Internal margins absent from the current wire contract stay separate.

    Construction preserves percentile_disc(.5) over known years. Graph reserves overlap;
    neither their sum nor overlapping regime margins count distinct documents.
    """

    as_of_date: date
    result: ResultatDpeIndicateur
    construction_known: int
    construction_unknown: int
    construction_median: int | None
    graph_issues: dict[str, int]
    source_states: dict[str, int]
    excluded_removed: int
    duplicate_inputs: int
    restricted_date_and_no_successor_known: int


def calculate_dpe_documents(  # noqa: C901
    documents: Iterable[CanonicalDpeDocument], *, as_of_date: date
) -> DpeDocumentaryCalculation:
    """Count a caller-selected commune population once, with one explicit date.

    Exact duplicate canonical inputs are idempotent. Any conflicting duplicate,
    even one later excluded, fails without leaking its private identifier.
    Tombstones OR removed source states are excluded conservatively. No class,
    date, graph reserve, BAN, geometry or address filter narrows the remainder.
    """
    as_of = TypeAdapter(DpeCalendarDate).validate_python(as_of_date)
    canonical: dict[str, CanonicalDpeDocument] = {}
    duplicates = 0
    for document in documents:
        if not isinstance(document, CanonicalDpeDocument):
            raise TypeError("Expected validated canonical DPE documents")
        previous = canonical.get(document.document_key)
        if previous is not None:
            if previous != document:
                raise ValueError("Conflicting canonical DPE documents")
            duplicates += 1
        else:
            canonical[document.document_key] = document

    classes: Counter[EnergyClass] = Counter()
    buildings: Counter[str] = Counter()
    validities: Counter[DpeLifecycleValidity] = Counter()
    replacements: Counter[ReplacementState] = Counter()
    regimes: Counter[str] = Counter()
    graphs: Counter[str] = Counter()
    sources: Counter[SourceState] = Counter()
    dates: list[date] = []
    years: list[int] = []
    total = removed = restricted = 0
    for document in canonical.values():
        if document.is_tombstone or document.source_state in REMOVED_STATES:
            removed += 1
            continue
        total += 1
        if document.energy_class is not None:
            classes[document.energy_class] += 1
        kind = document.building_type
        buildings[
            kind
            if kind in {"maison", "appartement"}
            else "inconnu"
            if kind is None
            else "autre"
        ] += 1
        state = validity_at(
            document.established_at,
            document.expires_at,
            as_of,
            date_issues=document.date_issues,
        )
        validities[state] += 1
        replacements[document.replacement_state] += 1
        regimes.update(document.regimes or {"inconnu"})
        graphs.update(document.graph_issues)
        sources[document.source_state] += 1
        if document.established_at is not None:
            dates.append(document.established_at)
        if document.construction_year is not None:
            years.append(document.construction_year)
        restricted += int(
            state == "not_expired_by_date"
            and document.replacement_state == "no_successor_known"
        )

    known = sum(classes.values())
    peak = max(classes.values(), default=0)
    modes = [label for label in CLASSES if known and classes[label] == peak]
    ordinal = None
    cumulative = 0
    for label in CLASSES:
        cumulative += classes[label]
        if known and cumulative * 2 >= known:
            ordinal = label
            break
    result = ResultatDpeIndicateur(
        etat="value",
        population=PopulationDpeIndicateur(
            total=total,
            classes_connues=known,
            types_batiment={k: buildings[k] for k in BUILDING_MARGINS},
            validite={k: validities[k] for k in VALIDITIES},
            remplacement={k: replacements[k] for k in REPLACEMENTS},
            regimes={k: regimes[k] for k in REGIME_MARGINS},
            source_inconnue=sources["unknown"],
            revision_non_verifiee=total,
        ),
        distribution=DistributionDpeIndicateur(
            classes={label: classes[label] for label in CLASSES},
            inconnue=total - known,
        ),
        modes=modes,
        mediane_ordinale=ordinal,
        part_fg_pct=(
            IndicateurNombre(
                etat="value", valeur=100.0 * (classes["F"] + classes["G"]) / known
            )
            if known
            else IndicateurAbsent(
                etat="zero_denominator", motif="Aucune classe DPE connue"
            )
        ),
        date_etablissement_min=min(dates, default=None),
        date_etablissement_max=max(dates, default=None),
        dates_etablissement_inconnues=total - len(dates),
    )
    return DpeDocumentaryCalculation(
        as_of_date=as_of,
        result=result,
        construction_known=len(years),
        construction_unknown=total - len(years),
        construction_median=sorted(years)[(len(years) - 1) // 2] if years else None,
        graph_issues={key: graphs[key] for key in GRAPH_ISSUES},
        source_states={key: sources[key] for key in SOURCE_STATES},
        excluded_removed=removed,
        duplicate_inputs=duplicates,
        restricted_date_and_no_successor_known=restricted,
    )

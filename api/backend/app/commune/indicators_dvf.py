"""Pure laboratory DVF counts and medians, never publication authorization.

Input is the complete reconstructed mutations_grain projection, not raw or
partially filtered dispositions. Its caller-provided hashes are traceability
pins, not self-validating proof. A future serving adapter must bind the source
to the reviewed manifest and current generation before exposing new indicators.

Decimal inputs become exact rational prices: a recurring quotient has no exact
finite Decimal representation. No float conversion, precision-context rounding,
or monetary quantization happens here. Public numeric conversion belongs to the
future adapter. Pending quantiles/trends never carry hidden numeric values.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.commune.indicator_schemas import (
    HostedCommuneCode,
    HostedDepartement,
    commune_departement,
    hosted_commune,
    hosted_departements,
)
from app.dvf.couverture import CLASSES_STATISTIQUES, couverture_suffisante


class DvfCalculationRefused(ValueError):  # noqa: N818 — typed input refusal
    def __init__(self, code: str = "invalid_canonical_input") -> None:
        self.code = code
        super().__init__(code)


def _decimal_input(value: Any) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Finite Decimal required")
    return value


def _date_input(value: Any) -> date:
    if type(value) is not date:
        raise ValueError("Calendar date required")
    return value


ExactDecimal = Annotated[Decimal, BeforeValidator(_decimal_input)]
CalendarDate = Annotated[date, BeforeValidator(_date_input)]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CommuneCode = HostedCommuneCode
Kind = Literal["residentiel_maison", "residentiel_appartement"]


class FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CanonicalMutation(FrozenInput):
    """One already reconstructed mutation, including ineligible records."""

    id_mutation: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^\S+$")]
    code_departement: HostedDepartement
    code_commune: CommuneCode | None
    date_mutation: CalendarDate | None
    classe_mutation: Literal[
        "residentiel_maison",
        "residentiel_appartement",
        "ensemble_residentiel",
        "mixte",
        "commerciale",
        "terrain_seul",
        "hors_perimetre",
    ]
    mono_local: Annotated[bool, Field(strict=True)]
    est_vente: Annotated[bool, Field(strict=True)] | None
    hors_stats_vefa: Annotated[bool, Field(strict=True)] | None
    nb_communes: Annotated[int, Field(strict=True, ge=0)] | None
    surface_bati_totale: ExactDecimal | None
    valeur_fonciere: ExactDecimal | None

    @model_validator(mode="after")
    def canonical_consistency(self) -> Self:
        if (
            self.code_commune
            and commune_departement(self.code_commune) != self.code_departement
        ):
            raise ValueError("Commune and department disagree")
        if self.classe_mutation in CLASSES_STATISTIQUES and not self.mono_local:
            raise ValueError("Residential statistical class must be mono-local")
        return self


class CanonicalStock(FrozenInput):
    """Explicit source contract; construction itself does not attest its truth."""

    input_contract: Literal["mutations_grain.complete-reconstruction.v1"]
    source_generation: UUID | None
    source_snapshot_sha256: Sha
    reconstruction_sha256: Sha
    mutations: tuple[CanonicalMutation, ...]


@dataclass(frozen=True)
class WindowStatistic:
    commune: str
    kind: Kind
    period: Literal["previous", "current"]
    start_inclusive: date
    end_exclusive: date
    count: int
    median_state: Literal["value", "empty_sample", "insufficient_sample"]
    median_eur_m2: Fraction | None
    quantiles_status: Literal["pending"] = "pending"


@dataclass(frozen=True)
class DvfLaboratoryResult:
    source_generation: UUID | None
    source_snapshot_sha256: str
    reconstruction_sha256: str
    observed_at: datetime
    end_exclusive: date
    cells: tuple[WindowStatistic, ...]
    trend_status: Literal["pending"] = "pending"
    publication_authorized: Literal[False] = False


def _previous_year(value: date) -> date:
    return value.replace(
        year=value.year - 1,
        day=min(value.day, monthrange(value.year - 1, value.month)[1]),
    )


def calculate_dvf(  # noqa: C901
    source: CanonicalStock,
    *,
    communes: tuple[str, ...],
    departments: tuple[str, ...],
    end_exclusive: date,
    observed_at: datetime,
) -> DvfLaboratoryResult:
    """Retain empty reference cells and suppress medians below the existing gate.

    Duplicate identities are checked globally before any eligibility/date/scope
    filter. Otherwise a conflicting ineligible copy could silently disappear.
    B and observation are explicit: no source maximum substitutes either date.
    *departments* are the received departements of the lot, as in the SQL
    projection: a mutation or a commune outside them refuses the calculation.
    """
    try:
        lot = hosted_departements(departments)
    except ValueError:
        raise DvfCalculationRefused() from None
    if (
        not isinstance(source, CanonicalStock)
        or type(end_exclusive) is not date
        or end_exclusive.year < 3
        or not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or end_exclusive > observed_at.date()
        or not isinstance(communes, tuple)
        or not communes
        or any(not hosted_commune(c, lot) for c in communes)
        or len(set(communes)) != len(communes)
    ):
        raise DvfCalculationRefused()
    middle = _previous_year(end_exclusive)
    start = _previous_year(middle)
    periods: tuple[tuple[Literal["previous", "current"], date, date], ...] = (
        ("previous", start, middle),
        ("current", middle, end_exclusive),
    )
    kinds: tuple[Kind, ...] = ("residentiel_maison", "residentiel_appartement")
    corpus: dict[tuple[str, str, str], list[Fraction]] = {
        (commune, kind, name): []
        for commune in sorted(communes)
        for kind in kinds
        for name, _, _ in periods
    }
    unique: dict[str, CanonicalMutation] = {}
    for row in source.mutations:
        previous = unique.get(row.id_mutation)
        if previous is not None and previous != row:
            raise DvfCalculationRefused("conflicting_canonical_mutation")
        unique[row.id_mutation] = row
    if any(row.code_departement not in lot for row in unique.values()):
        raise DvfCalculationRefused("mutation_outside_received_lot")
    for row in unique.values():
        if (
            row.classe_mutation not in CLASSES_STATISTIQUES
            or row.est_vente is not True
            or row.hors_stats_vefa is not False
            or row.nb_communes != 1
            or row.code_commune is None
            or row.date_mutation is None
            or row.surface_bati_totale is None
            or row.surface_bati_totale <= 0
            or row.valeur_fonciere is None
            or row.valeur_fonciere <= 0
        ):
            continue
        for name, lower, upper in periods:
            key = (row.code_commune, row.classe_mutation, name)
            if lower <= row.date_mutation < upper and key in corpus:
                corpus[key].append(
                    Fraction(row.valeur_fonciere) / Fraction(row.surface_bati_totale)
                )
                break
    cells = []
    for commune in sorted(communes):
        for kind in kinds:
            for name, lower, upper in periods:
                prices = corpus[(commune, kind, name)]
                n = len(prices)
                median = None
                state: Literal["value", "empty_sample", "insufficient_sample"] = (
                    "empty_sample" if n == 0 else "insufficient_sample"
                )
                if couverture_suffisante(n, "commune"):
                    ordered = sorted(prices)
                    median = (ordered[(n - 1) // 2] + ordered[n // 2]) / 2
                    state = "value"
                cells.append(
                    WindowStatistic(commune, kind, name, lower, upper, n, state, median)
                )
    return DvfLaboratoryResult(
        source.source_generation,
        source.source_snapshot_sha256,
        source.reconstruction_sha256,
        observed_at,
        end_exclusive,
        tuple(cells),
    )

"""Additive indicator contracts; no population calculation or publication policy."""

import re
from calendar import monthrange
from collections.abc import Collection, Sequence
from math import isclose
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.dpe.schemas import (
    SOURCE_SCOPE_DEPARTEMENTS,
    DpeCalendarDate,
    DpeLifecycleValidity,
    DpeSourceDepartement,
)
from app.dvf.couverture import SEUIL_ND_COMMUNE

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Count = Annotated[int, Field(strict=True, ge=0)]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Positive = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
EnergyClass = Literal["A", "B", "C", "D", "E", "F", "G"]
CLASSES: tuple[EnergyClass, ...] = ("A", "B", "C", "D", "E", "F", "G")

# Hostable departements (copy of app.scope.model.DEPARTEMENTS_HEBERGEABLES kept
# by app.dpe.schemas: public schemas never import the configured application).
HOSTED_DEPARTEMENTS = SOURCE_SCOPE_DEPARTEMENTS
HostedDepartement = DpeSourceDepartement
_ORDER = {code: index for index, code in enumerate(HOSTED_DEPARTEMENTS)}
# Hosted INSEE commune code: a hostable departement, then the commune number:
# two digits after an overseas departement (971..976), three otherwise. Same
# rule as app.scope.model.departement_from_insee and dc_departement_from_insee.
HOSTED_COMMUNE_PATTERN = (
    "^(?:(?:"
    + "|".join(code for code in HOSTED_DEPARTEMENTS if len(code) == 2)
    + ")[0-9]{3}|(?:"
    + "|".join(code for code in HOSTED_DEPARTEMENTS if len(code) == 3)
    + ")[0-9]{2})$"
)
_HOSTED_COMMUNE = re.compile(HOSTED_COMMUNE_PATTERN)
HostedCommuneCode = Annotated[
    str, StringConstraints(strict=True, pattern=HOSTED_COMMUNE_PATTERN)
]
DVF_QUALIFICATION_V2 = "dvf-delivery-qualification.v2"
DVF_QUALIFICATION_V3 = "dvf-delivery-qualification.v3"
# Reading rule of the v2 contract only: its lots were exactly 44 and 85. A v2
# anchor keeps this meaning forever; every other lot is qualified under v3.
V2_DEPARTEMENTS = frozenset({"44", "85"})


def commune_departement(code: str) -> str:
    """Departement of an INSEE commune code (overseas codes carry three digits)."""
    return code[:3] if code.startswith("97") else code[:2]


def hosted_commune(code: object, departements: Collection[str]) -> bool:
    """A hosted commune code whose departement is one of *departements*."""
    return (
        isinstance(code, str)
        and _HOSTED_COMMUNE.fullmatch(code) is not None
        and commune_departement(code) in departements
    )


def hosted_departements(value: object) -> tuple[str, ...]:
    """A non-empty list of distinct hostable departements, in canonical order."""
    if (
        not isinstance(value, (tuple, list))
        or not value
        or any(not isinstance(code, str) or code not in _ORDER for code in value)
        or list(value) != sorted(set(value), key=_ORDER.__getitem__)
    ):
        raise ValueError("Departements must be distinct hostable codes in order")
    return tuple(value)


class IndicatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class IndicateurAbsent(IndicatorModel):
    """No numeric or private payload is accepted in an absence branch."""

    etat: Literal[
        "not_reported",
        "out_of_coverage",
        "insufficient_sample",
        "suppressed",
        "not_qualified",
        "zero_denominator",
    ]
    motif: Text


class IndicateurNombre(IndicatorModel):
    etat: Literal["value"]
    valeur: Finite


NombreOuAbsence = Annotated[
    IndicateurNombre | IndicateurAbsent, Field(discriminator="etat")
]


class QuantilesDvf(IndicatorModel):
    etat: Literal["value"]
    p10: Positive
    p50: Positive
    p90: Positive
    qualification: Text

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError("Quantiles must be ordered")
        return self


QuantilesOuAbsence = Annotated[
    QuantilesDvf | IndicateurAbsent, Field(discriminator="etat")
]


class FenetreIndicateur(IndicatorModel):
    debut_inclus: DpeCalendarDate
    fin_exclue: DpeCalendarDate

    @model_validator(mode="after")
    def calendar_year(self) -> Self:
        end = self.fin_exclue
        start = (
            end.replace(
                year=end.year - 1,
                day=min(end.day, monthrange(end.year - 1, end.month)[1]),
            )
            if end.year > 1
            else None
        )
        if self.debut_inclus != start:
            raise ValueError("Window must cover exactly twelve calendar months")
        return self


class ProvenanceIndicateur(IndicatorModel):
    nom: Text
    producteur: Text
    licence: Text
    reference: Text
    date_publication_source: DpeCalendarDate | None
    date_snapshot: AwareDatetime | None
    date_import: AwareDatetime | None
    couverture: Literal["complete", "partial", "unknown"]


class ProvenanceDvf(ProvenanceIndicateur):
    """Historical event completeness is not established by a received lot."""

    couverture: Literal["unknown"]


class BlocIndicateur(IndicatorModel):
    version_contrat: Literal["1.0", "1.1"]
    version_population: Text
    date_calcul: AwareDatetime
    generation: UUID | None
    provenance: Sequence[ProvenanceIndicateur] = Field(min_length=1)

    @model_validator(mode="after")
    def chronology(self) -> Self:
        for source in self.provenance:
            if source.date_snapshot and source.date_snapshot > self.date_calcul:
                raise ValueError("Snapshot cannot follow calculation")
            if source.date_import and source.date_import > self.date_calcul:
                raise ValueError("Import cannot follow calculation")
        return self


class AncrageDvfInconnu(IndicatorModel):
    etat: Literal["unknown"]
    motif: Text


class LivraisonDvfRecue(IndicatorModel):
    """Public evidence references, not a verifier or supplier attestation.

    B ends this frozen received lot. Later corrections remain possible; no
    final historical completeness follows from receipt or reconstruction.
    """

    etat: Literal["delivery_received_and_stock_bound"]
    version_qualification: Literal[
        "dvf-delivery-qualification.v2", "dvf-delivery-qualification.v3"
    ]
    lot_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")]
    periode_debut_inclus: DpeCalendarDate
    periode_fin_exclue: DpeCalendarDate
    departements: list[HostedDepartement]
    acquisition_complete: Literal[True]
    revisions_possibles: Literal[True]
    exhaustivite_historique: Literal["unknown"]
    generation_fournisseur: None
    date_observation: AwareDatetime
    preuve_qualification_sha256: Sha256
    preuve_observation_sha256: Sha256
    preuve_reconstruction_sha256: Sha256
    snapshot_sha256: Sha256
    contenu_projete_sha256: Sha256
    cog_sha256: Sha256
    methode_projection: Literal["sha256-sorted-row-digests-json.v1"]
    generation_stock: UUID | None

    @model_validator(mode="before")
    @classmethod
    def explicit_facts(cls, value: object) -> object:
        if isinstance(value, dict) and any(
            value.get(key) is not True
            for key in ("acquisition_complete", "revisions_possibles")
        ):
            raise ValueError("Delivery facts require explicit true booleans")
        return value

    @model_validator(mode="after")
    def received_scope(self) -> Self:
        if self.version_qualification == DVF_QUALIFICATION_V2:
            if (
                len(self.departements) != len(V2_DEPARTEMENTS)
                or set(self.departements) != V2_DEPARTEMENTS
            ):
                raise ValueError(
                    "The v2 received scope is exactly departments 44 and 85"
                )
        else:
            # v3: the received departements of the lot, hostable, once, ordered.
            hosted_departements(self.departements)
        start, end = self.periode_debut_inclus, self.periode_fin_exclue
        if start >= end or (start.month, start.day, end.month, end.day) != (1, 1, 1, 1):
            raise ValueError("The lot must identify ordered complete annual files")
        if end > self.date_observation.date():
            raise ValueError(
                "A received annual period cannot end after its observation"
            )
        return self


AncrageTemporelDvf = Annotated[
    AncrageDvfInconnu | LivraisonDvfRecue, Field(discriminator="etat")
]


class StatistiquesFenetreDvf(IndicatorModel):
    fenetre: FenetreIndicateur | None
    motif_periode: Text | None = None
    effectif: Count | None
    mediane: NombreOuAbsence
    quantiles: QuantilesOuAbsence

    @model_validator(mode="after")
    def publication_consistency(self) -> Self:
        if self.fenetre is None:
            if (
                not self.motif_periode
                or self.effectif is not None
                or isinstance(self.mediane, IndicateurNombre)
                or isinstance(self.quantiles, QuantilesDvf)
            ):
                raise ValueError(
                    "Unknown window requires a reason and no published price"
                )
        elif self.motif_periode is not None:
            raise ValueError("A dated window must not carry an unknown-period reason")
        if isinstance(self.mediane, IndicateurNombre) and (
            self.effectif is None
            or self.effectif < SEUIL_ND_COMMUNE
            or self.mediane.valeur <= 0
        ):
            raise ValueError("Median requires positive prices and existing N gate")
        if isinstance(self.quantiles, QuantilesDvf):
            if not isinstance(self.mediane, IndicateurNombre):
                raise ValueError("Quantiles cannot expose a suppressed median")  # noqa: TRY004
            if self.quantiles.p50 != self.mediane.valeur:
                raise ValueError("P50 must equal the unrounded median")
        return self


class ClasseDvf(IndicatorModel):
    precedente: StatistiquesFenetreDvf
    courante: StatistiquesFenetreDvf
    variation_pct: NombreOuAbsence
    qualification_tendance: Text | None

    @model_validator(mode="after")
    def comparable(self) -> Self:
        before, current = self.precedente.fenetre, self.courante.fenetre
        if (
            before is not None
            and current is not None
            and before.fin_exclue != current.debut_inclus
        ):
            raise ValueError("Comparison windows must be consecutive and disjoint")
        if isinstance(self.variation_pct, IndicateurNombre):
            a, b = self.precedente.mediane, self.courante.mediane
            if not isinstance(a, IndicateurNombre) or not isinstance(
                b, IndicateurNombre
            ):
                raise ValueError("Variation requires both published medians")  # noqa: TRY004
            if not self.qualification_tendance:
                raise ValueError("Variation requires explicit qualification")
            expected = 100 * (b.valeur / a.valeur - 1)
            if not isclose(
                self.variation_pct.valeur, expected, rel_tol=1e-12, abs_tol=1e-10
            ):
                raise ValueError("Variation must use unrounded medians")
        return self


class IndicateursDvf(BlocIndicateur):
    version_contrat: Literal["1.1"]
    provenance: list[ProvenanceDvf] = Field(min_length=1)
    ancrage_temporel: AncrageTemporelDvf
    population: Literal["mutations_residentielles_mono_local_hors_vefa_mono_commune"]
    unite_prix: Literal["EUR/m2"]
    methode_quantiles: Literal["percentile_cont"]
    methode_variation: Literal["100*(mediane_courante/mediane_precedente-1)"]
    residentiel_maison: ClasseDvf
    residentiel_appartement: ClasseDvf

    @model_validator(mode="after")
    def common_windows(self) -> Self:
        a, b = self.residentiel_maison, self.residentiel_appartement
        if (a.precedente.fenetre, a.courante.fenetre) != (
            b.precedente.fenetre,
            b.courante.fenetre,
        ):
            raise ValueError("Both residential classes must share the delivery anchor")
        if any(
            window.fenetre and window.fenetre.fin_exclue > self.date_calcul.date()
            for window in (a.precedente, a.courante)
        ):
            raise ValueError("A transaction window cannot end after calculation")
        anchor = self.ancrage_temporel
        # Neither branch currently attests final historical completeness. A
        # future stronger evidence contract must use a new explicit branch.
        if any(p.couverture != "unknown" for p in self.provenance):
            raise ValueError(
                "Received-lot evidence does not qualify historical coverage"
            )
        if isinstance(anchor, AncrageDvfInconnu):
            if any(window.fenetre is not None for window in (a.precedente, a.courante)):
                raise ValueError(
                    "Unknown delivery anchor cannot invent analysis windows"
                )
        else:
            before, current = a.precedente.fenetre, a.courante.fenetre
            if (
                before is None
                or current is None
                or current.fin_exclue != anchor.periode_fin_exclue
                or before.debut_inclus < anchor.periode_debut_inclus
            ):
                raise ValueError(
                    "Both windows must belong to the same received lot ending at B"
                )
            if anchor.date_observation > self.date_calcul:
                raise ValueError("Receipt observation cannot follow calculation")
            if anchor.generation_stock != self.generation:
                raise ValueError(
                    "Delivery reconstruction must bind the indicator generation"
                )
        return self


class DistributionDpeIndicateur(IndicatorModel):
    classes: dict[EnergyClass, Count]
    inconnue: Count

    @model_validator(mode="after")
    def complete_keys(self) -> Self:
        if set(self.classes) != set(CLASSES):
            raise ValueError("All seven energy classes must be explicit")
        return self


class PopulationDpeIndicateur(IndicatorModel):
    total: Count
    classes_connues: Count
    types_batiment: dict[Literal["maison", "appartement", "autre", "inconnu"], Count]
    validite: dict[DpeLifecycleValidity, Count]
    remplacement: dict[
        Literal["not_checked", "no_successor_known", "replaced", "ambiguous"], Count
    ]
    regimes: dict[Literal["existant", "neuf", "inconnu"], Count]
    source_inconnue: Count
    revision_non_verifiee: Count

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        groups = (
            (self.types_batiment, {"maison", "appartement", "autre", "inconnu"}),
            (
                self.validite,
                {
                    "unknown",
                    "expired",
                    "not_expired_by_date",
                    "inconsistent_dates",
                    "future_establishment",
                },
            ),
            (
                self.remplacement,
                {"not_checked", "no_successor_known", "replaced", "ambiguous"},
            ),
        )
        for counts, keys in groups:
            if set(counts) != keys or sum(counts.values()) != self.total:
                raise ValueError("Disjoint dimensions must reconcile to documents")
        if set(self.regimes) != {"existant", "neuf", "inconnu"}:
            raise ValueError("All source regime margins must be explicit")
        known_regime = self.total - self.regimes["inconnu"]
        margins = self.regimes["existant"] + self.regimes["neuf"]
        if (
            known_regime < 0
            or max(self.regimes["existant"], self.regimes["neuf"]) > known_regime
            or not known_regime <= margins <= 2 * known_regime
        ):
            raise ValueError(
                "Regime margins overlap but must cover canonical documents"
            )
        if (
            max(self.classes_connues, self.source_inconnue, self.revision_non_verifiee)
            > self.total
        ):
            raise ValueError("Document counters exceed the canonical population")
        return self


class ResultatDpeIndicateur(IndicatorModel):
    etat: Literal["value"]
    population: PopulationDpeIndicateur
    distribution: DistributionDpeIndicateur
    modes: list[EnergyClass]
    mediane_ordinale: EnergyClass | None
    part_fg_pct: NombreOuAbsence
    date_etablissement_min: DpeCalendarDate | None
    date_etablissement_max: DpeCalendarDate | None
    dates_etablissement_inconnues: Count

    @model_validator(mode="after")
    def documentary_consistency(self) -> Self:  # noqa: C901
        counts = self.distribution.classes
        known = sum(counts.values())
        total = self.population.total
        if (
            self.population.classes_connues != known
            or total != known + self.distribution.inconnue
        ):
            raise ValueError(
                "Distribution denominator must count canonical diagnostics"
            )
        expected_modes = [
            c for c in CLASSES if known and counts[c] == max(counts.values())
        ]
        if self.modes != expected_modes:
            raise ValueError("Modes must contain all ties once in A-G order")
        cumulative = 0
        median = None
        for c in CLASSES:
            cumulative += counts[c]
            if known and cumulative * 2 >= known:
                median = c
                break
        if self.mediane_ordinale != median:
            raise ValueError("Ordinal median is percentile_disc, not the mode")
        if known:
            if not isinstance(self.part_fg_pct, IndicateurNombre) or not isclose(
                self.part_fg_pct.valeur,
                100 * (counts["F"] + counts["G"]) / known,
                rel_tol=1e-12,
                abs_tol=1e-10,
            ):
                raise ValueError("F/G share must use known classes as denominator")
        elif self.part_fg_pct.etat != "zero_denominator":
            raise ValueError("No known class means absent percentage, not zero")
        missing = self.dates_etablissement_inconnues
        if missing > total:
            raise ValueError("Unknown establishment dates exceed the population")
        low, high = self.date_etablissement_min, self.date_etablissement_max
        if missing == total:
            if low is not None or high is not None:
                raise ValueError("No observed dates means no invented date bounds")
        elif low is None or high is None or low > high:
            raise ValueError("Observed establishment dates require ordered bounds")
        observed = total - missing
        if observed == 1 and low != high:
            raise ValueError("One observed establishment date requires equal bounds")
        validity = self.population.validite
        # Each of these disjoint states requires a known establishment date in
        # validity_at. Unknown and inconsistent_dates may lack that date.
        if (
            validity["expired"]
            + validity["not_expired_by_date"]
            + validity["future_establishment"]
            > observed
        ):
            raise ValueError(
                "Dated validity states exceed observed establishment dates"
            )
        return self


class IndicateursDpe(BlocIndicateur):
    version_contrat: Literal["1.0"]
    population: Literal["diagnostics_canoniques_disponibles_hors_traces_retirees"]
    periode: Literal["bornes_etablissement_observees_non_exhaustives"]
    date_evaluation: DpeCalendarDate
    methode_mediane: Literal["percentile_disc"]
    methode_modes: Literal["toutes_classes_frequence_maximale_ordre_A_G"]
    resultat: Annotated[
        ResultatDpeIndicateur | IndicateurAbsent, Field(discriminator="etat")
    ]

    @model_validator(mode="after")
    def evaluation_date(self) -> Self:  # noqa: C901
        if self.date_evaluation > self.date_calcul.date():
            raise ValueError("Evaluation date cannot follow calculation")
        result = self.resultat
        if not isinstance(result, ResultatDpeIndicateur):
            return self
        low, high = result.date_etablissement_min, result.date_etablissement_max
        if low is None or high is None:
            return self
        observed = result.population.total - result.dates_etablissement_inconnues
        validity = result.population.validite
        future = validity["future_establishment"]
        inconsistent = validity["inconsistent_dates"]
        # These are necessary marginal constraints, not a reconstructed joint
        # distribution. Inconsistent source dates take precedence in validity_at.
        if high <= self.date_evaluation and future:
            raise ValueError("Nonfuture date bounds cannot contain future validity")
        if low <= self.date_evaluation and future >= observed:
            raise ValueError("The observed minimum includes a nonfuture document")
        if low >= self.date_evaluation and validity["expired"]:
            raise ValueError(
                "Expired validity requires establishment before evaluation"
            )
        if high >= self.date_evaluation and validity["expired"] >= observed:
            raise ValueError(
                "Expired states leave no known date for the observed maximum"
            )
        if high > self.date_evaluation and future + inconsistent == 0:
            raise ValueError(
                "A future maximum requires future or inconsistent validity"
            )
        if high > self.date_evaluation and (
            validity["expired"] + validity["not_expired_by_date"] >= observed
        ):
            raise ValueError(
                "Nonfuture validity states leave no date for the future maximum"
            )
        if low > self.date_evaluation and (
            validity["expired"]
            or validity["not_expired_by_date"]
            or future + inconsistent < observed
        ):
            raise ValueError("Future date bounds contradict the validity margins")
        return self

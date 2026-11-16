"""Typed absences of the flat ``/v1/commune`` columns (D-47).

A flat column served ``null`` says nothing about why. Each rule below derives
the reason from the served row and the hosting of the column's source only,
never from an extra read:

- ``out_of_coverage``: the source is not hosted for the commune; the reason is
  the D-24 key of ``couverture[source]`` (convention of 02-07);
- ``insufficient_sample``: a DVF median is null and its count is under
  ``SEUIL_ND_COMMUNE``; the real count is written in the reason;
- ``not_available``: the source has a row for the commune (its vintage or a
  sibling value is present) but this value is null;
- ``not_reported``: nothing proves a row of the source for the commune;
- ``zero_denominator``: the F/G share has no known class to divide by.

``suppressed`` is never produced: no import keeps the producer's
statistical-secrecy mark, and a state must be provable row by row (gate D-47,
decided by Ianis on 2026-09-24).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.schemas import DatasetHosting
from app.common.semantic_contract import ALLOWED_OUT_OF_COVERAGE_REASONS
from app.dvf.couverture import SEUIL_ND_COMMUNE

EtatAbsenceColonne = Literal[
    "out_of_coverage",
    "insufficient_sample",
    "not_available",
    "not_reported",
    "zero_denominator",
]


class EtatColonne(BaseModel):
    """Why a flat column is served null. No value is accepted in an absence."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    etat: EtatAbsenceColonne = Field(
        description=(
            "État de l'absence : hors couverture, effectif insuffisant, non "
            "publiée, non renseignée ou sans base de calcul."
        )
    )
    motif: Annotated[str, Field(min_length=1, pattern=r"\S")] = Field(
        description=(
            "Pour out_of_coverage, clé de allowed_out_of_coverage_reasons "
            "(dataset_non_heberge, territoire_hors_perimetre, "
            "source_sans_couverture) ; pour les autres états, phrase courte "
            "donnant la cause."
        )
    )
    # R-09: additive, omitted for every other absence.
    publiee_a: Literal["commune", "arrondissement"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Paris, Lyon, Marseille : maille où la source publie la valeur absente "
            "ici. « arrondissement » sur la commune (la valeur n'est jamais "
            "additionnée ni moyennée pour elle), « commune » sur un arrondissement "
            "(une statistique de la commune entière ne lui est pas attribuée). "
            "Omis sinon."
        ),
    )

    @model_validator(mode="after")
    def reason_matches_state(self) -> Self:
        if self.etat == "out_of_coverage":
            if self.motif not in ALLOWED_OUT_OF_COVERAGE_REASONS:
                raise ValueError("out_of_coverage needs a D-24 reason key")
        elif self.motif in ALLOWED_OUT_OF_COVERAGE_REASONS:
            raise ValueError("a D-24 reason only qualifies out_of_coverage")
        return self


@dataclass(frozen=True)
class SampleRule:
    """A median published only from ``SEUIL_ND_COMMUNE`` observations."""

    count_column: str
    # Plural noun of the counted observations, e.g. "mutations d'appartements".
    observations: str


@dataclass(frozen=True)
class ColumnAbsenceRule:
    """How a null flat column of the synthesis is qualified.

    ``presence``: columns whose non-null value proves a row of the source for
    the commune (its vintage, or a sibling value of the same row). Empty when
    nothing in the served row can prove it. ``zero_denominator``: reason of a
    share whose row is present but has nothing to divide by; ``block_state``
    is the path of an indicator absence that proves it on its own.
    """

    source: str
    not_reported: str
    presence: tuple[str, ...] = ()
    not_available: str | None = None
    sample: SampleRule | None = None
    zero_denominator: str | None = None
    block_state: tuple[str, ...] = ()


_NO_ROW = "La source chargée n'a aucune ligne pour la commune."
_ROW_WITHOUT_VALUE = (
    "La source a une ligne pour la commune à ce millésime, sans cette valeur."
)
_NO_SALE = (
    "Aucune mutation retenue pour la commune dans la fenêtre des 12 derniers mois."
)
_NO_DPE = "Aucun diagnostic DPE retenu pour la commune."
_NO_KNOWN_CLASS = "Aucune classe DPE connue parmi les diagnostics de la commune."
_NO_ZONE = "Aucune zone ABC chargée pour la commune."
_NO_PM_RIGHT = (
    "Aucun droit foncier de personne morale chargé pour la commune, ou "
    "identifiants ou surfaces incomplets."
)


def _source_row(
    source: str,
    vintage: str,
    values: tuple[str, ...],
    not_available: Mapping[str, str] | None = None,
) -> dict[str, ColumnAbsenceRule]:
    """Rules of the columns read from one row of *source* (latest vintage).

    A value is ``not_available`` when its vintage or a sibling value proves
    the row; the vintage itself is ``not_reported`` when null: the source has
    no row for the commune (its tables require a year).
    """
    specific = not_available or {}
    rules = {vintage: ColumnAbsenceRule(source=source, not_reported=_NO_ROW)}
    for column in values:
        rules[column] = ColumnAbsenceRule(
            source=source,
            not_reported=_NO_ROW,
            presence=(vintage, *(c for c in values if c != column)),
            not_available=specific.get(column, _ROW_WITHOUT_VALUE),
        )
    return rules


def _dvf_median(count: str, observations: str) -> ColumnAbsenceRule:
    return ColumnAbsenceRule(
        source="dvf", not_reported=_NO_SALE, sample=SampleRule(count, observations)
    )


# Column -> rule, for the 41 flat columns of the synthesis that carry a
# field_id. The source of each column is the one of ``COMMUNE_FIELD_SOURCES``
# (checked by tests/test_commune_absences.py). No rule produces
# ``suppressed`` (gate D-47).
COLUMN_ABSENCE_RULES: dict[str, ColumnAbsenceRule] = {
    **_source_row(
        "demographie",
        "annee_demographie",
        (
            "population",
            "nb_menages",
            "nb_logements",
            "nb_logements_vacants_recensement",
            "densite",
            "superficie",
        ),
    ),
    **_source_row(
        "revenus",
        "annee_revenus",
        ("revenu_median", "taux_pauvrete", "indice_gini", "part_imposee"),
    ),
    **_source_row(
        "vacance",
        "annee_vacance",
        ("taux_vacance", "nb_vacants_plus_2ans"),
        not_available={
            "taux_vacance": (
                "La ligne de la source pour ce millésime ne permet pas de "
                "publier le taux (taux ou parc privé absent ou nul)."
            )
        },
    ),
    **_source_row(
        "fiscalite",
        "annee_fiscalite",
        ("taux_tfpb", "taux_th_rs", "taux_teom", "nom_epci"),
    ),
    **_source_row(
        "equipements",
        "annee_equipements",
        ("nb_equipements",),
        not_available={
            "nb_equipements": (
                "Effectifs BPE incomplets au dernier millésime chargé : aucun "
                "total publié."
            )
        },
    ),
    "zone_abc": ColumnAbsenceRule(
        source="zonage_abc",
        not_reported=_NO_ZONE,
        presence=("zone_abc_reclassement",),
        not_available=_ROW_WITHOUT_VALUE,
    ),
    "zone_abc_reclassement": ColumnAbsenceRule(
        source="zonage_abc",
        not_reported=_NO_ZONE,
        presence=("zone_abc",),
        not_available=_ROW_WITHOUT_VALUE,
    ),
    # annee_loyers is null when the two types have different vintages: each
    # rent is proven by its own type object, which carries its own year.
    "loyer_m2_appartement": ColumnAbsenceRule(
        source="loyers",
        not_reported="Aucune prédiction de loyer des appartements pour la commune.",
        presence=("loyers_appartement",),
        not_available=(
            "La prédiction de loyer des appartements existe pour la commune, "
            "sans loyer au m²."
        ),
    ),
    "loyer_m2_maison": ColumnAbsenceRule(
        source="loyers",
        not_reported="Aucune prédiction de loyer des maisons pour la commune.",
        presence=("loyers_maison",),
        not_available=(
            "La prédiction de loyer des maisons existe pour la commune, sans "
            "loyer au m²."
        ),
    ),
    "annee_loyers": ColumnAbsenceRule(
        source="loyers",
        not_reported="Aucune prédiction de loyer pour la commune.",
        presence=("loyers_appartement", "loyers_maison"),
        not_available=(
            "Millésimes différents selon le type de logement : chaque objet "
            "loyers porte sa propre année."
        ),
    ),
    "prix_m2_median": _dvf_median("nb_mutations_12m", "mutations résidentielles"),
    "prix_m2_median_maison": _dvf_median(
        "nb_mutations_12m_maison", "mutations de maisons"
    ),
    "prix_m2_median_appartement": _dvf_median(
        "nb_mutations_12m_appartement", "mutations d'appartements"
    ),
    # A count is null only when the commune has no row in the DVF view.
    "nb_mutations_12m": ColumnAbsenceRule(source="dvf", not_reported=_NO_SALE),
    "nb_mutations_12m_maison": ColumnAbsenceRule(source="dvf", not_reported=_NO_SALE),
    "nb_mutations_12m_appartement": ColumnAbsenceRule(
        source="dvf", not_reported=_NO_SALE
    ),
    "dpe_passoires_pct": ColumnAbsenceRule(
        source="dpe",
        not_reported=_NO_DPE,
        presence=("annee_construction_mediane", "dpe_median"),
        zero_denominator=_NO_KNOWN_CLASS,
        block_state=("indicateurs_dpe", "resultat", "part_fg_pct"),
    ),
    "dpe_median": ColumnAbsenceRule(
        source="dpe",
        not_reported=_NO_DPE,
        presence=("annee_construction_mediane", "dpe_passoires_pct"),
        not_available=(
            "Aucune classe DPE connue parmi les diagnostics de la commune : "
            "pas de classe médiane."
        ),
    ),
    "annee_construction_mediane": ColumnAbsenceRule(
        source="dpe",
        not_reported=_NO_DPE,
        presence=("dpe_median", "dpe_passoires_pct"),
        not_available=(
            "Aucune année de construction renseignée dans les diagnostics de "
            "la commune."
        ),
    ),
    "nb_etablissements_scolaires": ColumnAbsenceRule(
        source="scolaire",
        not_reported=(
            "Aucune ligne de la carte scolaire chargée pour la commune, ou "
            "identifiants RNE incomplets."
        ),
    ),
    "nb_monuments": ColumnAbsenceRule(
        source="monuments",
        not_reported=(
            "Aucune notice Mérimée chargée pour la commune, ou références incomplètes."
        ),
    ),
    "nb_proprietaires_pm": ColumnAbsenceRule(
        source="foncier_pm",
        not_reported=_NO_PM_RIGHT,
        presence=("surface_pm_ha",),
        not_available=(
            "Identifiants SIREN incomplets dans les droits fonciers chargés : "
            "aucun compte publié."
        ),
    ),
    "surface_pm_ha": ColumnAbsenceRule(
        source="foncier_pm",
        not_reported=_NO_PM_RIGHT,
        presence=("nb_proprietaires_pm",),
        not_available=(
            "Surfaces de parcelles manquantes ou incohérentes : aucun total publié."
        ),
    ),
    "fibre_pct": ColumnAbsenceRule(
        source="connectivite",
        not_reported=(
            "Aucun immeuble chargé pour la commune, ou identifiants ou "
            "technologies manquants."
        ),
    ),
}


def _state(etat: str, motif: str) -> EtatColonne:
    return EtatColonne(etat=etat, motif=motif)


def _sample_state(rule: ColumnAbsenceRule, row: Mapping[str, Any]) -> EtatColonne:
    assert rule.sample is not None
    count = row.get(rule.sample.count_column)
    if count is None:
        return _state("not_reported", rule.not_reported)
    if count < SEUIL_ND_COMMUNE:
        return _state(
            "insufficient_sample",
            f"{count} {rule.sample.observations} retenues sur 12 mois, sous le "
            f"seuil de publication de {SEUIL_ND_COMMUNE}.",
        )
    return _state(
        "not_available",
        f"Médiane absente malgré {count} {rule.sample.observations} retenues "
        "sur 12 mois.",
    )


def _block_etat(row: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
    """``etat`` of the indicator object at *path* in *row*, if any."""
    node: Any = row
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node.get("etat") if isinstance(node, Mapping) else None


def column_state(
    rule: ColumnAbsenceRule,
    row: Mapping[str, Any],
    couverture: Mapping[str, DatasetHosting],
) -> EtatColonne:
    """State of one null column of *row*, the served synthesis row."""
    hosting = couverture[rule.source]
    if hosting.etat != "heberge":
        assert hosting.motif is not None  # DatasetHosting enforces it
        return _state("out_of_coverage", hosting.motif.value)
    if rule.sample is not None:
        return _sample_state(rule, row)
    present = any(row.get(column) is not None for column in rule.presence)
    if rule.zero_denominator and (
        present or _block_etat(row, rule.block_state) == "zero_denominator"
    ):
        return _state("zero_denominator", rule.zero_denominator)
    if rule.not_available and present:
        return _state("not_available", rule.not_available)
    return _state("not_reported", rule.not_reported)


def column_states(
    served_row: Mapping[str, Any], couverture: Mapping[str, DatasetHosting]
) -> dict[str, EtatColonne]:
    """State of every flat column served null in *served_row* (after masking)."""
    return {
        column: column_state(rule, served_row, couverture)
        for column, rule in COLUMN_ABSENCE_RULES.items()
        if served_row.get(column) is None
    }

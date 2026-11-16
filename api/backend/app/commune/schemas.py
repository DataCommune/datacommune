"""Pydantic schemas for the commune synthese endpoint.

Aggregates key indicators from 13 commune-level datasets: demographie,
revenus, zonage_abc, vacance, fiscalite, loyers, plus commune-level
aggregates of dvf, dpe, equipements, scolaire, monuments, foncier_pm
and connectivite.

D-01 (Phase 43, semantic contract): every `taux_*`/`*_pct` field is stored
and exposed in percentage points — never as a 0-1 ratio. See
`contracts/fiche-fields.v1.json` for the full field-by-field authority
(unit, precision, plausible range, zero semantics, formatter, states).
"""

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.schemas import DatasetHosting
from app.common.semantic_contract import list_field_ids
from app.common.source_catalog import AGGREGATE_REQUIRED_SOURCES, RESOLVER_SOURCE_MAP
from app.commune.absences import EtatColonne, column_states
from app.commune.indicator_schemas import (
    IndicateurAbsent,
    IndicateursDpe,
    IndicateursDvf,
)
from app.commune.tendance_prix import TendancePrix
from app.loyers.schemas import ResultatLoyer
from app.scope.coverage import hosting_by_source
from app.scope.model import InstanceScope, territoire_couverture

# The thirteen sources of the synthesis, in catalogue order.
COMMUNE_SOURCE_KEYS: tuple[str, ...] = tuple(RESOLVER_SOURCE_MAP["commune"])


# Reasons of a whole indicator block served absent ({etat, motif}).
_BLOCK_DESCRIPTION = (
    "Bloc d'indicateurs {source} datés, ou son absence {{etat, motif}}. Motifs "
    "d'un bloc entier absent (etat not_qualified) : "
    "indicateurs_communaux_non_publies (l'instance ne publie pas les blocs "
    "datés ; les colonnes plates de la synthèse restent les valeurs servies), "
    "commune_hors_build_qualifie (commune hébergée hors du build qualifié), "
    "{politique}source_generation_unqualified, indicator_policy_unqualified "
    "(bloc non qualifié par la politique). Null "
    "quand la source est hors couverture (voir couverture)."
)


class HeritageCommune(BaseModel):
    """Origin of a column an arrondissement serves from its commune (R-09)."""

    model_config = ConfigDict(extra="forbid")

    echelle: Literal["commune"] = Field(
        description="Échelle à laquelle la valeur est publiée : la commune entière."
    )
    code_insee: str = Field(
        description="Code INSEE de la commune (75056, 69123, 13055)."
    )
    nom: str | None = Field(
        description="Nom de la commune, tel que servi par sa ligne."
    )


class ResultatCommuneSynthese(BaseModel):
    """Complete commune profile from the v_commune_synthese matview."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "44109",
                    "nom_commune": "Nantes",
                    "code_departement": "44",
                    "code_region": "52",
                    "population": 320732,
                    "nb_menages": 170123,
                    "nb_logements": 187456,
                    "nb_logements_vacants_recensement": 14230,
                    "densite": 4915.2,
                    "superficie": 65.19,
                    "annee_demographie": 2021,
                    "revenu_median": 22480.0,
                    "taux_pauvrete": 15.2,
                    "indice_gini": 0.32,
                    "part_imposee": 62.1,
                    "annee_revenus": 2021,
                    "zone_abc": "B1",
                    "zone_abc_reclassement": False,
                    "taux_vacance": 7.8,
                    "nb_vacants_plus_2ans": 5120,
                    "annee_vacance": 2024,
                    "taux_tfpb": 39.12,
                    "taux_th_rs": 0.0,
                    "taux_teom": 8.5,
                    "nom_epci": "Nantes Metropole",
                    "annee_fiscalite": 2023,
                    "loyer_m2_appartement": 12.3,
                    "loyer_m2_maison": 10.8,
                    "annee_loyers": 2025,
                }
            ]
        }
    )

    code_insee: str
    type_zone: Literal["commune", "arrondissement"] | None = Field(
        default=None,
        description=(
            "Nature de la zone : « arrondissement » pour les 45 arrondissements "
            "municipaux de Paris, Lyon et Marseille, « commune » sinon. Déduit "
            "du code par le routeur, jamais d'une jointure ; null hors "
            "/v1/commune et comparaison."
        ),
    )
    commune_parent: str | None = Field(
        default=None,
        description=(
            "Code INSEE de la commune dont la zone est un arrondissement "
            "(75112 → 75056) ; null pour une commune."
        ),
    )
    # A block or its absence, never a bare null for a hosted commune (todo
    # 2026-11-01); null only when its source is out of coverage (couverture).
    indicateurs_dvf: IndicateursDvf | IndicateurAbsent | None = Field(
        default=None,
        description=_BLOCK_DESCRIPTION.format(
            source="DVF", politique="territorial_attribution_unqualified, "
        ),
    )
    indicateurs_dpe: IndicateursDpe | IndicateurAbsent | None = Field(
        default=None,
        description=_BLOCK_DESCRIPTION.format(source="DPE", politique=""),
    )
    nom_commune: str | None = None
    code_departement: str | None = None
    code_region: str | None = None
    # demographie
    population: int | None = None
    nb_menages: int | None = None
    nb_logements: int | None = None
    nb_logements_vacants_recensement: int | None = None
    densite: float | None = None
    superficie: float | None = None
    annee_demographie: int | None = None
    # revenus
    revenu_median: float | None = Field(
        default=None,
        description=(
            "Revenu déclaré médian annuel par unité de consommation, en "
            "euros, avant redistribution (Filosofi DEC, Q221). Ce revenu "
            "ne mesure pas le niveau de vie après impôts et prestations."
        ),
    )
    taux_pauvrete: float | None = Field(
        default=None,
        description=(
            "Taux de pauvreté au seuil de 60 % du niveau de vie médian "
            "métropolitain, en points de pourcentage. Calculé sur le "
            "revenu disponible après redistribution (Filosofi "
            "DISP_PAUVRES, TP6021), distinct du revenu déclaré."
        ),
    )
    indice_gini: float | None = Field(
        default=None,
        description=(
            "Indice de Gini du revenu déclaré par unité de consommation "
            "(Filosofi DEC), entre 0 et 1."
        ),
    )
    part_imposee: float | None = Field(
        default=None,
        description=(
            "Part des ménages fiscaux imposés, en points de pourcentage "
            "(Filosofi DEC, PMIMP21)."
        ),
    )
    annee_revenus: int | None = None
    # zonage_abc
    zone_abc: str | None = None
    zone_abc_reclassement: bool | None = None
    # vacance
    taux_vacance: float | None = None
    nb_vacants_plus_2ans: int | None = None
    annee_vacance: int | None = None
    # fiscalite
    taux_tfpb: float | None = None
    taux_th_rs: float | None = None
    taux_teom: float | None = None
    nom_epci: str | None = None
    annee_fiscalite: int | None = None
    # loyers
    loyer_m2_appartement: float | None = None
    loyer_m2_maison: float | None = None
    annee_loyers: int | None = Field(
        default=None,
        description=(
            "Millésime commun aux types de loyers affichés ; NULL si leurs "
            "années diffèrent. Chaque objet loyers conserve sa propre année."
        ),
    )
    loyers_appartement: ResultatLoyer | None = None
    loyers_maison: ResultatLoyer | None = None
    # dvf (commune aggregate)
    prix_m2_median: int | None = None
    nb_mutations_12m: int | None = None
    fenetre_mediane_dvf: str | None = Field(
        default=None,
        description=(
            "Bornes inclusives de la fenêtre réellement utilisée pour les "
            "médianes et effectifs DVF communaux, au format "
            "AAAA-MM-JJ → AAAA-MM-JJ."
        ),
    )
    tendance_prix: TendancePrix | IndicateurAbsent | None = Field(
        default=None,
        description=(
            "Prix médian au m² par année civile close de la livraison DVF, "
            "maisons et appartements séparés, sur les ventes que retiennent les "
            "médianes communales (vente réelle, hors VEFA, une seule commune, "
            "surface et prix positifs). Une année n'entre dans la période que "
            "lorsque la base la contient en entier ; la période glisse d'un an "
            "avec une livraison qui apporte une année complète. Paris, Lyon et "
            "Marseille : la commune est calculée sur l'ensemble de ses "
            "arrondissements. Médiane de ce qui s'est vendu chaque année : ni "
            "l'évolution d'un bien, ni une prévision. Servi par /v1/commune "
            "seulement. Absence {etat, motif} (etat not_qualified, motif "
            "tendance_prix_non_publiee) quand la série n'est pas lisible ou ne "
            "contient aucune année close : le reste de la synthèse est servi. "
            "Null quand DVF est hors couverture (voir couverture)."
        ),
    )
    # dvf (D-14, Phase 57): maison/appartement breakdown of the same
    # aggregate. Median is `None` ("n.d.") below SEUIL_ND_COMMUNE
    # (app.dvf.couverture); the count is always the real effectif.
    prix_m2_median_maison: int | None = None
    nb_mutations_12m_maison: int | None = None
    prix_m2_median_appartement: int | None = None
    nb_mutations_12m_appartement: int | None = None
    # dpe (commune aggregate)
    dpe_passoires_pct: float | None = None
    dpe_median: str | None = None
    annee_construction_mediane: int | None = None
    # equipements / scolaire / monuments (commune aggregate)
    nb_equipements: int | None = Field(
        default=None,
        description=(
            "Somme des effectifs des lignes BPE chargées au dernier millésime "
            "disponible pour la commune, sans déduplication d'établissements "
            "physiques. NULL si population absente ou effectifs incomplets ; "
            "zéro seulement si les effectifs source présents valent zéro."
        ),
    )
    annee_equipements: int | None = Field(
        default=None, description="Dernier millésime BPE chargé pour cette commune."
    )
    nb_etablissements_scolaires: int | None = Field(
        default=None,
        description=(
            "Codes RNE distincts observés dans la carte des secteurs scolaires "
            "de la commune ; pas un recensement de tous ses établissements. "
            "NULL si source absente ou identifiants incomplets."
        ),
    )
    nb_monuments: int | None = Field(
        default=None,
        description=(
            "Références de notices Mérimée distinctes chargées pour la commune, "
            "pas un dénombrement d'objets physiques. NULL si population absente "
            "ou références incomplètes."
        ),
    )
    # foncier_pm (commune aggregate)
    nb_proprietaires_pm: int | None = Field(
        default=None,
        description=(
            "SIREN distincts observés dans les droits fonciers chargés de la "
            "commune. NULL si population absente ou identifiants incomplets ; "
            "ne prouve pas l'exhaustivité des propriétaires."
        ),
    )
    surface_pm_ha: float | None = None
    # Hosting of each of the thirteen sources for this commune (D-24): a
    # column whose source is out of coverage is null, never 0 (SEM-01).
    couverture: dict[str, DatasetHosting] | None = None
    # D-47 (option A, 2026-09-24): additive, no published column changes type.
    etats: dict[str, EtatColonne] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "État déclaré de chaque colonne plate servie à null, indexé par le "
            "nom de la colonne : hors couverture (motif de couverture), "
            "effectif insuffisant, non publiée, non renseignée ou sans base de "
            "calcul. Une colonne servie avec une valeur n'y figure pas ; le "
            "champ est omis quand aucune colonne n'est nulle. Une absence ne "
            "vaut jamais zéro."
        ),
    )
    heritages: dict[str, HeritageCommune] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Arrondissement de Paris, Lyon ou Marseille : colonnes servies avec la "
            "valeur de sa commune, parce que la source ne la publie que pour la "
            "commune et qu'elle s'applique telle quelle à chacune de ses parties "
            "(zone ABC, taux de fiscalité locale votés, intercommunalité). Indexé "
            "par le nom de la colonne : échelle « commune », code et nom de la "
            "commune. La valeur n'est pas une mesure de l'arrondissement. Omis "
            "quand aucune colonne n'est héritée."
        ),
    )
    # connectivite (commune aggregate)
    fibre_pct: float | None = Field(
        default=None,
        description=(
            "Part en points de pourcentage des id_imb distincts avec une ligne "
            "FO parmi les id_imb chargés pour la commune. Population observée "
            "d'immeubles source, pas des adresses ou logements. NULL si absente "
            "ou si des identifiants/technologies manquent."
        ),
    )


# Identifiers, labels, contextual periods and the declared states of the other
# columns (``etats``, D-47): no standalone value or unit to audit.
NON_SEMANTIC_COMMUNE_COLUMNS: frozenset[str] = frozenset(
    {
        "code_insee",
        "type_zone",
        "commune_parent",
        "nom_commune",
        "code_departement",
        "code_region",
        "etats",
        "heritages",
        "fenetre_mediane_dvf",
    }
)

# Columns whose source is not written in their name (fiche-fields field ids
# are ``<source>.<column>`` for the others, see _derived_field_sources).
_HAND_FIELD_SOURCES: dict[str, str] = {
    "annee_demographie": "demographie",
    "annee_revenus": "revenus",
    "zone_abc": "zonage_abc",
    "zone_abc_reclassement": "zonage_abc",
    "annee_vacance": "vacance",
    "annee_fiscalite": "fiscalite",
    "annee_loyers": "loyers",
    "loyers_appartement": "loyers",
    "loyers_maison": "loyers",
    "indicateurs_dvf": "dvf",
    "indicateurs_dpe": "dpe",
    "tendance_prix": "dvf",
    "annee_equipements": "equipements",
}


def _derived_field_sources() -> dict[str, str]:
    """Column -> source for every contract field ``<source>.<column>``."""
    columns = set(ResultatCommuneSynthese.model_fields)
    derived: dict[str, str] = {}
    for field_id in list_field_ids():
        source, _, column = field_id.partition(".")
        if source in COMMUNE_SOURCE_KEYS and column in columns:
            derived[column] = source
    return derived


# Column of the synthesis -> catalogue source that feeds it.
COMMUNE_FIELD_SOURCES: dict[str, str] = {
    **_derived_field_sources(),
    **_HAND_FIELD_SOURCES,
}


def commune_coverage(
    scope: InstanceScope, code_insee: str
) -> dict[str, DatasetHosting]:
    """Hosting of the thirteen sources for the commune *code_insee*.

    The territory comes from ``territoire_couverture``: a collectivity code
    (975, 977, ...) is out of coverage, reason ``territoire_hors_perimetre``
    (D-123); a code naming no territory reads the scope as a whole.
    """
    territory = territoire_couverture(code_insee)
    return hosting_by_source(COMMUNE_SOURCE_KEYS, scope, territory)


def hosted_commune_sources(scope: InstanceScope, departement: str | None) -> list[str]:
    """Sources of the synthesis hosted for *departement* (None: the scope)."""
    hostings = hosting_by_source(COMMUNE_SOURCE_KEYS, scope, departement)
    return [source for source, hosting in hostings.items() if hosting.etat == "heberge"]


def apply_commune_coverage(
    row: Mapping[str, Any], couverture: Mapping[str, DatasetHosting]
) -> dict[str, Any] | None:
    """The synthesis row as served: out-of-coverage columns null, never 0.

    Every flat column served null carries its declared state in ``etats``
    (D-47, :mod:`app.commune.absences`). The single projection of every
    consumer of ``commune_synthese_par_codes`` (``/v1/commune``,
    ``/v1/resolve``, ``/v1/zone/stats``). ``None`` when the
    structural source (``demographie``) is out of coverage: the commune has
    no row to serve at all.
    """
    for source in AGGREGATE_REQUIRED_SOURCES["commune"]:
        if couverture[source].etat != "heberge":
            return None
    served = dict(row)
    for column, source in COMMUNE_FIELD_SOURCES.items():
        if column in served and couverture[source].etat != "heberge":
            served[column] = None
    served["couverture"] = dict(couverture)
    # Plain dicts: /v1/resolve serves this row as raw data, not as a model.
    states = column_states(served, couverture)
    served["etats"] = {
        column: state.model_dump() for column, state in states.items()
    } or None
    return served


# fiche-fields id of a top-level column whose id is not ``<source>.<column>``.
_COLUMN_FIELD_IDS: dict[str, str] = {
    "annee_demographie": "demographie.annee",
    "annee_revenus": "revenus.annee",
    "annee_vacance": "vacance.annee",
    "annee_fiscalite": "fiscalite.annee",
    "annee_loyers": "loyers.annee",
    "annee_equipements": "equipements.annee",
    "zone_abc": "zonage_abc.zone",
    "zone_abc_reclassement": "zonage_abc.reclassement",
}

# fiche-fields id of a numeric value nested in an object column: the object
# prefix, then (pattern on the dotted sub-path -> id suffix). One id per
# meaning: both rent objects share their fields, dict keys naming a category
# (a DPE class, a building type...) share the id of their dimension.
_NESTED_FIELD_IDS: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "loyers_appartement": ((re.compile(r"^(\w+)$"), r"loyers.prediction.\1"),),
    "loyers_maison": ((re.compile(r"^(\w+)$"), r"loyers.prediction.\1"),),
    "indicateurs_dvf": (
        (
            re.compile(
                r"^residentiel_(maison|appartement)\.(precedente|courante)\.effectif$"
            ),
            "dvf.indicateurs.effectif",
        ),
        (
            re.compile(
                r"^residentiel_(maison|appartement)\.(precedente|courante)"
                r"\.quantiles\.(p10|p50|p90)$"
            ),
            r"dvf.indicateurs.quantiles.\3",
        ),
        (
            re.compile(
                r"^residentiel_(maison|appartement)\.(precedente|courante)"
                r"\.mediane\.valeur$"
            ),
            r"dvf.indicateurs.\1.mediane_\2",
        ),
        (
            re.compile(r"^residentiel_(maison|appartement)\.variation_pct\.valeur$"),
            r"dvf.indicateurs.\1.variation_pct",
        ),
    ),
    "tendance_prix": (
        (
            re.compile(
                r"^(?:types\.\[\d+\]\.annees\.\[\d+\]\.annee|annee_(?:debut|fin))$"
            ),
            "dvf.tendance.annee",
        ),
        (re.compile(r"^seuil_(?:evolution|point)$"), "dvf.tendance.seuil"),
        (
            re.compile(r"^types\.\[\d+\]\.evolution_pct$"),
            "dvf.tendance.evolution_pct",
        ),
        (
            re.compile(r"^types\.\[\d+\]\.(?:annees\.\[\d+\]\.)?nb_ventes$"),
            "dvf.tendance.nb_ventes",
        ),
        (
            re.compile(r"^types\.\[\d+\]\.annees\.\[\d+\]\.prix_m2_median$"),
            "dvf.tendance.prix_m2_median",
        ),
    ),
    "indicateurs_dpe": (
        (
            re.compile(
                r"^resultat\.population\."
                r"(types_batiment|validite|remplacement|regimes)\.\w+$"
            ),
            r"dpe.indicateurs.population.\1",
        ),
        (
            re.compile(
                r"^resultat\.population\."
                r"(total|classes_connues|source_inconnue|revision_non_verifiee)$"
            ),
            r"dpe.indicateurs.population.\1",
        ),
        (
            re.compile(r"^resultat\.distribution\.classes\.[A-G]$"),
            "dpe.indicateurs.distribution.classes",
        ),
        (
            re.compile(r"^resultat\.distribution\.inconnue$"),
            "dpe.indicateurs.distribution.inconnue",
        ),
        (
            re.compile(r"^resultat\.part_fg_pct\.valeur$"),
            "dpe.indicateurs.part_fg_pct",
        ),
        (
            re.compile(r"^resultat\.dates_etablissement_inconnues$"),
            "dpe.indicateurs.dates_etablissement_inconnues",
        ),
        # D-46: the dominant class (strings, one id for every tied class).
        (re.compile(r"^resultat\.modes\.?\[\d+\]$"), "dpe.indicateurs.modes"),
        (
            re.compile(r"^resultat\.mediane_ordinale$"),
            "dpe.indicateurs.mediane_ordinale",
        ),
    ),
}


def commune_field_id(path: tuple[str, ...]) -> str | None:
    """fiche-fields id of the value at *path* in a /v1/commune row, or None.

    ``path`` starts with the column; nested keys follow, list items as
    ``[i]``. None: no contract entry is known for this value.
    """
    column, nested = path[0], ".".join(path[1:])
    if not nested:
        if column in _COLUMN_FIELD_IDS:
            return _COLUMN_FIELD_IDS[column]
        source = COMMUNE_FIELD_SOURCES.get(column)
        return None if source is None else f"{source}.{column}"
    for pattern, template in _NESTED_FIELD_IDS.get(column, ()):
        match = pattern.match(nested)
        if match:
            return match.expand(template)
    return None


# --- Comparison of communes (D-48) -------------------------------------------

# Why no gap is served between a column and the first one (COM-03), in order
# of precedence (see app.commune.comparison.compare_cell).
MotifNonCalcul = Literal[
    "commune_hors_couverture",
    "donnee_absente",
    "periode_inconnue",
    "periodes_differentes",
    "unites_differentes",
    "definitions_differentes",
    "non_numerique",
]

# State of a compared value: ``value`` or the absence state the synthesis
# declares for it. ``None``: the synthesis serves the value null without
# declaring why (never read as 0).
EtatCellule = Literal[
    "value",
    "not_reported",
    "out_of_coverage",
    "insufficient_sample",
    "not_available",
    "zero_denominator",
    "suppressed",
    "not_qualified",
]

ValeurComparee = int | float | bool | str | list[str]


class Ecart(BaseModel):
    """Gap of a column to the first one, served by the API (D-48)."""

    model_config = ConfigDict(extra="forbid")

    valeur: int | float = Field(
        description=(
            "Valeur de la colonne moins valeur de la première colonne, "
            "arrondie à la précision du champ au demi-éloignement de zéro. "
            "Un écart nul est servi (0), jamais confondu avec une absence."
        )
    )
    unite: str = Field(
        description=(
            "« point » pour les taux et parts (unités percent_point, "
            "percent_points et percent du contrat) ; unité du champ sinon."
        )
    )


class CelluleComparaison(BaseModel):
    """One commune's value for a criterion, with its verdict to column 1."""

    model_config = ConfigDict(extra="forbid")

    code_insee: str
    etat: EtatCellule | None = Field(
        description=(
            "value, ou l'état d'absence déclaré par la synthèse ; "
            "out_of_coverage pour une commune hors couverture ; null si la "
            "synthèse sert la valeur à null sans en déclarer la raison."
        )
    )
    valeur: ValeurComparee | None = Field(
        description="Valeur servie par la synthèse ; null pour toute absence."
    )
    periode: str | None = Field(
        description=(
            "Période de la valeur : millésime de la source, fenêtre "
            "« [début, fin[ », fenêtres d'une variation, calcul du bloc "
            "d'indicateurs, année et trimestre d'un loyer, ou période de "
            "provenance ; null si elle est inconnue."
        )
    )
    ecart: Ecart | None = Field(
        default=None,
        description="Écart à la première colonne ; null si non calculé.",
    )
    motif_non_calcul: MotifNonCalcul | None = Field(
        default=None,
        description="Pourquoi aucun écart n'est servi ; null si l'écart l'est.",
    )
    identique_a_la_reference: bool | None = Field(
        default=None,
        description=(
            "Vrai si valeur, état, période, unité et définition égalent ceux "
            "de la première colonne ; faux pour une commune hors couverture "
            "ou une valeur de période inconnue ; null pour la première colonne."
        ),
    )

    @model_validator(mode="after")
    def value_iff_served(self) -> Self:
        if isinstance(self.valeur, list) and not self.valeur:
            raise ValueError("an empty list is an absence, never a served value")
        if (self.valeur is not None) != (self.etat == "value"):
            raise ValueError("a value is served with etat 'value', and only then")
        if self.ecart is not None and self.motif_non_calcul is not None:
            raise ValueError("a gap and a reason not to compute it are exclusive")
        return self


class CritereComparaison(BaseModel):
    """One served value of the synthesis, compared across the columns."""

    model_config = ConfigDict(extra="forbid")

    cle: str = Field(
        description=(
            "Chemin pointé de la valeur dans une ligne de /v1/commune (nom de "
            "colonne, ou chemin dans un objet), stable d'une réponse à l'autre."
        )
    )
    field_id: str = Field(description="Identifiant du champ dans fiche-fields.")
    unite: str = Field(description="Unité du champ dans fiche-fields.")
    identiques: bool = Field(
        description=(
            "Vrai si valeurs, états, périodes, unités et définitions sont "
            "égaux dans toutes les colonnes (faux dès qu'une commune est hors "
            "couverture ou qu'une valeur servie a une période inconnue)."
        )
    )
    cellules: list[CelluleComparaison] = Field(min_length=1)

    @model_validator(mode="after")
    def verdict_per_column(self) -> Self:
        """Column 1 is the reference; every other one has a gap or a reason."""
        reference, *others = self.cellules
        if (
            reference.ecart is not None
            or reference.motif_non_calcul is not None
            or reference.identique_a_la_reference is not None
        ):
            raise ValueError("the first column carries no verdict")
        for cellule in others:
            if (cellule.ecart is None) == (cellule.motif_non_calcul is None):
                raise ValueError("a compared column has a gap or its reason")
            if cellule.identique_a_la_reference is None:
                raise ValueError("a compared column says if it is identical")
        return self


class CommuneComparee(BaseModel):
    """A column of the comparison: identity from the referential, synthesis."""

    model_config = ConfigDict(extra="forbid")

    code_insee: str
    nom: str = Field(description="Nom de la commune (référentiel geo_communes).")
    departement: str = Field(description="Code du département (référentiel).")
    hebergement: DatasetHosting = Field(
        description="Hébergement de la fiche commune, identique à /v1/commune."
    )
    synthese: ResultatCommuneSynthese | None = Field(
        description=(
            "Même projection que /v1/commune (états typés compris) ; null si "
            "la commune est hors couverture ou sans ligne de synthèse."
        )
    )


class CodeHorsCouverture(BaseModel):
    """A requested code of an overseas collectivity, out of coverage (D-123)."""

    model_config = ConfigDict(extra="forbid")

    code_insee: str
    hebergement: DatasetHosting = Field(
        description=(
            "Hébergement de la fiche commune pour ce territoire : hors "
            "couverture, motif territoire_hors_perimetre."
        )
    )


class ResultatComparaisonCommunes(BaseModel):
    """2 to 4 communes, their syntheses and the verdict of each criterion."""

    model_config = ConfigDict(extra="forbid")

    communes: list[CommuneComparee] = Field(
        description="Colonnes, dans l'ordre des codes demandés."
    )
    criteres: list[CritereComparaison] = Field(
        description="Une ligne par valeur comparée, une cellule par colonne."
    )
    codes_inconnus: list[str] = Field(
        description="Codes bien formés absents du référentiel, écartés des colonnes."
    )
    codes_hors_couverture: list[CodeHorsCouverture] = Field(
        default_factory=list,
        description=(
            "Codes d'une collectivité d'outre-mer (975, 977, 978, 984 à 989) "
            "absents du référentiel : hors couverture motivée, jamais dans "
            "codes_inconnus (D-123)."
        ),
    )

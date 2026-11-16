"""Pydantic schemas for zone-based endpoints (v2 frontend)."""

from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.schemas import DatasetHosting
from app.commune.absences import EtatColonne
from app.etl_dvf.qualification import CLASSES_MUTATION, RAISONS_PRIX_M2


class ResultatCommuneSearch(BaseModel):
    """Commune search result: referential identity, served population, hosting.

    ``population`` is the latest ``demographie`` year when that source is
    hosted for the commune's departement and has a row (``population_annee``
    served); otherwise the ``geo_communes`` referential value, whose year is
    unknown (``population_annee`` null, never invented). ``hebergement`` is the
    hosting ``/v1/commune`` would serve for this code (key ``commune``): a
    commune out of coverage is listed, never excluded (D-44).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code_insee": "44109",
                    "nom": "Nantes",
                    "code_postal": "44000",
                    "departement": "44",
                    "population": 320732,
                    "population_annee": 2022,
                    "population_source": "demographie",
                    "hebergement": {
                        "etat": "heberge",
                        "motif": None,
                        "departements": ["44"],
                    },
                },
                {
                    "code_insee": "33063",
                    "nom": "Bordeaux",
                    "code_postal": None,
                    "departement": "33",
                    "population": 261804,
                    "population_annee": None,
                    "population_source": "geo_communes",
                    "hebergement": {
                        "etat": "hors_couverture",
                        "motif": "territoire_hors_perimetre",
                        "departements": [],
                    },
                },
            ]
        }
    )

    code_insee: str
    nom: str
    code_postal: str | None = Field(
        default=None,
        description=(
            "Code postal lu dans la BAN ; null quand la BAN n'est pas hebergee "
            "pour le departement de la commune"
        ),
    )
    departement: str
    type_zone: Literal["commune", "arrondissement"] = Field(
        default="commune",
        description=(
            "Nature de la zone : « commune », ou « arrondissement » pour un "
            "arrondissement municipal de Paris, Lyon ou Marseille (listé avec "
            "inclure_arrondissements=true seulement)"
        ),
    )
    commune_parent: str | None = Field(
        default=None,
        description=(
            "Code INSEE de la commune d'un arrondissement (75112 → 75056) ; "
            "null pour une commune"
        ),
    )
    population: int | None = None
    population_annee: int | None = Field(
        default=None,
        description=(
            "Annee de la population servie ; null quand elle vient du "
            "referentiel geo_communes, dont l'annee n'est pas connue"
        ),
    )
    population_source: Literal["demographie", "geo_communes"] | None = Field(
        default=None,
        description="Source de la population servie ; null sans population",
    )
    hebergement: DatasetHosting = Field(
        description=(
            "Hebergement de la fiche de cette commune par l'instance (cle "
            "commune), identique a meta.hebergement.commune de /v1/commune"
        ),
    )


_DERNIERE_IMPORTATION = (
    "Jour (heure de Paris) du dernier import réussi, parmi les jeux hébergés "
    "ET importés pour ce département ({jeux}) ; null si aucun ne l'est. Date de "
    "la dernière modification des chiffres de ces jeux, jamais la date de la "
    "réponse ni la période des données. Un rechargement du référentiel "
    "géographique (communes, population de repli) ne la change pas."
)


class ResultatDepartement(BaseModel):
    """Departement entry from geo_departements, with the state of its pages.

    ``prix`` and ``indexable`` say, for each departement, whether its page and
    its commune pages enter the sitemap (plan 04.7-10, decision 3): computed by
    the API from the scope and the import journal, never a territory written
    in the client.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "44",
                    "nom": "Loire-Atlantique",
                    "region": "Pays de la Loire",
                    "derniere_importation": "2026-10-30",
                    "prix": {"etat": "heberge", "motif": None, "departements": ["44"]},
                    "indexable": True,
                }
            ]
        }
    )

    code: str
    nom: str
    region: str | None = Field(
        default=None,
        description="Nom officiel de la région, accentué (« Île-de-France »).",
    )
    derniere_importation: date | None = Field(
        description=_DERNIERE_IMPORTATION.format(jeux="dvf, dpe, demographie")
    )
    prix: DatasetHosting = Field(
        description=(
            "Hébergement de DVF, source des prix, pour ce département : motif "
            "quand il est hors couverture (jeu non hébergé, territoire hors "
            "périmètre, ou source sans couverture : DVF ne publie ni la Moselle, "
            "ni le Bas-Rhin, ni le Haut-Rhin, ni Mayotte)."
        )
    )
    indexable: bool = Field(
        description=(
            "Vrai quand DVF est hébergé ET importé pour ce département : sa page "
            "et ses fiches commune entrent dans le sitemap ; faux sinon "
            "(page servie en noindex)."
        )
    )

    @model_validator(mode="after")
    def _indexable_needs_hosted_prices(self) -> Self:
        if self.indexable and self.prix.etat != "heberge":
            raise ValueError("an indexable departement has its prices hosted")
        return self


class CommuneListRow(BaseModel):
    """Commune list row from geo_communes, with the served population (H6).

    Same rule as the search and the commune page: ``population`` is the
    latest ``demographie`` year when that source is hosted for the commune's
    departement and has a row (``population_annee`` served); otherwise the
    ``geo_communes`` referential value, whose year is unknown
    (``population_annee`` null, never invented).
    """

    code_insee: str
    nom: str
    departement: str
    type_zone: Literal["commune", "arrondissement"] = Field(
        default="commune",
        description=(
            "Nature de la zone : « commune », ou « arrondissement » pour un "
            "arrondissement municipal de Paris, Lyon ou Marseille (listé avec "
            "inclure_arrondissements=true seulement)"
        ),
    )
    commune_parent: str | None = Field(
        default=None,
        description=(
            "Code INSEE de la commune d'un arrondissement (75112 → 75056) ; "
            "null pour une commune"
        ),
    )
    population: int | None = None
    population_annee: int | None = Field(
        default=None,
        description=(
            "Annee de la population servie ; null quand elle vient du "
            "referentiel geo_communes, dont l'annee n'est pas connue"
        ),
    )
    population_source: Literal["demographie", "geo_communes"] | None = Field(
        default=None,
        description="Source de la population servie ; null sans population",
    )
    code_postal: str | None = Field(
        default=None,
        description=(
            "Servi avec le seul filtre code_insee : code postal lu dans la BAN "
            "quand elle est hebergee pour le departement, null sinon ; toujours "
            "null dans la liste paginee"
        ),
    )


class CommunesListResponse(BaseModel):
    """Paginated commune list response."""

    results: list[CommuneListRow]
    total_count: int
    page: int
    per_page: int
    has_next: bool


class PrixTypeAnnuaire(BaseModel):
    """Median price per m² of one type of dwelling, as the synthesis serves it.

    ``etat`` is the served state of the median column (``etats`` of
    ``/v1/commune``): null exactly when ``mediane_m2`` is served. ``effectif``
    is the served count of retained sales, null when the synthesis has no DVF
    row for the commune (``not_reported``): never turned into 0.
    """

    mediane_m2: int | None = Field(
        default=None, description="Prix médian au m², euros ; null avec son état"
    )
    effectif: int | None = Field(
        default=None,
        description=(
            "Mutations retenues sur la fenêtre DVF ; null quand la synthèse "
            "n'a aucune ligne DVF pour la commune (jamais 0 par défaut)"
        ),
    )
    etat: EtatColonne | None = Field(
        default=None,
        description=(
            "État servi de la médiane quand elle est nulle : hors couverture, "
            "effectif insuffisant (moins de 5 ventes, zéro compris), non "
            "renseignée, non publiée ; null quand la médiane est servie"
        ),
    )

    @model_validator(mode="after")
    def _state_iff_no_median(self) -> "PrixTypeAnnuaire":
        if (self.mediane_m2 is None) != (self.etat is not None):
            raise ValueError("etat is required for, and only for, a null median")
        return self


class PrixCommuneAnnuaire(BaseModel):
    """Median prices of houses and flats of one commune of the directory."""

    maison: PrixTypeAnnuaire
    appartement: PrixTypeAnnuaire


class CommuneAnnuaireRow(BaseModel):
    """One commune of the directory: identity, served population, prices.

    ``hebergement`` is the hosting ``/v1/commune`` would serve for the code. A
    commune out of coverage carries its prices as ``out_of_coverage`` states,
    without any read of the synthesis. ``prix`` is null only when the
    response's ``prix.etat`` is ``indisponible`` and the commune is hosted.
    """

    code_insee: str
    nom: str
    departement: str
    type_zone: Literal["commune", "arrondissement"] = Field(
        default="commune",
        description=(
            "Nature de la zone : « commune », ou « arrondissement » pour un "
            "arrondissement municipal de Paris, Lyon ou Marseille (listé avec "
            "inclure_arrondissements=true seulement)"
        ),
    )
    commune_parent: str | None = Field(
        default=None,
        description=(
            "Code INSEE de la commune d'un arrondissement (75112 → 75056) ; "
            "null pour une commune"
        ),
    )
    population: int | None = None
    population_annee: int | None = Field(
        default=None,
        description=(
            "Annee de la population servie ; null quand elle vient du "
            "referentiel geo_communes, dont l'annee n'est pas connue"
        ),
    )
    population_source: Literal["demographie", "geo_communes"] | None = Field(
        default=None,
        description="Source de la population servie ; null sans population",
    )
    hebergement: DatasetHosting
    prix: PrixCommuneAnnuaire | None = Field(
        default=None,
        description=(
            "Prix médians maisons et appartements ; null seulement quand la "
            "lecture des prix est indisponible (prix.etat = indisponible) pour "
            "une commune hébergée"
        ),
    )


class PrixAnnuaire(BaseModel):
    """State of the price read of the page, said once for the whole response."""

    etat: Literal["servi", "indisponible", "sans_lecture"] = Field(
        description=(
            "servi : synthèse lue pour les communes hébergées de la page ; "
            "indisponible : la synthèse a répondu 503, la liste reste servie ; "
            "sans_lecture : aucune commune hébergée dans la page, rien n'est lu"
        )
    )
    motif: str | None = Field(
        default=None, description="Cause de l'indisponibilité ; null sinon"
    )
    fenetre_mediane_dvf: str | None = Field(
        default=None,
        description=(
            "Bornes inclusives de la fenêtre des médianes et effectifs DVF, "
            "AAAA-MM-JJ → AAAA-MM-JJ ; null quand aucun effectif n'est servi"
        ),
    )


class CommunesAnnuaireResponse(BaseModel):
    """Directory page: filtered total, page of communes, price read state."""

    results: list[CommuneAnnuaireRow]
    total_count: int = Field(description="Communes de la sélection, tous filtres")
    page: int
    per_page: int
    has_next: bool
    couverture: Literal["servies", "toutes"]
    departements_servis: list[str] = Field(
        description=(
            "Départements où la synthèse commune est hébergée, ceux du filtre "
            "couverture=servies"
        )
    )
    prix: PrixAnnuaire


class ZoneResolveResponse(BaseModel):
    """Paginated zone resolve response."""

    results: list[dict[str, Any]]
    total_count: int
    page: int
    per_page: int
    has_next: bool


class ResultatZoneDvf(BaseModel):
    """Contrat de qualification d'une ligne DVF servie par `/v1/zone/resolve` (D-25).

    `zone_resolve_dvf` (`app.zone.queries`) retourne des dicts bruts --
    `ZoneResolveResponse.results` reste `list[dict[str, Any]]` car cette
    reponse est partagee avec la branche DPE (forme differente par ligne).
    Ce modele n'est donc pas branche dans le routeur : il documente et
    verrouille par test le contrat de qualification de la branche DVF, avec
    EXACTEMENT les memes noms de champs que `ResultatDvf`
    (`app.dvf.schemas`, plan 57-10) -- un meme fait ne porte jamais deux
    noms entre les deux endpoints publics.
    """

    classe_mutation: str | None = None
    prix_m2_raison: str | None = None
    mutation_mono_local: bool | None = None
    hors_stats_vefa: bool | None = None
    surface_bati_totale: int | None = None
    nb_locaux_residentiel: int | None = None
    nb_locaux_commercial: int | None = None

    @model_validator(mode="after")
    def _validate_classe_mutation(self) -> "ResultatZoneDvf":
        classe = self.classe_mutation
        if classe is not None and classe not in CLASSES_MUTATION:
            raise ValueError(
                f"classe_mutation {classe!r} n'appartient pas au vocabulaire "
                "ferme (app.etl_dvf.qualification.CLASSES_MUTATION)"
            )
        return self

    @model_validator(mode="after")
    def _validate_prix_m2_raison(self) -> "ResultatZoneDvf":
        raison = self.prix_m2_raison
        if raison is not None and raison not in RAISONS_PRIX_M2:
            raise ValueError(
                f"prix_m2_raison {raison!r} n'appartient pas au vocabulaire "
                "ferme (app.etl_dvf.qualification.RAISONS_PRIX_M2)"
            )
        return self


class DpeDistribution(BaseModel):
    """DPE label distribution counts."""

    A: int = 0
    B: int = 0
    C: int = 0
    D: int = 0
    E: int = 0
    F: int = 0
    G: int = 0


class ZoneStats(BaseModel):
    """Aggregated stats for a zone (commune or departement).

    D-25: `prix_median_m2`/`nb_transactions` now read `mutations_grain` (one
    row per mutation) -- `nb_transactions` counts MUTATIONS, not lines. The
    maison/appartement breakdown fields are additive: `prix_median_m2_maison`
    and `prix_median_m2_appartement` are `null` (`n.d.`) when their
    respective `nb_transactions_*` is below `SEUIL_ND_ZONE`
    (`app.dvf.couverture`, C-02) -- the count itself is never masked, only
    the median (masking a fragile effectif hides nothing sensitive; masking
    a median at 1-2 points prevents reading an individual sale's price).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "prix_median_m2": 3250,
                    "nb_transactions": 1523,
                    "surface_moyenne": 67.4,
                    "prix_median_m2_maison": 3480,
                    "nb_transactions_maison": 612,
                    "prix_median_m2_appartement": 3120,
                    "nb_transactions_appartement": 911,
                    "population": 320732,
                    "dpe_distribution": {
                        "A": 45,
                        "B": 123,
                        "C": 456,
                        "D": 389,
                        "E": 234,
                        "F": 178,
                        "G": 98,
                    },
                    "taux_vacance": 8.2,
                }
            ]
        }
    )

    prix_median_m2: float | None = None
    nb_transactions: int | None = None
    surface_moyenne: float | None = None
    prix_median_m2_maison: float | None = None
    nb_transactions_maison: int | None = None
    prix_median_m2_appartement: float | None = None
    nb_transactions_appartement: int | None = None
    population: int | None = None
    dpe_distribution: DpeDistribution | None = None
    taux_vacance: float | None = None


# --- Departement page and sitemap (plan 04.7-10, decisions 2 and 3) ---------
#
# Same vocabulary as /v1/commune: a value is served with no state, an absence
# is served null with its EtatColonne (out_of_coverage and its D-24 reason,
# not_reported, insufficient_sample, zero_denominator). A departement with no
# retained sale or document is ``not_reported``, never a count of 0.


class CommunesDepartement(BaseModel):
    """Size of the departement in the referential, read at request time."""

    nombre: int = Field(
        ge=0, description="Communes du référentiel geo_communes du département."
    )
    arrondissements: int = Field(
        ge=0,
        description=(
            "Arrondissements municipaux (Paris, Lyon, Marseille) du référentiel, "
            "comptés seulement là où la BAN est hébergée (règle "
            "d'inclure_arrondissements) ; 0 sinon. Jamais additionnés aux "
            "communes."
        ),
    )


class PopulationDepartement(BaseModel):
    """Sum of the communes' populations, never of their arrondissements."""

    total: int | None = Field(
        ge=1,
        description=(
            "Somme des populations des communes du département, au dernier "
            "millésime chargé de chaque commune (même ligne que sa fiche). Paris, "
            "Lyon et Marseille comptent une fois, jamais leurs arrondissements ; "
            "null avec son état."
        ),
    )
    communes_comptees: int | None = Field(
        ge=1, description="Communes dont la population entre dans la somme."
    )
    annee: int | None = Field(
        description=(
            "Millésime commun aux populations sommées ; null si leurs années "
            "diffèrent ou si la somme est absente (comme annee_loyers)."
        )
    )
    etat: EtatColonne | None = Field(
        description="État de la somme absente ; null quand elle est servie."
    )

    @model_validator(mode="after")
    def _served_or_stated(self) -> Self:
        if (self.total is None) != (self.etat is not None):
            raise ValueError("a total is served without state, an absence with one")
        if self.total is None and (
            self.communes_comptees is not None or self.annee is not None
        ):
            raise ValueError("an absent total has no base")
        if self.total is not None and self.communes_comptees is None:
            raise ValueError("a served total names the communes it counts")
        return self


class FenetreDvf(BaseModel):
    """The 12 months the medians are computed on, bounds included."""

    debut: date = Field(description="Premier jour de la fenêtre (inclus).")
    fin: date = Field(
        description=(
            "Dernier jour de la fenêtre (inclus) : date de la vente la plus "
            "récente chargée, toutes communes confondues (même fenêtre que les "
            "fiches commune)."
        )
    )


class PrixTypeDepartement(BaseModel):
    """Median price per m² of one type of dwelling over the departement."""

    mediane_m2: int | None = Field(
        description=(
            "Médiane du prix au m² des ventes retenues du département, en euros ; "
            "null avec son état."
        )
    )
    effectif: int | None = Field(
        ge=1,
        description=(
            "Ventes retenues sur la fenêtre : l'union des ventes des fiches "
            "commune du département (même filtre, même fenêtre), chaque vente une "
            "fois. Servi avec la médiane ou sous le seuil de publication ; null "
            "quand aucune vente n'est retenue (jamais 0)."
        ),
    )
    etat: EtatColonne | None = Field(
        description=(
            "État de la médiane absente : hors couverture, non renseignée (aucune "
            "vente retenue, ou jeu pas encore importé pour le département), "
            "effectif insuffisant (moins de 5 ventes) ; null quand elle est servie."
        )
    )

    @model_validator(mode="after")
    def _served_or_stated(self) -> Self:
        if (self.mediane_m2 is None) != (self.etat is not None):
            raise ValueError("a median is served without state, an absence with one")
        if self.mediane_m2 is not None and self.effectif is None:
            raise ValueError("a served median carries its count")
        if self.etat is not None and (
            (self.etat.etat == "insufficient_sample") != (self.effectif is not None)
        ):
            raise ValueError("only a median under the threshold keeps its count")
        return self


class PrixDepartement(BaseModel):
    """DVF block: the window and the two medians (houses, flats)."""

    fenetre: FenetreDvf | None = Field(
        description="Fenêtre des médianes ; null quand aucune vente n'est servie."
    )
    maison: PrixTypeDepartement
    appartement: PrixTypeDepartement

    @model_validator(mode="after")
    def _window_iff_a_count(self) -> Self:
        counted = self.maison.effectif is not None or (
            self.appartement.effectif is not None
        )
        if counted != (self.fenetre is not None):
            raise ValueError("the window is served with a count, and only then")
        return self


class ClassesDpe(BaseModel):
    """Retained DPE documents per energy class; every class is a real count."""

    A: int = Field(ge=0)
    B: int = Field(ge=0)
    C: int = Field(ge=0)
    D: int = Field(ge=0)
    E: int = Field(ge=0)
    F: int = Field(ge=0)
    G: int = Field(ge=0)


class DpeDepartement(BaseModel):
    """DPE documents of the departement, same population as the commune pages.

    The documentary population of ``v_commune_dpe`` (legacy, expired and
    replaced documents included; tombstones and deactivated ones excluded),
    over the documents coded at a commune or an arrondissement of the
    departement in the referential; each document once.
    """

    classes: ClassesDpe | None = Field(
        description="Documents par classe énergie A à G ; null avec l'état du bloc."
    )
    inconnue: int | None = Field(
        ge=0, description="Documents sans classe A à G connue."
    )
    total: int | None = Field(
        ge=1, description="Documents retenus ; null quand il n'y en a aucun."
    )
    classes_connues: int | None = Field(
        ge=0, description="Documents de classe A à G : la somme des classes."
    )
    part_fg_pct: float | None = Field(
        description=(
            "Part des documents classés F ou G parmi ceux de classe connue, en "
            "points de pourcentage arrondis au dixième ; null avec son état."
        )
    )
    date_min: date | None = Field(
        description="Date d'établissement la plus ancienne des documents retenus."
    )
    date_max: date | None = Field(
        description="Date d'établissement la plus récente des documents retenus."
    )
    etat: EtatColonne | None = Field(
        description=(
            "État du bloc absent (hors couverture, non renseigné) ; null quand "
            "les effectifs sont servis."
        )
    )
    etat_part_fg: EtatColonne | None = Field(
        description=(
            "État de la part F/G absente : celui du bloc, ou zero_denominator "
            "quand aucune classe n'est connue ; null quand elle est servie."
        )
    )

    @model_validator(mode="after")
    def _counts_add_up(self) -> Self:
        counts = (self.classes, self.inconnue, self.total, self.classes_connues)
        if self.etat is not None:
            if any(value is not None for value in counts) or (
                self.date_min is not None or self.date_max is not None
            ):
                raise ValueError("an absent block serves no count")
            if self.etat_part_fg != self.etat or self.part_fg_pct is not None:
                raise ValueError("an absent block gives its state to the share")
            return self
        if any(value is None for value in counts):
            raise ValueError("a served block serves every count")
        assert self.classes is not None and self.classes_connues is not None
        if sum(self.classes.model_dump().values()) != self.classes_connues:
            raise ValueError("the classes add up to the known classes")
        if self.classes_connues + (self.inconnue or 0) != self.total:
            raise ValueError("known and unknown classes add up to the total")
        if (self.part_fg_pct is None) != (self.etat_part_fg is not None):
            raise ValueError("a share is served without state, an absence with one")
        if (self.etat_part_fg is not None) != (self.classes_connues == 0):
            raise ValueError("the share is absent exactly without a known class")
        if self.etat_part_fg is not None and (
            self.etat_part_fg.etat != "zero_denominator"
        ):
            raise ValueError("a served block lacks its share for no known class only")
        return self


class CommunePrincipale(BaseModel):
    """One of the most populated zones of the departement, linked to its page."""

    code_insee: str
    nom: str
    type_zone: Literal["commune", "arrondissement"] = Field(
        description=(
            "« arrondissement » quand le département n'a qu'une commune (Paris) : "
            "ses arrondissements sont listés à sa place."
        )
    )
    commune_parent: str | None = Field(
        description="Commune d'un arrondissement (75112 → 75056) ; null sinon."
    )
    population: int | None = None
    population_annee: int | None = Field(
        default=None,
        description=(
            "Année de la population servie ; null quand elle vient du référentiel "
            "geo_communes, dont l'année n'est pas connue."
        ),
    )
    population_source: Literal["demographie", "geo_communes"] | None = Field(
        default=None, description="Source de la population servie ; null sans."
    )


class ResultatDepartementSynthese(BaseModel):
    """Key figures of a departement, their definitions living in fiche-fields.

    Identity and communes come from the referential, whatever the scope; the
    three blocks from ``v_departement_synthese`` behind the publication barrier
    of the commune chain, each with the state of its own source.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "44",
                    "nom": "Loire-Atlantique",
                    "region": "Pays de la Loire",
                    "derniere_importation": "2026-10-30",
                    "indexable": True,
                    "couverture": {
                        "dvf": {"etat": "heberge", "departements": ["44", "85"]},
                        "dpe": {"etat": "heberge", "departements": ["44", "85"]},
                        "demographie": {
                            "etat": "heberge",
                            "departements": ["44", "85"],
                        },
                    },
                    "communes": {"nombre": 207, "arrondissements": 0},
                    "population": {
                        "total": 1450000,
                        "communes_comptees": 207,
                        "annee": 2022,
                        "etat": None,
                    },
                    "prix": {
                        "fenetre": {"debut": "2024-06-30", "fin": "2025-06-30"},
                        "maison": {"mediane_m2": 2950, "effectif": 6120, "etat": None},
                        "appartement": {
                            "mediane_m2": 3480,
                            "effectif": 7350,
                            "etat": None,
                        },
                    },
                    "dpe": {
                        "classes": {
                            "A": 4100,
                            "B": 8200,
                            "C": 30100,
                            "D": 52000,
                            "E": 38000,
                            "F": 14800,
                            "G": 9900,
                        },
                        "inconnue": 2100,
                        "total": 159200,
                        "classes_connues": 157100,
                        "part_fg_pct": 15.7,
                        "date_min": "2021-07-01",
                        "date_max": "2026-10-28",
                        "etat": None,
                        "etat_part_fg": None,
                    },
                    "communes_principales": [
                        {
                            "code_insee": "44109",
                            "nom": "Nantes",
                            "type_zone": "commune",
                            "commune_parent": None,
                            "population": 320732,
                            "population_annee": 2022,
                            "population_source": "demographie",
                        },
                        {
                            "code_insee": "44184",
                            "nom": "Saint-Nazaire",
                            "type_zone": "commune",
                            "commune_parent": None,
                            "population": 72299,
                            "population_annee": 2022,
                            "population_source": "demographie",
                        },
                    ],
                }
            ]
        }
    )

    code: str
    nom: str
    region: str | None = Field(
        description="Nom officiel de la région, accentué (« Pays de la Loire »)."
    )
    derniere_importation: date | None = Field(
        description=_DERNIERE_IMPORTATION.format(jeux="dvf, dpe, demographie")
    )
    indexable: bool = Field(
        description=(
            "Même valeur que /v1/departements : DVF hébergé ET importé pour le "
            "département ; faux, la page est servie en noindex."
        )
    )
    couverture: dict[str, DatasetHosting] = Field(
        description=(
            "Hébergement de dvf, dpe et demographie pour ce département, comme "
            "couverture de /v1/commune."
        )
    )
    communes: CommunesDepartement
    population: PopulationDepartement
    prix: PrixDepartement
    dpe: DpeDepartement
    communes_principales: list[CommunePrincipale] = Field(
        max_length=10,
        description=(
            "Les 10 communes les plus peuplées (population servie), ou les "
            "arrondissements quand le département n'a qu'une commune (Paris) ; "
            "à égalité, par code. Liste du référentiel, servie même hors "
            "couverture."
        ),
    )


class FicheDepartement(BaseModel):
    """A commune or arrondissement page of the departement."""

    code_insee: str
    nom: str
    type_zone: Literal["commune", "arrondissement"]


class FichesDepartement(BaseModel):
    """Every page of a departement in one call, for the sitemap."""

    code: str
    derniere_importation: date | None = Field(
        description=(
            "Jour (heure de Paris) du dernier import réussi parmi les sources de "
            "la fiche commune hébergées ET importées pour ce département ; null si "
            "aucune ne l'est. Une date par jeu et par périmètre : les fiches du "
            "département la partagent."
        )
    )
    fiches: list[FicheDepartement] = Field(
        description=(
            "Communes du référentiel et, là où la BAN est hébergée, arrondissements "
            "municipaux (règle d'inclure_arrondissements), triés par code."
        )
    )

    @model_validator(mode="after")
    def _sorted_and_unique(self) -> Self:
        codes = [fiche.code_insee for fiche in self.fiches]
        if codes != sorted(set(codes)):
            raise ValueError("the pages are sorted by code, each once")
        return self


class CommuneVoisine(BaseModel):
    """A commune or arrondissement page near another one."""

    code_insee: str
    nom: str
    departement_code: str
    type_zone: Literal["commune", "arrondissement"]
    limitrophe: bool = Field(
        description=(
            "Vrai quand les contours du référentiel se touchent (à 0,001 degré "
            "près, les contours étant simplifiés) ; faux pour une page seulement "
            "proche."
        )
    )


class CommunesVoisines(BaseModel):
    """The pages near a commune or arrondissement page, for its links."""

    code_insee: str
    voisines: list[CommuneVoisine] = Field(
        description=(
            "Les limitrophes d'abord, par population décroissante, puis les plus "
            "proches par distance. Une commune liste des communes ; un "
            "arrondissement liste les autres arrondissements de sa commune et les "
            "communes autour, jamais sa propre commune."
        )
    )

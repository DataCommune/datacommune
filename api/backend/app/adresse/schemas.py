"""Public models of the address search and identity routes (04-02).

No model here carries a coordinate: the BAN point and the parcel geometries
stay internal, the served identifier is the BAN key (LIEU-01).
"""

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.common.schemas import DatasetHosting, OutOfCoverageReason, ProblemDetail
from app.commune.schemas import Ecart
from app.dpe.schemas import DpeInventoryDistribution

TypeResultat = Literal["adresse", "commune"]

EtatParcelle = Literal[
    "unique",
    "ambigue",
    "point_hors_parcelle",
    "hors_couverture",
    "indisponible",
    "indeterminee",
]


class DepartementRef(BaseModel):
    """A departement: its code, and its name from the national referential."""

    code: str
    nom: str | None = None


class CommuneRef(BaseModel):
    """A commune: INSEE code and name."""

    code_insee: str
    nom: str | None = None


class ResultatRechercheAdresse(BaseModel):
    """One search result: a numbered BAN address, or a street without number.

    A ``voie`` has neither ``numero`` nor ``ban_id``: an address page needs a
    number (D-107).
    """

    type_resultat: Literal["adresse", "voie"]
    ban_id: str | None = None
    numero: str | None = None
    rep: str | None = None
    nom_voie: str | None = None
    code_postal: str | None = None
    code_commune: str | None = None
    nom_commune: str | None = None
    departement: DepartementRef | None = None


class TerritoireRecherche(BaseModel):
    """Territory named by the input (commune name or postal code).

    A postal code names a departement only: ``code_insee`` and ``nom`` stay
    null, never guessed.
    """

    code_insee: str | None = None
    nom: str | None = None
    departement: DepartementRef


class HebergementBanRecherche(DatasetHosting):
    """BAN hosting of a search, shaped like ``meta.hebergement.ban`` (03-15).

    ``territoire``: the territory recognized in the input when it is out of
    coverage; ``homonymes``: every commune of that name when there are
    several, all out of coverage (D-105).
    """

    territoire: TerritoireRecherche | None = None
    homonymes: list[TerritoireRecherche] = Field(default_factory=list)


class CorrespondanceNumeroRecherche(BaseModel):
    """Match of the requested number within the returned results only.

    ``exacte`` compares only the parsed house number and suffix. It does not
    establish that the full address or street is an exact match. ``proches``
    and ``aucune_dans_resultats`` say nothing about rows outside the returned
    list, the whole BAN corpus, or the existence of a place.
    """

    etat: Literal["exacte", "proches", "aucune_dans_resultats"]
    numero_demande: int
    suffixe_demande: str | None = None
    portee: Literal["resultats_retournes"] = "resultats_retournes"


class RechercheAdresses(BaseModel):
    """Served type order (D-106), typed results and BAN hosting."""

    ordre_types: list[TypeResultat]
    resultats: list[ResultatRechercheAdresse]
    hebergement: dict[Literal["ban"], HebergementBanRecherche]
    correspondance_numero: CorrespondanceNumeroRecherche | None = None
    # R-08 (2026-11-10): a read stopped by its budget is neither an outage
    # (the same input meets the same budget at every try) nor an absence.
    etat: Literal["aboutie", "trop_large"] = Field(
        default="aboutie",
        description=(
            "aboutie : la recherche est menee a bout, resultats vide est une "
            "absence dans la BAN de l'instance. trop_large : la recherche "
            "approchee sur toute la base a depasse son budget ; resultats est "
            "vide et n'etablit aucune absence (preciser la commune ou le code "
            "postal)."
        ),
    )


class RelationParcelle(BaseModel):
    """Parcel of an address: the one containing the BAN point, alone (N-02).

    ``unique``: one parcel contains the point (``rattachement`` names the
    rule). ``point_hors_parcelle``: no parcel contains it, never replaced by
    the nearest one. ``ambigue``: several contain it (boundary), none chosen.
    ``hors_couverture``: cadastre not hosted for the departement, not read.
    ``indisponible``: the cadastre read failed. ``indeterminee``: the BAN
    rows of the key give contradictory points.
    """

    etat: EtatParcelle
    id_parcelle: str | None = None
    nombre_candidats: int | None = None
    rattachement: Literal["parcelle_du_point"] | None = None
    motif: str | None = None


class IdentiteAdresse(BaseModel):
    """Identity of one BAN key, without coordinate.

    ``etat=indeterminee``: several BAN rows carry the key and disagree; no
    row is chosen, the fields they do not share stay null.
    """

    etat: Literal["etablie", "indeterminee"] = "etablie"
    ban_id: str
    libelle: str | None = None
    numero: str | None = None
    rep: str | None = None
    nom_voie: str | None = None
    code_postal: str | None = None
    commune: CommuneRef
    departement: DepartementRef
    parcelle: RelationParcelle | None = None


class CouvertureAdresse(BaseModel):
    """BAN coverage of the departement of the key (N-07)."""

    etat: Literal["heberge", "hors_couverture"]
    motif: OutOfCoverageReason | None = None


# --- 04-03: landmarks and declared DPE documents ------------------------------

# D-04 closed set of value states (``fiche-fields.v1.json``).
EtatValeur = Literal[
    "value",
    "not_reported",
    "out_of_coverage",
    "temporary_error",
    "not_applicable",
    "insufficient_sample",
    "not_available",
    "zero_denominator",
    "suppressed",
    "not_qualified",
]

# SEM-02: nature of the match between a value and the address (closed list
# ``allowed_rattachements`` of ``fiche-fields.v1.json``).
Rattachement = Literal[
    "adresse_saisie_diagnostiqueur",
    "parcelle_du_point",
    "batiment_du_point",
    "batiment_rnb_de_l_adresse",
    "au_point",
    "rayon_du_point",
    "commune_de_l_adresse",
    "eligibilite_adresse",
    "commune_reference",
    "adresse_declaree_vente",
    "adresse_brute_exacte",
]


class SourceValeur(BaseModel):
    """Source of a served value: catalogue key, name, producer, https URL."""

    cle: str
    nom: str
    producteur: str
    url: str


class ReferenceCommune(BaseModel):
    """Reference value of the commune (D-103): never a datum of the place."""

    valeur: int | float | None = None
    unite: str
    territoire: CommuneRef
    source: SourceValeur
    millesime: str | None = None
    url: str


class ValeurLieu(BaseModel):
    """One served value of the place with its state and its meaning (SEM-01/02).

    ``field_id`` names the ``fiche-fields`` entry that carries the unit and
    the definition; ``rattachement`` says how the value relates to the
    address. A value is present only when ``etat=value``; every other state
    carries no value, and out of coverage carries its reason in ``motif``.
    """

    field_id: str
    etat: EtatValeur
    motif: str | None = None
    valeur: int | float | str | None = None
    unite: str | None = None
    periode: str | None = None
    source: SourceValeur | None = None
    rattachement: Rattachement | None = None
    reference: ReferenceCommune | None = None


class DocumentSelectionne(BaseModel):
    """A document named by the most-recent selection: number and classes."""

    numero: str
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None
    date_etablissement: dt.date | None = None


MotifSelection = Literal[
    "document_valide",
    "ancienne_methode",
    "expire",
    "remplace",
    "remplacement_incertain",
    "validite_inconnue",
    "aucun_document_valide",
    "aucun_document_date",
    "aucun_document",
    "population_incomplete",
]


class SelectionPlusRecent(BaseModel):
    """Most recent document of the whole population, never of a page (D-109).

    ``unique``: one document at the latest date (``document``). ``egalite``:
    several documents share the latest date; all are listed in
    ``documents_ex_aequo`` and none is chosen. ``indeterminee``: no dated
    candidate. ``motif`` says whether the date is the one of a valid
    document (``document_valide``) or, when no valid document exists, why
    the latest one is not valid; it never calls such a document valid.
    """

    etat: Literal["unique", "egalite", "indeterminee"]
    date: dt.date | None = None
    document: DocumentSelectionne | None = None
    documents_ex_aequo: list[DocumentSelectionne] = Field(default_factory=list)
    motif: MotifSelection


class RepereDpe(ValeurLieu):
    """Landmark ①: number of DPE declared at the address and the most recent."""

    selection: SelectionPlusRecent | None = None


class RepereVentes(BaseModel):
    """Landmark ②: sales of the parcel that contains the BAN point (N-02)."""

    nombre: ValeurLieu
    derniere: ValeurLieu


class RepereReferenceCommune(BaseModel):
    """Landmark ④: median prices of the commune, as a reference (D-103).

    The same values as ``/v1/commune`` for the commune of the address,
    attached to the commune (``commune_reference``), never to the place.
    """

    maisons: ValeurLieu
    appartements: ValeurLieu


class ReperesAdresse(BaseModel):
    """The landmarks of the address page, in their display order (D-113).

    ① DPE declared at the address, ② sales of the parcel of the point,
    ③ clay exposure at the point, ④ the commune median as a reference.
    """

    dpe: RepereDpe
    ventes: RepereVentes
    argiles: ValeurLieu
    reference_commune: RepereReferenceCommune


class ElementLieu(BaseModel):
    """One item of a served list (a stop, a risk, a zone, a registration).

    ``libelle`` as the source publishes it; ``distance_m`` is the geodesic
    distance to the BAN point when measured (never a travel time);
    ``nature`` the kind of item when the list mixes several (``arret``,
    ``gare``). No coordinate, no internal key.
    """

    libelle: str
    distance_m: int | None = None
    nature: str | None = None


class ValeurTheme(ValeurLieu):
    """One served value of a theme of the place (04-06, D-104).

    A boolean is served as such (published eligibility). A list field serves
    its count in ``valeur`` and its items in ``elements``;
    ``liste_incomplete`` says the source answered a partial read, never an
    exhaustive one. ``rayon_m`` is the radius a proximity value was read in;
    a radius that reaches a departement not hosted (C16) is a partial read
    with the reason ``territoire_hors_perimetre``, and ``out_of_coverage``
    when nothing was found on the hosted side (never a zero).
    """

    valeur: bool | int | float | str | None = None
    elements: list[ElementLieu] = Field(default_factory=list)
    liste_incomplete: bool = False
    rayon_m: int | None = None


class GroupeTheme(BaseModel):
    """A group of values of a theme; a titled group never mixes scopes."""

    cle: str
    titre: str | None = None
    champs: list[ValeurTheme] = Field(default_factory=list)


CleTheme = Literal["batiment_parcelle", "risques", "urbanisme", "reseaux_transports"]
EtatTheme = Literal["value", "not_available", "out_of_coverage", "temporary_error"]


class ThemeLieu(BaseModel):
    """One folded theme of the place, after the sales (D-104, D-115).

    ``applicable=false`` is the only case where the page omits a theme; a
    theme out of coverage, in outage or without a single parcel or point is
    served with its state and reason, with ``groupes=[]``, except « Bâtiment
    et parcelle », which still carries the RNB field of the BAN key (and no
    value of the parcel). With
    ``etat=value``, each value keeps its own state: an outage of one source
    of the theme stays visible next to the values served by the others.
    """

    cle: CleTheme
    titre: str
    applicable: bool = True
    etat: EtatTheme
    motif: str | None = None
    groupes: list[GroupeTheme] = Field(default_factory=list)
    liste_incomplete: bool = False


class FicheAdresse(BaseModel):
    """Identity and coverage of one address page, its landmarks and themes.

    ``reperes`` is null and ``themes`` empty when the BAN of the departement
    is out of coverage: nothing of the place is read. The themes come in the
    fixed order of D-115; the commune of the address (reference) is read by
    the page from ``/v1/commune``, never served here.
    """

    identite: IdentiteAdresse
    couverture: CouvertureAdresse
    reperes: ReperesAdresse | None = None
    themes: list[ThemeLieu] = Field(default_factory=list)


BadgeDocument = Literal[
    "plus_recent",
    "meme_date_plus_recente",
    "ancienne_methode",
    "expire",
    "remplace",
    "remplacement_incertain",
    "dates_incoherentes",
    "etablissement_futur",
]


MotifAdresseDeclaree = Literal["complement_present", "non_verifiable_sans_complement"]


class AdresseDeclaree(BaseModel):
    """Address typed in a document, as published (P-ADR-01, LIEU-03).

    ``publiee``: the raw text as typed, established without complement;
    ``masquee``: the whole field withheld, with its reason (the raw text is
    kept internally, never served); ``absente``: the document carries none.
    """

    etat: Literal["publiee", "masquee", "absente"]
    texte: str | None = None
    motif: MotifAdresseDeclaree | None = None


class DocumentDpeCarte(BaseModel):
    """One declared document of the list: the source number, never a reference.

    ``adresse_declaree`` is served only when the raw address is not strictly
    concordant with the BAN key (``app.dpe.concordance``); ``None`` otherwise.
    """

    numero: str
    etiquette_dpe: str | None = None
    etiquette_ges: str | None = None
    type_batiment: str | None = None
    surface_habitable: float | None = None
    date_etablissement: dt.date | None = None
    date_fin_validite: dt.date | None = None
    badges: list[BadgeDocument] = Field(default_factory=list)
    adresse_declaree: AdresseDeclaree | None = None


class DocumentDpeCarteAdresseBrute(DocumentDpeCarte):
    """A document attached by its exact raw address (plan 04.1-13, D-127).

    Its ADEME key is absent from the BAN; its raw address names this BAN
    address exactly, as the only candidate, with the same street code. A
    declared attachment, never the ADEME key and never a dwelling. Its
    status badges are served; no selection badge (the most recent document is
    the one of the ADEME key).
    """

    rattachement: Literal["adresse_brute_exacte"] = "adresse_brute_exacte"


class SignalPlusieursLogements(BaseModel):
    """Signal of several dwellings on the parcel of the point (LIEU-02).

    Served only for a ``unique`` parcel and a hosted condominium registry: a
    registered condominium on that parcel. A parcel signal designates no
    dwelling and no document; the registry identity is not served.
    """

    etat: Literal["value", "temporary_error"]
    motif: Literal["copropriete_immatriculee"] | None = None
    nombre_lots_habitation: int | None = None
    rattachement: Literal["parcelle_du_point"] = "parcelle_du_point"
    source: SourceValeur


CritereD110 = Literal[
    "maison",
    "document_unique_cle",
    "concordance_stricte",
    "batiment_unique_parcelle_du_point",
    "annexes_exclues_usage_inconnu_compte",
]


class ConstatD110(BaseModel):
    """« Une adresse, un bâtiment, un DPE : les éléments concordent » (D-110).

    Served only when every criterion holds (DPE-01 option-d, P-D110-NULL):
    a house, the only document of the BAN key (retired ones included), a
    strict concordance of its raw address, one parcel containing the BAN
    point, one building on it that is not an explicit annex (an unknown use
    counts). A concordance of sources, never the identity of a dwelling.
    """

    criteres: list[CritereD110]
    sources: list[SourceValeur]


class DocumentsDpeAdresse(BaseModel):
    """Chronological list of the DPE declared at an address (D-109, D-111).

    Total, distribution and selection come from the same snapshot as the
    page; ``generation`` identifies that snapshot and is carried by the page
    links (``?page=2&g=…``).
    """

    etat: Literal["value", "out_of_coverage"]
    motif: OutOfCoverageReason | None = None
    rattachement: Literal["adresse_saisie_diagnostiqueur"] = (
        "adresse_saisie_diagnostiqueur"
    )
    source: SourceValeur | None = None
    total: int | None = None
    page: int
    pages: int | None = None
    par_page: int
    generation: str | None = None
    distribution: DpeInventoryDistribution | None = None
    date_import: dt.date | None = None
    documents: list[DocumentDpeCarte] = Field(default_factory=list)
    selection: SelectionPlusRecent | None = None
    signal_plusieurs_logements: SignalPlusieursLogements | None = None
    constat_d110: ConstatD110 | None = None
    # Plan 04.1-13: a separate group, outside ``total``, ``distribution``,
    # ``selection`` and ``constat_d110`` (those describe the ADEME key only).
    # ``not_available``: the served generation was imported without the
    # attachment (``total_adresse_brute`` null, never a zero). ``value``: its
    # total and at most ``ADRESSE_BRUTE_MAX`` most recent cards, the same on
    # every page of the list. Null out of coverage.
    etat_adresse_brute: Literal["value", "not_available"] | None = None
    total_adresse_brute: int | None = None
    documents_adresse_brute: list[DocumentDpeCarteAdresseBrute] = Field(
        default_factory=list
    )


# Constant codes of the problems of the address routes (never the input).
CodeProblemeAdresse = Literal[
    "adresse_inconnue",
    "voie_sans_numero",
    "geocodage_indisponible",
    "adresse_indisponible",
    "page_hors_limites",
    "generation_changee",
    "documents_dpe_indisponibles",
    "ventes_indisponibles",
    "ids_invalides",
    "designations_invalides",
    "comparaison_indisponible",
    "departement_invalide",
]


class ProblemeAdresse(ProblemDetail):
    """RFC 9457 problem with a constant ``code`` extension member.

    The problem never reflects the input (T-04-02-03).
    """

    code: CodeProblemeAdresse


# --- 04-05: sales of the parcel of the point and similar sales (D-114, D-115) --

MotifMinimisation = Literal["vente_isolee_recente", "isolement_indetermine"]
TypeComparable = Literal["maison", "appartement"]


class CompositionVente(BaseModel):
    """Locals of one mutation as declared in DVF (counts, never a dwelling)."""

    maisons: int = 0
    appartements: int = 0
    dependances: int = 0
    locaux_activite: int = 0


class MinimisationVente(BaseModel):
    """Why the price of a sale is withheld (DVF reuse restrictions).

    ``vente_isolee_recente``: within 24 months of the last imported DVF date,
    one of its parcels carries this mutation only. ``isolement_indetermine``:
    a recent sale whose isolation cannot be established; no exact price is
    published either.
    """

    motif: MotifMinimisation


class CarteVente(BaseModel):
    """One DVF mutation: never attached to a dwelling, never identified.

    ``prix`` (the amount of the whole mutation) and ``prix_m2`` are null when
    the price is withheld (``prix_affiche=false``, ``minimisation`` set):
    the exact amount never leaves the API. ``prix_m2`` is also null outside a
    single-local residential sale (no honest price per m2). ``distance_m`` is
    the geodesic distance to the BAN point, served for similar sales only.
    ``hors_stats_vefa`` marks a VEFA (« Vente en l'état futur
    d'achèvement », plan 04.1-15, D-12): a sale that is listed but never
    counted in a statistic.
    """

    date: dt.date
    nature: str | None = None
    composition: CompositionVente
    surface_batie: int | None = None
    prix: float | None = None
    prix_m2: int | None = None
    prix_affiche: bool
    minimisation: MinimisationVente | None = None
    distance_m: int | None = None
    hors_stats_vefa: bool = False


class VentesParcelle(BaseModel):
    """Sales of the parcel that contains the BAN point (D-115, LIEU-02, N-02).

    ``value``: the distinct mutations of the parcel, 10 per page, latest
    first, over the whole imported DVF period (same population as landmark
    ②). ``not_available``: the point designates no single parcel (``motif``
    ``point_hors_parcelle``, ``parcelles_multiples`` with
    ``nombre_parcelles``, ``parcelle_indeterminee``); ``out_of_coverage``:
    BAN, DVF or cadastre not hosted; ``temporary_error``: the cadastre read
    failed. ``generation`` is carried by the page links (``vg``).
    """

    etat: Literal["value", "not_available", "out_of_coverage", "temporary_error"]
    motif: str | None = None
    nombre_parcelles: int | None = None
    rattachement: Literal["parcelle_du_point"] = "parcelle_du_point"
    source: SourceValeur
    periode: str | None = None
    total: int | None = None
    page: int
    pages: int | None = None
    par_page: int
    generation: str | None = None
    ventes: list[CarteVente] = Field(default_factory=list)


MotifVentesAdresse = Literal[
    "suffixe_non_distingue",
    "suffixe_sans_correspondance",
    "suffixe_non_importe",
    "sans_numero",
    "adresse_indeterminee",
]


class VentesAdresse(BaseModel):
    """Sales whose declared DVF address is the address of the page (C37).

    A relation distinct from the sales of the parcel, never merged with them:
    the DVF sales (``est_vente``) declared at the number, the normalised
    street and the commune of the BAN address, 10 per page, latest first.
    Default rule ``numero`` (C37): the suffix is not compared, a sale
    declared at « 2 » or at « 2 B » belongs to the BAN address « 2 » without
    suffix only. Rule ``numero_et_suffixe`` (C44, plan 04.1-14, on request):
    « 2 » takes the sales declared without suffix, « 2 bis » those declared
    « 2 B », by the measured BAN ``rep`` -> DVF table. ``value`` with
    ``total`` 0 says that no sale was attached to the address, not that none
    took place: only the address of the first line of a sale is kept, and
    some declared sales find no BAN address (C44).
    ``not_available``: an address with a suffix under the default rule
    (``suffixe_non_distingue``), a suffix without single correspondence
    (``suffixe_sans_correspondance``), a DVF generation imported without the
    suffix under the suffix rule (``suffixe_non_importe``), an address
    without a usable number (``sans_numero``), or whose BAN rows disagree
    (``adresse_indeterminee``); ``out_of_coverage``: BAN or DVF not hosted.
    A failed read is a 503, never an empty list. ``generation`` is the DVF
    token of the parcel list.
    VEFA (plan 04.1-15, D-128): by default (``vefa=exclues``) the sales
    (« Vente ») only. Under ``vefa=incluses`` the VEFA declared at the address
    are listed too, each card marked ``hors_stats_vefa``; ``total`` counts the
    listed mutations (the pages) and ``nombre_vefa`` the VEFA among them,
    ``total - nombre_vefa`` being the count of the sales
    (``adresse.dvf.ventes_adresse.nombre``, which never takes a VEFA).
    ``nombre_vefa`` is null when the VEFA were not searched.
    """

    etat: Literal["value", "not_available", "out_of_coverage"]
    motif: str | None = None
    rattachement: Literal["adresse_declaree_vente"] = "adresse_declaree_vente"
    source: SourceValeur
    periode: str | None = None
    total: int | None = None
    nombre_vefa: int | None = None
    page: int
    pages: int | None = None
    par_page: int
    generation: str | None = None
    ventes: list[CarteVente] = Field(default_factory=list)


class PeriodeAppliquee(BaseModel):
    """Calendar years applied, counted back from the last imported DVF date."""

    annees: int
    debut: int | None = None
    fin: int | None = None


class CriteresAppliques(BaseModel):
    """Criteria of the similar sales as applied by the API (D-114).

    The surface bounds are computed here (rounded to the square metre) and
    are exactly the ones filtered on; the page words them as served.
    """

    type: TypeComparable
    surface: int | None = None
    tolerance: int
    surface_min: int | None = None
    surface_max: int | None = None
    perimetre: Literal["rayon", "commune"]
    rayon_m: int | None = None
    periode: PeriodeAppliquee


class VentesRessemblantes(BaseModel):
    """Similar sales around the BAN point or in its commune (D-114).

    Filter, order (distance, then date descending, then mutation) and count
    are the API's; ``effectif`` is computed before pagination. No sale is
    attached to a dwelling; withheld prices are withheld as in the parcel
    list. ``not_available`` (``position_indeterminee``): the key has no
    single BAN point, no distance can be measured; without reason, no DVF
    date is known (nothing measured). A radius that reaches a departement
    not hosted (C16) serves ``liste_incomplete`` and the reason
    ``territoire_hors_perimetre``: ``effectif`` counts the hosted side only,
    and without any sale there the state is ``out_of_coverage``, never 0.
    """

    etat: Literal["value", "not_available", "out_of_coverage"]
    motif: str | None = None
    liste_incomplete: bool = False
    rattachement: Literal["rayon_du_point", "commune_de_l_adresse"]
    source: SourceValeur
    criteres_appliques: CriteresAppliques
    effectif: int | None = None
    page: int
    pages: int | None = None
    par_page: int
    generation: str | None = None
    ventes: list[CarteVente] = Field(default_factory=list)


# --- 04-07: addresses compared, same rule as the communes (CMP-01, D-116) ------

# Reasons not to serve a gap between two addresses: the 03-09 rule, with the
# reason of a column out of coverage naming the address, and on the rows of
# the designated documents (04-11) the reasons of the documents (04-10).
MotifNonCalculAdresse = Literal[
    "adresse_hors_couverture",
    "donnee_absente",
    "periode_inconnue",
    "periodes_differentes",
    "unites_differentes",
    "definitions_differentes",
    "non_numerique",
    "methodes_differentes",
    "methode_indeterminee",
    "adresse_declaree_non_affichee",
    "liste_incomplete",
]
CleGroupeComparaison = Literal["documents", "ventes", "lieu", "commune_reference"]


class DesignationDpe(BaseModel):
    """The DPE the visitor designated on a compared address (04-11, D-102).

    Kept only when the number is declared at this address (same source key,
    retired documents excluded, as the list of the address page). ``designe``:
    read, with the class and the establishment date of the document page.
    ``temporary_error``: the DPE block could not be read (refused publication,
    failed read); the pair is kept, never called « not declared ».
    ``out_of_coverage``: the DPE is not hosted for the address. A designation
    lives in the link only; it attaches the document to no dwelling.
    """

    numero: str
    etat: Literal["designe", "temporary_error", "out_of_coverage"]
    motif: str | None = None
    classe_energie: str | None = None
    date_etablissement: dt.date | None = None


class DesignationEcartee(BaseModel):
    """A requested pair whose number is not declared at its address."""

    ban_id: str
    numero: str


class ColonneAdresse(BaseModel):
    """A compared address: its served identity and coverage, no coordinate.

    ``numero_voie`` is « {numéro}{indice} {voie} », null when the BAN rows of
    the key disagree on it (``identite_etat=indeterminee``). ``designation``:
    the DPE designated on this address, if any (04-11).
    """

    ban_id: str
    libelle: str | None = None
    numero_voie: str | None = None
    code_postal: str | None = None
    commune: CommuneRef
    departement: DepartementRef
    couverture: CouvertureAdresse
    identite_etat: Literal["etablie", "indeterminee"] = "etablie"
    designation: DesignationDpe | None = None


class CelluleComparaisonAdresse(ValeurTheme):
    """One address's value for a row: the value of its page, and its verdict.

    The value, state, reason, source and attachment are those of the address
    page (``/v1/adresse/{ban_id}``), never recomputed. Columns 2 to 4 carry
    the gap to column 1, the reason why none is served, or for a comparable
    non-numeric value the verdict ``identique`` / ``different`` (no figure).
    ``selection`` is the served most-recent selection of ① on the row of the
    most recent document.
    """

    ban_id: str
    selection: SelectionPlusRecent | None = None
    ecart: Ecart | None = None
    motif_non_calcul: MotifNonCalculAdresse | None = None
    verdict: Literal["identique", "different"] | None = None
    identique_a_la_reference: bool | None = None

    @model_validator(mode="after")
    def one_verdict_at_most(self) -> Self:
        served = [self.ecart, self.motif_non_calcul, self.verdict]
        if sum(item is not None for item in served) > 1:
            raise ValueError("a gap, a reason and a verdict are exclusive")
        return self


class LigneComparaisonAdresse(BaseModel):
    """One compared value, one cell per column (column 1 is the reference)."""

    cle: str
    field_id: str
    identiques: bool = Field(
        description=(
            "Vrai si valeurs, états, périodes, unités et définitions sont égaux "
            "dans toutes les colonnes ; faux dès qu'une adresse est hors "
            "couverture. Deux absences de même état et de même période sont "
            "identiques (D-116)."
        )
    )
    designe: bool = Field(
        default=False,
        description=(
            "Vrai pour une ligne du DPE désigné par le visiteur (04-11) : la "
            "valeur du document, rattaché à aucun logement ; une colonne sans "
            "désignation y porte l'état not_applicable, motif aucun_dpe_designe."
        ),
    )
    cellules: list[CelluleComparaisonAdresse] = Field(min_length=1)

    @model_validator(mode="after")
    def verdict_per_column(self) -> Self:
        reference, *others = self.cellules
        if (
            reference.ecart is not None
            or reference.motif_non_calcul is not None
            or reference.verdict is not None
            or reference.identique_a_la_reference is not None
        ):
            raise ValueError("column 1 is the reference: no verdict")
        for cellule in others:
            if (
                cellule.ecart is None
                and cellule.motif_non_calcul is None
                and cellule.verdict is None
            ):
                raise ValueError("a compared cell needs a gap, a reason or a verdict")
        return self


class GroupeComparaisonAdresse(BaseModel):
    """A group of rows; « Commune de l'adresse (référence) » always comes last."""

    cle: CleGroupeComparaison
    titre: str
    lignes: list[LigneComparaisonAdresse]


class ComparaisonAdresses(BaseModel):
    """2 to 4 addresses compared row by row, in four groups (CMP-01, D-116).

    ``ids_ecartes``: the requested keys the BAN of this instance does not
    know (or a street without number), named so the link can say it was
    altered. ``designations_ecartees``: the designated pairs whose number is
    not declared at their address (04-11), removed from the comparison.
    Nothing is attached to a dwelling.
    """

    colonnes: list[ColonneAdresse]
    groupes: list[GroupeComparaisonAdresse]
    ids_ecartes: list[str] = Field(default_factory=list)
    designations_ecartees: list[DesignationEcartee] = Field(default_factory=list)

    @model_validator(mode="after")
    def reference_last(self) -> Self:
        cles = [groupe.cle for groupe in self.groupes]
        if cles and cles[-1] != "commune_reference":
            raise ValueError("the commune reference group is always last")
        return self

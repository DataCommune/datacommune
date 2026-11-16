"""Origin of every public response property in the OpenAPI contract (x-source).

Each property of each response schema reached from a public route carries an
``x-source`` extension naming where its value comes from, and
``info.x-sources`` publishes the dictionary once. The kinds of value:

- a key of :data:`app.common.source_catalog.DATASET_CATALOG`: the value is read
  from that source (copied, cast, renamed, formatted or picked from a source
  row), including figures the source computed itself;
- ``referentiel-communes``: read in ``geo_communes``/``geo_departements``,
  imported by ``scripts/import_geo_boundaries.py`` (geo.api.gouv.fr and
  france-geojson), a referential outside the catalog;
- ``identite-territoriale``: code and name of a commune or departement whose
  origin depends on the route (BAN row, commune synthesis, referential, or a
  departement code cut from an INSEE or postal code) in shared schemas;
- ``datacommune``: DataCommune produced the value -- protocol (envelope,
  grouping, pagination, state, reason, verdict, attachment, attribution),
  internal identifier or table, or any calculation (count, sum, median, rate,
  distance, centroid, classification, comparison). A property grouping an
  object of mixed origins carries it too: the origin of each value is on the
  properties of that object;
- ``par-valeur``: the origin varies with the object and the response serves
  it: a ``source``/``sources``/``provenance`` field of the object or of its
  theme or indicator block, the key of the map holding it, ``meta.source``, or
  a discriminant of the object (``type_transport``, ``type_detecte``,
  ``population_source``, ``entite``, ``cle`` of a comparison criterion).

Attribution, in order: property override, schema override, then the module of
the Pydantic model behind the schema (longest module prefix of
:data:`MODULE_SOURCES`). A property matched by none stays unannotated, and
``tests/test_openapi_sources.py`` names it. Internal routes are out of scope.
"""

import enum
import re
import typing
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.routing import BaseRoute

from app.common.source_catalog import DATASET_CATALOG

DATACOMMUNE = "datacommune"
PAR_VALEUR = "par-valeur"
REFERENTIEL = "referentiel-communes"
IDENTITE_TERRITORIALE = "identite-territoriale"

# Routes whose schemas need no x-source (account, billing, administration).
INTERNAL_PATH_PREFIXES = ("/auth", "/billing", "/stripe", "/v1/admin", "/v1/validation")

_SITE_SOURCES_URL = "https://datacommune.fr/datasets"

# Rule (c): module of the Pydantic model -> source. Longest prefix wins.
MODULE_SOURCES: dict[str, str] = {
    "app.antennes": "antennes",
    "app.ban": "ban",
    "app.batiments": "batiments",
    "app.bdnb": "bdnb",
    "app.cadastre": "cadastre",
    "app.connectivite": "connectivite",
    "app.copropriete": "copropriete",
    "app.demographie": "demographie",
    "app.dpe": "dpe",
    "app.dvf": "dvf",
    "app.equipements": "equipements",
    "app.errial": "errial",
    "app.fiscalite": "fiscalite",
    "app.foncier_pm": "foncier_pm",
    "app.georisques": "georisques",
    "app.loyers": "loyers",
    "app.monuments": "monuments",
    "app.permis": "permis",
    "app.revenus": "revenus",
    "app.rnb": "rnb",
    "app.scolaire": "scolaire",
    "app.sirene": "sirene",
    # Stops (``arret``, transports) and SNCF stations (``gare``, gares) in one
    # list: ``type_transport`` names the source of each row.
    "app.transport": PAR_VALEUR,
    "app.urbanisme": "urbanisme",
    "app.vacance": "vacance",
    "app.zonage_abc": "zonage_abc",
    # DataCommune's own structures; their source fields are overridden below.
    "app.adresse": DATACOMMUNE,
    "app.carte": DATACOMMUNE,
    "app.common": DATACOMMUNE,
    "app.commune": DATACOMMUNE,
    # Indicator blocks serve their own ``provenance``.
    "app.commune.indicator_schemas": PAR_VALEUR,
    "app.datasets": DATACOMMUNE,
    "app.dpe.comparaison_public": DATACOMMUNE,
    "app.dpe.document_public": DATACOMMUNE,
    "app.dpe.matching_schemas": DATACOMMUNE,
    "app.health": DATACOMMUNE,
    "app.logements": DATACOMMUNE,
    "app.rapport": DATACOMMUNE,
    "app.resolve": DATACOMMUNE,
    "app.zone": DATACOMMUNE,
}

# Rule (b): every property of the schema, before the module rule.
SCHEMA_SOURCES: dict[str, str] = {
    # FastAPI's own 422 body: an error schema.
    "HTTPValidationError": DATACOMMUNE,
    "ValidationError": DATACOMMUNE,
    # Indicator protocol and DVF delivery anchoring (ETL proofs, windows).
    "AncrageDvfInconnu": DATACOMMUNE,
    "FenetreIndicateur": DATACOMMUNE,
    "IndicateurAbsent": DATACOMMUNE,
    "LivraisonDvfRecue": DATACOMMUNE,
    "ProvenanceDvf": DATACOMMUNE,
    "ProvenanceIndicateur": DATACOMMUNE,
    # DPE attribution decisions, lifecycle and inventory protocol.
    "DpeAttribution": DATACOMMUNE,
    "DpeBuildingContext": DATACOMMUNE,
    "DpeInventoryDistribution": DATACOMMUNE,
    "DpeInventoryPage": DATACOMMUNE,
    "DpeLifecycle": DATACOMMUNE,
    "DatesDocument": "dpe",
    "DocumentSelectionne": "dpe",
    # DVF attribution decision, address-search echo and envelope.
    "DvfAttribution": DATACOMMUNE,
    "RechercheDvfAdresse": "ban",
    "ReponseDvfAdresse": DATACOMMUNE,
    "ReponsePermis": DATACOMMUNE,
    # Shared territory refs: the name comes from the BAN row (DPE document),
    # the commune synthesis (reference value) or geo_communes (identity).
    "CommuneRef": IDENTITE_TERRITORIALE,
    "ResultatDepartement": REFERENTIEL,
}


def _props(schema: str, source: str, *names: str) -> dict[str, str]:
    return {f"{schema}.{name}": source for name in names}


_DPE_DOCUMENT_FIELDS = (
    "numero",
    "etiquette_dpe",
    "etiquette_ges",
    "type_batiment",
    "surface_habitable",
    "date_etablissement",
    "date_fin_validite",
)
_BAN_ADDRESS_FIELDS = ("ban_id", "numero", "rep", "nom_voie", "code_postal")
_RESOLVED_BAN_FIELDS = ("adresse", "commune", "code_postal", "ban_id")
# The resolution path (``type_detecte``: adresse/gps -> BAN or input point,
# cadastre -> parcel centroid and code) names the origin of these.
_RESOLVED_BY_PATH = ("latitude", "longitude", "code_insee")
_SERVED_POPULATION = ("population", "population_annee")
_REFERENTIAL_IDENTITY = ("code_insee", "nom", "departement")

# Rule (a): one property, before every other rule.
PROPERTY_SOURCES: dict[str, str] = {
    # -- address pages (app.adresse) --
    **_props("AdresseDeclaree", "dpe", "texte"),
    **_props("CarteVente", "dvf", "date", "nature", "prix"),
    **_props("CelluleComparaisonAdresse", PAR_VALEUR, "valeur", "periode", "elements"),
    **_props("CelluleComparaisonAdresse", "ban", "ban_id"),
    **_props(
        "ColonneAdresse", "ban", "ban_id", "libelle", "numero_voie", "code_postal"
    ),
    **_props("DesignationDpe", "dpe", "numero", "classe_energie", "date_etablissement"),
    **_props("DocumentDpeCarte", "dpe", *_DPE_DOCUMENT_FIELDS),
    **_props("DocumentDpeCarteAdresseBrute", "dpe", *_DPE_DOCUMENT_FIELDS),
    **_props("ElementLieu", PAR_VALEUR, "libelle", "nature"),
    **_props("IdentiteAdresse", "ban", "libelle", *_BAN_ADDRESS_FIELDS),
    **_props("ReferenceCommune", PAR_VALEUR, "valeur", "millesime"),
    **_props("RelationParcelle", "cadastre", "id_parcelle"),
    **_props("RepereDpe", PAR_VALEUR, "valeur", "periode"),
    **_props(
        "ResultatRechercheAdresse",
        "ban",
        "code_commune",
        "nom_commune",
        *_BAN_ADDRESS_FIELDS,
    ),
    **_props("SelectionPlusRecent", "dpe", "date"),
    **_props("SignalPlusieursLogements", "copropriete", "nombre_lots_habitation"),
    **_props("ValeurLieu", PAR_VALEUR, "valeur", "periode"),
    **_props("ValeurTheme", PAR_VALEUR, "valeur", "periode", "elements"),
    **_props("VentesAdresse", "dvf", "periode"),
    **_props("VentesParcelle", "dvf", "periode"),
    # -- interactive map (app.carte): states, bounds, label points and counts
    # are DataCommune's; what is copied from a source is named here --
    **_props("CentreCarte", "ban", "lon", "lat"),
    **_props("ContexteCarte", "cadastre", "cadastre_millesime"),
    **_props("EntiteParcelle", "cadastre", "id"),
    **_props("EtatVentes", "dvf", "periode"),
    **_props("GeometrieMultiPolygone", "cadastre", "coordinates"),
    **_props("ParcelleAdresse", "cadastre", "id_parcelle"),
    # The parcels of a sale are named by DVF; their shape is the cadastre's.
    **_props("ParcelleDuGroupe", "dvf", "id_parcelle"),
    **_props("ProprietesParcelle", "cadastre", "id_parcelle", "contenance"),
    **_props("TuileParcelles", "cadastre", "cadastre_millesime"),
    **_props("VentesParcelleCarte", "dvf", "periode"),
    # -- common envelope --
    **_props("ResolutionOutcome", "cadastre", "parcelles_candidates"),
    # -- commune synthesis and comparison (v_commune_synthese columns) --
    **_props("CelluleComparaison", PAR_VALEUR, "valeur", "periode"),
    **_props(
        "ResultatCommuneSynthese",
        "demographie",
        "code_insee",
        "nom_commune",
        "code_departement",
        "code_region",
        "population",
        "nb_menages",
        "nb_logements",
        "nb_logements_vacants_recensement",
        "annee_demographie",
    ),
    **_props(
        "ResultatCommuneSynthese",
        "revenus",
        "revenu_median",
        "taux_pauvrete",
        "indice_gini",
        "part_imposee",
        "annee_revenus",
    ),
    **_props(
        "ResultatCommuneSynthese", "zonage_abc", "zone_abc", "zone_abc_reclassement"
    ),
    **_props(
        "ResultatCommuneSynthese", "vacance", "nb_vacants_plus_2ans", "annee_vacance"
    ),
    **_props(
        "ResultatCommuneSynthese",
        "fiscalite",
        "taux_tfpb",
        "taux_th_rs",
        "taux_teom",
        "nom_epci",
        "annee_fiscalite",
    ),
    **_props(
        "ResultatCommuneSynthese",
        "loyers",
        "loyer_m2_appartement",
        "loyer_m2_maison",
        "annee_loyers",
        "loyers_appartement",
        "loyers_maison",
    ),
    **_props("ResultatCommuneSynthese", "equipements", "annee_equipements"),
    # -- indicator blocks: protocol beside the served values --
    **_props("ClasseDvf", DATACOMMUNE, "qualification_tendance"),
    **_props("IndicateurNombre", DATACOMMUNE, "etat"),
    **_props(
        "IndicateursDpe",
        DATACOMMUNE,
        "version_contrat",
        "version_population",
        "date_calcul",
        "generation",
        "provenance",
        "population",
        "periode",
        "date_evaluation",
        "methode_mediane",
        "methode_modes",
    ),
    **_props(
        "IndicateursDvf",
        DATACOMMUNE,
        "version_contrat",
        "version_population",
        "date_calcul",
        "generation",
        "provenance",
        "ancrage_temporel",
        "population",
        "unite_prix",
        "methode_quantiles",
        "methode_variation",
    ),
    **_props("QuantilesDvf", DATACOMMUNE, "etat", "qualification"),
    **_props("ResultatDpeIndicateur", DATACOMMUNE, "etat"),
    **_props("StatistiquesFenetreDvf", DATACOMMUNE, "fenetre", "motif_periode"),
    # -- DPE (app.dpe.*) --
    **_props("AdresseBanDocument", "dpe", "ban_id"),
    **_props("AdresseBanDocument", "ban", "libelle", "numero", "rep", "nom_voie"),
    **_props("CelluleComparaisonDocument", PAR_VALEUR, "valeur", "periode"),
    **_props("CelluleComparaisonDocument", "dpe", "numero"),
    **_props("ColonneDocument", "dpe", "numero"),
    **_props("DocumentDpePublic", "dpe", "numero"),
    **_props("DpeCandidate", DATACOMMUNE, "perime"),
    **_props(
        "DpeConsultedDocument",
        DATACOMMUNE,
        "association",
        "address_check",
        "source",
        "source_coverage",
        "graph_state",
        "imported_at",
        "version",
        "context_ref",
        "document_ref",
        "validity_by_date",
        "lifecycle",
    ),
    **_props("DpeInventoryItem", DATACOMMUNE, "document_ref", "lifecycle"),
    **_props("DpePublicDiagnostic", DATACOMMUNE, "perime"),
    **_props("DpeSurfaceComparison", "dpe", "source"),
    **_props("DpeTypeComparison", "dpe", "source"),
    **_props("SourceIdentity", "dpe", "reference"),
    **_props("StatutPublic", "dpe", "date", "remplace_par", "remplace"),
    # -- DVF: the mutation grain is rebuilt from the source lines (sums,
    # centroid, classification); the projected culture fields are always null.
    **_props(
        "ResultatDvf",
        DATACOMMUNE,
        "type_local",
        "surface_reelle_bati",
        "nombre_pieces_principales",
        "surface_terrain",
        "prix_m2",
        "code_type_local",
        "code_nature_culture",
        "nature_culture",
        "code_nature_culture_speciale",
        "nature_culture_speciale",
        "longitude",
        "latitude",
        "classe_mutation",
        "prix_m2_raison",
        "mutation_mono_local",
        "nb_locaux_maison",
        "nb_locaux_appartement",
        "nb_locaux_commercial",
        "nb_parcelles",
        "hors_stats_vefa",
        "surface_bati_totale",
    ),
    **_props("DvfAttribution", "dvf", "nb_lots_source"),
    **_props("RechercheDvfAdresse", DATACOMMUNE, "etat", "rayon_m"),
    # -- local datasets: values DataCommune computes on import or read --
    **_props("ResultatDemographie", DATACOMMUNE, "superficie", "densite"),
    **_props("ResultatParcelle", DATACOMMUNE, "centroide_lat", "centroide_lon"),
    # Filosofi import: departement cut from the INSEE code, names left empty.
    **_props(
        "ResultatRevenus",
        DATACOMMUNE,
        "nom_commune",
        "code_departement",
        "nom_departement",
        "nom_region",
    ),
    **_props(
        "ResultatVacance",
        DATACOMMUNE,
        "nb_vacants_moins_2ans",
        "taux_vacance",
        "taux_vacance_moins_2ans",
        "taux_vacance_plus_2ans",
    ),
    **_props("ResultatArret", DATACOMMUNE, "distance_m", "type_transport"),
    **_props("ResultatGeocodage", DATACOMMUNE, "score"),
    **_props("ResultatReverseGeocodage", DATACOMMUNE, "distance_m"),
    # -- proxied sources: DataCommune's account of the read --
    **_props("ResultatBDNB", DATACOMMUNE, "nombre_retournes", "couverture"),
    **_props("ResultatRNB", DATACOMMUNE, "nombre_retournes", "couverture"),
    **_props("ResultatErrial", DATACOMMUNE, "sources"),
    **_props("ResultatPollutions", DATACOMMUNE, "sources"),
    **_props("ResultatRisques", DATACOMMUNE, "sources"),
    **_props("ResultatUrbanisme", DATACOMMUNE, "sources"),
    # -- building record (deprecated /v1/bien/logements) --
    **_props("Denominateur", "copropriete", "lots_declares"),
    **_props("ResultatLogements", "cadastre", "id_parcelle"),
    **_props("VenteImmeuble", "dvf", "date_mutation", "valeur_fonciere"),
    # -- address report: themes serve their sources[] --
    **_props("Chiffre", PAR_VALEUR, "valeur", "millesime"),
    **_props("Theme", PAR_VALEUR, "details"),
    # -- resolution --
    **_props("ResultatResolution", "ban", *_RESOLVED_BAN_FIELDS),
    **_props("ResultatResolution", "cadastre", "id_parcelle", "parcelles_candidates"),
    **_props("ResultatResolution", PAR_VALEUR, *_RESOLVED_BY_PATH),
    **_props("ResultatMultiResolution", "ban", *_RESOLVED_BAN_FIELDS),
    **_props(
        "ResultatMultiResolution", "cadastre", "id_parcelle", "parcelles_candidates"
    ),
    **_props("ResultatMultiResolution", PAR_VALEUR, *_RESOLVED_BY_PATH, "datasets"),
    # -- communes and zones: identities read in geo_communes --
    **_props("CommuneAnnuaireRow", REFERENTIEL, *_REFERENTIAL_IDENTITY),
    **_props("CommuneAnnuaireRow", PAR_VALEUR, *_SERVED_POPULATION),
    **_props("CommuneListRow", REFERENTIEL, *_REFERENTIAL_IDENTITY),
    **_props("CommuneListRow", PAR_VALEUR, *_SERVED_POPULATION),
    **_props("CommuneListRow", "ban", "code_postal"),
    **_props("ResultatCommuneSearch", REFERENTIEL, *_REFERENTIAL_IDENTITY),
    **_props("ResultatCommuneSearch", PAR_VALEUR, *_SERVED_POPULATION),
    **_props("ResultatCommuneSearch", "ban", "code_postal"),
    **_props("CommuneComparee", REFERENTIEL, *_REFERENTIAL_IDENTITY),
    **_props("CelluleComparaison", REFERENTIEL, "code_insee"),
    # Region names come from a static table of app.zone.router (DEPT_REGION).
    # The hosting of the prices, the indexing rule and the day of the last
    # import (dataset_metadata, etl_runs) are DataCommune's own (04.7-10).
    **_props(
        "ResultatDepartement",
        DATACOMMUNE,
        "region",
        "derniere_importation",
        "prix",
        "indexable",
    ),
    # -- departement page (04.7-10): identity read in geo_departements and
    # geo_communes; counts, medians, shares and the population total (a sum)
    # computed by DataCommune; the census vintage and the DPE dates picked in
    # the source rows.
    **_props("ResultatDepartementSynthese", REFERENTIEL, "code", "nom"),
    **_props("PopulationDepartement", "demographie", "annee"),
    **_props("DpeDepartement", "dpe", "date_min", "date_max"),
    **_props("CommunePrincipale", REFERENTIEL, "code_insee", "nom"),
    **_props("CommunePrincipale", PAR_VALEUR, *_SERVED_POPULATION),
    **_props("FichesDepartement", REFERENTIEL, "code"),
    **_props("FicheDepartement", REFERENTIEL, "code_insee"),
    **_props("ZoneResolveResponse", PAR_VALEUR, "results"),
    **_props("ZoneStats", "demographie", "population"),
    # -- shared territory refs and the properties holding them --
    # Departement code: cut from an INSEE or postal code, a request filter, or
    # read in geo_communes; its name is always read in geo_departements.
    **_props("DepartementRef", IDENTITE_TERRITORIALE, "code"),
    **_props("DepartementRef", REFERENTIEL, "nom"),
    **_props("TerritoireRecherche", REFERENTIEL, "code_insee", "nom"),
    **_props("TerritoireRecherche", IDENTITE_TERRITORIALE, "departement"),
    **_props("AdresseBanDocument", IDENTITE_TERRITORIALE, "commune"),
    **_props("ColonneAdresse", IDENTITE_TERRITORIALE, "commune", "departement"),
    **_props(
        "HebergementBanRecherche", IDENTITE_TERRITORIALE, "territoire", "homonymes"
    ),
    **_props("IdentiteAdresse", IDENTITE_TERRITORIALE, "commune", "departement"),
    **_props("ReferenceCommune", IDENTITE_TERRITORIALE, "territoire"),
    **_props("ResultatRechercheAdresse", IDENTITE_TERRITORIALE, "departement"),
    # -- read accounts shared by every proxied source: the total is the one
    # announced upstream, named by the map key or by meta.source.
    **_props("ProxySource", PAR_VALEUR, "total"),
    # -- report attachment: the retained key belongs to the rule's entity --
    **_props("Rattachement", PAR_VALEUR, "cle"),
}


def source_dictionary() -> dict[str, dict[str, str]]:
    """``info.x-sources``: the catalog attribution plus DataCommune's values."""
    dictionary = {
        key: {
            "nom": entry.nom,
            "producteur": entry.producteur,
            "licence": entry.licence,
            "url": entry.url,
        }
        for key, entry in DATASET_CATALOG.items()
    }
    # Only what scripts/import_geo_boundaries.py states: its download URLs.
    # No licence is recorded in the repository for these files.
    dictionary[REFERENTIEL] = {
        "nom": (
            "Référentiel des communes et des départements (tables geo_communes "
            "et geo_departements)"
        ),
        "producteur": (
            "geo.api.gouv.fr pour les communes et la liste des départements ; "
            "dépôt gregoiredavid/france-geojson pour les noms et contours des "
            "départements de métropole (import scripts/import_geo_boundaries.py)"
        ),
        "licence": "Non déclarée dans le dépôt (source hors du catalogue)",
        "url": "https://geo.api.gouv.fr",
        "description": (
            "Communes lues sur geo.api.gouv.fr/departements/{dept}/communes, "
            "liste des départements sur geo.api.gouv.fr/departements, "
            "départements de métropole sur raw.githubusercontent.com/"
            "gregoiredavid/france-geojson ; un département d'outre-mer prend "
            "le nom de la liste geo.api.gouv.fr."
        ),
    }
    dictionary[IDENTITE_TERRITORIALE] = {
        "nom": (
            "Code et nom officiels de la commune ou du département, lus selon l'objet"
        ),
        "producteur": (
            "Selon l'objet : BAN (DINUM / IGN) pour la commune de l'adresse d'un "
            "document DPE ; recensement (INSEE) par la synthèse communale pour le "
            "territoire d'une valeur de référence, à défaut le référentiel ; "
            "référentiel des communes et "
            "des départements (entrée referentiel-communes) pour l'identité d'une "
            "adresse et la recherche"
        ),
        "licence": (
            "Celle de la source lue : Licence Ouverte 2.0 pour la BAN et le "
            "recensement ; non déclarée dans le dépôt pour le référentiel"
        ),
        "url": _SITE_SOURCES_URL,
        "description": (
            "Schémas partagés par plusieurs routes. Le code de commune est celui "
            "de la clé ou de la ligne BAN. Le code de département est tiré par "
            "DataCommune du code INSEE ou du code postal, repris d'un filtre de "
            "la requête, ou lu dans le référentiel ; son nom est lu dans le "
            "référentiel."
        ),
    }
    dictionary[DATACOMMUNE] = {
        "nom": "DataCommune (valeur produite par DataCommune)",
        "producteur": "DataCommune",
        "licence": "Aucune licence propre déclarée",
        "url": _SITE_SOURCES_URL,
        "description": (
            "Protocole (enveloppe, regroupement, pagination, état, motif, "
            "verdict, rattachement, attribution, renvoi de la requête), "
            "identifiant ou table interne, ou calcul de DataCommune (compte, "
            "somme, médiane, taux, distance, centroïde, classement, "
            "comparaison). Sur une propriété qui regroupe un objet d'origines "
            "diverses, l'origine de chaque valeur est portée par les propriétés "
            "de cet objet."
        ),
    }
    dictionary[PAR_VALEUR] = {
        "nom": "Source servie avec la valeur",
        "producteur": "Celui de la source servie dans la réponse",
        "licence": "Celle de la source servie dans la réponse",
        "url": _SITE_SOURCES_URL,
        "description": (
            "La source varie d'un objet à l'autre et la réponse la sert : champ "
            "source, sources ou provenance de l'objet, de son thème ou de son "
            "bloc d'indicateurs, clé du dictionnaire qui le contient, "
            "meta.source, ou discriminant de l'objet (type_transport, "
            "type_detecte, population_source, entite du rattachement, cle du "
            "critère comparé). Une clé de source servie (cle, dataset) est une "
            "clé de ce dictionnaire ; population_source « geo_communes » "
            "désigne l'entrée referentiel-communes."
        ),
    }
    return dictionary


def _is_internal(path: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in INTERNAL_PATH_PREFIXES
    )


def _refs(node: Any) -> Iterator[str]:
    """Component schema names referenced anywhere under *node*."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            yield ref.rsplit("/", 1)[1]
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def public_schema_names(spec: Mapping[str, Any]) -> set[str]:
    """Schemas reached, through ``$ref``, from a response of a public route."""
    schemas = spec.get("components", {}).get("schemas", {})
    pending = [
        name
        for path, operations in spec.get("paths", {}).items()
        if not _is_internal(path)
        for operation in operations.values()
        if isinstance(operation, dict)
        for name in _refs(operation.get("responses", {}))
    ]
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reached or name not in schemas:
            continue
        reached.add(name)
        pending.extend(_refs(schemas[name]))
    return reached


def _response_models(routes: Iterable[BaseRoute]) -> Iterator[Any]:
    for route in routes:
        if not isinstance(route, APIRoute):
            continue
        yield route.response_model
        for response in (route.responses or {}).values():
            if isinstance(response, dict) and "model" in response:
                yield response["model"]


def _model_classes(annotation: Any, found: set[type]) -> None:
    """Collect the models and enums under *annotation* (generics, unions...)."""
    if isinstance(annotation, typing.TypeAliasType):
        _model_classes(annotation.__value__, found)
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel | enum.Enum):
        if annotation in found:
            return
        found.add(annotation)
        if issubclass(annotation, BaseModel):
            for field in annotation.model_fields.values():
                _model_classes(field.annotation, found)
        return
    for argument in typing.get_args(annotation):
        _model_classes(argument, found)


def _schema_names(cls: type) -> set[str]:
    """OpenAPI names Pydantic may give *cls* (generic, long and mode forms)."""
    names = set()
    for raw in (cls.__name__, f"{cls.__module__}.{cls.__qualname__}"):
        base = re.sub(r"[^a-zA-Z0-9.\-_]", "_", raw).replace(".", "__")
        names.update({base, f"{base}-Input", f"{base}-Output"})
    return names


def schema_modules(routes: Iterable[BaseRoute]) -> dict[str, str]:
    """OpenAPI schema name -> module of the model behind it, from the routes.

    A name two modules could produce is left out: no guess, the test names it.
    """
    classes: set[type] = set()
    for model in _response_models(routes):
        _model_classes(model, classes)
    candidates: dict[str, set[str]] = {}
    for cls in classes:
        for name in _schema_names(cls):
            candidates.setdefault(name, set()).add(cls.__module__)
    return {
        name: next(iter(modules))
        for name, modules in candidates.items()
        if len(modules) == 1
    }


def _module_source(module: str | None, module_sources: Mapping[str, str]) -> str | None:
    if module is None:
        return None
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        source = module_sources.get(".".join(parts[:end]))
        if source is not None:
            return source
    return None


def annotate_sources(
    spec: dict[str, Any],
    routes: Iterable[BaseRoute],
    *,
    module_sources: Mapping[str, str] = MODULE_SOURCES,
    schema_sources: Mapping[str, str] = SCHEMA_SOURCES,
    property_sources: Mapping[str, str] = PROPERTY_SOURCES,
) -> None:
    """Write ``x-source`` on public response properties and ``info.x-sources``."""
    schemas = spec.get("components", {}).get("schemas", {})
    modules = schema_modules(routes)
    for name in sorted(public_schema_names(spec)):
        default = schema_sources.get(name) or _module_source(
            modules.get(name), module_sources
        )
        for prop, prop_schema in schemas[name].get("properties", {}).items():
            source = property_sources.get(f"{name}.{prop}", default)
            if source is not None:
                prop_schema["x-source"] = source
    spec["info"]["x-sources"] = source_dictionary()


def install_openapi_sources(app: FastAPI) -> None:
    """Annotate the schema once, when FastAPI first generates it, then cache."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        spec = app.openapi_schema
        if spec is None:
            spec = generate()
            annotate_sources(spec, app.routes)
            app.openapi_schema = spec
        return spec

    app.openapi_schema = None
    app.openapi = openapi  # type: ignore[method-assign]

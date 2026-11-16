"""Canonical source catalog — single static authority for provenance.

This module is the one place where a dataset's identity, attribution,
static coverage policy and official publication cadence are declared
(D-01, D-03). Legacy helpers in :mod:`app.common.sources` are derived from
:data:`DATASET_CATALOG`, never duplicated.

The resolver uses aliases and aggregates (``risques`` -> ``georisques``,
``commune`` -> thirteen sources). :data:`RESOLVER_SOURCE_MAP` and
:func:`expand_source_keys` make that expansion explicit and deterministic:
no implicit string matching, an error on unknown aliases.

The module is fully static: no DB, no network, no router import. Dynamic
facts (last successful import, effective coverage, editorial state) are
assembled elsewhere from ``dataset_metadata``/``etl_runs``.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.common.schemas import PublicationCadence

# Irregular / unpublished cadence: no invented interval or uniform threshold.
_IRREGULAR = PublicationCadence(statut="non_publiee_ou_irreguliere")


@dataclass(frozen=True)
class SourceCoverage:
    """Departements the producer publishes (D-24 ``source_sans_couverture``).

    The only territorial authority per source in the repository: the import
    guard, the departements a pipeline expects in staging and the reason
    served by the API all read it.

    departements_inclus: ``None`` for a national source, else the only
        departements the source holds (a local resource).
    departements_exclus: departements the producer states it does not publish.
    """

    departements_inclus: tuple[str, ...] | None = None
    departements_exclus: tuple[str, ...] = ()

    def covers(self, departement: str) -> bool:
        if departement in self.departements_exclus:
            return False
        return (
            self.departements_inclus is None or departement in self.departements_inclus
        )


@dataclass(frozen=True)
class SourceLimits:
    """Limits of the source itself, as published on « Données & méthode ».

    What the source covers (territory, market, publication cadence, field
    definitions) — never what a reuser may do with it, which stays in
    ``restrictions``. Arbitrated copy: rendered word for word, never reworded.
    """

    chapeau: str
    puces: tuple[str, ...]


@dataclass(frozen=True)
class DatasetCatalogEntry:
    """Immutable canonical entry: attribution + static coverage + cadence.

    coverage_policy:
        "instance"  — locally ingested, scoped to the demo instance (44/85)
        "national"  — locally ingested with national reach
        "upstream"  — proxied API, coverage follows the upstream provider
    """

    dataset: str
    nom: str
    producteur: str
    licence: str
    url: str
    coverage_policy: str
    cadence: PublicationCadence
    # Licence terms as read on the producer's own pages (LIC-01). Every value
    # is copied from the source, never inferred: an unreadable licence is
    # written "non specifiee par le producteur" with the page consulted.
    licence_url: str
    conditions_redistribution: str
    restrictions: tuple[str, ...]
    licence_verifiee_le: str
    # Departements the source publishes; national unless stated.
    source_coverage: SourceCoverage = SourceCoverage()
    # Published reason of a public quarantine (D-40): the dataset is served
    # ``quarantined`` with this reason whatever its metadata row says, and is
    # never presented as reliable. Arbitrated copy, recopied word for word.
    quarantaine: str | None = None
    # Limits of the source served with the dataset (arbitrated copy, D-20).
    limites_source: SourceLimits | None = None


def source_covers(entry: DatasetCatalogEntry, departement: str) -> bool:
    """True unless the source is known to hold no row for *departement*."""
    return entry.source_coverage.covers(departement)


def covered_departements(keys: Iterable[str], departements: Iterable[str]) -> list[str]:
    """Departements of *departements* published by at least one source *keys*.

    Order of *departements* is kept. Empty: nothing to import or serve there.
    """
    entries = [DATASET_CATALOG[key] for key in keys]
    return [
        dept
        for dept in departements
        if any(source_covers(entry, dept) for entry in entries)
    ]


# Licence texts as published (consulted 2026-09-21). Versions 1.0 and 2.0 of
# the Licence Ouverte (Etalab) state the same attribution condition.
_LO2_URL = "https://www.data.gouv.fr/pages/legal/licences/etalab-2.0"
_LO1_URL = "https://www.data.gouv.fr/pages/legal/licences/etalab-1.0"
_ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
_LO_CONDITIONS = (
    "Réutilisation libre, y compris commerciale, sous réserve de mentionner "
    "la paternité de l'information : sa source (a minima le nom du "
    "producteur) et la date de sa dernière mise à jour ; la mention ne doit "
    "ni conférer un caractère officiel à la réutilisation ni suggérer une "
    "caution du producteur."
)
_ODBL_CONDITIONS = (
    "Réutilisation libre sous ODbL 1.0 : attribuer toute utilisation publique "
    "de la base ou des œuvres produites à partir d'elle et indiquer sa "
    "licence ; partage à l'identique : toute base adaptée utilisée "
    "publiquement doit être offerte sous ODbL ; en cas de redistribution avec "
    "des mesures techniques restrictives, une version sans ces mesures doit "
    "aussi être redistribuée."
)
# INSEE legal notice (https://www.insee.fr/fr/information/2008466).
_INSEE_CONDITIONS = (
    "Licence Ouverte 2.0 (mentions légales INSEE) : réutilisation libre, y "
    "compris commerciale, sous réserve de mentionner la source sous la forme "
    "« Source : Insee », la date de dernière mise à jour lorsqu'elle "
    "est connue, et de ne pas altérer le sens des informations. Licence "
    "relevée sur : https://www.insee.fr/fr/information/2008466"
)
# DGFiP dataset page + CGU of 2020-10-16, set by article R*112 A-3 LPF.
_DVF_RESTRICTIONS = (
    "La réutilisation ne peut avoir ni pour objet ni pour effet de "
    "permettre la réidentification des personnes concernées "
    "(article R*112 A-3 du Livre des procédures fiscales).",
    "Les informations ne peuvent faire l'objet d'une indexation sur les "
    "moteurs de recherche en ligne : le réutilisateur met en place des "
    "mesures empêchant l'indexation des données identifiantes par des "
    "moteurs de recherche externes (robots.txt, captcha).",
    "La réutilisation n'est autorisée que pour concourir à la "
    "transparence des marchés fonciers et immobiliers, et doit être "
    "conforme à la législation sur la protection des données (RGPD, "
    "loi Informatique et libertés).",
)
# Territorial exclusions are a coverage fact, not a reuse restriction: they are
# declared in ``source_coverage`` (codes, read by the import guard) and stated
# once to the visitor in the arbitrated limits below (dossier 57-COPIE-DVF,
# family 5, decided on 2026-08-25), never a second time in the restrictions.
_DVF_LIMITES = SourceLimits(
    chapeau=(
        "Le fichier des demandes de valeurs foncières (DVF) est publié par la "
        "direction générale des finances publiques. Il ne couvre pas tout le "
        "territoire ni tout le marché ; ces limites sont celles de la source, et "
        "elles ne sont pas comblées."
    ),
    puces=(
        "Bas-Rhin, Haut-Rhin et Moselle ne sont pas couverts : ces départements "
        "relèvent du livre foncier, un régime de publicité foncière distinct, et "
        "leurs transactions ne figurent pas dans DVF.",
        "Mayotte n'est pas couverte.",
        "Les ventes en l'état futur d'achèvement (VEFA) sont présentes dans DVF, "
        "mais certaines ne comportent pas de descriptif du local ou de surface "
        "exploitable. DataCommune les conserve dans l'historique, les étiquette "
        "et les exclut de ses statistiques de prix afin de ne pas mélanger neuf "
        "et ancien.",
        "DVF est actualisé deux fois par an, en avril et en octobre. Une "
        "transaction récente ou enregistrée tardivement peut ne pas encore "
        "apparaître, et des millésimes antérieurs peuvent être complétés lors "
        "d'une mise à jour.",
        "Les surfaces et le nombre de pièces proviennent d'informations fiscales "
        "et cadastrales ; leur définition peut différer de celle utilisée dans un "
        "diagnostic énergétique.",
    ),
)
_GEORISQUES_RESTRICTIONS = (
    "Les données et informations dont l'État ne détient pas la propriété "
    "sont signalées et doivent faire l'objet d'une demande d'autorisation "
    "auprès de leur propriétaire (mentions légales Géorisques).",
)
_SIRENE_RESTRICTIONS = (
    "La base Sirene contient des données à caractère personnel (RGPD, loi "
    "Informatique et libertés) : le réutilisateur tient compte du statut de "
    "diffusion le plus récent de chaque personne physique, qui intègre les "
    "oppositions à l'utilisation de leurs données par des tiers.",
    "Les unités à diffusion partielle (statut « P ») ont leur "
    "identité, leur adresse dans la commune et leur géolocalisation masquées ; "
    "les entreprises non diffusibles ne sont pas accessibles dans l'API.",
)


# ---------------------------------------------------------------------------
# DATASET_CATALOG — canonical identity for every source key.
# Only BAN and DPE declare an ``official`` cadence: their weekly publication
# is documented by IGN/ADEME (see 44-RESEARCH). Every other cadence stays
# honestly ``non_publiee_ou_irreguliere`` until a public proof exists.
# ---------------------------------------------------------------------------

DATASET_CATALOG: dict[str, DatasetCatalogEntry] = {
    # -- ETL datasets (local storage) --
    "ban": DatasetCatalogEntry(
        dataset="ban",
        nom="Base Adresse Nationale (BAN)",
        producteur="DINUM / IGN",
        licence="Licence Ouverte 2.0",
        url="https://adresse.data.gouv.fr",
        coverage_policy="instance",
        cadence=PublicationCadence(
            statut="official",
            label="Hebdomadaire",
            evidence_url=(
                "https://cartes.gouv.fr/aide/fr/guides-utilisateur/"
                "utiliser-les-services-de-la-geoplateforme/geocodage/"
            ),
            intervalle_jours=7,
        ),
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Licence relevée sur : "
            "https://www.data.gouv.fr/datasets/base-adresse-nationale"
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "cadastre": DatasetCatalogEntry(
        dataset="cadastre",
        nom="Cadastre Etalab",
        producteur="DGFiP / Etalab",
        licence="Licence Ouverte 1.0",
        url="https://cadastre.data.gouv.fr",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO1_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " La fiche data.gouv.fr du jeu Etalab déclare la licence fr-lo, que le "
            "registre des licences de data.gouv.fr associe au document "
            "Licence_Ouverte.pdf de 2014, soit la Licence Ouverte version 1.0 ; le "
            "Plan Cadastral Informatisé dont il dérive est publié sous Licence "
            "Ouverte 2.0."
        )
        + " Licence relevée sur : https://www.data.gouv.fr/datasets/cadastre",
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "dvf": DatasetCatalogEntry(
        dataset="dvf",
        nom="Demandes de valeurs foncières (DVF)",
        producteur="DGFiP / Etalab",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        # Both the DGFiP file and its geolocated Etalab derivative are
        # published under lov2; the reuse limits are the DGFiP's own.
        conditions_redistribution=(
            _LO_CONDITIONS + " Conditions générales d'utilisation DGFiP : "
            "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/"
            "20201026-172435/conditions-generales-dutilisation-20201016.pdf"
        ),
        restrictions=_DVF_RESTRICTIONS,
        licence_verifiee_le="2026-09-21",
        # Territorial exclusions stated by the producer (see _DVF_LIMITES).
        source_coverage=SourceCoverage(departements_exclus=("57", "67", "68", "976")),
        limites_source=_DVF_LIMITES,
    ),
    "dpe": DatasetCatalogEntry(
        dataset="dpe",
        nom="Diagnostics de performance énergétique (DPE)",
        producteur="ADEME",
        licence="Licence Ouverte 2.0",
        url="https://data.ademe.fr/datasets/dpe03existant",
        coverage_policy="instance",
        cadence=PublicationCadence(
            statut="official",
            label="Hebdomadaire",
            evidence_url="https://data.ademe.fr/datasets/dpe03existant",
            intervalle_jours=7,
        ),
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "loyers": DatasetCatalogEntry(
        dataset="loyers",
        nom="Carte des loyers",
        producteur="ANIL / MTE",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Mention imposée par le producteur : « Estimations ANIL, à partir des "
            "données du Groupe SeLoger et de leboncoin »."
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # The 2025 rent map files (four housing types) publish neither
        # Saint-Pierre-et-Miquelon nor Mayotte (measured in plan 02-06).
        source_coverage=SourceCoverage(departements_exclus=("975", "976")),
    ),
    "batiments": DatasetCatalogEntry(
        dataset="batiments",
        # The EN DASH of the published name is written as an escape (ruff RUF001).
        nom="BD TOPO \u2013 Bâtiments",
        producteur="IGN",
        licence="Licence Ouverte 2.0",
        url="https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Notice de métadonnées IGN IGNF_BD-TOPO (catalogue data.geopf.fr) : "
            "Licence Ouverte / Open Licence 2.0."
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "monuments": DatasetCatalogEntry(
        dataset="monuments",
        nom="Monuments historiques",
        producteur="Ministère de la Culture",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "connectivite": DatasetCatalogEntry(
        dataset="connectivite",
        nom="Ma Connexion Internet (Arcep)",
        producteur="Arcep",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/ma-connexion-internet",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "scolaire": DatasetCatalogEntry(
        dataset="scolaire",
        nom="Carte scolaire des collèges",
        producteur="DGESCO / MEN",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/carte-scolaire-des-colleges-publics",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # Measured on the national file of 2026-10-29 (archived run 70,
        # 499 680 rows): no row for the academie of Toulouse but 65 (09, 12,
        # 31, 32, 46, 81, 82), Charente-Maritime, 971, 972, 976. 22, 65 and
        # 2A hold only 5, 1 and 1 border rows, communes sectorised to a
        # college of the neighbouring departement (29, 64, 2B): the producer
        # does not publish their own sectorisation.
        source_coverage=SourceCoverage(
            departements_exclus=(
                "09",
                "12",
                "17",
                "22",
                "2A",
                "31",
                "32",
                "46",
                "65",
                "81",
                "82",
                "971",
                "972",
                "976",
            )
        ),
    ),
    "transports": DatasetCatalogEntry(
        dataset="transports",
        nom="Arrêts de transport",
        producteur="PAN transport",
        licence="Licence Ouverte 2.0",
        url="https://transport.data.gouv.fr/datasets/arrets-de-transport-en-france",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # Measured on the national file of 2026-10-29 (archived run 64,
        # 638 000 stops): none in Mayotte; 971-974 are published.
        source_coverage=SourceCoverage(departements_exclus=("976",)),
    ),
    "gares": DatasetCatalogEntry(
        dataset="gares",
        nom="Gares SNCF",
        producteur="SNCF",
        licence="ODbL 1.0",
        url="https://ressources.data.sncf.com/explore/dataset/liste-des-gares/",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_ODBL_URL,
        conditions_redistribution=_ODBL_CONDITIONS
        + (
            " Licence relevée sur : "
            "https://ressources.data.sncf.com/explore/dataset/liste-des-gares/ (FAQ "
            "ODbL SNCF : https://data.sncf.com/pages/licence/)."
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # SNCF stations are on the mainland network only: 0 of the 3 884
        # stations of the file of 2026-10-29 lie outside metropolitan France,
        # none in Corsica (its railway is not SNCF's).
        source_coverage=SourceCoverage(
            departements_exclus=("2A", "2B", "971", "972", "973", "974", "976")
        ),
    ),
    "encadrement_loyers": DatasetCatalogEntry(
        dataset="encadrement_loyers",
        nom="Encadrement des loyers",
        producteur="DRIHL",
        licence="ODbL 1.0",
        url="https://www.data.gouv.fr/datasets/logement-encadrement-des-loyers",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_ODBL_URL,
        conditions_redistribution=_ODBL_CONDITIONS
        + " Jeu publié sur data.gouv.fr par la Ville de Paris.",
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # Surface refused with HTTP 503 (COR-09/COR-13).
        quarantaine=(
            "Service indisponible. Cette indisponibilité ne signifie pas absence "
            "d'encadrement des loyers. Réponse HTTP 503, y compris avec une "
            "sélection manuelle de quartier. La proximité du centroïde ne démontre "
            "pas le périmètre réglementaire applicable à une adresse. La "
            "publication reprendra après qualification des périmètres, des "
            "catégories de logement et du millésime de référence."
        ),
    ),
    "foncier_pm": DatasetCatalogEntry(
        dataset="foncier_pm",
        nom="Fichiers des personnes morales (MAJIC)",
        producteur="DGFiP / Etalab",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/fichiers-des-locaux-et-des-parcelles-des-personnes-morales",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "fiscalite": DatasetCatalogEntry(
        dataset="fiscalite",
        nom="Fiscalité locale des particuliers",
        producteur="DGFiP",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "zonage_abc": DatasetCatalogEntry(
        dataset="zonage_abc",
        nom="Zonage ABC (tension immobilière)",
        producteur="Ministère de la Transition écologique",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "revenus": DatasetCatalogEntry(
        dataset="revenus",
        nom="Filosofi (revenus et pauvreté)",
        producteur="INSEE",
        licence="Licence Ouverte 2.0",
        url="https://www.insee.fr/fr/statistiques/7756855",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_INSEE_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # Measured on FILO2021_DEC_COM of 2026-10-29 (archived run 73, 34 929
        # communes, CODGEO): overseas, only 972 (34) and 974 (24) are
        # published; no commune of 971, 973 nor 976.
        source_coverage=SourceCoverage(departements_exclus=("971", "973", "976")),
    ),
    "demographie": DatasetCatalogEntry(
        dataset="demographie",
        nom="Recensement de la population",
        producteur="INSEE",
        licence="Licence Ouverte 2.0",
        url="https://www.insee.fr/fr/statistiques/8290591",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_INSEE_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # No commune of Mayotte in any of the three INSEE files (legal
        # populations ensemble.zip, base-cc logement and evol-struct-pop 2022,
        # read on 2026-10-30) nor in the merged file of run 74 (34 960 rows);
        # 971 to 974 are published.
        source_coverage=SourceCoverage(departements_exclus=("976",)),
    ),
    "copropriete": DatasetCatalogEntry(
        dataset="copropriete",
        nom="Registre national des copropriétés (RNIC)",
        producteur="ANAH",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/registre-national-dimmatriculation-des-coproprietes",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "permis": DatasetCatalogEntry(
        dataset="permis",
        nom="Permis de construire (source locale en quarantaine)",
        producteur="Attribution a requalifier",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/base-des-permis-de-construire-et-autres-autorisations-durbanisme",
        coverage_policy="unknown",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Jeu publié par l'EPT Plaine Commune (fichier local, source en "
            "quarantaine)."
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # Local resource of Seine-Saint-Denis only (KI-16).
        source_coverage=SourceCoverage(departements_inclus=("93",)),
        # Partial local resource, route refused with HTTP 503 (COR-07). The
        # apostrophe (U+2019) is the typographic one of the arbitrated copy,
        # kept as published (escaped for RUF001).
        quarantaine=(
            "Source en quarantaine : ressource locale observée uniquement dans le "
            "département 93, couverture non qualifiée. Une liste vide ne signifie "
            "pas absence de permis. La date de dépôt n\u2019est pas fournie ; la date "
            "de commencement ne la remplace pas."
        ),
    ),
    "antennes": DatasetCatalogEntry(
        dataset="antennes",
        nom="Antennes relais (ANFR)",
        producteur="ANFR",
        licence="Licence Ouverte 2.0",
        url="https://data.anfr.fr/explore/dataset/observatoire_2g_3g_4g",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "equipements": DatasetCatalogEntry(
        dataset="equipements",
        nom="Base permanente des équipements (BPE)",
        producteur="INSEE",
        licence="Licence Ouverte 2.0",
        url="https://www.insee.fr/fr/statistiques/8217537",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_INSEE_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "vacance": DatasetCatalogEntry(
        dataset="vacance",
        nom="Logements vacants (LOVAC)",
        producteur="CEREMA",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/logements-vacants-du-parc-prive-par-commune-departement-region-france-de-2020-a-2026",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS,
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # LOVAC communes26 publishes no commune of Saint-Pierre-et-Miquelon
        # (34 912 communes, 101 departements; measured in plan 02-06).
        source_coverage=SourceCoverage(departements_exclus=("975",)),
    ),
    # -- Proxied APIs (real-time, coverage follows upstream) --
    "bdnb": DatasetCatalogEntry(
        dataset="bdnb",
        nom="Base de données nationale des bâtiments (BDNB)",
        producteur="CSTB",
        licence="Licence Ouverte 2.0",
        url="https://bdnb.io",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Citation demandée par le CSTB (bdnb.io/faq) : « Base de Données "
            "Nationale des Bâtiments (BDNB) - Millésime <code> - CSTB ». Les "
            "conditions propres au service API ne sont pas publiées sur cette fiche."
        )
        + (
            " Licence relevée sur : "
            "https://www.data.gouv.fr/datasets/base-de-donnees-nationale-des-batiments"
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
        # The CSTB declares metropolitan France only (data.gouv.fr dataset,
        # ``spatial.zones = country-subset:fr:metro``, read 2026-11-02), and
        # the 2026-02-b departemental exports of 971-976 answer 403 (plan
        # 04.7-19): out of coverage with its reason, never a zero. Corsica is
        # published (dep2a, dep2b).
        source_coverage=SourceCoverage(
            departements_exclus=("971", "972", "973", "974", "975", "976")
        ),
    ),
    "errial": DatasetCatalogEntry(
        dataset="errial",
        nom="ERRIAL (état des risques réglementaires)",
        producteur="BRGM / MTE",
        licence="Licence Ouverte 2.0",
        url="https://errial.georisques.gouv.fr",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + " ERRIAL renvoie aux mentions légales de Géorisques."
        + " Licence relevée sur : https://www.georisques.gouv.fr/mentions-legales",
        restrictions=_GEORISQUES_RESTRICTIONS,
        licence_verifiee_le="2026-09-21",
    ),
    "georisques": DatasetCatalogEntry(
        dataset="georisques",
        nom="Géorisques",
        producteur="BRGM / MTE",
        licence="Licence Ouverte 2.0",
        url="https://www.georisques.gouv.fr",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + " Licence relevée sur : https://www.georisques.gouv.fr/mentions-legales",
        restrictions=_GEORISQUES_RESTRICTIONS,
        licence_verifiee_le="2026-09-21",
    ),
    "rnb": DatasetCatalogEntry(
        dataset="rnb",
        nom="Référentiel national des bâtiments (RNB)",
        producteur="beta.gouv.fr / CSTB",
        licence="Licence Ouverte 2.0",
        url="https://rnb.beta.gouv.fr",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Licence relevée sur : "
            "https://www.data.gouv.fr/datasets/referentiel-national-des-batiments"
        ),
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    "sirene": DatasetCatalogEntry(
        dataset="sirene",
        nom="API Sirene (Recherche Entreprises)",
        producteur="INSEE / Etalab",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/dataservices/api-recherche-dentreprises",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Licence de la base Sirene (INSEE) ; la fiche de l'API Recherche "
            "d'entreprises (DINUM) ne déclare pas de licence propre."
        )
        + (
            " Licence relevée sur : "
            "https://www.data.gouv.fr/datasets/base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret"
        ),
        restrictions=_SIRENE_RESTRICTIONS,
        licence_verifiee_le="2026-09-21",
    ),
    "urbanisme": DatasetCatalogEntry(
        dataset="urbanisme",
        nom="API Carto GPU",
        producteur="IGN",
        licence="Licence Ouverte 1.0",
        url="https://apicarto.ign.fr",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO1_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " API Carto conserve la licence des sources ; les CGU du Géoportail de "
            "l'urbanisme soumettent les données d'urbanisme à la licence ouverte "
            "consultable à l'adresse etalab.gouv.fr/wp-content/uploads/2014/05/"
            "Licence_Ouverte.pdf, document de 2014, soit la Licence Ouverte "
            "version 1.0 (la version n'est pas écrite dans les CGU)."
        )
        + " Licence relevée sur : https://www.geoportail-urbanisme.gouv.fr/cgu/",
        restrictions=(),
        licence_verifiee_le="2026-09-21",
    ),
    # -- 04.7-04: terrain of the address report, read live --
    "altimetrie": DatasetCatalogEntry(
        dataset="altimetrie",
        nom="RGE ALTI® (calcul altimétrique de la Géoplateforme)",
        producteur="IGN",
        licence="Licence Ouverte (version non précisée par la notice)",
        url=(
            "https://cartes.gouv.fr/aide/fr/guides-utilisateur/"
            "utiliser-les-services-de-la-geoplateforme/calcul-altimetrique/"
        ),
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=(
            "https://data.geopf.fr/csw?service=CSW&version=2.0.2"
            "&request=GetRecordById&id=IGNF_RGE-ALTI"
        ),
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Notice de métadonnées IGNF_RGE-ALTI (catalogue data.geopf.fr) : "
            "« Licence Ouverte / Open License (compatible ODC-BY, CC-BY 2.0) » et "
            "« Pas de restriction d'accès public selon INSPIRE ». La version de "
            "la licence n'est pas écrite (la compatibilité CC-BY 2.0 est la "
            "formule de la version 1.0, l'IGN diffuse en version 2.0 depuis "
            "2021) : aucune version n'est affirmée ici. Le service est limité à "
            "5 requêtes par seconde depuis une même adresse IP."
        )
        + (
            " Licence relevée sur : https://data.geopf.fr/csw (GetRecordById "
            "IGNF_RGE-ALTI) et sur la page du service."
        ),
        restrictions=(),
        licence_verifiee_le="2026-10-30",
    ),
    "geologie": DatasetCatalogEntry(
        dataset="geologie",
        nom="Carte lithologique simplifiée au 1/1 000 000 (géoservice BRGM)",
        producteur="BRGM",
        licence="Sans condition déclarée par le géoservice",
        url="https://geoservices.brgm.fr/geologie",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=(
            "https://geoservices.brgm.fr/geologie?SERVICE=WMS&VERSION=1.3.0"
            "&REQUEST=GetCapabilities"
        ),
        conditions_redistribution=(
            "Le géoservice du BRGM déclare « AccessConstraints : None » et "
            "« Fees : no conditions apply », avec l'attribution « BRGM » sur la "
            "couche LITHO_1M_SIMPLIFIEE (GetCapabilities relu le 2026-10-30). "
            "Mention : « BRGM, carte lithologique simplifiée au 1/1 000 000 ». La "
            "fiche de métadonnées liée par le service et la page des conditions "
            "d'utilisation d'InfoTerre répondent 404 le 2026-10-30 : aucune "
            "licence nommée n'a pu y être relue."
        ),
        restrictions=(
            "Carte au millionième : la roche dominante d'un secteur, jamais une "
            "observation à l'adresse ; ne pas la présenter à une échelle plus "
            "fine que celle de la carte.",
        ),
        licence_verifiee_le="2026-10-30",
    ),
    # -- 04.7-05: noise of the address report, read live --
    "axes_transport": DatasetCatalogEntry(
        dataset="axes_transport",
        # The EN DASH of the published name is written as an escape (ruff RUF001).
        nom="BD TOPO \u2013 Réseaux routier et ferré (WFS de la Géoplateforme)",
        producteur="IGN",
        licence="Licence Ouverte 2.0",
        url="https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_BD-TOPO",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Notice de métadonnées IGN IGNF_BD-TOPO (catalogue data.geopf.fr, "
            "relue le 2026-10-30) : « Licence Ouverte / Open License », liée au "
            "texte etalab.gouv.fr/wp-content/uploads/2018/11/open-licence.pdf "
            "(version 2.0). Tronçons de route d'importance 1 et 2 et voies "
            "ferrées principales, lus au point par le WFS de la Géoplateforme."
        ),
        restrictions=(
            "Distance à l'axe de la voie, jamais le classement sonore de l'axe, "
            "que seul l'arrêté préfectoral fixe.",
        ),
        licence_verifiee_le="2026-10-30",
    ),
    "peb": DatasetCatalogEntry(
        dataset="peb",
        nom="Plans d'exposition au bruit et de gêne sonore des aérodromes",
        producteur="DGAC",
        licence="Licence non déclarée par le producteur",
        url=(
            "https://cartes.gouv.fr/rechercher-une-donnee/dataset/"
            "DGAC_Plan_d_Exposition_au_Bruit_PEB"
        ),
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=(
            "https://data.geopf.fr/csw?REQUEST=GetRecordById&SERVICE=CSW"
            "&VERSION=2.0.2&elementSetName=full"
            "&ID=DGAC_Plan_d_Exposition_au_Bruit_PEB"
        ),
        conditions_redistribution=(
            "Aucune licence déclarée : la notice DGAC / SNIA de la Géoplateforme "
            "ne porte aucune contrainte, la fiche cartes.gouv.fr non plus, et "
            "data.gouv.fr écrit « License Not Specified » (relus le 2026-10-30). "
            "Mention : « DGAC / SNIA, plan d'exposition au bruit », avec la date "
            "de l'arrêté de chaque zone."
        ),
        restrictions=(
            "Zone du plan approuvé par arrêté, à la date de cet arrêté ; un plan "
            "en révision n'est pas encore servi.",
        ),
        licence_verifiee_le="2026-10-30",
    ),
    # -- 04.7-06: living here, read live --
    "delinquance": DatasetCatalogEntry(
        dataset="delinquance",
        nom=(
            "Bases statistiques communale et départementale de la délinquance "
            "enregistrée par la police et la gendarmerie nationales (SSMSI)"
        ),
        producteur="SSMSI (Ministère de l'Intérieur)",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/621df2954fa5a3b5a023e23c/",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Licence « lov2 » de la fiche data.gouv.fr du jeu (relue le "
            "2026-10-30). Lue par l'API tabulaire de data.gouv.fr. Sous le seuil "
            "de diffusion du SSMSI, aucune valeur n'est publiée ; l'estimation "
            "complémentaire du fichier n'est jamais servie."
        ),
        restrictions=(
            "Chiffres de la commune (de l'arrondissement à Paris, Lyon et "
            "Marseille), jamais de l'adresse ; délinquance enregistrée, non "
            "subie.",
        ),
        licence_verifiee_le="2026-10-30",
    ),
    "eau_potable": DatasetCatalogEntry(
        dataset="eau_potable",
        nom="Qualité de l'eau potable (API Hub'Eau, contrôle sanitaire des ARS)",
        producteur="Ministère chargé de la santé (ARS), diffusé par Hub'Eau",
        licence="Licence Ouverte (version non précisée par les CGU Hub'Eau)",
        url="https://hubeau.eaufrance.fr/page/api-qualite-eau-potable",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url="https://hubeau.eaufrance.fr/page/conditions-generales",
        conditions_redistribution=(
            "CGU de Hub'Eau, § 5.1.3 (relues le 2026-10-30) : « La réutilisation "
            "des Jeux de données est régie par la licence ouverte Etalab », sans "
            "numéro de version, et l'utilisateur doit citer l'auteur des jeux de "
            "données. Mention : « Ministère chargé de la santé, contrôle "
            "sanitaire des eaux (ARS), via Hub'Eau »."
        ),
        restrictions=(
            "Résultats par commune et par unité de distribution ; l'unité qui "
            "dessert l'adresse n'est pas publiée.",
        ),
        licence_verifiee_le="2026-10-30",
    ),
    "annuaire_education": DatasetCatalogEntry(
        dataset="annuaire_education",
        nom=(
            "Annuaire de l'éducation, indices de position sociale et "
            "indicateurs de valeur ajoutée"
        ),
        producteur="Ministère de l'Éducation nationale (DEPP)",
        licence="Licence Ouverte 2.0",
        url="https://data.education.gouv.fr/explore/dataset/fr-en-annuaire-education/",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " « Licence Ouverte v2.0 (Etalab) » dans les métadonnées des jeux "
            "fr-en-annuaire-education, fr-en-ips-ecoles-ap2022, "
            "fr-en-indicateurs-valeur-ajoutee-colleges et "
            "fr-en-indicateurs-de-resultat-des-lycees-gt_v2 (relues le "
            "2026-10-30), fr-en-ips-colleges-ap2023 et fr-en-ips-lycees-ap2023 "
            "(relues le 2026-10-31)."
        ),
        restrictions=(
            "Un IPS décrit le public d'un établissement, jamais sa qualité.",
        ),
        licence_verifiee_le="2026-10-31",
    ),
    # -- 04.7-07: living here part 2, read live --
    "qualite_air": DatasetCatalogEntry(
        dataset="qualite_air",
        nom="Indice ATMO de la qualité de l'air (prévision du jour)",
        producteur="Atmo France et les AASQA",
        licence="ODbL 1.0",
        url=(
            "https://www.data.gouv.fr/datasets/"
            "indice-de-la-qualite-de-lair-quotidien-par-commune-indice-atmo"
        ),
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_ODBL_URL,
        conditions_redistribution=_ODBL_CONDITIONS
        + (
            " « Licence : ODbL 1.0 » et source à citer « Atmo France et les "
            "Associations agréées de surveillance de la qualité de l'air » "
            "(notice Atmo Data du 2 octobre 2024, relue le 2026-10-31). Lu par "
            "le WFS public data.atmo-france.org (couche ind_atmo_2021), sans "
            "compte. L'intercommunalité d'une commune est lue sur "
            "geo.api.gouv.fr (DGCL et Insee, Licence Ouverte selon le dépôt "
            "decoupage-administratif)."
        ),
        restrictions=(
            "Prévision d'un jour pour la commune ou l'intercommunalité publiée "
            "par l'AASQA, jamais une exposition à l'adresse.",
        ),
        licence_verifiee_le="2026-10-31",
    ),
    "filosofi_carreaux": DatasetCatalogEntry(
        dataset="filosofi_carreaux",
        nom=(
            "Filosofi 2015, données carroyées à 200 m (revenus, pauvreté et "
            "niveau de vie)"
        ),
        producteur="INSEE (diffusion Géoplateforme IGN)",
        licence="Licence Ouverte 2.0",
        url="https://www.insee.fr/fr/statistiques/4176290",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_INSEE_CONDITIONS
        + (
            " Lu par le WFS de la Géoplateforme (couche "
            "INSEE.FILOSOFI.INDICATORS:carreaux_200m, fiche INSEE_DONNEES : "
            "licence ouverte Etalab, relue le 2026-10-31). La couche sert le "
            "millésime 2015 : trois carreaux comparés le 2026-10-31 aux "
            "fichiers Insee 2015, 2017, 2019 et 2021 sont égaux à 2015 seul."
        ),
        restrictions=(
            "Niveau de vie moyen des habitants en 2015 du carreau de 200 m qui "
            "contient l'adresse, jamais celui d'un logement ; un carreau imputé "
            "n'est pas servi.",
        ),
        licence_verifiee_le="2026-10-31",
    ),
    "iris": DatasetCatalogEntry(
        dataset="iris",
        nom=(
            "Contours IRIS et recensement de la population à l'IRIS "
            "(catégories socioprofessionnelles)"
        ),
        producteur="INSEE et IGN",
        licence="Licence Ouverte 2.0",
        url="https://api.insee.fr/melodi/",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_INSEE_CONDITIONS
        + (
            " Contours lus par le WFS de la Géoplateforme (couche "
            "STATISTICALUNITS.IRIS:contours_iris, édition 2026-01-01, "
            "coédition Insee et IGN sous Licence Ouverte) ; population lue par "
            "l'API Melodi (DS_RP_POPULATION_COMP_IRIS, recensement 2023, "
            "« Licence Ouverte » au catalogue Insee). Relu le 2026-10-31."
        ),
        restrictions=(
            "Composition du quartier (IRIS), jamais des occupants de l'adresse.",
        ),
        licence_verifiee_le="2026-10-31",
    ),
    # -- 04.7-08: housing, read live --
    "zone_tendue": DatasetCatalogEntry(
        dataset="zone_tendue",
        nom="Communes en zone tendue (taxe sur les logements vacants)",
        producteur="DILA (service-public.gouv.fr)",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/5bc605788b4c414795053dca",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Fichier de référence du simulateur des zones tendues "
            "(TaxeLogementVacant.json, dépôt pidila/sp-simulateurs-data), "
            "« Licence Ouverte » sur la fiche data.gouv.fr, mise à jour du "
            "2026-10-30, relue le 2026-10-31."
        ),
        restrictions=(
            "Liste de la taxe sur les logements vacants, au niveau de la "
            "commune ; un arrondissement est lu sur sa commune.",
        ),
        licence_verifiee_le="2026-10-31",
    ),
    "encadrement_communes": DatasetCatalogEntry(
        dataset="encadrement_communes",
        nom=("Communes à encadrement du niveau des loyers (loi ELAN, article 140)"),
        producteur="Naistor, d'après les décrets et arrêtés préfectoraux",
        licence="Licence Ouverte 2.0",
        url="https://www.data.gouv.fr/datasets/6ae3c8c1252503d60d5c00df",
        coverage_policy="upstream",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Liste rassemblée par Naistor, commune par commune, avec le "
            "décret de périmètre, l'arrêté préfectoral et leur source ; "
            "« Licence Ouverte 2.0 » sur la fiche data.gouv.fr, vérifiée par "
            "le producteur le 2026-10-29, relue le 2026-10-31. Ce n'est pas "
            "une liste de l'État : les arrêtés de Paris, Lyon et Lille ont "
            "été contrôlés contre leur texte le 2026-10-31."
        ),
        restrictions=(
            "Encadrement au niveau de la commune ; quand le décret ne couvre "
            "qu'une partie de la commune, l'adresse n'est pas située. Les "
            "loyers de référence ne sont pas servis.",
        ),
        licence_verifiee_le="2026-10-31",
    ),
    # -- Multi-source communal synthesis --
    "commune": DatasetCatalogEntry(
        dataset="commune",
        nom="Profil commune (synthèse multi-sources)",
        producteur="INSEE",
        licence="Licence Ouverte 2.0",
        url="https://www.insee.fr/fr/information/2560452",
        coverage_policy="instance",
        cadence=_IRREGULAR,
        licence_url=_LO2_URL,
        conditions_redistribution=_LO_CONDITIONS
        + (
            " Synthèse de 13 sources toutes sous Licence Ouverte 2.0 ; elle reprend "
            "les conditions les plus restrictives de ses sources, celles de DVF (voir "
            "l'entrée dvf) et la mention imposée pour la Carte des loyers."
        ),
        restrictions=_DVF_RESTRICTIONS,
        licence_verifiee_le="2026-09-21",
    ),
}


# ---------------------------------------------------------------------------
# RESOLVER_SOURCE_MAP — explicit alias/aggregate expansion for /v1/resolve.
# Every key of DATASETS_DISPONIBLES maps to one or more canonical catalog
# entries. Simple identities are explicit; aliases and aggregates carry an
# ordered, deterministic source list. No implicit string equality.
# ---------------------------------------------------------------------------

# Ordered communal synthesis sources (exactly thirteen, D-01).
_COMMUNE_SOURCES = [
    "demographie",
    "revenus",
    "zonage_abc",
    "vacance",
    "fiscalite",
    "loyers",
    "dvf",
    "dpe",
    "equipements",
    "scolaire",
    "monuments",
    "foncier_pm",
    "connectivite",
]

RESOLVER_SOURCE_MAP: dict[str, list[str]] = {
    "ban": ["ban"],
    "cadastre": ["cadastre"],
    "dvf": ["dvf"],
    "dpe": ["dpe"],
    "loyers": ["loyers"],
    "risques": ["georisques"],
    "pollutions": ["georisques"],
    "urbanisme": ["urbanisme"],
    "batiments": ["batiments"],
    "monuments": ["monuments"],
    "connectivite": ["connectivite"],
    "entreprises": ["sirene"],
    "scolaire": ["scolaire"],
    "transports": ["transports", "gares"],
    "fiscalite": ["fiscalite"],
    "zonage_abc": ["zonage_abc"],
    "copropriete": ["copropriete"],
    "permis": ["permis"],
    "vacance": ["vacance"],
    "revenus": ["revenus"],
    "demographie": ["demographie"],
    "errial": ["errial"],
    "bdnb": ["bdnb"],
    "rnb": ["rnb"],
    "antennes": ["antennes"],
    "equipements": ["equipements"],
    "commune": _COMMUNE_SOURCES,
}

# Structural source of an aggregate key: without it the key has no row to
# serve (v_commune_synthese is built FROM demographie_latest), so the whole
# key is out of coverage with that source's reason, whatever the others.
AGGREGATE_REQUIRED_SOURCES: dict[str, tuple[str, ...]] = {
    "commune": ("demographie",),
}


def _normalize_key(key: str) -> str:
    """Normalize a resolver key: hyphen -> underscore (``zonage-abc``)."""
    return key.replace("-", "_")


def expand_source_keys(keys: list[str]) -> list[str]:
    """Expand resolver keys into an ordered, deduplicated canonical list.

    - hyphenated keys are normalized to underscore form;
    - each key expands via :data:`RESOLVER_SOURCE_MAP`, preserving order;
    - duplicates (by canonical key) are removed, first occurrence wins;
    - an unknown alias raises :class:`KeyError`.

    Pure function: no DB, no network.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        canonical_key = _normalize_key(raw)
        if canonical_key not in RESOLVER_SOURCE_MAP:
            raise KeyError(canonical_key)
        for source in RESOLVER_SOURCE_MAP[canonical_key]:
            if source not in seen:
                seen.add(source)
                ordered.append(source)
    return ordered

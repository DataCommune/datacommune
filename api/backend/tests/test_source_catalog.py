"""Static gates for the canonical source catalog (Phase 44, PROV-01/PROV-03).

These tests are pure: no DB, no network. They prove that the catalog is
complete, that resolver aliases/aggregates expand deterministically toward
existing canonical entries, and that only proven cadences are declared
``official`` (D-01 to D-06).
"""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.common.schemas import (
    DatasetCoverage,
    DatasetProvenance,
    EditorialState,
    PublicationCadence,
    SourcePeriod,
)
from app.common.source_catalog import (
    DATASET_CATALOG,
    RESOLVER_SOURCE_MAP,
    DatasetCatalogEntry,
    expand_source_keys,
)
from app.common.sources import DATASET_SOURCES, get_meta_source, get_meta_sources
from app.resolve.multi import DATASETS_DISPONIBLES

LEGACY_ATTRIBUTION_KEYS = {"nom", "licence", "url", "producteur"}

pytestmark = pytest.mark.unit

# Ordered commune aggregate, exactly 13 canonical sources (D-01).
COMMUNE_SOURCES = [
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


# ── Catalog completeness ──────────────────────────────────────────────────


def test_catalog_covers_all_legacy_source_keys():
    """Every current DATASET_SOURCES key has a canonical catalog entry."""
    assert set(DATASET_CATALOG) == set(DATASET_SOURCES)


def test_catalog_entries_are_frozen_dataclasses():
    entry = DATASET_CATALOG["ban"]
    assert isinstance(entry, DatasetCatalogEntry)
    with pytest.raises(FrozenInstanceError):
        entry.nom = "mutated"  # type: ignore[misc]


# ── Resolver alias coverage (D-01) ────────────────────────────────────────


def test_resolver_map_matches_datasets_disponibles():
    """RESOLVER_SOURCE_MAP covers exactly the resolver dataset keys.

    Any orphan (a resolver key without a source, or a stale map key) fails.
    """
    assert set(RESOLVER_SOURCE_MAP) == DATASETS_DISPONIBLES


def test_every_resolver_target_exists_in_catalog():
    """No alias may produce a canonical identity that does not exist."""
    for key, targets in RESOLVER_SOURCE_MAP.items():
        assert targets, f"{key} maps to an empty source list"
        for target in targets:
            assert target in DATASET_CATALOG, (
                f"{key} -> {target} is not a canonical catalog entry"
            )


def test_known_aliases_map_to_expected_sources():
    assert RESOLVER_SOURCE_MAP["risques"] == ["georisques"]
    assert RESOLVER_SOURCE_MAP["pollutions"] == ["georisques"]
    assert RESOLVER_SOURCE_MAP["entreprises"] == ["sirene"]


# ── Expansion semantics ───────────────────────────────────────────────────


def test_commune_expands_to_thirteen_ordered_sources():
    assert expand_source_keys(["commune"]) == COMMUNE_SOURCES
    assert len(COMMUNE_SOURCES) == 13


def test_expand_normalizes_hyphen_to_underscore():
    assert expand_source_keys(["zonage-abc"]) == ["zonage_abc"]


def test_expand_dedups_by_canonical_key():
    assert expand_source_keys(["risques", "pollutions"]) == ["georisques"]


def test_expand_preserves_request_order():
    assert expand_source_keys(["entreprises", "risques"]) == ["sirene", "georisques"]


def test_expand_transports_aggregate_is_ordered():
    assert expand_source_keys(["transports"]) == ["transports", "gares"]


def test_expand_unknown_alias_raises_keyerror():
    with pytest.raises(KeyError):
        expand_source_keys(["dataset_inexistant"])


# ── Cadence proof (D-05/D-06) ─────────────────────────────────────────────


def test_only_ban_and_dpe_are_official():
    official = {k for k, e in DATASET_CATALOG.items() if e.cadence.statut == "official"}
    assert official == {"ban", "dpe"}


def test_official_entries_carry_https_evidence_and_interval():
    for key in ("ban", "dpe"):
        cadence = DATASET_CATALOG[key].cadence
        assert cadence.evidence_url and cadence.evidence_url.startswith("https://")
        assert cadence.label
        assert cadence.intervalle_jours == 7


def test_official_cadence_requires_https_evidence():
    with pytest.raises(ValidationError):
        PublicationCadence(
            statut="official",
            label="Hebdomadaire",
            evidence_url="http://insecure.example",
            intervalle_jours=7,
        )


def test_official_cadence_requires_label():
    with pytest.raises(ValidationError):
        PublicationCadence(
            statut="official",
            label="",
            evidence_url="https://example.gouv.fr",
            intervalle_jours=7,
        )


def test_official_cadence_requires_positive_interval():
    with pytest.raises(ValidationError):
        PublicationCadence(
            statut="official",
            label="Hebdomadaire",
            evidence_url="https://example.gouv.fr",
            intervalle_jours=0,
        )


def test_irregular_cadence_rejects_invented_interval():
    """No uniform threshold (e.g. 545 days) may pose as source truth."""
    with pytest.raises(ValidationError):
        PublicationCadence(
            statut="non_publiee_ou_irreguliere",
            intervalle_jours=545,
        )


def test_irregular_cadence_minimal_is_valid():
    cadence = PublicationCadence(statut="non_publiee_ou_irreguliere")
    assert cadence.intervalle_jours is None


# ── Provenance contract (D-02) ────────────────────────────────────────────


def test_dataset_provenance_has_ten_fields():
    assert set(DatasetProvenance.model_fields) == {
        "nom",
        "licence",
        "url",
        "producteur",
        "dataset",
        "periode",
        "derniere_importation_reussie",
        "couverture",
        "cadence",
        "etat",
    }


def test_dataset_provenance_builds_with_structured_facts():
    prov = DatasetProvenance(
        nom="Base Adresse Nationale (BAN)",
        licence="Licence Ouverte 2.0",
        url="https://adresse.data.gouv.fr",
        producteur="DINUM / IGN",
        dataset="ban",
        periode=SourcePeriod(statut="inconnue"),
        derniere_importation_reussie=datetime(2026, 7, 1),
        couverture=DatasetCoverage(type="departements", departements=["44", "85"]),
        cadence=PublicationCadence(
            statut="official",
            label="Hebdomadaire",
            evidence_url="https://cartes.gouv.fr/aide",
            intervalle_jours=7,
        ),
        etat=EditorialState.INCONNU,
    )
    assert prov.etat is EditorialState.INCONNU
    assert prov.couverture.departements == ["44", "85"]


# ── Legacy helper compatibility (Task 2) ──────────────────────────────────


def test_get_meta_source_ban_keeps_legacy_shape():
    """/ban meta still exposes exactly the four attribution fields."""
    meta = get_meta_source("ban")
    src = meta["source"]
    assert set(src.keys()) == LEGACY_ATTRIBUTION_KEYS
    assert src["nom"] == "Base Adresse Nationale (BAN)"
    assert src["producteur"] == "DINUM / IGN"


def test_get_meta_sources_transport_keeps_ordered_pair():
    """/transports meta keeps an ordered list of two attributions."""
    meta = get_meta_sources(["transports", "gares"])
    srcs = meta["source"]
    assert isinstance(srcs, list)
    assert [s["nom"] for s in srcs] == ["Arrêts de transport", "Gares SNCF"]
    for s in srcs:
        assert set(s.keys()) == LEGACY_ATTRIBUTION_KEYS


def test_dataset_sources_projects_all_catalog_entries():
    """DATASET_SOURCES is a projection of the catalog, not a parallel registry."""
    assert set(DATASET_SOURCES) == set(DATASET_CATALOG)
    for key, entry in DATASET_CATALOG.items():
        attribution = DATASET_SOURCES[key]
        assert attribution.nom == entry.nom
        assert attribution.licence == entry.licence
        assert attribution.url == entry.url
        assert attribution.producteur == entry.producteur


# ── Publication names in accented French (03-17 K12, C34, V36) ─────────────

# ASCII spellings of the former catalog; none may come back in a published name
# or producer: the frontend mirror follows the catalog, never the reverse.
_ASCII_TOKENS = (
    "Foncieres",
    "Energetique",
    "Batiments",
    "Ministere",
    "Colleges",
    "Arrets",
    "Fiscalite",
    "Immobiliere",
    "Pauvrete",
    "Coproprietes",
    "Donnees",
    "Etat des",
    "Reglementaires",
    "Referentiel",
    "synthese",
    "Georisques",
    "Equipements",
)


def test_publication_names_are_written_in_accented_french():
    """No name nor producer of the catalog keeps a former unaccented spelling."""
    offenders = [
        (key, field, value, token)
        for key, entry in DATASET_CATALOG.items()
        for field, value in (("nom", entry.nom), ("producteur", entry.producteur))
        for token in _ASCII_TOKENS
        if token in value
    ]
    assert offenders == []


# The thirteen names served by GET /v1/commune, written here one by one: the
# frontend mirror (COMMUNE_META_SOURCE_NOMS) is compared to the catalog by
# test_commune_source_mirror_contract.py; this table pins the catalog itself.
_COMMUNE_PUBLICATION_NAMES = {
    "demographie": "Recensement de la population",
    "revenus": "Filosofi (revenus et pauvreté)",
    "zonage_abc": "Zonage ABC (tension immobilière)",
    "vacance": "Logements vacants (LOVAC)",
    "fiscalite": "Fiscalité locale des particuliers",
    "loyers": "Carte des loyers",
    "dvf": "Demandes de valeurs foncières (DVF)",
    "dpe": "Diagnostics de performance énergétique (DPE)",
    "equipements": "Base permanente des équipements (BPE)",
    "scolaire": "Carte scolaire des collèges",
    "monuments": "Monuments historiques",
    "foncier_pm": "Fichiers des personnes morales (MAJIC)",
    "connectivite": "Ma Connexion Internet (Arcep)",
}


def test_commune_source_names_are_the_published_ones():
    """The thirteen commune sources carry exactly their published name."""
    assert list(_COMMUNE_PUBLICATION_NAMES) == COMMUNE_SOURCES
    for key in COMMUNE_SOURCES:
        assert DATASET_CATALOG[key].nom == _COMMUNE_PUBLICATION_NAMES[key], key

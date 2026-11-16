"""Every public response property names the origin of its value (x-source).

Contract of app/openapi_sources.py, checked on ``app.openapi()`` in-process (no
DB, no network): each property of each schema reached from a public route
carries ``x-source``, each value is a key of ``info.x-sources``, and each
dictionary entry names its source, producer and licence. Presence is not
truth: a hand-written table pins the expected origin of representative
properties, read in the code that fills them. The two mutation tests prove the
coverage check catches a schema the tables lose and the truth check a wrong
source.
"""

import copy
from typing import Any

import pytest

from app.main import app
from app.openapi_sources import (
    MODULE_SOURCES,
    PROPERTY_SOURCES,
    SCHEMA_SOURCES,
    annotate_sources,
    public_schema_names,
)

pytestmark = pytest.mark.unit

_REQUIRED_FIELDS = ("nom", "producteur", "licence")

# Expected origin, written by hand from the code that fills each property --
# never derived from the attribution tables under test.
_TRUTH: dict[str, str] = {
    # DVF grain (app/etl_dvf/reconstruction.py, app/dvf/queries.py): the price
    # and the mutation key are copied, prix_m2 and the centroid are computed.
    "ResultatDvf.valeur_fonciere": "dvf",
    "ResultatDvf.id_mutation": "dvf",
    "ResultatDvf.prix_m2": "datacommune",
    "ResultatDvf.latitude": "datacommune",
    "CarteVente.prix": "dvf",  # app/adresse/ventes.py: prix=float(valeur)
    # ADEME rows; coordinates from coordonnee_cartographique_*_ban.
    "ResultatDpe.etiquette_dpe": "dpe",
    "ResultatDpe.longitude": "dpe",
    "AdresseDeclaree.texte": "dpe",  # app/dpe/public_projection.py: adresse_brut
    "DpePublicDiagnostic.perime": "datacommune",
    # BAN rows; the score is a pg_trgm word_similarity (app/ban/queries.py).
    "ResultatGeocodage.nom_voie": "ban",
    "ResultatGeocodage.score": "datacommune",
    # Cadastre rows; centroid by ST_Centroid (app/cadastre/queries.py).
    "ResultatParcelle.contenance": "cadastre",
    "ResultatParcelle.centroide_lat": "datacommune",
    # INSEE census rows; density recomputed from geometry (demographie.backfill).
    "ResultatDemographie.population": "demographie",
    "ResultatDemographie.densite": "datacommune",
    "ResultatVacance.taux_vacance": "datacommune",  # computed by etl_vacance
    "ResultatLoyer.loyer_m2": "loyers",
    "ResultatCommuneSynthese.revenu_median": "revenus",  # r.revenu_median
    "ResultatCommuneSynthese.prix_m2_median": "datacommune",  # DVF median
    # Proxied rows passed through (**row); counts made by DataCommune.
    "RisqueNaturel.libelle_risque": "georisques",
    "BatimentRNB.rnb_id": "rnb",
    "BatimentBDNB.classe_bilan_dpe": "bdnb",
    "ResultatBDNB.nombre_retournes": "datacommune",
    "ResultatEntreprise.distance": "sirene",
    "SignalPlusieursLogements.nombre_lots_habitation": "copropriete",
    # Origin served beside the value: source field, row type, map key.
    "ValeurLieu.valeur": "par-valeur",
    "ResultatArret.stop_name": "par-valeur",
    "ProxySource.total": "par-valeur",
    # Territories: gc.nom of geo_communes; CommuneRef read in BAN, synthesis
    # or geo_communes depending on the route.
    "CommuneListRow.nom": "referentiel-communes",
    "DepartementRef.nom": "referentiel-communes",
    "CommuneRef.nom": "identite-territoriale",
    "PaginationMeta.curseur_suivant": "datacommune",
    # Departement page (04.7-10): the import day is read in DataCommune's own
    # journal (etl_runs), the population summed by DataCommune and its vintage
    # read in demographie rows, the DPE dates in the ADEME rows, the pages
    # listed from geo_communes.
    "ResultatDepartement.indexable": "datacommune",
    "ResultatDepartementSynthese.derniere_importation": "datacommune",
    "PopulationDepartement.total": "datacommune",
    "PopulationDepartement.annee": "demographie",
    "DpeDepartement.date_max": "dpe",
    "PrixTypeDepartement.mediane_m2": "datacommune",
    "FicheDepartement.code_insee": "referentiel-communes",
}


def _missing_sources(spec: dict[str, Any]) -> list[str]:
    """``Schema.property`` of every public response property without x-source."""
    schemas = spec["components"]["schemas"]
    return [
        f"{name}.{prop}"
        for name in sorted(public_schema_names(spec))
        for prop, prop_schema in schemas[name].get("properties", {}).items()
        if "x-source" not in prop_schema
    ]


def _assert_every_property_sourced(spec: dict[str, Any]) -> None:
    missing = _missing_sources(spec)
    assert not missing, (
        f"{len(missing)} public response properties without x-source "
        f"(app/openapi_sources.py): {', '.join(missing)}"
    )


def _assert_sources_match_truth(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    wrong = []
    for key, expected in _TRUTH.items():
        name, prop = key.split(".", 1)
        served = schemas[name]["properties"][prop].get("x-source")
        if served != expected:
            wrong.append(f"{key}: expected {expected}, annotated {served}")
    assert not wrong, f"x-source contradicts the code for {len(wrong)}: {wrong}"


def _unannotated(spec: dict[str, Any]) -> dict[str, Any]:
    """A copy of *spec* stripped of every x-source and of info.x-sources."""
    bare = copy.deepcopy(spec)
    bare["info"].pop("x-sources", None)
    for schema in bare["components"]["schemas"].values():
        for prop_schema in schema.get("properties", {}).values():
            prop_schema.pop("x-source", None)
    return bare


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return app.openapi()


def test_every_public_response_property_has_a_source(spec: dict[str, Any]) -> None:
    assert public_schema_names(spec), "no public response schema found"
    _assert_every_property_sourced(spec)


def test_every_source_is_declared(spec: dict[str, Any]) -> None:
    declared = spec["info"]["x-sources"]
    schemas = spec["components"]["schemas"]
    undeclared = sorted(
        f"{name}.{prop}={prop_schema['x-source']}"
        for name in public_schema_names(spec)
        for prop, prop_schema in schemas[name].get("properties", {}).items()
        if prop_schema.get("x-source") not in declared
    )
    assert not undeclared, f"x-source absent from info.x-sources: {undeclared}"


def test_every_declared_source_names_origin_producer_and_licence(
    spec: dict[str, Any],
) -> None:
    incomplete = sorted(
        key
        for key, entry in spec["info"]["x-sources"].items()
        if not all(str(entry.get(field, "")).strip() for field in _REQUIRED_FIELDS)
    )
    assert not incomplete, f"x-sources entries missing {_REQUIRED_FIELDS}: {incomplete}"


def test_representative_sources_match_the_code(spec: dict[str, Any]) -> None:
    assert len(set(_TRUTH.values())) >= 10, "truth table covers too few sources"
    _assert_sources_match_truth(spec)


def test_a_wrong_source_fails_the_truth_check(spec: dict[str, Any]) -> None:
    """Mutation: annotate the DVF price as computed; the check must name it."""
    wrong = {**PROPERTY_SOURCES, "ResultatDvf.valeur_fonciere": "datacommune"}
    mutant = _unannotated(spec)
    annotate_sources(mutant, app.routes, property_sources=wrong)

    with pytest.raises(AssertionError) as failure:
        _assert_sources_match_truth(mutant)

    message = str(failure.value)
    assert "ResultatDvf.valeur_fonciere: expected dvf, annotated datacommune" in message
    assert "ResultatDvf.id_mutation" not in message


def test_overrides_name_existing_public_schemas_and_properties(
    spec: dict[str, Any],
) -> None:
    """A stale or misspelt override would silently fall back to the module rule."""
    schemas = spec["components"]["schemas"]
    public = public_schema_names(spec)
    stale = sorted(name for name in SCHEMA_SOURCES if name not in public)
    for key in PROPERTY_SOURCES:
        name, prop = key.split(".", 1)
        if name not in public or prop not in schemas[name].get("properties", {}):
            stale.append(key)
    assert not stale, f"overrides naming no public schema or property: {stale}"


def test_a_schema_lost_by_the_tables_fails_the_coverage_check(
    spec: dict[str, Any],
) -> None:
    """Mutation: drop the DVF module rule; the check must fail and name it."""
    amputated = {key: src for key, src in MODULE_SOURCES.items() if key != "app.dvf"}
    mutant = _unannotated(spec)
    annotate_sources(mutant, app.routes, module_sources=amputated)

    with pytest.raises(AssertionError) as failure:
        _assert_every_property_sourced(mutant)

    message = str(failure.value)
    assert "ResultatDvf.id_mutation" in message
    assert "ResultatDvf.valeur_fonciere" in message
    # Property overrides still apply: only the module rule was lost.
    assert "ResultatDvf.prix_m2" not in message
    # The unmutated tables cover the same stripped copy entirely.
    control = _unannotated(spec)
    annotate_sources(control, app.routes)
    _assert_every_property_sourced(control)

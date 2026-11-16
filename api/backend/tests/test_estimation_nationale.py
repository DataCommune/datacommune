"""National size estimate on synthetic measurements (plan 04.1-22)."""

from __future__ import annotations

import pytest

from scripts import estimation_nationale as est

pytestmark = pytest.mark.unit

TAILLES_PARTITIONNEES = {
    "tables": {"parcelles": {"exact_count": 300}},
    "relations": [
        {"name": "parcelles_44", "parent": "parcelles", "departement": "44",
         "exact_count": 100, "pg_relation_size": 10_000, "toast_bytes": 0,
         "pg_indexes_size": 2_000},
        {"name": "parcelles_85", "parent": "parcelles", "departement": "85",
         "exact_count": 200, "pg_relation_size": 30_000, "toast_bytes": 2_000,
         "pg_indexes_size": 6_000},
        {"name": "parcelles_75", "parent": "parcelles", "departement": "75",
         "exact_count": 0, "pg_relation_size": 8_192, "toast_bytes": 0,
         "pg_indexes_size": 8_192},
    ],
}  # fmt: skip
TAILLES_SIMPLES = {
    "tables": {
        "eligibilite_fixe": {
            "exact_count": 1_000,
            "table_bytes": 90_000,
            "toast_bytes": 10_000,
            "indexes_bytes": 50_000,
        }
    },
    "relations": [],
}


def test_a_partitioned_table_gives_one_sample_per_measured_departement() -> None:
    mesures = est.echantillons(TAILLES_PARTITIONNEES, "parcelles", "a.json", [])
    assert [(e.departements, e.lignes) for e in mesures] == [
        (("44",), 100),
        (("85",), 200),
    ]
    assert mesures[1].tas_par_ligne == 160.0


def test_a_plain_table_gives_one_sample_for_its_departements() -> None:
    (mesure,) = est.echantillons(
        TAILLES_SIMPLES, "eligibilite_fixe", "b.json", ["44", "85"]
    )
    assert (mesure.departements, mesure.tas_par_ligne, mesure.index_par_ligne) == (
        ("44", "85"),
        100.0,
        50.0,
    )


def test_central_is_weighted_and_high_is_the_densest_sample() -> None:
    mesures = est.echantillons(TAILLES_PARTITIONNEES, "parcelles", "a.json", [])
    resultat = est.estimer("parcelles", 1_000_000, mesures, ecart_version=0.1)
    # Weighted: (10 000 + 32 000) / 300 = 140 heap, 8 000 / 300 index.
    assert resultat["centrale_octets"] == round((42_000 + 8_000) / 300 * 1_000_000)
    # Densest: 85 at 160 heap + 30 index, then +10 % version gap.
    assert resultat["haute_octets"] == round(190 * 1_000_000 * 1.1)
    assert resultat["haute_octets"] >= resultat["centrale_octets"]
    noms = {c["nom"]: c["etiquette"] for c in resultat["composantes"]}
    assert noms == {
        "lignes_nationales": "ÉTABLI",
        "tas_centrale": "DÉDUIT",
        "index_centrale": "DÉDUIT",
        "tas_haute": "DÉDUIT",
        "index_haute": "DÉDUIT",
        "ecart_version": "HYPOTHÈSE",
    }


def test_high_is_never_below_central() -> None:
    (mesure,) = est.echantillons(TAILLES_SIMPLES, "eligibilite_fixe", "b.json", ["44"])
    resultat = est.estimer("eligibilite_fixe", 10, [mesure])
    assert resultat["haute_octets"] == resultat["centrale_octets"] == 1_500


def test_no_sample_is_an_error() -> None:
    with pytest.raises(ValueError, match="no measured sample"):
        est.estimer("batiments", 10, [])


def test_usable_capacity_per_file_system() -> None:
    disque = 960 * est.GO
    assert est.capacite_utilisable(disque, "ext4") == int(disque * 0.95)
    assert est.capacite_utilisable(disque, "ext4-m1") == int(disque * 0.99)
    assert est.capacite_utilisable(disque, "zfs") == int(disque * 0.80)
    with pytest.raises(ValueError):
        est.capacite_utilisable(disque, "btrfs")


def test_seed_partitions_under_the_threshold_are_skipped() -> None:
    mesures = est.echantillons(
        TAILLES_PARTITIONNEES, "parcelles", "a.json", [], min_lignes=150
    )
    assert [e.departements for e in mesures] == [("85",)]


def test_first_import_peak_scales_with_rows() -> None:
    mesure = {
        "disque_octets": 1_000,
        "wal_genere_octets": 500,
        "anon_octets": 64,
        "duree_s": 10,
    }
    pic = est.pic_premier_import(mesure, 100, 1_000)
    assert pic["disque_au_dessus_base_octets"] == 10_000
    assert pic["wal_genere_octets"] == 5_000
    assert pic["duree_s"] == 100
    assert (pic["anon_mesure_octets"], pic["anon_lineaire_octets"]) == (64, 640)


def test_sequential_envelope_counts_what_is_already_published() -> None:
    enveloppe = est.enveloppe_sequentielle(
        ["a", "b"], {"a": 100, "b": 10}, {"a": 150, "b": 300}, socle=5
    )
    # a: 5 + 150 ; b: 5 + 100 (a published) + 300.
    assert [e["pic"] for e in enveloppe["etapes"]] == [155, 405]
    assert (enveloppe["pic_octets"], enveloppe["pic_pendant"]) == (405, "b")
    assert enveloppe["fin"] == 115


def test_capacity_report_and_archive_retention() -> None:
    rapport = est.rapport_capacite(100, "zfs", {"pic": 40 * est.GO})
    assert rapport["capacite_utilisable_octets"] == 80 * est.GO
    assert rapport["besoins"]["pic"]["part_utilisable"] == 0.5
    assert rapport["besoins"]["pic"]["marge_octets"] == 40 * est.GO
    archive = est.archive_nationale({"dpe": 10, "dvf": 5}, retention=2)
    assert (archive["une_edition_octets"], archive["retenue_octets"]) == (15, 30)

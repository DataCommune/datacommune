"""Unit tests for logements/service.py -- floor parsing, dedup, honest counts."""

from datetime import date

import pytest

pytestmark = pytest.mark.unit

from app.logements.service import (
    CLE_RATTACHEMENT,
    RATTACHEMENT_ELARGI,
    RATTACHEMENT_EXACT,
    dedup_logements,
    est_distinguable,
    normalise_complement,
    parse_etage,
)


def _row(**overrides) -> dict:
    """Build a minimal distinguishable appartement DPE row, overridable per test."""
    row = {
        "numero_dpe": "DPE-0001",
        "date_etablissement_dpe": date(2023, 1, 1),
        "date_fin_validite_dpe": date(2033, 1, 1),
        "numero_dpe_remplace": None,
        "type_batiment": "appartement",
        "surface_habitable": 60.0,
        "etiquette_dpe": "D",
        "numero_etage_appartement": 2,
        "complement_adresse_logement": "Appt 1",
        "identifiant_ban": None,
    }
    row.update(overrides)
    return row


class TestParseEtage:
    """Strict floor parsing: 0 alone never means ground floor (D-06/D-21)."""

    def test_etage_zero_is_unknown(self):
        assert parse_etage(0, None) is None

    def test_etage_zero_with_text_proof_of_rdc(self):
        assert parse_etage(0, "Bat. 1; Etage RDC; Porte Droite") == 0

    def test_etage_zero_with_text_floor_wins(self):
        assert parse_etage(0, "Etage : 2") == 2
        assert parse_etage(0, "2ème Etage") == 2
        assert parse_etage(0, "Etage 4") == 4

    def test_etage_structure_non_zero_is_trusted(self):
        assert parse_etage(4, None) == 4
        assert parse_etage(-1, None) == -1

    def test_etage_none_and_no_complement_is_unknown(self):
        assert parse_etage(None, None) is None

    def test_etage_opaque_complement_is_unknown(self):
        assert parse_etage(0, "Bat. N/A 078262") is None
        assert parse_etage(0, "419") is None

    def test_sous_sol_from_text(self):
        assert parse_etage(0, "Sous-sol") == -1


class TestNormaliseComplement:
    """Stable, accent-free, lowercase normalization (D-05 grouping key)."""

    def test_normalise_complement_casse_et_accents(self):
        valeur = normalise_complement("  Appt 12 – Bât. A ")  # noqa: RUF001
        assert valeur == valeur.lower()
        assert "â" not in valeur
        assert normalise_complement(None) == ""

    def test_normalise_complement_abreviations(self):
        a = normalise_complement("Apt 12")
        b = normalise_complement("Appartement 12")
        c = normalise_complement("APPT 12")
        assert a == b == c


class TestEstDistinguable:
    """A DPE only becomes a card if it carries a readable discriminant (D-07/D-23)."""

    def test_est_distinguable_false_sans_discriminant(self):
        row = {"numero_etage_appartement": 0, "complement_adresse_logement": None}
        assert est_distinguable(row) is False

    def test_est_distinguable_false_complement_opaque(self):
        row_numero = {
            "numero_etage_appartement": 0,
            "complement_adresse_logement": "419",
        }
        row_na = {
            "numero_etage_appartement": 0,
            "complement_adresse_logement": "Bat. N/A 078262",
        }
        assert est_distinguable(row_numero) is False
        assert est_distinguable(row_na) is False

    def test_est_distinguable_true_avec_porte(self):
        row = {
            "numero_etage_appartement": None,
            "complement_adresse_logement": "Appt 12",
        }
        assert est_distinguable(row) is True


class TestDedupPasseExacte:
    """Exact ``numero_dpe_remplace`` pass precedes the heuristic pass (D-05)."""

    def test_dedup_passe_exacte_remplacement(self):
        ancien = _row(numero_dpe="A")
        nouveau = _row(numero_dpe="B", numero_dpe_remplace="A")
        logements, non_distinguables, _ = dedup_logements([ancien, nouveau])
        numeros = {logement["numero_dpe"] for logement in logements}
        assert "A" not in numeros
        assert non_distinguables == 0

    def test_dedup_chaine_de_remplacement(self):
        a = _row(numero_dpe="A")
        b = _row(numero_dpe="B", numero_dpe_remplace="A")
        c = _row(numero_dpe="C", numero_dpe_remplace="B")
        logements, _, _ = dedup_logements([a, b, c])
        assert [logement["numero_dpe"] for logement in logements] == ["C"]


class TestDedupHeuristique:
    """Grouping key = (etage, surface +/-2 m2, complement normalise); latest wins."""

    def test_dedup_heuristique_cle_d05(self):
        ancien = _row(
            numero_dpe="A",
            surface_habitable=61.0,
            date_etablissement_dpe=date(2018, 1, 1),
        )
        recent = _row(
            numero_dpe="B",
            surface_habitable=62.5,
            date_etablissement_dpe=date(2023, 1, 1),
        )
        logements, _, _ = dedup_logements([ancien, recent])
        assert len(logements) == 1
        assert logements[0]["numero_dpe"] == "B"

    def test_dedup_surface_ecart_superieur_a_2(self):
        a = _row(numero_dpe="A", surface_habitable=61.0)
        b = _row(numero_dpe="B", surface_habitable=64.0)
        logements, _, _ = dedup_logements([a, b])
        assert len(logements) == 2

    def test_dedup_etage_different_ne_groupe_pas(self):
        a = _row(numero_dpe="A", numero_etage_appartement=2, surface_habitable=60.0)
        b = _row(numero_dpe="B", numero_etage_appartement=3, surface_habitable=60.0)
        logements, _, _ = dedup_logements([a, b])
        assert len(logements) == 2

    def test_dedup_etage_inconnu_ne_groupe_pas_avec_etage_connu(self):
        a = _row(numero_dpe="A", numero_etage_appartement=None, surface_habitable=60.0)
        b = _row(numero_dpe="B", numero_etage_appartement=2, surface_habitable=60.0)
        logements, _, _ = dedup_logements([a, b])
        assert len(logements) == 2


class TestNonDistinguables:
    """Non-distinguishable DPE rows never produce a card (D-07/D-23)."""

    def test_non_distinguables_comptes_sans_carte(self):
        opaques = ["419", "522", "320"]
        rows = [
            _row(
                numero_dpe=str(i),
                numero_etage_appartement=0,
                complement_adresse_logement=texte,
            )
            for i, texte in enumerate(opaques)
        ]
        logements, non_distinguables, _ = dedup_logements(rows)
        assert logements == []
        assert non_distinguables == 3

    def test_non_distinguables_dedupe_les_remplacements(self):
        opaque = {"numero_etage_appartement": 0, "complement_adresse_logement": "419"}
        autre = {"numero_etage_appartement": 0, "complement_adresse_logement": "522"}
        rows = [
            _row(numero_dpe="A", **opaque),
            _row(numero_dpe="B", numero_dpe_remplace="A", **opaque),
            _row(numero_dpe="C", **autre),
        ]
        logements, non_distinguables, _ = dedup_logements(rows)
        assert logements == []
        assert non_distinguables == 2


class TestPeremptionEtTypeBatiment:
    """Expired/collective DPE handling (D-08/Pitfall 5)."""

    def test_dpe_perime_reste_dans_la_liste(self):
        row = _row(numero_dpe="A", date_fin_validite_dpe=date(2020, 1, 1))
        logements, _, _ = dedup_logements([row])
        assert len(logements) == 1
        assert logements[0]["perime"] is True

    def test_dpe_type_immeuble_exclu_de_la_liste(self):
        row = _row(numero_dpe="A", type_batiment="immeuble")
        logements, non_distinguables, _ = dedup_logements([row])
        assert logements == []
        assert non_distinguables == 0

    def test_dedup_ordre_deterministe(self):
        rows = [
            _row(numero_dpe="Z", numero_etage_appartement=3, surface_habitable=70.0),
            _row(numero_dpe="A", numero_etage_appartement=1, surface_habitable=40.0),
        ]
        premier, _, _ = dedup_logements(rows)
        second, _, _ = dedup_logements(list(reversed(rows)))
        ordre_premier = [logement["numero_dpe"] for logement in premier]
        ordre_second = [logement["numero_dpe"] for logement in second]
        assert ordre_premier == ordre_second

    def test_dedup_conserve_les_discriminants_structures(self):
        row = _row(
            numero_dpe="A",
            numero_etage_appartement=None,
            complement_adresse_logement="Appt 12",
        )
        logements, _, _ = dedup_logements([row])
        assert logements[0]["etage"] is None
        assert logements[0]["batiment"] is None
        assert logements[0]["porte"] == "12"

    def test_distribution_compte_tous_les_dpe_dedoublonnes(self):
        rows = [
            _row(numero_dpe="A", numero_etage_appartement=1, etiquette_dpe="C"),
            _row(numero_dpe="B", numero_etage_appartement=2, etiquette_dpe="D"),
            _row(
                numero_dpe="C",
                numero_etage_appartement=0,
                complement_adresse_logement="1",
                etiquette_dpe="D",
            ),
            _row(
                numero_dpe="D",
                numero_etage_appartement=0,
                complement_adresse_logement="2",
                etiquette_dpe="D",
            ),
            _row(
                numero_dpe="E",
                numero_etage_appartement=0,
                complement_adresse_logement="3",
                etiquette_dpe="E",
            ),
        ]
        _, _, distribution = dedup_logements(rows)
        assert distribution == {"C": 1, "D": 3, "E": 1}

    def test_distribution_omet_les_classes_absentes(self):
        rows = [_row(numero_dpe="A", etiquette_dpe="C")]
        _, _, distribution = dedup_logements(rows)
        assert "G" not in distribution

    def test_distribution_ignore_les_etiquettes_nulles(self):
        rows = [_row(numero_dpe="A", etiquette_dpe=None)]
        _, _, distribution = dedup_logements(rows)
        assert distribution == {}


class TestProvenanceCarte:
    """The per-row attachment tag becomes a public card field (Lot D)."""

    def test_carte_expose_la_provenance_du_rattachement(self):
        row = _row(numero_dpe="A", **{CLE_RATTACHEMENT: RATTACHEMENT_EXACT})
        logements, _, _ = dedup_logements([row])
        assert logements[0]["rattachement"] == RATTACHEMENT_EXACT

    def test_cluster_mixte_garde_la_provenance_de_la_carte_affichee(self):
        # Same (etage, complement, surface) cluster: the most recent row wins
        # the card, and the card carries THAT row's provenance -- never a
        # merge of the two.
        rows = [
            _row(
                numero_dpe="ANCIEN",
                date_etablissement_dpe=date(2020, 1, 1),
                **{CLE_RATTACHEMENT: RATTACHEMENT_EXACT},
            ),
            _row(
                numero_dpe="RECENT",
                date_etablissement_dpe=date(2024, 1, 1),
                **{CLE_RATTACHEMENT: RATTACHEMENT_ELARGI},
            ),
        ]
        logements, _, _ = dedup_logements(rows)
        assert len(logements) == 1
        assert logements[0]["numero_dpe"] == "RECENT"
        assert logements[0]["rattachement"] == RATTACHEMENT_ELARGI

    def test_ligne_sans_tag_rend_une_provenance_none(self):
        logements, _, _ = dedup_logements([_row(numero_dpe="A")])
        assert logements[0]["rattachement"] is None

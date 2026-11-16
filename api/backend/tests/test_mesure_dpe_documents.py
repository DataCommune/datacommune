"""Offline tests for the DPE document measurement (plan 04-01, D-108).

No database, no network: queries go through a connection double that records
every SQL string and answers from a table keyed by the normalised statement.
The matrix covers the plan's five holes:

(a) the pure aggregations count the synthetic rows as expected (document
    without key, orphan key, pre-2021-07-01 document, expired document, house
    on a single-key address whose parcel holds one building, house whose
    parcel holds two buildings);
(b) ``main`` refuses a connection whose ``transaction_read_only`` is not on;
(c) every SQL statement of the module passes the REAL ``assert_select_only``;
(d) the JSON output of a poisoned synthetic set carries no DPE number and no
    BAN key (T-58-02);
(e) without ``--base`` nor ``--capture`` the CLI exits 2 with its usage.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

from scripts import mesure_dpe_documents as mesure
from scripts import mesure_dpe_maison, verify_golden_cases
from scripts.verify_golden_cases import GoldenSecurityError, assert_select_only

POISON_DPE = "2644E0039039K"
POISON_BAN = "44021_0104_00273"
MOTIF_DPE = re.compile(r"(?<![0-9A-Za-z])\d{4}[A-Z]\d{7}[A-Z](?![0-9A-Za-z])")
MOTIF_BAN = re.compile(r"\d{5}_[0-9A-Za-z]{4,5}_\d{5}")


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


def _fait(**surcharges: Any) -> dict[str, Any]:
    """One grouped fact row as FAITS_DOCUMENTS_*_SQL returns it."""
    ligne: dict[str, Any] = {
        "code_departement": "44",
        "regime": "existant",
        "type_batiment": "appartement",
        "cle_vide": False,
        "cle_jointe": True,
        "sans_position": False,
        "adresse_sans_position": False,
        "tombstone": False,
        "graphe_incoherent": False,
        "cible_absente": False,
        "remplace": False,
        "date_etablissement_absente": False,
        "ancienne_methode": False,
        "date_fin_absente": False,
        "expire_import": False,
        "expire_mesure": False,
        "classe_docs_cle": "2-5",
        "classe_docs_cle_regime": "2-5",
        "premier_de_cle": False,
        "premier_de_cle_regime": False,
        "premiere_maison_consultable_de_cle": False,
        "batiments_parcelle": "non_calcule",
        "batiments_hors_annexe": "non_calcule",
        "concordance_adresse": "concordant",
        "complement_brut": False,
        "complement_logement": False,
        "id_rnb_renseigne": False,
        "effectif": 1,
    }
    ligne.update(surcharges)
    return ligne


def _faits_synthetiques() -> list[dict[str, Any]]:
    return [
        # document without key (geolocated by ADEME)
        _fait(
            cle_vide=True,
            cle_jointe=False,
            classe_docs_cle=None,
            concordance_adresse=None,
        ),
        # orphan key (absent from adresses)
        _fait(cle_jointe=False, classe_docs_cle=None, concordance_adresse=None),
        # pre-2021-07-01 document, first of its address (2 documents)
        _fait(
            ancienne_methode=True,
            premier_de_cle=True,
            premier_de_cle_regime=True,
        ),
        # expired document at import date, second document of that address
        _fait(
            expire_import=True,
            expire_mesure=True,
            concordance_adresse="ecart",
            complement_brut=True,
        ),
        # house alone at its address, parcel with one building, concordant
        _fait(
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premier_de_cle_regime=True,
            premiere_maison_consultable_de_cle=True,
            batiments_parcelle="1",
            batiments_hors_annexe="1",
        ),
        # house alone at its address, parcel with two buildings (one annex)
        _fait(
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premier_de_cle_regime=True,
            premiere_maison_consultable_de_cle=True,
            batiments_parcelle="2+",
            batiments_hors_annexe="1",
            concordance_adresse="non_evaluable",
            complement_logement=True,
        ),
    ]


# ---------------------------------------------------------------------------
# (a) pure aggregations
# ---------------------------------------------------------------------------


def test_motif_ecart_cascade_fermee() -> None:
    assert mesure.motif_ecart(_fait()) is None
    assert mesure.motif_ecart(_fait(cle_vide=True, cle_jointe=False)) == "cle_ban_vide"
    assert mesure.motif_ecart(_fait(cle_jointe=False)) == "cle_ban_orpheline"
    assert mesure.motif_ecart(_fait(adresse_sans_position=True)) == "sans_geometrie"
    assert mesure.motif_ecart(_fait(graphe_incoherent=True)) == "graphe_incoherent"
    assert mesure.motif_ecart(_fait(tombstone=True)) == "autre"
    assert set(mesure.MOTIFS_ECART) == {
        "cle_ban_vide",
        "cle_ban_orpheline",
        "sans_geometrie",
        "graphe_incoherent",
        "autre",
    }


def test_agreger_consultables() -> None:
    resultat = mesure.agreger_consultables(_faits_synthetiques())
    dep = resultat["consultables"]["44"]
    assert dep["total"] == 6
    assert dep["consultables_adresse"] == 4
    assert dep["consultables_numero_seul"] == 2
    motifs = resultat["ecartes_par_motif"]["44"]
    attendus = {
        "cle_ban_vide": 1,
        "cle_ban_orpheline": 1,
        "sans_geometrie": 0,
        "graphe_incoherent": 0,
        "autre": 0,
    }
    assert {motif: motifs[motif] for motif in mesure.MOTIFS_ECART} == attendus
    assert (
        dep["consultables_adresse"]
        + sum(motifs[motif] for motif in mesure.MOTIFS_ECART)
        == dep["total"]
    )
    assert motifs["par_regime"]["existant"] == attendus


def test_agreger_consultables_refuse_une_partition_fausse() -> None:
    with pytest.raises(mesure.PartitionError):
        mesure.verifier_partition(
            {"44": {"total": 3, "consultables_adresse": 1}}, {"44": {"cle_ban_vide": 1}}
        )


def test_agreger_dates() -> None:
    dates = mesure.agreger_dates(_faits_synthetiques())
    dep = dates["44"]["existant"]
    assert dep["total"] == 6
    assert dep["ancienne_methode"] == 1
    assert dep["expires_a_l_import"] == 1
    assert dep["remplaces"] == 0


def test_agreger_documents_par_cle() -> None:
    parcle = mesure.agreger_documents_par_cle(_faits_synthetiques())["44"]
    # three addresses: one with 2 documents, two with 1 document
    assert parcle["adresses_avec_dpe"] == 3
    assert parcle["histogramme"]["1"] == 2
    assert parcle["histogramme"]["2-5"] == 1
    assert parcle["adresses_plus_de_10"] == 0


def test_agreger_maisons_d110() -> None:
    d110 = mesure.agreger_maisons_d110(_faits_synthetiques())["44"]
    assert d110["maisons_consultables"] == 2
    assert d110["i_cle_un_seul_document"] == 2
    assert d110["ii_parcelle_un_batiment"] == 1
    assert d110["iii_cumul"] == 1
    assert d110["iv_cumul_adresse_concordante"] == 1
    assert d110["iii_cumul_hors_annexes"] == 2
    assert d110["adresses_avec_dpe"] == 3
    assert d110["parts"]["iii_sur_maisons"] == 0.5
    assert d110["parts"]["iii_sur_adresses"] == pytest.approx(0.3333, abs=1e-4)


def test_d110_parts_i_et_ii_comptent_des_adresses_distinctes() -> None:
    faits = [
        _fait(type_batiment="appartement", premier_de_cle=True, batiments_parcelle="1"),
        _fait(
            type_batiment="maison",
            premier_de_cle=False,
            premiere_maison_consultable_de_cle=True,
            batiments_parcelle="1",
        ),
        _fait(
            type_batiment="maison",
            premier_de_cle=False,
            premiere_maison_consultable_de_cle=False,
            batiments_parcelle="1",
        ),
        _fait(
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premiere_maison_consultable_de_cle=True,
            batiments_parcelle="1",
        ),
    ]
    d110 = mesure.agreger_maisons_d110(faits)["44"]
    assert d110["adresses_avec_dpe"] == 2
    assert d110["i_adresses_distinctes"] == 1
    assert d110["ii_adresses_distinctes"] == 2
    assert d110["parts"]["i_sur_adresses"] == 0.5
    assert d110["parts"]["ii_sur_adresses"] == 1.0


def test_blocs_b_a_f_conservent_totaux_et_ventilent_par_regime() -> None:
    lignes = [
        _fait(
            regime="existant",
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premier_de_cle_regime=True,
            premiere_maison_consultable_de_cle=True,
        ),
        _fait(
            regime="neuf",
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premier_de_cle_regime=True,
            premiere_maison_consultable_de_cle=True,
        ),
    ]
    consultables = mesure.agreger_consultables(lignes)
    assert consultables["consultables"]["44"]["total"] == 2
    assert consultables["consultables"]["44"]["par_regime"]["existant"]["total"] == 1
    assert consultables["consultables"]["44"]["par_regime"]["neuf"]["total"] == 1
    assert consultables["ecartes_par_motif"]["44"]["par_regime"]["neuf"] == {
        motif: 0 for motif in mesure.MOTIFS_ECART
    }
    par_cle = mesure.agreger_documents_par_cle(lignes)["44"]
    assert par_cle["adresses_avec_dpe"] == 2
    assert par_cle["par_regime"]["existant"]["adresses_avec_dpe"] == 1
    d110 = mesure.agreger_maisons_d110(lignes)["44"]
    assert d110["maisons_consultables"] == 2
    assert d110["par_regime"]["neuf"]["maisons_consultables"] == 1
    declarees = mesure.agreger_adresses_declarees(lignes)["44"]
    assert declarees["documents_a_cle_jointe"] == 2
    assert declarees["par_regime"]["existant"]["documents_a_cle_jointe"] == 1


def test_bloc_e_conserve_totaux_et_ventile_les_formes_par_regime() -> None:
    numero = [
        {
            "code_departement": "44",
            "regime": regime,
            "conforme": True,
            "forme_ademe": True,
            "departement_concorde": True,
            "longueur": 13,
            "a_chiffre": True,
            "a_majuscule": True,
            "a_minuscule": False,
            "a_autre": False,
            "effectif": 1,
        }
        for regime in ("existant", "neuf")
    ]
    cles = [
        {
            "origine": "diagnostics_dpe",
            "code_departement": "44",
            "regime": regime,
            "absente": False,
            "conforme": True,
            "longueur": 16,
            "segments": 3,
            "profil": "5-4-5",
            "a_minuscule": True,
            "a_majuscule": False,
            "a_autre": True,
            "effectif": 1,
        }
        for regime in ("existant", "neuf")
    ]
    formes = mesure.agreger_formes(numero, cles)
    assert formes["numero_dpe"]["44"]["total"] == 2
    assert formes["numero_dpe"]["44"]["par_regime"]["existant"]["total"] == 1
    dpe = formes["cles_ban"]["diagnostics_dpe"]["44"]
    assert dpe["total"] == 2
    assert dpe["par_regime"]["neuf"]["total"] == 1


def test_agreger_adresses_declarees() -> None:
    adr = mesure.agreger_adresses_declarees(_faits_synthetiques())["44"]
    assert adr["documents_a_cle_jointe"] == 4
    assert adr["adresse_divergente"] == 1
    assert adr["non_evaluable"] == 1
    assert adr["complement_dans_adresse_brute"] == 1
    assert adr["complement_logement_renseigne"] == 1


def test_agreger_formes() -> None:
    lignes_dpe = [
        {
            "code_departement": "44",
            "conforme": True,
            "forme_ademe": True,
            "departement_concorde": True,
            "longueur": 13,
            "a_chiffre": True,
            "a_majuscule": True,
            "a_minuscule": False,
            "a_autre": False,
            "effectif": 9,
        },
        {
            "code_departement": "44",
            "conforme": False,
            "forme_ademe": False,
            "departement_concorde": False,
            "longueur": 12,
            "a_chiffre": True,
            "a_majuscule": False,
            "a_minuscule": True,
            "a_autre": False,
            "effectif": 1,
        },
    ]
    lignes_cle = [
        {
            "origine": "adresses",
            "code_departement": "44",
            "absente": False,
            "conforme": True,
            "longueur": 16,
            "segments": 3,
            "profil": "5-4-5",
            "a_minuscule": False,
            "a_majuscule": False,
            "a_autre": False,
            "effectif": 4,
        },
        {
            "origine": "adresses",
            "code_departement": "44",
            "absente": False,
            "conforme": False,
            "longueur": 9,
            "segments": 2,
            "profil": "5-3",
            "a_minuscule": True,
            "a_majuscule": False,
            "a_autre": False,
            "effectif": 1,
        },
    ]
    formes = mesure.agreger_formes(lignes_dpe, lignes_cle)
    numero = formes["numero_dpe"]["44"]
    assert numero["total"] == 10
    assert numero["conformes"] == 9
    assert numero["departement_concorde"] == 9
    assert numero["formes_non_conformes"] == [
        {"longueur": 12, "classes": "chiffres+minuscules", "effectif": 1}
    ]
    cle = formes["cles_ban"]["adresses"]["44"]
    assert cle["conformes"] == 4
    assert cle["formes_non_conformes"] == [
        {
            "longueur": 9,
            "segments": 2,
            "profil": "5-3",
            "classes": "minuscules",
            "effectif": 1,
        }
    ]
    assert cle["profils_segments"] == [
        {"profil": "5-4-5", "conforme": True, "effectif": 4},
        {"profil": "5-3", "conforme": False, "effectif": 1},
    ]


def test_agreger_capture() -> None:
    capture = [
        {
            "numero": "A1",
            "code_insee": "44109",
            "etiquette": "D",
            "surface": "50.0",
            "date": "2024-01-02",
        },
        {
            "numero": "A2",
            "code_insee": "44109",
            "etiquette": "C",
            "surface": "80",
            "date": "2024-01-02",
        },
        {
            "numero": "A3",
            "code_insee": "49007",
            "etiquette": "E",
            "surface": "30",
            "date": "2024-01-02",
        },
        {
            "numero": "A4",
            "code_insee": "44143",
            "etiquette": "E",
            "surface": "30",
            "date": "2024-01-02",
        },
        {
            "numero": "A5",
            "code_insee": "85191",
            "etiquette": "A",
            "surface": "70",
            "date": "2024-01-02",
        },
    ]
    trouves = [
        {
            "numero_dpe": "A1",
            "code_departement": "44",
            "etiquette_dpe": "D",
            "surface_habitable": Decimal("50.0"),
            "date_etablissement_dpe": date(2024, 1, 2),
        },
        {
            "numero_dpe": "A2",
            "code_departement": "44",
            "etiquette_dpe": "B",
            "surface_habitable": Decimal("81.5"),
            "date_etablissement_dpe": date(2024, 1, 3),
        },
        {
            "numero_dpe": "A5",
            "code_departement": "85",
            "etiquette_dpe": "A",
            "surface_habitable": Decimal("70"),
            "date_etablissement_dpe": date(2024, 1, 2),
        },
    ]
    faits = [
        _fait(
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premier_de_cle_regime=True,
            premiere_maison_consultable_de_cle=True,
            batiments_parcelle="1",
            batiments_hors_annexe="1",
        ),
        _fait(cle_jointe=False, classe_docs_cle=None, concordance_adresse=None),
        _fait(
            code_departement="85",
            type_batiment="maison",
            classe_docs_cle="1",
            classe_docs_cle_regime="1",
            premier_de_cle=True,
            premier_de_cle_regime=True,
            premiere_maison_consultable_de_cle=True,
            batiments_parcelle="1",
            batiments_hors_annexe="1",
        ),
    ]
    resultat = mesure.agreger_capture(capture, trouves, faits, ["44", "85"])
    assert resultat["documents_captures"] == 5
    assert resultat["retrouves_en_base"] == 3
    assert resultat["consultables_adresse"] == 2
    assert resultat["consultables_numero_seul"] == 1
    assert resultat["ecartes_par_motif"] == {
        "cle_ban_vide": 0,
        "cle_ban_orpheline": 1,
        "sans_geometrie": 0,
        "graphe_incoherent": 0,
        "autre": 0,
        "par_regime": {
            "existant": {
                "cle_ban_vide": 0,
                "cle_ban_orpheline": 1,
                "sans_geometrie": 0,
                "graphe_incoherent": 0,
                "autre": 0,
            }
        },
    }
    assert resultat["absents"] == {
        "commune_hors_couverture": 1,
        "absent_de_l_import": 1,
    }
    assert resultat["champs_differents"] == {
        "compares": 3,
        "etiquette_dpe": 1,
        "surface_habitable": 1,
        "date_etablissement_dpe": 1,
    }
    assert (
        "non mesurable sur la capture seule"
        in resultat["maisons_cle_unique_capture_seule"]
    )
    d110 = resultat["d110_retrouves_attributs_base"]
    assert d110["adresses_avec_dpe"] == 2
    assert d110["iii_cumul"] == 2
    assert d110["parts"]["iii_sur_adresses"] == 1.0
    assert d110["par_regime"]["existant"]["adresses_avec_dpe"] == 2


def test_extraire_numeros_capture(tmp_path: Path) -> None:
    ligne = {
        "numero_dpe": POISON_DPE,
        "code_insee_ban": "44109",
        "etiquette_dpe": "D",
        "surface_habitable_logement": 50.5,
        "date_etablissement_dpe": "2024-01-02",
    }
    (tmp_path / "dpe-44109.json").write_text(
        json.dumps({"results": [ligne, ligne]}), encoding="utf-8"
    )
    (tmp_path / "dpe-complement-no-coordinates.json").write_text(
        json.dumps({"results": [dict(ligne, numero_dpe="X")]}), encoding="utf-8"
    )
    lignes = mesure.extraire_numeros_capture(tmp_path)
    assert lignes == [
        {
            "numero": POISON_DPE,
            "code_insee": "44109",
            "etiquette": "D",
            "surface": "50.5",
            "date": "2024-01-02",
        }
    ]


def test_extraction_refuse_une_destination_dans_le_depot(tmp_path: Path) -> None:
    racine = Path(mesure.__file__).resolve().parents[3]
    with pytest.raises(GoldenSecurityError):
        mesure.ecrire_fichier_capture([], racine / "numeros.txt")


def test_normalisation_de_voie_identique_au_bloc_2() -> None:
    """The voie normalisation is the block-2 one of mesure_dpe_maison, verbatim."""
    source = _normalise(mesure_dpe_maison.CONCORDANCE_VOIE_SQL)
    for colonne in ("d.adresse_brut", "d.nom_rue_ban"):
        assert _normalise(mesure.normaliser_voie_sql(colonne)) in source
    assert mesure.NUMERO_TETE_RE is mesure_dpe_maison.NUMERO_TETE_RE


# ---------------------------------------------------------------------------
# (a') blocks H, I, J and D-109: pure functions
# ---------------------------------------------------------------------------


def _verifier_normalisation(normaliser: Any) -> None:
    assert normaliser("Rue de l'Église") == "rue de l eglise"
    assert normaliser("R. DE L EGLISE") == "rue de l eglise"
    assert normaliser("  ALL.  des   Chênes ") == "allee des chenes"
    assert normaliser("Bd Saint-Œuf") == "boulevard saint oeuf"
    # the type is expanded on the first word only; other words stay as written
    assert normaliser("Rue du Bd") == "rue du bd"
    assert normaliser("Rue St Pierre") == "rue st pierre"


def test_normalisation_de_voie() -> None:
    _verifier_normalisation(mesure.normaliser_voie)


def test_une_normalisation_cassee_est_detectee() -> None:
    """Mutation check: every broken variant fails the same assertions."""
    mutants = (
        # no street-type expansion
        mesure.normaliser_libelle,
        # accents and punctuation kept
        lambda t: " ".join(t.lower().split()),
        # expansion on every word, not only the type position
        lambda t: " ".join(
            mesure.TYPES_VOIE_ABREGES.get(w, w)
            for w in mesure.normaliser_libelle(t).split()
        ),
    )
    for mutant in mutants:
        with pytest.raises(AssertionError):
            _verifier_normalisation(mutant)


def _verifier_decoupage(decouper: Any) -> None:
    assert decouper("12 bis RUE DE L'ÉGLISE") == (12, "bis", "rue de l eglise")
    assert decouper("12B r. de l'église") == (12, "b", "rue de l eglise")
    assert decouper("7ter Bd Victor Hugo") == (7, "ter", "boulevard victor hugo")
    assert decouper("0045 AV. Jean Jaurès") == (45, "", "avenue jean jaures")
    # a detached letter stays in the street: never dropped, never guessed
    assert decouper("12 b rue x") == (12, "", "b rue x")
    assert decouper("12-14 rue x") is None
    assert decouper("rue sans numero") is None
    assert decouper(None) is None


def test_decoupage_numero_et_suffixe() -> None:
    _verifier_decoupage(mesure.decouper_adresse)


def test_un_decoupage_qui_perd_le_suffixe_est_detecte() -> None:
    def sans_suffixe(texte: Any) -> Any:
        decoupe = mesure.decouper_adresse(texte)
        return None if decoupe is None else (decoupe[0], "", decoupe[2])

    with pytest.raises(AssertionError):
        _verifier_decoupage(sans_suffixe)


def test_adresse_complete_sans_code_postal_ni_commune() -> None:
    document = {
        "adresse_brut": "LIEU DIT LA HAYE",
        "adresse_complete_brut": "12 Rue de la Paix 44000 Nantes",
        "code_postal_brut": "44000",
        "nom_commune_brut": "Nantes",
    }
    assert mesure.adresse_du_document(document) == (
        (12, "", "rue de la paix"),
        "adresse_complete_brut",
    )


_BAN_LOCALE = [
    {
        "code_commune": "44109",
        "numero": "12",
        "rep": "bis",
        "nom_voie": "Rue de l'Église",
        "code_postal": "44000",
        "ban_id": "44109_a001_00012_bis",
    },
    {
        "code_commune": "44109",
        "numero": "3",
        "rep": "",
        "nom_voie": "Allée des Chênes",
        "code_postal": "44100",
        "ban_id": "44109_a002_00003",
    },
    # the same street name twice in one commune (merged communes): ambiguous
    {
        "code_commune": "44109",
        "numero": "5",
        "rep": None,
        "nom_voie": "Rue du Port",
        "code_postal": "44000",
        "ban_id": "44109_a003_00005",
    },
    {
        "code_commune": "44109",
        "numero": "5",
        "rep": None,
        "nom_voie": "RUE DU PORT",
        "code_postal": "44000",
        "ban_id": "44109_a004_00005",
    },
    # another commune: never a candidate
    {
        "code_commune": "44020",
        "numero": "12",
        "rep": "bis",
        "nom_voie": "Rue de l'Église",
        "code_postal": "44000",
        "ban_id": "44020_a001_00012_bis",
    },
]


def _orphelin(cle: str, adresse: str, code_postal: str = "44000") -> dict[str, Any]:
    return {
        "code_departement": "44",
        "identifiant_ban": cle,
        "adresse_brut": adresse,
        "adresse_complete_brut": None,
        "code_postal_brut": code_postal,
        "nom_commune_brut": "Nantes",
    }


def _orphelins() -> list[dict[str, Any]]:
    return [
        _orphelin("44109", "12 BIS R DE L EGLISE"),  # unique, postcode agrees
        _orphelin("44109_a002", "3 ALL DES CHENES"),  # unique, same street
        _orphelin("44109_zzzz", "3 allée des chênes"),  # unique, other street
        _orphelin("44109_abcdef", "3 all des chenes"),  # unique, new code format
        _orphelin("44109", "5 rue du port"),  # two candidates
        _orphelin("44109", "12 rue de l eglise"),  # suffix never dropped
        _orphelin("44109", "LIEU DIT LA HAYE"),  # no number
        _orphelin("44109_a001_00014", "12 bis rue de l'eglise"),  # 14 != 12
    ]


def _index_ban(documents: list[dict[str, Any]]) -> Any:
    besoins = set()
    for document in documents:
        decoupe, _ = mesure.adresse_du_document(document)
        if decoupe is not None:
            besoins.add((document["identifiant_ban"].split("_")[0], decoupe[2]))
    return mesure.indexer_adresses(_BAN_LOCALE, besoins)


def test_recuperation_par_adresse_brute_exacte() -> None:
    documents = _orphelins()
    index = _index_ban(documents)
    classes = [("44", mesure.classer_document(d, index)) for d in documents]
    resultat, cles = mesure.agreger_recuperables(classes)
    commune = resultat["44"]["commune"]
    assert commune["documents"] == 4
    assert commune["avec_numero"] == 3
    assert commune["sans_numero"] == 1
    assert commune["correspondance_unique"] == 1
    assert commune["correspondances_multiples"] == 1
    assert commune["aucune_correspondance"] == 1
    assert commune["code_postal_brut_concordant_parmi_uniques"] == 1
    assert commune["unique_sans_contradiction"] == 1
    voie = resultat["44"]["voie"]
    assert voie["correspondance_unique"] == 3
    assert voie["voie_de_cle_differente_parmi_uniques"] == 2
    assert voie["dont_voie_differente_meme_format"] == 1
    assert voie["format_voie_indetermine_parmi_uniques"] == 1
    assert voie["unique_sans_contradiction"] == 1
    assert voie["code_postal_brut_concordant_parmi_uniques"] == 0
    numerotee = resultat["44"]["numerotee"]
    assert numerotee["correspondance_unique"] == 1
    assert numerotee["contradiction_numero_cle"] == 1
    assert numerotee["contradiction_numero_cle_parmi_uniques"] == 1
    assert numerotee["unique_sans_contradiction"] == 0
    tous = resultat["44"]["commune_ou_voie"]
    assert tous["documents"] == 7
    assert tous["adresses_distinctes_sans_contradiction"] == 2
    assert cles["44"] == {"44109_a001_00012_bis", "44109_a002_00003"}
    assert cles["44:numerotee"] == set()


def test_recuperation_dedoublonne_deux_lignes_ban_strictement_identiques() -> None:
    document = _orphelin("44109", "12 bis rue de l eglise")
    index = mesure.indexer_adresses(
        [_BAN_LOCALE[0], dict(_BAN_LOCALE[0])], {("44109", "rue de l eglise")}
    )
    assert (
        mesure.classer_document(document, index)["verdict"] == "correspondance_unique"
    )


def test_recuperation_compare_suffixe_de_la_cle_ademe() -> None:
    document = _orphelin("44109_a001_00012_ter", "12 bis rue de l eglise")
    classe = mesure.classer_document(document, _index_ban([document]))
    assert classe["contradiction_suffixe"] is True
    resultat, cles = mesure.agreger_recuperables([("44", classe)])
    assert resultat["44"]["numerotee"]["unique_sans_contradiction"] == 0
    assert cles["44:numerotee"] == set()


def test_suffixe_de_cle_non_interpretable_reste_indetermine() -> None:
    document = _orphelin("44109_a001_00012_inconnu", "12 bis rue de l eglise")
    classe = mesure.classer_document(document, _index_ban([document]))
    assert classe["contradiction_suffixe"] is None
    resultat, cles = mesure.agreger_recuperables([("44", classe)])
    numerotee = resultat["44"]["numerotee"]
    assert numerotee["suffixe_cle_ininterpretable_parmi_uniques"] == 1
    assert numerotee["unique_sans_contradiction"] == 0
    assert cles["44:numerotee"] == set()


def test_code_voie_de_format_different_reste_indetermine() -> None:
    document = _orphelin("44109_abcdef", "3 allée des chênes")
    classe = mesure.classer_document(document, _index_ban([document]))
    assert classe["voie_de_cle"] == "format_different"
    resultat, cles = mesure.agreger_recuperables([("44", classe)])
    voie = resultat["44"]["voie"]
    assert voie["format_voie_indetermine_parmi_uniques"] == 1
    assert voie["unique_sans_contradiction"] == 0
    assert cles["44"] == set()


@pytest.mark.parametrize(
    ("adresse", "verdict"),
    [
        ("rue sans numero", "sans_numero"),
        ("Appartement 3, 12 rue des Lilas", "format_non_reconnu"),
        ("12-14 rue des Lilas", "plage_numeros"),
    ],
)
def test_echec_extraction_distingue_absence_format_et_plage(
    adresse: str, verdict: str
) -> None:
    assert (
        mesure.classer_document(_orphelin("44109", adresse), {})["verdict"] == verdict
    )


def test_absence_reelle_ne_prend_pas_le_code_postal_pour_un_numero() -> None:
    document = {
        **_orphelin("44109", "rue sans numero"),
        "adresse_complete_brut": "rue sans numero 44000 Nantes",
        "code_postal_brut": "44000",
        "nom_commune_brut": "Nantes",
    }
    assert mesure.classer_document(document, {})["verdict"] == "sans_numero"


def test_agreger_rnb() -> None:
    lignes = [
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "id_rnb_renseigne": True,
            "provenance": "Reprise RNB",
            "effectif": 3,
        },
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "id_rnb_renseigne": False,
            "provenance": None,
            "effectif": 2,
        },
        {
            "code_departement": "44",
            "type_batiment": "appartement",
            "id_rnb_renseigne": True,
            "provenance": "BDNB",
            "effectif": 1,
        },
        {
            "code_departement": "44",
            "type_batiment": "appartement",
            "id_rnb_renseigne": False,
            "provenance": "",
            "effectif": 4,
        },
    ]
    faits = [
        _fait(
            type_batiment="maison",
            batiments_parcelle="sans_parcelle",
            id_rnb_renseigne=True,
        ),
        _fait(type_batiment="maison", batiments_parcelle="sans_parcelle"),
        _fait(type_batiment="maison", batiments_parcelle="1", id_rnb_renseigne=True),
    ]
    rnb = mesure.agreger_rnb(lignes, faits)["44"]
    assert rnb["documents"] == 10
    assert rnb["id_rnb_renseigne"] == 4
    assert {k: v["documents"] for k, v in rnb["par_provenance"].items()} == {
        "(null)": 2,
        "(vide)": 4,
        "BDNB": 1,
        "Reprise RNB": 3,
    }
    assert rnb["par_type_batiment"]["maison"]["id_rnb_renseigne"] == 3
    assert rnb["maisons_point_hors_parcelle"] == {
        "maisons": 2,
        "dont_id_rnb_renseigne": 1,
        "part": 0.5,
    }


def test_agreger_d109() -> None:
    def ligne(plusieurs: bool, unique: bool, type_recent: str, n: int) -> Any:
        return {
            "code_departement": "44",
            "plusieurs": plusieurs,
            "recent_unique": unique,
            "type_recent": type_recent,
            "effectif": n,
        }

    d109 = mesure.agreger_d109(
        [
            ligne(False, True, "maison", 10),
            ligne(True, True, "appartement", 6),
            ligne(True, False, "appartement", 2),
            ligne(True, True, "maison", 1),
            ligne(True, False, "mixte", 1),
        ]
    )["44"]
    assert d109["adresses_2_dpe_et_plus"] == 10
    assert d109["plus_recent_appartement"] == 8
    assert d109["part_plus_recent_appartement"] == 0.8
    assert d109["plus_recent_a_egalite_de_date"] == 3
    assert d109["egalite_de_date_mixte"] == 1


def test_d109_sans_date_n_est_pas_une_egalite() -> None:
    ligne = {
        "code_departement": "44",
        "plusieurs": True,
        "recent_unique": False,
        "type_recent": "sans_date",
        "effectif": 1,
    }
    d109 = mesure.agreger_d109([ligne])["44"]
    assert d109["plus_recent_sans_date"] == 1
    assert d109.get("plus_recent_a_egalite_de_date", 0) == 0


_BAN_BRUTE = [
    {"id": "K1", "cad_parcelles": "P1", "type_position": "entrée"},
    {"id": "K2", "cad_parcelles": "P2|P3", "type_position": "entrée"},
    {"id": "K3", "cad_parcelles": "P4", "type_position": "segment"},
    {"id": "K4", "cad_parcelles": "", "type_position": "entrée"},
    {"id": "KX", "cad_parcelles": "P9", "type_position": "entrée"},
]


def _maison_cle(
    cle: str, n_docs: int, n_maison: int, point: str | None, nb: int, nha: int
) -> dict[str, Any]:
    return {
        "code_departement": "44",
        "identifiant_ban": cle,
        "n_docs": n_docs,
        "n_maison": n_maison,
        "parcelle_point": point,
        "n_batiments": nb if point else None,
        "n_hors_annexe": nha if point else None,
    }


def test_lire_ban_brute_et_recalcul_d110() -> None:
    maisons = [
        _maison_cle("K1", 1, 1, None, 0, 0),  # outside, cad P1 (1 building)
        _maison_cle("K2", 1, 1, "P2", 1, 1),  # 2 cad parcels -> point rule
        _maison_cle("K3", 2, 2, "P5", 1, 1),  # cad P4 wins over the point
        _maison_cle("K4", 1, 1, None, 0, 0),  # outside, empty cad
        _maison_cle("K5", 1, 1, None, 0, 0),  # outside, absent from the CSV
    ]
    lu = mesure.lire_ban_brute(_BAN_BRUTE, {m["identifiant_ban"] for m in maisons})
    assert lu["compte"]["adresses_csv"] == 5
    assert lu["compte"]["cad_parcelles_renseigne"] == 4
    assert lu["compte"]["cad_2_parcelles_et_plus"] == 1
    assert lu["references"] == {"P1", "P2", "P3", "P4", "P9"}
    assert set(lu["par_cle"]) == {"K1", "K2", "K3", "K4"}
    r = mesure.recalculer_d110(maisons, lu["par_cle"], {"P1": (1, 1), "P4": (2, 1)}, 10)
    assert r["maisons"] == 6
    assert r["cles_maison_absentes_du_csv"] == 1
    assert r["maisons_point_hors_parcelle"] == 3
    assert r["hors_parcelle_cad_unique_trouvee"] == 1
    assert r["hors_parcelle_cad_un_batiment"] == 1
    assert (r["source_cad_parcelles"], r["source_point"]) == (3, 1)
    assert r["source_indeterminee"] == 2
    assert (r["ii_point"], r["iii_point"]) == (3, 1)
    assert (r["ii_regle"], r["ii_regle_hors_annexes"]) == (2, 4)
    assert (r["iii_regle"], r["iii_regle_hors_annexes"]) == (2, 2)
    assert r["cles_point_et_cad_unique"] == 1
    assert r["cles_point_et_cad_meme_parcelle"] == 0
    assert r["hors_parcelle_par_type_position"] == {
        "entrée": 2,
        "(absente du csv)": 1,
    }
    assert r["hors_parcelle_par_etat_cad_parcelles"] == {
        "1": 1,
        "absente_du_csv": 1,
        "vide": 1,
    }
    assert r["parts"]["iii_regle_sur_adresses"] == 0.2


# ---------------------------------------------------------------------------
# (b) read-only connection enforced by main
# ---------------------------------------------------------------------------


class _Resultat:
    def __init__(self, lignes: list[dict[str, Any]]) -> None:
        self._lignes = lignes

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._lignes)

    def fetchone(self) -> dict[str, Any] | None:
        return self._lignes[0] if self._lignes else None

    def __iter__(self) -> Any:
        return iter(list(self._lignes))


class _FausseConnexion:
    def __init__(
        self,
        reponses: dict[str, list[dict[str, Any]]] | None = None,
        etat_lecture: str = "on",
    ) -> None:
        self.reponses = reponses or {}
        self.requetes_emises: list[str] = []
        self.etat_lecture = etat_lecture
        self.read_only = False
        self.isolation_level = None
        self.fermee = False

    def execute(self, sql: str, params: Any = None) -> _Resultat:
        self.requetes_emises.append(sql)
        if _normalise(sql) == _normalise(verify_golden_cases.READ_ONLY_STATE_SQL):
            return _Resultat([{"state": self.etat_lecture}])
        return _Resultat(self.reponses.get(_normalise(sql), []))

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.fermee = True


def test_main_refuse_une_connexion_en_ecriture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _FausseConnexion(etat_lecture="off")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setattr(verify_golden_cases.psycopg, "connect", lambda *a, **k: conn)
    assert mesure.main(["--base", "--json", "-"]) == 1
    assert conn.fermee
    sortie = capsys.readouterr()
    assert sortie.out == ""
    assert conn.requetes_emises == [verify_golden_cases.READ_ONLY_STATE_SQL]


# ---------------------------------------------------------------------------
# (c) every statement is allowlisted
# ---------------------------------------------------------------------------


def test_toutes_les_requetes_passent_la_garde_reelle() -> None:
    assert mesure.ALLOWED_SQL
    for sql in mesure.ALLOWED_SQL:
        assert_select_only(sql, mesure.ALLOWED_SQL)
        texte = _normalise(sql).upper()
        assert texte.startswith(("SELECT", "WITH"))
        for verbe in (
            "INSERT",
            "UPDATE",
            "DELETE",
            "CREATE",
            "ALTER",
            "DROP",
            "TRUNCATE",
            "COPY",
            "GRANT",
        ):
            assert f" {verbe} " not in f" {texte} "

    conn = _FausseConnexion()
    mesure.mesurer_base(conn, ["44"], date(2026, 9, 30))
    mesure.mesurer_capture(conn, [], ["44", "85"])
    assert conn.requetes_emises
    for sql in conn.requetes_emises:
        assert_select_only(sql, mesure.ALLOWED_SQL)


def test_une_requete_hors_liste_est_refusee() -> None:
    with pytest.raises(GoldenSecurityError):
        mesure._executer(_FausseConnexion(), "SELECT 1", {})


# ---------------------------------------------------------------------------
# (d) no identifier in the output
# ---------------------------------------------------------------------------


def _reponses_empoisonnees() -> dict[str, list[dict[str, Any]]]:
    faits = [
        dict(f, numero_dpe=POISON_DPE, identifiant_ban=POISON_BAN)
        for f in _faits_synthetiques()
    ]
    return {
        _normalise(mesure.FAITS_DOCUMENTS_BASE_SQL): faits,
        _normalise(mesure.FAITS_DOCUMENTS_CAPTURE_SQL): faits,
        _normalise(mesure.FORMES_NUMERO_DPE_SQL): [
            {
                "code_departement": "44",
                "conforme": True,
                "forme_ademe": True,
                "departement_concorde": True,
                "longueur": 13,
                "a_chiffre": True,
                "a_majuscule": True,
                "a_minuscule": False,
                "a_autre": False,
                "effectif": 1,
                "numero_dpe": POISON_DPE,
            },
        ],
        _normalise(mesure.FORMES_CLES_BAN_SQL): [
            {
                "origine": "adresses",
                "code_departement": "44",
                "absente": False,
                "conforme": True,
                "longueur": 16,
                "segments": 3,
                "a_minuscule": False,
                "a_majuscule": False,
                "a_autre": False,
                "effectif": 1,
                "ban_id": POISON_BAN,
            },
        ],
        _normalise(mesure.CAPTURE_DOCUMENTS_SQL): [
            {
                "numero_dpe": POISON_DPE,
                "code_departement": "44",
                "etiquette_dpe": "D",
                "surface_habitable": Decimal("50"),
                "date_etablissement_dpe": date(2024, 1, 2),
            },
        ],
        _normalise(mesure.DATE_IMPORT_SQL): [{"termine_le": None}],
        _normalise(mesure.RUN_BAN_SQL): [{"id": 32, "completed_at": None}],
        _normalise(mesure.ADRESSES_PAR_DEPARTEMENT_SQL): [
            {"code_departement": "44", "effectif": 5}
        ],
        _normalise(mesure.DOCUMENTS_ORPHELINS_SQL): [
            *_orphelins(),
            _orphelin(POISON_BAN, f"12 rue {POISON_DPE}"),
        ],
        _normalise(mesure.ADRESSES_COMMUNES_SQL): _BAN_LOCALE,
        _normalise(mesure.CLES_AVEC_DPE_SQL): [{"cles": 2, "cles_avec_dpe": 1}],
        _normalise(mesure.RNB_SQL): [
            {
                "code_departement": "44",
                "type_batiment": "maison",
                "id_rnb_renseigne": True,
                "provenance": "Reprise RNB",
                "effectif": 2,
                "id_rnb": POISON_DPE,
            }
        ],
        _normalise(mesure.D109_SQL): [
            {
                "code_departement": "44",
                "plusieurs": True,
                "recent_unique": True,
                "type_recent": "appartement",
                "effectif": 1,
                "identifiant_ban": POISON_BAN,
            }
        ],
        _normalise(mesure.MAISONS_CLES_SQL): [
            _maison_cle(POISON_BAN, 1, 1, None, 0, 0),
            _maison_cle("44021_0104_00275", 1, 1, "44021000AB0002", 2, 1),
        ],
        _normalise(mesure.PARCELLES_TROUVEES_SQL): [
            {"references_distinctes": 2, "trouvees": 2}
        ],
        _normalise(mesure.BATIMENTS_PARCELLES_SQL): [
            {"id_parcelle": "44021000AB0001", "n_batiments": 1, "n_hors_annexe": 1}
        ],
    }


def _archive_ban(tmp_path: Path) -> Path:
    """A real source archive (sentinel, object, record) holding one raw BAN
    CSV, written by the archive module itself."""
    from app.etl import source_archive

    racine = tmp_path / "archive"
    racine.mkdir()
    (racine / source_archive.SENTINEL_NAME).write_text(
        source_archive.SENTINEL_CONTENT, encoding="utf-8"
    )
    telechargement = tmp_path / "dl"
    (telechargement / "raw").mkdir(parents=True)
    fichier = telechargement / "raw" / "adresses-44.csv"
    fichier.write_text(
        "id;numero;type_position;cad_parcelles\n"
        f"{POISON_BAN};273;entrée;44021000AB0001\n"
        "44021_0104_00275;275;entrée;44021000AB0002|44021000AB0003\n",
        encoding="utf-8",
    )
    source_archive.archive_sources(
        racine,
        "ban",
        "32-20260925T211831Z",
        [fichier],
        telechargement,
        "telechargement",
    )
    return racine


def test_blocs_h_i_j_sur_une_archive_reelle(tmp_path: Path) -> None:
    conn = _FausseConnexion(_reponses_empoisonnees())
    rapport = mesure.mesurer_base(
        conn, ["44"], date(2026, 9, 30), _archive_ban(tmp_path)
    )
    j = rapport["cad_parcelles"]
    assert j["statut"] == "mesure"
    assert j["run_ban"] == "32-20260925T211831Z"
    dep = j["departements"]["44"]
    assert dep["adresses_csv"] == 2
    assert dep["cad_2_parcelles_et_plus"] == 1
    assert dep["parcelles_referencees_distinctes"] == 3
    assert dep["d110_recalcule"]["hors_parcelle_cad_un_batiment"] == 1
    h = rapport["recuperation_adresse_brute"]["departements"]["44"]
    assert h["numerotee"]["documents"] == 2
    assert h["commune_ou_voie"]["adresses_sans_contradiction_ayant_deja_un_dpe"] == 1
    assert rapport["id_rnb"]["44"]["id_rnb_renseigne"] == 2
    assert rapport["d109_plus_recent"]["44"]["plus_recent_appartement"] == 1
    emises = {_normalise(sql) for sql in conn.requetes_emises}
    for sql in (
        mesure.RUN_BAN_SQL,
        mesure.DOCUMENTS_ORPHELINS_SQL,
        mesure.ADRESSES_COMMUNES_SQL,
        mesure.CLES_AVEC_DPE_SQL,
        mesure.RNB_SQL,
        mesure.D109_SQL,
        mesure.MAISONS_CLES_SQL,
        mesure.PARCELLES_TROUVEES_SQL,
        mesure.BATIMENTS_PARCELLES_SQL,
        mesure.ADRESSES_PAR_DEPARTEMENT_SQL,
    ):
        assert _normalise(sql) in emises
    for sql in conn.requetes_emises:
        assert_select_only(sql, mesure.ALLOWED_SQL)
    texte = mesure.serialiser(rapport)
    assert not MOTIF_DPE.findall(texte)
    assert not MOTIF_BAN.findall(texte)


def test_bloc_j_non_mesure_sans_archive(tmp_path: Path) -> None:
    conn = _FausseConnexion(_reponses_empoisonnees())
    assert mesure.mesurer_cad_parcelles(conn, ["44"], {}, {}, None) == {
        "statut": "non_mesure",
        "raison": "SOURCE_ARCHIVE_DIR absent",
    }
    # an empty directory (unmounted disk) is refused by the sentinel check
    j = mesure.mesurer_cad_parcelles(conn, ["44"], {}, {}, tmp_path)
    assert j["statut"] == "non_mesure"
    assert "sentinelle_absente" in j["raison"] or "record_absent" in j["raison"]


def test_sortie_json_sans_identifiant() -> None:
    conn = _FausseConnexion(_reponses_empoisonnees())
    rapport = mesure.mesurer_base(conn, ["44"], date(2026, 9, 30))
    rapport["capture"] = mesure.mesurer_capture(
        conn,
        [
            {
                "numero": POISON_DPE,
                "code_insee": "44109",
                "etiquette": "D",
                "surface": "50",
                "date": "2024-01-02",
            }
        ],
        ["44", "85"],
    )
    texte = mesure.serialiser(rapport)
    assert not MOTIF_DPE.findall(texte)
    assert not MOTIF_BAN.findall(texte)
    assert rapport["capture"]["retrouves_en_base"] == 1


def test_serialiser_refuse_un_identifiant() -> None:
    with pytest.raises(GoldenSecurityError):
        mesure.serialiser({"fuite": POISON_DPE})
    with pytest.raises(GoldenSecurityError):
        mesure.serialiser({"fuite": POISON_BAN})
    # six-character voie segment (real BAN shape) and two-segment key
    for cle in ("85191_b0x4ng_00012", "85191_b0x4ng_00012_bis", "44109_0012"):
        with pytest.raises(GoldenSecurityError):
            mesure.serialiser({"fuite": cle})
    # aggregated keys and shape labels stay publishable
    mesure.serialiser({"iii_cumul_hors_annexes": 1, "profil": "5-6-5"})


# ---------------------------------------------------------------------------
# (e) usage
# ---------------------------------------------------------------------------


def test_sans_argument_sortie_2_avec_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert mesure.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()

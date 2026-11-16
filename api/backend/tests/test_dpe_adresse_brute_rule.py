"""One raw-address rule for what is measured and what is imported (04.1-13).

Block H of ``scripts/mesure_dpe_documents.py`` measured the gain of the
attachment (04-MESURE-DPE); the DPE import applies it
(``app.etl_dpe.adresse_brute``). The script imports the rule from the import
package: the same objects, so no drift is possible. This file proves it by
identity, then compares the outputs on labels covering every normalisation
step (accents, œ/æ, street-type abbreviations, attached and detached
suffixes, leading complements, ranges), so that a later copy in the script
would be caught on behaviour too.
"""

import pytest

from app.etl_dpe import adresse_brute
from scripts import mesure_dpe_documents as mesure

pytestmark = pytest.mark.unit

PARTAGES = (
    "REGLE_NORMALISATION",
    "TYPES_CLE",
    "VERDICTS_H",
    "TYPES_VOIE_ABREGES",
    "adresse_du_document",
    "classer_document",
    "decouper_adresse",
    "indexer_adresses",
    "normaliser_libelle",
    "normaliser_voie",
    "sans_contradiction",
)
LIBELLES = (
    "Rue de l'Église",
    "R. DE L EGLISE",
    "  ALL.  des   Chênes ",
    "Bd Saint-Œuf",
    "Æther pl",
    "Rue du Bd",
    "12 bis RUE DE L'ÉGLISE",
    "12bis rue de l'église",
    "12B r. de l'église",
    "12 b rue x",
    "7ter Bd Victor Hugo",
    "7 quinquies imp des Lilas",
    "0045 AV. Jean Jaurès",
    "Appartement 3, 12 rue des Lilas",
    "Bât. B 4 chem du Moulin",
    "12-14 rue des Lilas",
    "12/14 rue des Lilas",
    "rue sans numero",
    "",
    None,
)


@pytest.mark.parametrize("nom", PARTAGES)
def test_the_script_uses_the_import_rule_itself(nom):
    assert getattr(mesure, nom) is getattr(adresse_brute, nom)


def test_the_rule_text_names_every_abbreviation():
    for abrege, developpe in adresse_brute.TYPES_VOIE_ABREGES.items():
        assert f"{abrege}={developpe}" in mesure.REGLE_NORMALISATION


@pytest.mark.parametrize("libelle", LIBELLES)
def test_both_readings_agree_on_every_label(libelle):
    for fonction in ("normaliser_libelle", "normaliser_voie", "decouper_adresse"):
        assert getattr(mesure, fonction)(libelle) == getattr(adresse_brute, fonction)(
            libelle
        )
    document = {"adresse_brut": libelle, "adresse_complete_brut": libelle}
    lue = adresse_brute.lire_adresse_brute(libelle, libelle, None, None)
    decoupe, cas = mesure.adresse_du_document(document)
    if decoupe is None:
        assert lue.cas == cas and lue.numero is None
    else:
        assert (lue.cas, lue.numero, lue.suffixe, lue.voie) == ("numero", *decoupe)


def test_the_reading_keeps_the_three_failures_apart():
    lire = adresse_brute.lire_adresse_brute
    assert lire("rue sans numero", None, None, None).cas == "sans_numero"
    assert lire("Appartement 3, 12 rue des Lilas", None, None, None).cas == (
        "format_non_reconnu"
    )
    assert lire("12-14 rue des Lilas", None, None, None).cas == "plage_numeros"
    # The complete address carries the number once its postcode and commune go.
    lue = lire("Appartement 3", "12 r des Lilas 44000 Nantes", "44000", "Nantes")
    assert (lue.cas, lue.numero, lue.suffixe, lue.voie, lue.source) == (
        "numero",
        12,
        "",
        "rue des lilas",
        "adresse_complete_brut",
    )

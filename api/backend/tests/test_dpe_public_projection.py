"""Public projection of the declared address and the concordance rule (04-19).

P-ADR-01 (Ianis, 2026-10-02): the raw address is published as typed only
when it is established without complement; otherwise the whole field is
masked with its reason. The concordance is the single rule of
``app.dpe.concordance``, at parity with the measurement script (N-12).
"""

from typing import get_args

import pytest

from app.adresse.schemas import AdresseDeclaree, MotifAdresseDeclaree
from app.dpe import concordance
from app.dpe.concordance import FaitsConcordance, concordance_adresse
from app.dpe.public_projection import (
    COLONNES_COMPLEMENT,
    MOTIFS_ADRESSE_DECLAREE,
    AdresseBruteDocument,
    IdentiteBan,
    projeter_adresse_declaree,
)
from scripts import mesure_dpe_documents, mesure_dpe_maison

pytestmark = pytest.mark.unit

LILAS = IdentiteBan(
    nom_voie="Rue des Lilas", nom_commune="La Roche-sur-Yon", code_postal="85000"
)
SENTINELLE = "SENTINELLE-0419"


def _projeter(brut, *, complete=None, complement=None, identite=LILAS):
    return projeter_adresse_declaree(
        AdresseBruteDocument(
            adresse_brut=brut,
            adresse_complete_brut=complete,
            complements={"complement_adresse_logement": complement},
        ),
        identite,
    )


# --- published: established without complement -------------------------------


@pytest.mark.parametrize(
    "brut",
    [
        "14 rue des Lilas",
        "12 bis rue des Lilas",
        "12B Rue des Lilas",
        "14, rue des Lilas",
        "14 RUE DES LILAS 85000",
        "14 rue des Lilas 85000 La Roche-sur-Yon",
        "14 rue des Lilas La Roche sur Yon",
    ],
)
def test_an_address_established_without_complement_is_published_as_typed(brut):
    projection = _projeter(brut)

    assert projection == AdresseDeclaree(etat="publiee", texte=brut, motif=None)


def test_the_published_text_is_never_corrected_nor_replaced_by_the_ban():
    projection = _projeter("  14 Rue des LILAS ")

    assert projection.texte == "  14 Rue des LILAS "


# --- masked: not verifiable without complement --------------------------------


@pytest.mark.parametrize(
    "brut",
    [
        f"12 rue des Lilas {SENTINELLE}",  # a remaining token
        "Appartement 3 12 rue des Lilas",  # no leading number
        "12 rue du Port",  # another street
        "12-14 rue des Lilas",  # a range is not decomposable
        "12 rue des Lilas (M. Dupont)",  # character outside the admitted set
        "12 rue des Lilas 张伟",  # erased by the normalisation: still refused
        "12 rue des Lilas 44000 Nantes",  # another commune stays in the street
        "12345 rue des Lilas",  # five digits are not a number of the BAN
        "12 ter",  # no street
        "rue des Lilas",
        # A trailing five-digit token that is not the BAN postcode (an entry
        # code, a phone fragment): never published (decision n° 25, API WR-02).
        "14 rue des Lilas 45678",
        "14 rue des Lilas 06123",
        "14 rue des Lilas 45678 La Roche-sur-Yon",
    ],
)
def test_a_text_not_entirely_decomposed_on_the_ban_street_is_masked(brut):
    projection = _projeter(brut)

    assert projection == AdresseDeclaree(
        etat="masquee", texte=None, motif="non_verifiable_sans_complement"
    )


def test_unknown_ban_street_masks_every_text():
    projection = _projeter("12 rue des Lilas", identite=IdentiteBan(nom_voie=None))

    assert projection.etat == "masquee"
    assert projection.motif == "non_verifiable_sans_complement"


# --- masked: a complement is stored -------------------------------------------


def test_a_stored_complement_masks_the_whole_field():
    projection = _projeter("14 rue des Lilas", complement=f"porte {SENTINELLE}")

    assert projection == AdresseDeclaree(
        etat="masquee", texte=None, motif="complement_present"
    )
    assert SENTINELLE not in projection.model_dump_json()


@pytest.mark.parametrize(
    "complete",
    [
        "14 rue des Lilas Appartement 4 85000 La Roche-sur-Yon",
        "14 rue des Lilas chez Mme Martin",
        "14 rue des Lilas Bat B",
    ],
)
def test_complement_words_of_the_full_raw_address_only_add_a_refusal(complete):
    projection = _projeter("14 rue des Lilas", complete=complete)

    assert projection.etat == "masquee"
    assert projection.motif == "complement_present"


def test_the_full_raw_address_is_never_published():
    projection = _projeter(
        "14 rue des Lilas", complete=f"14 rue des Lilas {SENTINELLE} 85000"
    )

    assert projection == AdresseDeclaree(etat="publiee", texte="14 rue des Lilas")
    assert SENTINELLE not in projection.model_dump_json()


@pytest.mark.parametrize("brut", [None, "", "   "])
def test_an_empty_text_is_absent(brut):
    assert _projeter(brut) == AdresseDeclaree(etat="absente", texte=None, motif=None)


def test_the_reasons_are_the_closed_list_of_the_schema():
    assert MOTIFS_ADRESSE_DECLAREE == (
        "complement_present",
        "non_verifiable_sans_complement",
    )
    assert get_args(MotifAdresseDeclaree) == MOTIFS_ADRESSE_DECLAREE
    assert COLONNES_COMPLEMENT == ("complement_adresse_logement",)


def test_the_facts_query_reads_every_complement_column():
    """API IN-04: the SQL is built from the tuple, never written apart."""
    for colonne in COLONNES_COMPLEMENT:
        assert f"d.{colonne}" in concordance.FAITS_DOCUMENTS_SQL


def test_a_complement_word_inside_the_ban_street_is_not_a_complement():
    """API IN-03: « rue de la Porte » is a street, not a door."""
    porte = IdentiteBan(nom_voie="Rue de la Porte", code_postal="44000")

    publiee = _projeter(
        "12 rue de la Porte",
        complete="12 rue de la Porte 44000 Nantes",
        identite=porte,
    )
    refusee = _projeter(
        "12 rue de la Porte",
        complete="12 rue de la Porte porte 3 44000 Nantes",
        identite=porte,
    )

    assert publiee == AdresseDeclaree(etat="publiee", texte="12 rue de la Porte")
    assert (refusee.etat, refusee.motif) == ("masquee", "complement_present")


# --- strict decomposition ------------------------------------------------------


def test_the_decomposition_accounts_for_every_token():
    decomposee = concordance.extraire_adresse_brute(
        "12 bis, Rue des Lilas 85000 La Roche-sur-Yon", commune="La Roche-sur-Yon"
    )

    assert decomposee == concordance.AdresseBrute(
        numero="12",
        suffixe="bis",
        voie="rue des lilas",
        code_postal="85000",
        commune="la roche sur yon",
    )


# --- concordance: the script's rule ---------------------------------------------


def test_the_concordance_regexes_are_the_script_ones():
    assert concordance.NUMERO_TETE_RE == mesure_dpe_maison.NUMERO_TETE_RE
    assert concordance.ADRESSE_SQL_RE == mesure_dpe_documents.ADRESSE_SQL_RE
    for colonne in ("a.nom_voie", "d.adresse_brut"):
        assert concordance.normaliser_voie_sql(
            colonne
        ) == mesure_dpe_documents.normaliser_voie_sql(colonne)


def _faits(**valeurs) -> FaitsConcordance:
    base = {
        "numero_brut": 12,
        "numero_ban": 12,
        "numero_ban_interpretable": True,
        "suffixe_brut": None,
        "suffixe_ban": None,
        "suffixe_ban_interpretable": True,
        "adresse_brute_interpretable": True,
        "voie_ban_normalisee": "rue du port",
        "voie_brut_normalisee": "rue du port",
    }
    return FaitsConcordance(**{**base, **valeurs})


@pytest.mark.parametrize(
    ("valeurs", "verdict"),
    [
        ({}, "concordant"),
        ({"suffixe_brut": "bis"}, "ecart"),
        ({"numero_brut": 14}, "ecart"),
        ({"voie_brut_normalisee": "rue du portail"}, "ecart"),
        ({"numero_brut": None}, "non_evaluable"),
        ({"numero_ban": None}, "non_evaluable"),
        ({"numero_ban_interpretable": False}, "non_evaluable"),
        ({"suffixe_ban_interpretable": False}, "non_evaluable"),
        ({"adresse_brute_interpretable": False}, "non_evaluable"),
        ({"voie_ban_normalisee": None}, "non_evaluable"),
        ({"voie_brut_normalisee": None}, "non_evaluable"),
    ],
)
def test_the_concordance_needs_number_suffix_and_whole_street(valeurs, verdict):
    assert concordance_adresse(_faits(**valeurs)) == verdict


def test_a_typed_postcode_without_a_known_ban_postcode_masks_the_text():
    """Decision n° 25: published only when equal to the BAN postcode."""
    sans_code = IdentiteBan(nom_voie="Rue des Lilas", nom_commune="La Roche-sur-Yon")

    assert _projeter("14 rue des Lilas 85000", identite=sans_code).etat == "masquee"
    assert _projeter("14 rue des Lilas", identite=sans_code).etat == "publiee"

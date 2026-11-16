"""The single projection of a /v1 value into the five MCP states (D-05, D-06).

Pure: no application, no client. The table of D-05 is played row by row, and
the ``Mesure`` model is shown to refuse every shape that would let an absence
be read as a number.
"""

import pytest
from pydantic import ValidationError

from app.mcp.contrat import (
    MOTIFS_DU_MANQUE,
    Mesure,
    ValeurEcartee,
    echelle_du_rattachement,
    mesure,
    source_du_catalogue,
)

pytestmark = pytest.mark.unit

DVF = source_du_catalogue("dvf")


def _mesure(**champs):
    base = {
        "champ": "dvf.nb_mutations_12m",
        "etat_v1": "value",
        "valeur": 12,
        "motif_v1": None,
        "source": DVF,
        "date": "2025",
        "echelle": "commune",
    }
    return mesure(**{**base, **champs})


def test_a_served_value_and_a_measured_zero():
    assert (_mesure().etat, _mesure().valeur) == ("servi", 12)
    zero = _mesure(valeur=0)
    assert (zero.etat, zero.valeur, zero.motif) == ("zero", 0, None)


def test_a_zero_the_contract_calls_implausible_is_missing():
    # dvf.prix_m2_median: a median price of 0 is not a measure.
    absurde = _mesure(champ="dvf.prix_m2_median", valeur=0)
    assert (absurde.etat, absurde.valeur, absurde.motif) == (
        "manquant",
        None,
        "zero_non_plausible",
    )


def test_a_boolean_false_is_served_not_zero():
    faux = _mesure(champ="adresse.connectivite.eligibilite_fibre", valeur=False)
    assert (faux.etat, faux.valeur) == ("servi", False)


@pytest.mark.parametrize(("etat_v1", "motif"), sorted(MOTIFS_DU_MANQUE.items()))
def test_every_absence_state_is_missing_without_value(etat_v1, motif):
    # Even a 0 sent next to an absence state is dropped: the state is the truth.
    manque = _mesure(etat_v1=etat_v1, valeur=0, motif_v1="cause de la source")
    assert (manque.etat, manque.valeur, manque.motif) == ("manquant", None, motif)
    assert manque.precision == "cause de la source"
    assert "valeur" not in manque.model_dump(exclude_none=True)


def test_an_outage_is_its_own_state_never_a_lack_nor_a_zero():
    # I-4 (Ianis, 2026-11-15, « Cinq, comme I-4 »): an outage is a fifth state.
    panne = _mesure(etat_v1="temporary_error", valeur=0, motif_v1="Délai dépassé.")
    assert (panne.etat, panne.valeur, panne.motif, panne.precision) == (
        "panne",
        None,
        "source_indisponible",
        "Délai dépassé.",
    )
    assert "temporary_error" not in MOTIFS_DU_MANQUE


def test_out_of_coverage_keeps_its_d24_reason():
    hors = _mesure(
        etat_v1="out_of_coverage", valeur=None, motif_v1="dataset_non_heberge"
    )
    assert (hors.etat, hors.motif, hors.valeur) == (
        "hors_couverture",
        "dataset_non_heberge",
        None,
    )


def test_a_null_without_declared_reason_is_missing():
    manque = _mesure(etat_v1=None, valeur=None)
    assert (manque.etat, manque.motif) == ("manquant", "raison_non_declaree")


def test_unknown_date_is_said_never_left_out():
    assert _mesure(date=None).date == "inconnue"


@pytest.mark.parametrize(
    ("changement", "raison"),
    [
        ({"source": None}, "source_inconnue"),
        ({"echelle": None}, "echelle_inconnue"),
        ({"etat_v1": "value", "valeur": None}, "valeur_absente_pour_l_etat_value"),
        ({"etat_v1": "inventé"}, "etat_inconnu:inventé"),
        ({"valeur": ["a", "b"]}, "valeur_non_scalaire"),
    ],
)
def test_a_value_without_proof_is_set_aside(changement, raison):
    with pytest.raises(ValeurEcartee) as ecart:
        _mesure(**changement)
    assert ecart.value.raison == raison


@pytest.mark.parametrize(
    "forme",
    [
        {"etat": "manquant", "valeur": 0, "motif": "non_renseigne"},
        {"etat": "hors_couverture", "valeur": 0, "motif": "dataset_non_heberge"},
        {"etat": "panne", "valeur": 0, "motif": "source_indisponible"},
        {"etat": "panne"},
        {"etat": "manquant"},
        {"etat": "servi", "valeur": 0},
        {"etat": "zero", "valeur": 3},
        {"etat": "servi"},
        {"etat": "servi", "valeur": 3, "motif": "non_renseigne"},
    ],
)
def test_the_model_refuses_a_number_in_an_absence(forme):
    with pytest.raises(ValidationError):
        Mesure(
            champ="dvf.nb_mutations_12m",
            echelle="commune",
            source=DVF,
            date="2025",
            **forme,
        )


@pytest.mark.parametrize(
    ("rattachement", "champ", "echelle"),
    [
        ("parcelle_du_point", "x", "parcelle"),
        ("rayon_du_point", "x", "rayon"),
        ("batiment_rnb_de_l_adresse", "x", "batiment"),
        ("au_point", "x", "adresse"),
        ("commune_reference", "x", "commune"),
        # No attachment on the value: the one of its contract entry.
        (None, "adresse.dvf.ventes_parcelle.nombre", "parcelle"),
        (None, "champ.sans.contrat", None),
    ],
)
def test_scale_from_the_attachment(rattachement, champ, echelle):
    assert echelle_du_rattachement(rattachement, champ) == echelle

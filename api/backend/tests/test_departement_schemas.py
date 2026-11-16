"""Response contract of the departement routes (plan 04.7-10, decisions 2 and 3).

The models refuse what the page must never show: a value next to its absence
state, a zero sale or document count served as a figure (« jamais 0 »), class
counts that do not add up, an indexable departement whose prices are not
hosted, or a sitemap list out of order. Pure models: no route, no database.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.common.schemas import DatasetHosting
from app.commune.absences import EtatColonne
from app.zone.schemas import (
    ClassesDpe,
    CommunePrincipale,
    CommunesDepartement,
    DpeDepartement,
    FenetreDvf,
    FicheDepartement,
    FichesDepartement,
    PopulationDepartement,
    PrixDepartement,
    PrixTypeDepartement,
    ResultatDepartement,
    ResultatDepartementSynthese,
)

pytestmark = pytest.mark.unit

HOSTED = DatasetHosting(etat="heberge", departements=["44"])
OUT = DatasetHosting(etat="hors_couverture", motif="dataset_non_heberge")
NOT_REPORTED = EtatColonne(etat="not_reported", motif="Aucune vente retenue.")
OUT_STATE = EtatColonne(etat="out_of_coverage", motif="dataset_non_heberge")
FEW = EtatColonne(etat="insufficient_sample", motif="4 ventes, sous le seuil de 5.")


# ------------------------------------------------------------------ prices --


def test_a_served_median_carries_no_state():
    prix = PrixTypeDepartement(mediane_m2=3200, effectif=40, etat=None)
    assert (prix.mediane_m2, prix.effectif) == (3200, 40)
    with pytest.raises(ValidationError):
        PrixTypeDepartement(mediane_m2=3200, effectif=40, etat=FEW)


def test_a_null_median_needs_its_state():
    with pytest.raises(ValidationError):
        PrixTypeDepartement(mediane_m2=None, effectif=None, etat=None)


def test_a_masked_median_keeps_its_real_count():
    prix = PrixTypeDepartement(mediane_m2=None, effectif=4, etat=FEW)
    assert prix.effectif == 4
    with pytest.raises(ValidationError):
        PrixTypeDepartement(mediane_m2=None, effectif=None, etat=FEW)


@pytest.mark.parametrize("etat", [NOT_REPORTED, OUT_STATE])
def test_no_sale_is_never_a_count_of_zero(etat):
    with pytest.raises(ValidationError):
        PrixTypeDepartement(mediane_m2=None, effectif=0, etat=etat)
    with pytest.raises(ValidationError):
        PrixTypeDepartement(mediane_m2=None, effectif=3, etat=etat)
    assert PrixTypeDepartement(mediane_m2=None, effectif=None, etat=etat).etat == etat


# -------------------------------------------------------------- population --


def test_a_population_is_served_with_its_base_or_absent_with_its_state():
    served = PopulationDepartement(
        total=1_450_000, communes_comptees=207, annee=2022, etat=None
    )
    assert served.annee == 2022
    # Years differ between communes: the total stays, the year is null.
    assert (
        PopulationDepartement(
            total=700_000, communes_comptees=255, annee=None, etat=None
        ).annee
        is None
    )
    absent = PopulationDepartement(
        total=None, communes_comptees=None, annee=None, etat=NOT_REPORTED
    )
    assert absent.etat == NOT_REPORTED
    with pytest.raises(ValidationError):
        PopulationDepartement(total=None, communes_comptees=None, annee=None, etat=None)
    with pytest.raises(ValidationError):
        PopulationDepartement(
            total=None, communes_comptees=3, annee=None, etat=NOT_REPORTED
        )
    with pytest.raises(ValidationError):
        PopulationDepartement(total=0, communes_comptees=0, annee=None, etat=None)


# --------------------------------------------------------------------- DPE --


def _classes(**counts) -> ClassesDpe:
    return ClassesDpe(**{c: counts.get(c, 0) for c in "ABCDEFG"})


def _dpe(**overrides) -> dict:
    base = {
        "classes": _classes(A=1, B=2, F=3, G=1),
        "inconnue": 2,
        "total": 9,
        "classes_connues": 7,
        "part_fg_pct": 57.1,
        "date_min": date(2021, 7, 1),
        "date_max": date(2026, 9, 30),
        "etat": None,
        "etat_part_fg": None,
    }
    return {**base, **overrides}


def test_served_dpe_counts_add_up():
    dpe = DpeDepartement(**_dpe())
    assert dpe.classes_connues == 7
    with pytest.raises(ValidationError):
        DpeDepartement(**_dpe(classes_connues=8, inconnue=1))  # classes sum to 7
    with pytest.raises(ValidationError):
        DpeDepartement(**_dpe(total=10))  # 7 known + 2 unknown


def test_no_document_is_a_state_never_a_zero_total():
    absent = DpeDepartement(
        **_dpe(
            classes=None,
            inconnue=None,
            total=None,
            classes_connues=None,
            part_fg_pct=None,
            date_min=None,
            date_max=None,
            etat=NOT_REPORTED,
            etat_part_fg=NOT_REPORTED,
        )
    )
    assert absent.total is None
    with pytest.raises(ValidationError):
        DpeDepartement(
            **_dpe(
                classes=_classes(),
                inconnue=0,
                total=0,
                classes_connues=0,
                part_fg_pct=None,
                etat_part_fg=EtatColonne(
                    etat="zero_denominator", motif="Aucune classe connue."
                ),
            )
        )


def test_a_share_without_known_class_is_zero_denominator():
    dpe = DpeDepartement(
        **_dpe(
            classes=_classes(),
            inconnue=3,
            total=3,
            classes_connues=0,
            part_fg_pct=None,
            etat_part_fg=EtatColonne(
                etat="zero_denominator", motif="Aucune classe connue."
            ),
        )
    )
    assert dpe.etat_part_fg.etat == "zero_denominator"
    with pytest.raises(ValidationError):
        DpeDepartement(**_dpe(part_fg_pct=None))
    with pytest.raises(ValidationError):
        DpeDepartement(**_dpe(etat_part_fg=NOT_REPORTED))


def test_an_absent_block_gives_its_state_to_the_share():
    with pytest.raises(ValidationError):
        DpeDepartement(
            **_dpe(
                classes=None,
                inconnue=None,
                total=None,
                classes_connues=None,
                part_fg_pct=None,
                date_min=None,
                date_max=None,
                etat=OUT_STATE,
                etat_part_fg=NOT_REPORTED,
            )
        )


# ------------------------------------------------------- list and sitemap --


def test_an_indexable_departement_has_its_prices_hosted():
    row = ResultatDepartement(
        code="44",
        nom="Loire-Atlantique",
        region="Pays de la Loire",
        derniere_importation=date(2026, 10, 30),
        prix=HOSTED,
        indexable=True,
    )
    assert row.indexable
    with pytest.raises(ValidationError):
        ResultatDepartement(
            code="22",
            nom="Côtes-d'Armor",
            region="Bretagne",
            derniere_importation=None,
            prix=OUT,
            indexable=True,
        )


def test_the_new_fields_of_the_list_are_required():
    with pytest.raises(ValidationError):
        ResultatDepartement(code="44", nom="Loire-Atlantique", region=None)


def test_the_fiches_are_sorted_by_code_without_duplicate():
    fiches = FichesDepartement(
        code="75",
        derniere_importation=None,
        fiches=[
            FicheDepartement(code_insee="75056", nom="Paris", type_zone="commune"),
            FicheDepartement(
                code_insee="75101",
                nom="Paris 1er Arrondissement",
                type_zone="arrondissement",
            ),
        ],
    )
    assert [f.code_insee for f in fiches.fiches] == ["75056", "75101"]
    with pytest.raises(ValidationError):
        FichesDepartement(
            code="75",
            derniere_importation=None,
            fiches=[
                FicheDepartement(
                    code_insee="75101",
                    nom="Paris 1er Arrondissement",
                    type_zone="arrondissement",
                ),
                FicheDepartement(code_insee="75056", nom="Paris", type_zone="commune"),
            ],
        )
    with pytest.raises(ValidationError):
        FichesDepartement(
            code="44",
            derniere_importation=None,
            fiches=[
                FicheDepartement(code_insee="44109", nom="Nantes", type_zone="commune"),
                FicheDepartement(code_insee="44109", nom="Nantes", type_zone="commune"),
            ],
        )


# ------------------------------------------------------- OpenAPI examples --

# Every response model of the departement routes (plan 04.7-10).
_NEW_MODELS = (
    ResultatDepartement,
    ResultatDepartementSynthese,
    CommunesDepartement,
    PopulationDepartement,
    FenetreDvf,
    PrixTypeDepartement,
    PrixDepartement,
    ClassesDpe,
    DpeDepartement,
    CommunePrincipale,
    FicheDepartement,
    FichesDepartement,
)


def _examples(model) -> list[dict]:
    extra = model.model_config.get("json_schema_extra") or {}
    return list(extra.get("examples", []))


@pytest.mark.parametrize("model", _NEW_MODELS, ids=lambda model: model.__name__)
def test_every_example_of_the_new_schemas_is_valid(model):
    # Review Codex 04.7-10 n. 5: a published example the model refuses.
    for example in _examples(model):
        model.model_validate(example)

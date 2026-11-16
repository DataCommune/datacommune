"""Arrondissements of Paris, Lyon and Marseille in the address report (04.7-09).

A communal theme read at the arrondissement says so (granularity
``arrondissement`` with its code and name); read at the commune for want of
a row of the arrondissement, it says the commune, flags the value as
communal and names the city in its verdict. Never the other way round.
"""

import pytest
from pydantic import ValidationError

from app.adresse.schemas import CommuneRef
from app.rapport.alignement import aligner
from app.rapport.resume import est_de_l_adresse
from app.rapport.schemas import Granularite
from app.rapport.socle import nom_arrondissement
from app.rapport.themes import fiscalite, loyers, revenus, zonage_abc
from tests.test_rapport_themes import (  # noqa: F401 (autouse fixtures)
    _contexte,
    _publication_validee,
    _requete,
    _sans_provenance,
)

pytestmark = pytest.mark.unit


def _a(contexte, code, nom):
    return contexte.__class__(
        **{
            **contexte.__dict__,
            "identite": contexte.identite.model_copy(
                update={"commune": CommuneRef(code_insee=code, nom=nom)}
            ),
        }
    )


@pytest.mark.parametrize(
    ("code", "nom"),
    [("75112", "12e arrondissement de Paris"), ("75101", "1er arrondissement de Paris"),
     ("69381", "1er arrondissement de Lyon"), ("69389", "9e arrondissement de Lyon"),
     ("13203", "3e arrondissement de Marseille"),
     ("13216", "16e arrondissement de Marseille")],
)  # fmt: skip
def test_the_name_of_an_arrondissement_comes_from_its_code(code, nom):
    assert nom_arrondissement(code) == nom


def test_the_arrondissement_level_carries_its_arrondissement_and_only_it():
    g = Granularite(
        niveau="arrondissement", arrondissement={"code": "75112", "nom": "x"}
    )
    assert g.arrondissement.code == "75112"
    with pytest.raises(ValidationError):
        Granularite(niveau="arrondissement")
    with pytest.raises(ValidationError):
        Granularite(niveau="commune", arrondissement={"code": "75112", "nom": "x"})


def test_an_arrondissement_is_the_area_never_the_address():
    g = Granularite(
        niveau="arrondissement", arrondissement={"code": "75112", "nom": "x"}
    )
    assert not est_de_l_adresse(g)


# The four communal themes, each with the row its query returns.
THEMES = [
    (fiscalite, "fiscalite_par_codes", {"taux_tfpb": 20.5, "annee": 2025}),
    (loyers, "loyers_par_codes", None),
    (revenus, "revenus_par_codes", {"revenu_median": 33680, "annee": 2023}),
    (zonage_abc, "zonage_par_codes", {"zone": "A bis"}),
]


def _ligne_loyers(code):
    return [
        {"code_insee": code, "type_habitat": "appartement", "loyer_m2": 30.5,
         "annee": 2025},
    ]  # fmt: skip


@pytest.mark.parametrize(("module", "requete", "ligne"), THEMES)
@pytest.mark.parametrize(
    ("code", "ville"), [("75112", "Paris"), ("69381", "Lyon"), ("13203", "Marseille")]
)
async def test_a_row_of_the_arrondissement_is_read_at_the_arrondissement(
    monkeypatch, module, requete, ligne, code, ville
):
    lignes = _ligne_loyers(code) if ligne is None else [{"code_insee": code, **ligne}]
    monkeypatch.setattr(module, requete, _requete(lignes))
    theme = aligner(await module.lire(_a(_contexte(), code, f"{ville} arr.")))
    assert theme.etat == "ok", theme
    assert theme.granularite.niveau == "arrondissement"
    assert theme.granularite.arrondissement.code == code
    assert theme.details.get("valeur_communale") is False


@pytest.mark.parametrize(("module", "requete", "ligne"), THEMES)
@pytest.mark.parametrize(
    ("code", "commune", "ville"),
    [("75112", "75056", "Paris"), ("69381", "69123", "Lyon"),
     ("13203", "13055", "Marseille")],
)  # fmt: skip
async def test_a_value_of_the_commune_is_said_communal_and_names_the_city(
    monkeypatch, module, requete, ligne, code, commune, ville
):
    lignes = (
        _ligne_loyers(commune) if ligne is None else [{"code_insee": commune, **ligne}]
    )
    monkeypatch.setattr(module, requete, _requete(lignes))
    theme = await module.lire(_a(_contexte(), code, f"{ville} arr."))
    assert theme.etat == "ok", theme
    assert theme.granularite.niveau == "commune"
    assert theme.granularite.arrondissement is None
    assert theme.details["valeur_communale"] is True
    assert ville in theme.verdict
    assert len(theme.verdict) <= 140


@pytest.mark.parametrize(("module", "requete", "ligne"), THEMES)
async def test_a_commune_outside_plm_is_unchanged(monkeypatch, module, requete, ligne):
    lignes = (
        _ligne_loyers("44109") if ligne is None else [{"code_insee": "44109", **ligne}]
    )
    monkeypatch.setattr(module, requete, _requete(lignes))
    theme = await module.lire(_contexte())
    assert theme.granularite.niveau == "commune"
    assert "valeur_communale" not in theme.details


# --- Revue Codex n° 1 ----------------------------------------------------------


def _loyers_larges(code, appartement=30.5, maison=40.5):
    return [
        {"code_insee": code, "type_habitat": "appartement", "loyer_m2": appartement,
         "annee": 2025, "type_prediction": "maille"},
        {"code_insee": code, "type_habitat": "maison", "loyer_m2": maison,
         "annee": 2025, "type_prediction": "maille"},
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("code", "commune", "ville"),
    [("75112", "75056", "Paris"), ("69381", "69123", "Lyon"),
     ("13203", "13055", "Marseille")],
)  # fmt: skip
@pytest.mark.parametrize("maison", [40.5, 100.5])
async def test_a_long_communal_rent_still_names_its_city(
    monkeypatch, code, commune, ville, maison
):
    monkeypatch.setattr(
        loyers, "loyers_par_codes", _requete(_loyers_larges(commune, maison=maison))
    )
    theme = await loyers.lire(_a(_contexte(), code, f"{ville} arr."))
    assert ville in theme.verdict
    assert "la commune" not in theme.verdict
    assert len(theme.verdict) <= 140


@pytest.mark.parametrize("maison", [40.5, 100.5])
async def test_a_long_rent_of_the_arrondissement_fits_and_says_it(monkeypatch, maison):
    monkeypatch.setattr(
        loyers, "loyers_par_codes", _requete(_loyers_larges("75112", maison=maison))
    )
    theme = await loyers.lire(_a(_contexte(), "75112", "Paris 12e"))
    assert "l'arrondissement" in theme.verdict
    assert "commune" not in theme.verdict
    assert len(theme.verdict) <= 140
    # The verdict survives a round trip through the contract.
    assert type(theme).model_validate(theme.model_dump()).verdict == theme.verdict


async def test_a_zone_of_the_arrondissement_is_never_said_of_the_commune(monkeypatch):
    monkeypatch.setattr(
        zonage_abc,
        "zonage_par_codes",
        _requete([{"code_insee": "75112", "zone": "A bis"}]),
    )
    theme = await zonage_abc.lire(_a(_contexte(), "75112", "Paris 12e"))
    assert theme.verdict.startswith("Zone A bis pour l'arrondissement")
    assert "commune" not in theme.verdict

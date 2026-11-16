"""No route names a volunteer manager of a co-ownership (RGPD).

A volunteer or cooperative manager is a co-owner, a natural person: the
registry queries name a professional manager only, as the address report
does since 04.7-08, so ``GET /v1/copropriete`` and ``/v1/resolve`` alike
never serve it (review Codex: the resolver leaked it too).
"""

import pytest

from app.common.schemas import DatasetHosting
from app.copropriete import router
from app.copropriete.queries import copropriete_par_codes, copropriete_par_numero
from app.copropriete.syndic import nom_syndic_public, type_syndic
from app.resolve import multi
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit


def _ligne(type_syndic, nom_syndic):
    return {
        "numero_immatriculation": "AA1234567",
        "nom_copropriete": "Résidence",
        "adresse": "2 rue des Essais",
        "code_commune": "44109",
        "code_postal": "44000",
        "nb_lots_total": 24,
        "nb_lots_habitation": 18,
        "nb_lots_commerciaux": 0,
        "nb_lots_stationnement": 6,
        "nom_syndic": nom_syndic,
        "type_syndic": type_syndic,
        "periode_construction": "1949-1960",
        "annee_reglement": 1958,
    }


class _Ligne:
    def __init__(self, valeurs):
        self._mapping = valeurs


class _Session:
    """Only the SQL is faked: the queries run as they are."""

    def __init__(self, lignes):
        self.lignes = lignes

    async def execute(self, sql, parametres):
        return [_Ligne(ligne) for ligne in self.lignes]


async def _servi(monkeypatch, ligne):
    hosting = RouteHosting(
        {"copropriete": DatasetHosting(etat="heberge", departements=["44"])}
    )
    reponse = await router.get_copropriete(
        hosting=hosting,
        db=_Session([ligne]),  # type: ignore[arg-type]
        lat=None,
        lon=None,
        code_insee=None,
        numero="AA1234567",
        limite=50,
    )
    return reponse.donnees[0]


@pytest.mark.parametrize(
    "type_", ["bénévole", "Benevole", "coopératif", "non connu", None, "autre"]
)
async def test_a_manager_that_is_not_professional_is_never_named(monkeypatch, type_):
    servi = await _servi(monkeypatch, _ligne(type_, "Jean Dupont"))
    assert servi.nom_syndic is None
    assert servi.type_syndic == type_


async def test_a_professional_manager_is_named(monkeypatch):
    servi = await _servi(monkeypatch, _ligne("professionnel", " Cabinet Exemple "))
    assert servi.nom_syndic == "Cabinet Exemple"


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [("professionnel", "professionnel"), ("PROFESSIONNEL", "professionnel"),
     (" Professionnel ", "professionnel"), ("bénévole", "benevole"),
     ("BÉNÉVOLE", "benevole"), ("coopératif", "cooperatif"),
     ("COOPÉRATIF", "cooperatif"), ("non connu", "non_connu"), ("", "non_connu"),
     (None, "non_connu")],
)  # fmt: skip
def test_the_rule_reads_the_type_without_accents_nor_case(valeur, attendu):
    assert type_syndic(valeur) == attendu


def test_a_blank_name_is_no_name():
    assert nom_syndic_public("professionnel", "  ") is None


@pytest.mark.parametrize("type_", ["bénévole", "coopératif", "non connu", None])
async def test_the_registry_queries_never_name_a_person(type_):
    session = _Session([_ligne(type_, "Jean Dupont")])
    for lignes in (
        await copropriete_par_codes(session, ["44109"]),  # type: ignore[arg-type]
        await copropriete_par_numero(session, "AA1234567"),  # type: ignore[arg-type]
    ):
        assert lignes[0]["nom_syndic"] is None


async def test_the_resolver_reads_the_registry_through_the_rule():
    # /v1/resolve reads ``copropriete_par_codes`` itself: the rule holds there.
    assert multi.copropriete_par_codes is copropriete_par_codes

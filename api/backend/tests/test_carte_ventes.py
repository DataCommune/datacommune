"""04.10-05: the sales of one parcel of the interactive map, without a database.

``GET /v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes`` serves the sales of a
parcel the visitor picked on the map of an address. What is proven here is what
does not need PostGIS: the route has a per-visitor bucket of its own, tighter
than the one of the tiles (never the quota of the address page), no API key,
bounded inputs whose refusal reflects nothing, one constant 404 whatever the
reason the pair of the path is not served, the problems of the sales route of
the address page, and an answer that is never stored.

One price rule: the reader of the map awaits the page reader of
``app.adresse.ventes`` with its own page size, and defines no rule of its own.

The reads themselves (bounds of the address, same cards as the address page,
groups) are proven on PostGIS
(``tests/integration/test_carte_ventes_contract.py``).
"""

import ast
import json
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.adresse.relations import PointBan
from app.adresse.schemas import CarteVente, CompositionVente, MinimisationVente
from app.carte import router
from app.config import settings
from app.database import get_db
from app.main import app
from app.rate_limit import _buckets
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE, SCOPE_44_85, catalogue_scope
from tests.scope_doubles import route_hosting as hebergement
from tests.test_carte_parcelles import (
    TITRE_VALIDATION,
    SessionSansLecture,
    _appels,
    _route,
    _routes_de,
    _vider,
)

pytestmark = pytest.mark.unit

GABARIT = "/v1/carte/{ban_id}/parcelle/{id_parcelle}/ventes"
GABARIT_TUILE = "/v1/carte/parcelles/{z}/{x}/{y}"
BAN_ID = "85191_0420_00012"
# Synthetic identifiers: a departement code, then letters no cadastre holds.
PARCELLE = "85000000ZZ0001"
VENTES = f"/v1/carte/{BAN_ID}/parcelle/{PARCELLE}/ventes"
TUILE = "/v1/carte/parcelles/16/32500/23000"
JETON = "0123456789abcdef"
# The one 404 of the route: an unknown key, an unknown parcel and a parcel
# outside the bounds of the address answer this body and nothing else.
INTROUVABLE = {
    "type": "about:blank",
    "title": "Parcelle introuvable autour de cette adresse",
    "status": 404,
    "instance": GABARIT,
}
BACKEND = Path(__file__).resolve().parents[1]


def _carte(jour: date, prix: float | None) -> CarteVente:
    return CarteVente(
        date=jour,
        nature="Vente",
        composition=CompositionVente(maisons=1),
        surface_batie=90,
        prix=prix,
        prix_m2=None if prix is None else round(prix / 90),
        prix_affiche=prix is not None,
        minimisation=None
        if prix is not None
        else MinimisationVente(motif="vente_isolee_recente"),
    )


def _servies(etat: str = "value"):
    """A served page of two sales of one parcel, the second one withheld."""
    from app.carte.schemas import VentesParcelleCarte

    if etat != "value":
        return VentesParcelleCarte(
            etat=etat, motif="dataset_non_heberge", page=1, par_page=5
        )
    return VentesParcelleCarte(
        etat="value",
        periode="2021-2025",
        annee_debut=2021,
        total=2,
        page=1,
        pages=1,
        par_page=5,
        generation=JETON,
        ventes=[_carte(date(2022, 3, 1), 180_000.0), _carte(date(2025, 6, 1), None)],
        groupes=[None, None],
    )


@pytest.fixture(autouse=True)
def lecteur(monkeypatch):
    """Empty caches and buckets; the reader of the route is a stub."""
    _vider()
    stub = AsyncMock(side_effect=lambda *args, **kwargs: _servies())
    # ``raising=False``: before the route exists the tests fail on what they
    # assert; ``test_the_route_hands_...`` proves the patch holds.
    monkeypatch.setattr(router, "lire_ventes_de_la_parcelle", stub, raising=False)
    yield stub
    _vider()


@pytest.fixture()
async def client():
    async def _db():
        yield SessionSansLecture()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _regler(monkeypatch, nom: str, valeur: int) -> None:
    """Set one setting; an unknown one fails here without printing the others.

    Neither an ``assert`` on ``settings`` nor ``monkeypatch.setattr`` on a
    missing attribute: both print the whole settings object in the report.
    """
    connus = set(type(settings).model_fields)
    if nom not in connus:
        pytest.fail(f"setting {nom} does not exist")
    monkeypatch.setattr(settings, nom, valeur)


# --- The page is served --------------------------------------------------------


async def test_the_route_hands_key_parcel_page_and_token_to_the_reader(client, lecteur):
    reponse = await client.get(VENTES, params={"page": 3, "vg": JETON})

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("application/json")
    # A page of one generation is never stored.
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.headers["x-content-type-options"] == "nosniff"
    corps = reponse.json()
    assert set(corps) == {"statut", "donnees", "meta"}
    assert corps["donnees"]["par_page"] == 5
    assert len(corps["donnees"]["groupes"]) == len(corps["donnees"]["ventes"]) == 2
    # DVF then the cadastre, as the sales route of the address page.
    assert [source["nom"] for source in corps["meta"]["source"]] == [
        "Demandes de valeurs foncières (DVF)",
        "Cadastre Etalab",
    ]
    assert lecteur.await_count == 1
    assert lecteur.await_args.args[-4:] == (BAN_ID, PARCELLE, 3, JETON)


async def test_page_one_without_token_is_the_default(client, lecteur):
    assert (await client.get(VENTES)).status_code == 200
    assert lecteur.await_args.args[-4:] == (BAN_ID, PARCELLE, 1, None)


async def test_dvf_out_of_coverage_is_served_and_never_stored(client, lecteur):
    lecteur.side_effect = lambda *a, **k: _servies("out_of_coverage")

    reponse = await client.get(VENTES)

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    donnees = reponse.json()["donnees"]
    assert (donnees["etat"], donnees["motif"]) == (
        "out_of_coverage",
        "dataset_non_heberge",
    )
    assert donnees["total"] is None and donnees["ventes"] == []


# --- Own bucket, tighter than the tiles, outside the page quota -----------------


async def test_the_sales_bucket_is_its_own_and_answers_429_with_retry_after(
    client, monkeypatch
):
    _regler(monkeypatch, "carte_ventes_rate_limit_per_ip", 2)
    # The other buckets of the map are smaller: were one of them spent here,
    # the second call would stop.
    _regler(monkeypatch, "carte_rate_limit_per_ip", 1)
    _regler(monkeypatch, "carte_parcelles_rate_limit_per_ip", 1)
    _regler(monkeypatch, "carte_fond_rate_limit_per_ip", 1)

    statuts = [(await client.get(VENTES)).status_code for _ in range(2)]
    refusee = await client.get(VENTES)

    assert statuts == [200, 200]
    assert refusee.status_code == 429
    assert re.fullmatch(r"[1-9][0-9]*", refusee.headers["retry-after"])
    assert refusee.headers["cache-control"] == "no-store"
    assert refusee.headers["content-type"].startswith("application/problem+json")
    assert "donnees" not in refusee.text
    assert any(cle.startswith("carte:ventes:ip:") for cle in _buckets)
    autres = ("carte:ip:", "carte:parcelles:ip:", "carte:fond:ip:")
    assert not any(cle.startswith(autres) for cle in _buckets)


async def test_the_sales_do_not_spend_the_bucket_of_the_tiles(client, monkeypatch):
    _regler(monkeypatch, "carte_ventes_rate_limit_per_ip", 1)
    _regler(monkeypatch, "carte_parcelles_rate_limit_per_ip", 1)
    from tests.test_carte_parcelles import _tuile

    monkeypatch.setattr(router, "lire_tuile", AsyncMock(return_value=_tuile()))

    assert (await client.get(VENTES)).status_code == 200
    assert (await client.get(TUILE)).status_code == 200
    assert (await client.get(VENTES)).status_code == 429
    assert (await client.get(TUILE)).status_code == 429


async def test_the_sales_bucket_is_per_visitor(client, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    _regler(monkeypatch, "carte_ventes_rate_limit_per_ip", 1)
    un = {"CF-Connecting-IP": "203.0.113.10"}
    autre = {"CF-Connecting-IP": "203.0.113.11"}

    assert (await client.get(VENTES, headers=un)).status_code == 200
    assert (await client.get(VENTES, headers=un)).status_code == 429
    assert (await client.get(VENTES, headers=autre)).status_code == 200


async def test_the_sales_bucket_is_spent_before_any_read(client, lecteur, monkeypatch):
    _regler(monkeypatch, "carte_ventes_rate_limit_per_ip", 1)
    await client.get(VENTES)
    lecteur.reset_mock()

    # Another parcel of the same address: the bucket is the visitor's, not the
    # parcel's, so walking the parcels of a map is what it slows down.
    refusee = await client.get(f"/v1/carte/{BAN_ID}/parcelle/85000000ZZ0002/ventes")

    assert refusee.status_code == 429
    lecteur.assert_not_awaited()


def test_the_route_has_no_api_key_and_no_quota_dependency():
    from app.auth.dependencies import check_quota_hybrid

    appels = set(_appels(_route(GABARIT).dependant))

    assert check_quota_hybrid not in appels
    assert router.limite_ventes_carte in appels
    for autre in (router.limite_carte, router.limite_parcelles, router.limite_fond):
        assert autre not in appels
    assert "security" not in app.openapi()["paths"][GABARIT]["get"]


def test_the_limit_and_the_page_size_are_the_planned_ones():
    from app.adresse import ventes as fiche

    defauts = {nom: champ.default for nom, champ in type(settings).model_fields.items()}

    assert defauts["carte_ventes_rate_limit_per_ip"] == 60
    # Tighter than the tiles: a view is several tiles, a look at sales is one call.
    assert (
        defauts["carte_ventes_rate_limit_per_ip"]
        < defauts["carte_parcelles_rate_limit_per_ip"]
    )
    assert defauts["carte_ventes_par_page"] == 5
    # The page of the address page did not move.
    assert fiche.PAR_PAGE == 10
    assert defauts["carte_parcelles_rate_limit_per_ip"] == 600
    assert defauts["carte_fond_rate_limit_per_ip"] == 600
    assert defauts["carte_rate_limit_per_ip"] == 60


# --- Bounded inputs, nothing of them reflected ----------------------------------


@pytest.mark.parametrize(
    ("cle", "parcelle", "saisie"),
    [
        ("pas-une-cle", PARCELLE, "pas-une-cle"),
        ("85191_0420_00012 x", PARCELLE, "00012 x"),
        ("7" * 60, PARCELLE, "7" * 60),
        ("parcelles", PARCELLE, "parcelles/parcelle"),
        ("85191_0420_0001z", PARCELLE, "0001z"),
        (BAN_ID, "85000000zz0001", "85000000zz0001"),
        (BAN_ID, "85000000ZZ001", "85000000ZZ001"),
        (BAN_ID, "85000000ZZ00011", "85000000ZZ00011"),
        (BAN_ID, "85000000ZZ-001", "85000000ZZ-001"),
        (BAN_ID, "85000000ZZ000%27", "ZZ000'"),
        (BAN_ID, "85000000ZZ 001", "ZZ 001"),
        (BAN_ID, "qqxxqqxxqqxxqq", "qqxxqqxxqqxxqq"),
    ],
)
async def test_a_malformed_key_or_parcel_is_a_constant_problem(
    client, lecteur, cle, parcelle, saisie
):
    reponse = await client.get(f"/v1/carte/{cle}/parcelle/{parcelle}/ventes")

    assert reponse.status_code == 422
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    corps = reponse.json()
    assert corps["title"] == TITRE_VALIDATION
    assert corps["instance"] in (GABARIT, GABARIT_TUILE)
    assert saisie not in reponse.text
    lecteur.assert_not_awaited()


@pytest.mark.parametrize(
    ("params", "saisie"),
    [
        ({"page": "0"}, None),
        ({"page": "100001"}, "100001"),
        ({"page": "zzqq"}, "zzqq"),
        ({"vg": "zzqqzzqq"}, "zzqqzzqq"),
        ({"vg": "0123456789abcdef0"}, "0123456789abcdef0"),
        ({"vg": "0123456789ABCDEF"}, "0123456789ABCDEF"),
    ],
)
async def test_a_malformed_page_or_token_is_refused_before_any_read(
    client, lecteur, params, saisie
):
    reponse = await client.get(VENTES, params=params)

    assert reponse.status_code == 422
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.json()["instance"] == GABARIT
    assert BAN_ID not in reponse.text and PARCELLE not in reponse.text
    if saisie is not None:
        assert saisie not in reponse.text
    lecteur.assert_not_awaited()


async def test_an_unknown_query_parameter_is_ignored(client, lecteur):
    """No bounds, no page size, no parcel list comes from the visitor."""
    reponse = await client.get(
        VENTES,
        params={
            "emprise": "-180,-90,180,90",
            "ouest": "-180",
            "est": "180",
            "par_page": "500",
            "limite": "999999",
            "departement": "44",
        },
    )

    assert reponse.status_code == 200
    assert lecteur.await_args.args[-4:] == (BAN_ID, PARCELLE, 1, None)
    assert lecteur.await_args.kwargs == {}


def test_the_sales_path_matches_its_route_only():
    assert _routes_de(VENTES) == [GABARIT]
    # Look-alikes are no route at all.
    assert _routes_de(f"/v1/carte/{BAN_ID}/parcelle/{PARCELLE}") == []
    assert _routes_de(f"{VENTES}/2") == []
    assert _routes_de(f"{VENTES}.png") == []
    assert _routes_de(f"/v1/carte/{BAN_ID}/parcelle/ventes") == []
    # No list route: the sales are asked one parcel at a time.
    assert _routes_de(f"/v1/carte/{BAN_ID}/ventes") == []
    assert _routes_de(f"/v1/carte/{BAN_ID}/parcelles/ventes") == []
    assert _routes_de(f"/v1/carte/parcelle/{PARCELLE}/ventes") == []


# --- Problems: one constant 404, and those of the address page ------------------


async def test_a_pair_that_is_not_served_is_one_constant_404(client, lecteur):
    """Unknown key, unknown parcel, parcel outside the bounds: the reader says
    None for each, and the route cannot say which."""
    lecteur.side_effect = lambda *a, **k: None

    reponse = await client.get(VENTES)

    assert reponse.status_code == 404
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.json() == INTROUVABLE
    assert BAN_ID not in reponse.text and PARCELLE not in reponse.text


@pytest.mark.parametrize(
    ("issue", "statut", "code"),
    [
        ("generation", 409, "generation_changee"),
        ("page", 404, "page_hors_limites"),
        ("panne", 503, "ventes_indisponibles"),
    ],
)
async def test_reader_errors_are_the_problems_of_the_sales_of_the_address_page(
    client, lecteur, issue, statut, code
):
    from app.adresse.documents import GenerationChangeeError, PageHorsLimitesError
    from app.adresse.ventes import VentesIndisponiblesError

    lecteur.side_effect = {
        "generation": GenerationChangeeError(),
        "page": PageHorsLimitesError(),
        "panne": VentesIndisponiblesError(),
    }[issue]

    reponse = await client.get(VENTES, params={"page": 2, "vg": JETON})

    assert reponse.status_code == statut
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    corps = reponse.json()
    assert corps["code"] == code and corps["instance"] == GABARIT
    for saisie in (BAN_ID, PARCELLE, JETON):
        assert saisie not in reponse.text
    # An outage is never a list, an empty one least of all.
    assert "ventes" not in corps and "donnees" not in corps


# --- OpenAPI --------------------------------------------------------------------


def test_the_sales_route_is_documented_in_ascii():
    operation = app.openapi()["paths"][GABARIT]["get"]

    assert set(operation["responses"]) >= {"200", "404", "409", "422", "429", "503"}
    assert operation["summary"].isascii() and operation["description"].isascii()
    parametres = {p["name"]: p for p in operation["parameters"]}
    assert set(parametres) == {"ban_id", "id_parcelle", "page", "vg"}
    assert parametres["id_parcelle"]["schema"]["pattern"] == "^[0-9A-Z]{14}$"
    assert parametres["page"]["in"] == "query" and parametres["vg"]["in"] == "query"


# --- One price rule: the reader of the address page, with another page size -----


@pytest.mark.parametrize(
    ("total", "par_page", "pages"),
    [(0, 5, 1), (1, 5, 1), (5, 5, 1), (6, 5, 2), (7, 5, 2), (23, 5, 5), (23, 10, 3)],
)
def test_the_pages_follow_the_page_size(total, par_page, pages):
    from app.adresse import ventes as fiche

    assert fiche.nombre_pages(total, par_page) == pages


def test_the_map_module_defines_no_price_rule_and_takes_the_reader_of_the_page():
    from app.adresse import ventes as fiche
    from app.carte import ventes as carte

    assert carte.lire_page_parcelle is fiche.lire_page_parcelle
    source = (BACKEND / "app/carte/ventes.py").read_text(encoding="utf-8")
    arbre = ast.parse(source)
    definis = {
        noeud.name
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    assignes = {
        cible.id
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Assign)
        for cible in noeud.targets
        if isinstance(cible, ast.Name)
    }
    # Neither a rule nor a threshold of its own, under any of the names the
    # address page gives them.
    interdits = {
        "minimiser",
        "carte_vente",
        "seuil_recence",
        "_isolements",
        "_cartes",
        "_fenetre",
        "FENETRE_RECENCE_MOIS",
        "_ISOLEMENT_SQL",
        "_PAGE_PARCELLE_SQL",
        "PAR_PAGE",
    }
    assert not (definis | assignes) & interdits
    for mot in ("valeur_fonciere", "prix_m2", "MinimisationVente", "CarteVente("):
        assert mot not in source, mot
    assert re.search(r"^from app\.adresse\.ventes import", source, re.MULTILINE)


class _Session:
    """Savepoints and the one statement the map adds: the first DVF date."""

    def __init__(self):
        self.instructions: list[str] = []

    def begin_nested(self):
        @asynccontextmanager
        async def savepoint():
            yield

        return savepoint()

    async def execute(self, statement, params=None):
        self.instructions.append(str(statement))

        class _Resultat:
            def scalar_one(self):
                return date(2021, 1, 5)

        return _Resultat()


@pytest.fixture()
def lecture(monkeypatch):
    """The reader of the map with its three reads replaced: the BAN point, the
    membership of the parcel, the page of the address page."""
    from app.adresse import blocs
    from app.adresse import ventes as fiche
    from app.carte import ventes as carte

    async def sans_garde(session, datasets):
        return None

    monkeypatch.setattr(blocs, "guard_publication", sans_garde)
    point = AsyncMock(return_value=PointBan(lon=-1.4, lat=46.6))
    dedans = AsyncMock(return_value=True)
    page = AsyncMock(
        return_value=fiche.PageVentes(
            generation=JETON, total=0, pages=1, cartes=[], faits=None, lignes=[]
        )
    )
    monkeypatch.setattr(carte, "point_de_la_cle", point)
    monkeypatch.setattr(carte, "_dans_l_emprise", dedans)
    monkeypatch.setattr(carte, "lire_page_parcelle", page)
    return carte, point, dedans, page


async def test_the_map_asks_the_shared_reader_for_five_sales_a_page(
    lecture, monkeypatch
):
    carte, _, dedans, page = lecture
    session = _Session()

    resultat = await carte.lire_ventes_de_la_parcelle(
        session,
        FULL_SCOPE,
        hebergement("ban", "cadastre", "dvf"),
        BAN_ID,
        PARCELLE,
        2,
        JETON,
    )

    assert page.await_args.args == (session, PARCELLE, 2, JETON, 5)
    assert (resultat.etat, resultat.par_page, resultat.page) == ("value", 5, 2)
    assert (resultat.total, resultat.pages, resultat.generation) == (0, 1, JETON)
    assert resultat.annee_debut == 2021
    assert resultat.ventes == [] and resultat.groupes == []
    # The parcel was looked for in its own departement, inside the bounds of
    # the point the server read.
    assert dedans.await_args.args[1:3] == (PARCELLE, "85")
    assert dedans.await_args.args[3] == carte.emprise_de_deplacement(-1.4, 46.6)

    _regler(monkeypatch, "carte_ventes_par_page", 3)
    await carte.lire_ventes_de_la_parcelle(
        session,
        FULL_SCOPE,
        hebergement("ban", "cadastre", "dvf"),
        BAN_ID,
        PARCELLE,
        1,
        None,
    )
    assert page.await_args.args == (session, PARCELLE, 1, None, 3)


async def test_the_address_page_still_asks_for_ten(monkeypatch):
    from app.adresse import blocs
    from app.adresse import ventes as fiche
    from app.adresse.schemas import RelationParcelle

    async def sans_garde(session, datasets):
        return None

    async def point(session, ban_id):
        return True, PointBan(lon=-1.4, lat=46.6)

    async def parcelle(session, point, hosting):
        return RelationParcelle(
            etat="unique",
            id_parcelle=PARCELLE,
            nombre_candidats=1,
            rattachement="parcelle_du_point",
        )

    page = AsyncMock(
        return_value=fiche.PageVentes(
            generation=JETON, total=23, pages=3, cartes=[], faits=None, lignes=[]
        )
    )
    monkeypatch.setattr(blocs, "guard_publication", sans_garde)
    monkeypatch.setattr(fiche, "_point", point)
    monkeypatch.setattr(fiche, "resoudre_parcelle", parcelle)
    monkeypatch.setattr(fiche, "lire_page_parcelle", page)
    session = _Session()

    resultat = await fiche.lire_ventes_parcelle(
        session, BAN_ID, 2, JETON, hebergement("ban", "cadastre", "dvf")
    )

    assert page.await_args.args == (session, PARCELLE, 2, JETON, 10)
    assert (resultat.par_page, resultat.total, resultat.pages) == (10, 23, 3)
    assert resultat.generation == JETON and resultat.etat == "value"


@pytest.mark.parametrize("issue", ["voie", "inconnue", "sans_point"])
async def test_an_address_without_a_point_reads_nothing_of_the_parcel(lecture, issue):
    from app.adresse.service import AdresseInconnueError, VoieSansNumeroError

    carte, point, dedans, page = lecture
    point.side_effect = {
        "voie": VoieSansNumeroError(),
        "inconnue": AdresseInconnueError(),
    }.get(issue)
    point.return_value = None

    resultat = await carte.lire_ventes_de_la_parcelle(
        _Session(),
        FULL_SCOPE,
        hebergement("ban", "cadastre", "dvf"),
        BAN_ID,
        PARCELLE,
        1,
        None,
    )

    assert resultat is None
    dedans.assert_not_awaited()
    page.assert_not_awaited()


async def test_an_outage_of_the_address_is_an_outage_of_the_sales(lecture):
    from app.adresse.service import AdresseIndisponibleError
    from app.adresse.ventes import VentesIndisponiblesError

    carte, point, dedans, page = lecture
    point.side_effect = AdresseIndisponibleError()

    with pytest.raises(VentesIndisponiblesError):
        await carte.lire_ventes_de_la_parcelle(
            _Session(),
            FULL_SCOPE,
            hebergement("ban", "cadastre", "dvf"),
            BAN_ID,
            PARCELLE,
            1,
            None,
        )
    dedans.assert_not_awaited()
    page.assert_not_awaited()


async def test_a_parcel_of_a_departement_without_cadastre_is_not_looked_for(lecture):
    carte, _, dedans, page = lecture

    resultat = await carte.lire_ventes_de_la_parcelle(
        _Session(),
        SCOPE_44_85,
        hebergement("ban", "cadastre", "dvf", scope=SCOPE_44_85),
        BAN_ID,
        "49000000ZZ0001",
        1,
        None,
    )

    assert resultat is None
    dedans.assert_not_awaited()
    page.assert_not_awaited()


async def test_a_parcel_outside_the_bounds_never_reaches_the_sales(lecture):
    carte, _, dedans, page = lecture
    dedans.return_value = False
    session = _Session()

    resultat = await carte.lire_ventes_de_la_parcelle(
        session,
        FULL_SCOPE,
        hebergement("ban", "cadastre", "dvf"),
        BAN_ID,
        PARCELLE,
        1,
        None,
    )

    assert resultat is None
    page.assert_not_awaited()
    assert session.instructions == []


async def test_dvf_without_coverage_for_the_parcel_is_said_before_any_sales_read(
    lecture,
):
    """The coverage is the one of the departement of the parcel, not of the key."""
    carte, _, dedans, page = lecture
    scope = catalogue_scope(("67", "85"))
    session = _Session()

    resultat = await carte.lire_ventes_de_la_parcelle(
        session,
        scope,
        hebergement("ban", "cadastre", "dvf", scope=scope),
        BAN_ID,
        "67000000ZZ0001",
        4,
        None,
    )

    assert (resultat.etat, resultat.motif) == (
        "out_of_coverage",
        "source_sans_couverture",
    )
    assert (resultat.page, resultat.par_page, resultat.total) == (4, 5, None)
    assert resultat.ventes == [] and resultat.groupes == []
    assert dedans.await_args.args[1:3] == ("67000000ZZ0001", "67")
    page.assert_not_awaited()
    assert session.instructions == []


# --- The parcels sold together: a dated fact, counted here, never by the front ----

G1, G2, G3 = "85000000ZG0001", "85000000ZG0002", "85000000ZG0003"
AILLEURS_44 = "44000000ZG0001"
NON_HEBERGEE_49 = "49000000ZG0001"
EMPRISE = {"ouest": -1.5, "sud": 46.5, "est": -1.4, "nord": 46.6}


def _au_cadastre(id_parcelle: str, contenance: int | None = 100, dedans: bool = True):
    """One row of the cadastre read of a group; no shape outside the bounds."""
    carre = [[[[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1], [0.0, 0.0]]]]
    return {
        "id_parcelle": id_parcelle,
        "contenance": contenance,
        "dedans": dedans,
        "geometrie": json.dumps({"type": "MultiPolygon", "coordinates": carre})
        if dedans
        else None,
        "lon": 0.0500004 if dedans else None,
        "lat": 0.0499996 if dedans else None,
    }


def _cadastre(*lignes: dict) -> dict[str, dict]:
    return {ligne["id_parcelle"]: ligne for ligne in lignes}


@pytest.mark.parametrize("liees", [[], [G1]])
def test_a_sale_of_one_parcel_has_no_group(liees):
    from app.carte.ventes import groupe_vendu

    assert groupe_vendu(liees, _cadastre(_au_cadastre(G1)), 1000) is None


def test_three_parcels_in_the_cadastre_and_in_the_bounds_are_a_whole_group():
    from app.carte.ventes import groupe_vendu

    groupe = groupe_vendu(
        [G1, G2, G3],
        _cadastre(_au_cadastre(G1, 100), _au_cadastre(G2, 250), _au_cadastre(G3, 380)),
        1000,
    )

    assert (groupe.total, groupe.autres) == (3, 2)
    assert (groupe.absentes_du_cadastre, groupe.hors_emprise) == (0, 0)
    assert groupe.surface_totale_m2 == 730
    assert [p.id_parcelle for p in groupe.parcelles] == [G1, G2, G3]
    for parcelle in groupe.parcelles:
        assert parcelle.geometry.type == "MultiPolygon"
        assert parcelle.geometry.coordinates[0][0][2] == [0.1, 0.1]
        # The label point, to six decimals as every coordinate of the map.
        assert parcelle.etiquette == (0.05, 0.05)


def test_a_parcel_absent_from_the_cadastre_leaves_no_total():
    from app.carte.ventes import groupe_vendu

    groupe = groupe_vendu(
        [G1, G2, G3], _cadastre(_au_cadastre(G1, 100), _au_cadastre(G3, 380)), 1000
    )

    assert (groupe.total, groupe.autres, groupe.absentes_du_cadastre) == (3, 2, 1)
    # Never the sum of the two left; an absent parcel is not one without surface.
    assert groupe.surface_totale_m2 is None
    assert groupe.sans_surface == 0
    assert [p.id_parcelle for p in groupe.parcelles] == [G1, G3]
    assert groupe.hors_emprise == 0


def test_a_parcel_without_surface_leaves_no_total_and_is_not_absent():
    from app.carte.ventes import groupe_vendu

    groupe = groupe_vendu(
        [G1, G2], _cadastre(_au_cadastre(G1, 100), _au_cadastre(G2, None)), 1000
    )

    assert groupe.surface_totale_m2 is None
    assert (groupe.absentes_du_cadastre, groupe.hors_emprise) == (0, 0)
    # Said to the visitor (R3 of the review of 2026-11-15): how many have no surface.
    assert groupe.sans_surface == 1
    assert [p.id_parcelle for p in groupe.parcelles] == [G1, G2]


def test_a_surface_of_zero_is_a_surface():
    from app.carte.ventes import groupe_vendu

    groupe = groupe_vendu(
        [G1, G2], _cadastre(_au_cadastre(G1, 100), _au_cadastre(G2, 0)), 1000
    )

    assert groupe.surface_totale_m2 == 100


def test_a_parcel_outside_the_bounds_is_counted_not_drawn_and_in_the_total():
    from app.carte.ventes import groupe_vendu

    groupe = groupe_vendu(
        [G1, G2, G3],
        _cadastre(
            _au_cadastre(G1, 100),
            _au_cadastre(G2, 900, dedans=False),
            _au_cadastre(G3, 380),
        ),
        1000,
    )

    assert (groupe.total, groupe.hors_emprise, groupe.absentes_du_cadastre) == (3, 1, 0)
    assert [p.id_parcelle for p in groupe.parcelles] == [G1, G3]
    assert groupe.surface_totale_m2 == 1380


@pytest.mark.parametrize(("plafond", "dessinees"), [(2, None), (3, 3), (1000, 3)])
def test_beyond_the_cap_the_parcels_are_not_served_and_the_counts_are(
    plafond, dessinees
):
    from app.carte.ventes import groupe_vendu

    groupe = groupe_vendu(
        [G1, G2, G3, AILLEURS_44],
        _cadastre(
            _au_cadastre(G1, 100),
            _au_cadastre(G2, 250),
            _au_cadastre(G3, 380),
            _au_cadastre(AILLEURS_44, 60, dedans=False),
        ),
        plafond,
    )

    # The cap counts the parcels that would be drawn, not those of the sale.
    assert (groupe.total, groupe.autres, groupe.hors_emprise) == (4, 3, 1)
    assert groupe.surface_totale_m2 == 790
    if dessinees is None:
        assert groupe.parcelles is None
    else:
        assert len(groupe.parcelles) == dessinees


def test_the_cap_of_a_group_is_the_planned_one():
    defauts = {nom: champ.default for nom, champ in type(settings).model_fields.items()}

    assert defauts["carte_groupe_geometries_max"] == 1000
    # It caps the shapes of one answer about a sale; a tile is still served
    # whole, and no setting of the map reads as a cap of its parcels.
    carte = [nom for nom in defauts if nom.startswith("carte_")]
    assert not [nom for nom in carte if "plafond" in nom or "max_parcelles" in nom]


def test_no_model_of_the_sales_route_can_carry_a_mutation_identifier():
    from app.carte import schemas

    assert list(schemas.GroupeVendu.model_fields) == [
        "total",
        "autres",
        "absentes_du_cadastre",
        "hors_emprise",
        "sans_surface",
        "surface_totale_m2",
        "parcelles",
    ]
    assert list(schemas.ParcelleDuGroupe.model_fields) == [
        "id_parcelle",
        "geometry",
        "etiquette",
    ]
    for modele in (
        schemas.VentesParcelleCarte,
        schemas.GroupeVendu,
        schemas.ParcelleDuGroupe,
        CarteVente,
    ):
        assert not [nom for nom in modele.model_fields if "mutation" in nom]


class _SessionDesGroupes:
    """The two reads of the groups of a page, answered from two lists."""

    def __init__(self, liens: list[tuple[str, str]], cadastre: list[dict]):
        self.liens = liens
        self.cadastre = cadastre
        self.appels: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.appels.append((sql, dict(params or {})))
        if "mutation_parcelles" in sql:
            lignes = [
                {"id_mutation": vente, "id_parcelle": parcelle}
                for vente, parcelle in sorted(self.liens)
                if vente in params["ids"]
            ]
        else:
            lignes = [
                ligne
                for ligne in self.cadastre
                if ligne["id_parcelle"] in params["ids"]
                and ligne["id_parcelle"][:2] in params["departements"]
            ]

        class _Resultat:
            def mappings(self):
                return self

            def all(self):
                return lignes

        return _Resultat()


def _emprise():
    from app.carte.schemas import EmpriseCarte

    return EmpriseCarte(**EMPRISE)


async def test_the_groups_of_a_page_cost_two_statements_and_follow_the_sales(
    monkeypatch,
):
    from app.carte import ventes as carte

    session = _SessionDesGroupes(
        liens=[
            ("vente-a", G1),
            ("vente-b", G1),
            ("vente-b", AILLEURS_44),
            ("vente-c", G1),
            ("vente-c", G2),
            ("vente-c", G3),
            ("vente-d", G1),
            ("vente-d", NON_HEBERGEE_49),
            ("hors-page", G2),
        ],
        cadastre=[
            _au_cadastre(G1, 100),
            _au_cadastre(G2, 250),
            _au_cadastre(AILLEURS_44, 60),
            _au_cadastre(NON_HEBERGEE_49, 500),
        ],
    )
    lignes = [{"id_mutation": f"vente-{rang}"} for rang in "cabd"]

    groupes = await carte._groupes(
        session,
        SCOPE_44_85,
        hebergement("ban", "cadastre", "dvf", scope=SCOPE_44_85),
        _emprise(),
        lignes,
    )

    assert len(session.appels) == 2
    (liens_sql, liens), (cadastre_sql, cadastre) = session.appels
    assert "mutation_parcelles" in liens_sql and "mutations_grain" not in liens_sql
    assert liens == {"ids": ["vente-c", "vente-a", "vente-b", "vente-d"]}
    assert "parcelles" in cadastre_sql and "mutation" not in cadastre_sql
    # Every parcel of a sale of several, once; the departements come from the
    # identifiers, and the one whose cadastre is not hosted is not read.
    assert cadastre == {
        "ids": [AILLEURS_44, NON_HEBERGEE_49, G1, G2, G3],
        "departements": ["44", "85"],
        **EMPRISE,
    }
    # One entry per sale, in the order of the page.
    assert [None if g is None else g.total for g in groupes] == [3, None, 2, 2]
    trois, _, autre_departement, non_hebergee = groupes
    assert (trois.absentes_du_cadastre, trois.surface_totale_m2) == (1, None)
    assert [p.id_parcelle for p in trois.parcelles] == [G1, G2]
    assert autre_departement.surface_totale_m2 == 160
    assert [p.id_parcelle for p in autre_departement.parcelles] == [AILLEURS_44, G1]
    # Not hosted: not in our cadastre, so no total.
    assert (non_hebergee.absentes_du_cadastre, non_hebergee.surface_totale_m2) == (
        1,
        None,
    )

    _regler(monkeypatch, "carte_groupe_geometries_max", 1)
    plafonnes = await carte._groupes(
        session,
        SCOPE_44_85,
        hebergement("ban", "cadastre", "dvf", scope=SCOPE_44_85),
        _emprise(),
        lignes,
    )
    # Two parcels to draw are beyond a cap of one; one is not.
    assert plafonnes[0].parcelles is None and plafonnes[2].parcelles is None
    assert (plafonnes[0].total, plafonnes[0].autres) == (3, 2)
    assert plafonnes[2].surface_totale_m2 == 160
    assert plafonnes[1] is None
    assert [p.id_parcelle for p in plafonnes[3].parcelles] == [G1]


async def test_sales_of_one_parcel_each_read_no_cadastre_row():
    from app.carte import ventes as carte

    session = _SessionDesGroupes(
        liens=[("vente-a", G1), ("vente-b", G1)], cadastre=[_au_cadastre(G1)]
    )
    lignes = [{"id_mutation": "vente-a"}, {"id_mutation": "vente-b"}]

    groupes = await carte._groupes(
        session, FULL_SCOPE, hebergement("ban", "cadastre", "dvf"), _emprise(), lignes
    )

    assert groupes == [None, None]
    assert len(session.appels) == 1 and "mutation_parcelles" in session.appels[0][0]


async def test_a_page_without_sale_reads_nothing_for_its_groups():
    from app.carte import ventes as carte

    session = _SessionDesGroupes(liens=[], cadastre=[])

    groupes = await carte._groupes(
        session, FULL_SCOPE, hebergement("ban", "cadastre", "dvf"), _emprise(), []
    )

    assert groupes == [] and session.appels == []


def test_the_sales_route_is_in_the_published_contract():
    contrat = json.loads(
        (BACKEND.parents[1] / "contracts/openapi.v1.json").read_text(encoding="utf-8")
    )

    operation = contrat["paths"][GABARIT]["get"]
    assert operation["summary"].isascii() and operation["description"].isascii()
    schemas = contrat["components"]["schemas"]
    assert set(schemas["GroupeVendu"]["properties"]) == {
        "total",
        "autres",
        "absentes_du_cadastre",
        "hors_emprise",
        "sans_surface",
        "surface_totale_m2",
        "parcelles",
    }

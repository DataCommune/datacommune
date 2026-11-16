"""The report service and its route, without a database (04.7-01).

The identity is read first; its transaction ends before any upstream call
(API CR-01); the upstream readers run in parallel; each theme is read in the
order of the registry; a theme or an upstream that raises is the outage of
its own row only; the cache serves a report without any read and never
keeps an outage. The route keeps the constant problems of the address page.
"""

from datetime import date

import pytest

from app.adresse.schemas import (
    CommuneRef,
    CouvertureAdresse,
    DepartementRef,
    FicheAdresse,
    IdentiteAdresse,
)
from app.adresse.service import (
    AdresseInconnueError,
    AdresseIndisponibleError,
    VoieSansNumeroError,
)
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.database import get_db
from app.main import app
from app.rapport import cache, router, service
from app.rapport.registre import THEMES
from app.rapport.schemas import Granularite, Rattachement
from app.rapport.socle import DefinitionTheme, PanneAmont, sans_reponse, theme
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit

AS_OF = date(2026, 10, 30)
BAN_ID = "85191_0420_00012"
HEBERGE = DatasetHosting(etat="heberge", departements=["85"])
HOSTING = RouteHosting({"ban": HEBERGE, "cadastre": HEBERGE})


class _Session:
    def __init__(self, journal):
        self.journal = journal

    async def rollback(self):
        self.journal.append("rollback")


def _fiche(etat="heberge") -> FicheAdresse:
    return FicheAdresse(
        identite=IdentiteAdresse(
            ban_id=BAN_ID,
            commune=CommuneRef(code_insee="85191", nom="La Roche-sur-Yon"),
            departement=DepartementRef(code="85", nom="Vendée"),
        ),
        couverture=CouvertureAdresse(
            etat=etat,
            motif=OutOfCoverageReason.DATASET_NON_HEBERGE
            if etat != "heberge"
            else None,
        ),
    )


@pytest.fixture(autouse=True)
def _cache_vide():
    cache.vider()
    yield
    cache.vider()


@pytest.fixture
def journal(monkeypatch):
    evenements: list[str] = []

    async def identite(session, ban_id, hosting):
        evenements.append("identite")
        return _fiche(), None

    monkeypatch.setattr(service, "lire_identite", identite)
    return evenements


def _definition(id_, *, ton="neutre", lire=None, amont=None, entite=None, ordre=1):
    async def lire_defaut(contexte):
        contexte_journal = contexte.amonts.get(id_, "aucun")
        return theme(
            DEFINITIONS[id_],
            etat="ok",
            ton=ton,
            verdict=f"Lu avec {contexte_journal}.",
            rattachement=Rattachement(entite=entite, confiance="sure")
            if entite
            else None,
        )

    definition = DefinitionTheme(
        id=id_,
        section="marche",
        ordre=ordre,
        titre=id_,
        granularite=Granularite(niveau="adresse"),
        sources=("ban",),
        lire=lire or lire_defaut,
        amont=amont,
        rattachement_entite=entite,
    )
    DEFINITIONS[id_] = definition
    return definition


DEFINITIONS: dict[str, DefinitionTheme] = {}


async def _rapport(journal, definitions):
    return await service.lire_rapport(
        _Session(journal), BAN_ID, HOSTING, None, AS_OF, definitions
    )


async def test_identity_then_rollback_then_upstreams_then_themes(journal):
    async def amont(contexte):
        journal.append("amont")
        return "amont lu"

    async def lire(contexte):
        journal.append("theme")
        return theme(
            DEFINITIONS["b"],
            etat="ok",
            ton="neutre",
            verdict=f"{contexte.amonts['b']}.",
        )

    definitions = [_definition("a"), _definition("b", amont=amont, lire=lire)]
    rapport = await _rapport(journal, definitions)
    assert journal == ["identite", "rollback", "amont", "theme"]
    assert [t.id for t in rapport.themes] == ["a", "b"]
    assert rapport.themes[1].verdict == "amont lu."


async def test_a_theme_that_raises_is_its_own_outage_only(journal):
    async def casse(contexte):
        raise RuntimeError("bug")

    definitions = [
        _definition("a", lire=casse, entite="parcelle_du_point"),
        _definition("b"),
    ]
    rapport = await _rapport(journal, definitions)
    a, b = rapport.themes
    assert (a.etat, a.ton, a.motif) == ("panne", "info", "erreur_theme")
    assert a.rattachement is not None and a.rattachement.confiance == "incertaine"
    assert b.etat == "ok"


async def test_an_upstream_that_raises_reaches_its_theme_as_an_outage(journal):
    async def amont(contexte):
        raise TimeoutError

    async def lire(contexte):
        assert isinstance(contexte.amonts["a"], PanneAmont)
        return theme(DEFINITIONS["a"], etat="ok", ton="neutre", verdict="Vu.")

    rapport = await _rapport(journal, [_definition("a", amont=amont, lire=lire)])
    assert rapport.themes[0].etat == "ok"


async def test_a_mandatory_attachment_missing_is_an_outage(journal):
    async def sans_rattachement(contexte):
        return theme(DEFINITIONS["a"], etat="ok", ton="neutre", verdict="Sans entité.")

    definitions = [_definition("a", lire=sans_rattachement, entite="parcelle_du_point")]
    rapport = await _rapport(journal, definitions)
    assert rapport.themes[0].etat == "panne"


async def test_the_cache_serves_without_any_read_and_never_keeps_an_outage(journal):
    definitions = [_definition("a", ton="vigilance")]
    premier = await _rapport(journal, definitions)
    second = await _rapport(journal, definitions)
    assert second is premier
    assert journal.count("identite") == 1
    assert [p.theme for p in premier.resume.points_adresse] == ["a"]

    cache.vider()

    async def casse(contexte):
        raise RuntimeError

    en_panne = [_definition("b", lire=casse)]
    await _rapport(journal, en_panne)
    await _rapport(journal, en_panne)
    assert journal.count("identite") == 3


async def test_an_address_out_of_coverage_reads_no_theme(monkeypatch, journal):
    async def identite(session, ban_id, hosting):
        journal.append("identite")
        return _fiche("hors_couverture"), None

    monkeypatch.setattr(service, "lire_identite", identite)

    async def jamais(contexte):
        raise AssertionError("no theme is read out of coverage")

    rapport = await _rapport(journal, [_definition("a", lire=jamais)])
    assert rapport.themes == [] and journal == ["identite"]
    assert {c.etat for c in rapport.resume.chiffres_cles} == {"hors_couverture"}


def test_the_registry_serves_the_route():
    assert service.lire_rapport.__defaults__[-1] is THEMES


# --- Route --------------------------------------------------------------------


async def _sans_base():
    yield None


@pytest.mark.parametrize(
    ("erreur", "statut", "code"),
    [
        (VoieSansNumeroError, 404, "voie_sans_numero"),
        (AdresseInconnueError, 404, "adresse_inconnue"),
        (AdresseIndisponibleError, 503, "adresse_indisponible"),
    ],
)
async def test_route_problems_are_constant_and_never_stored(
    client, monkeypatch, erreur, statut, code
):
    async def leve(*args, **kwargs):
        raise erreur

    monkeypatch.setattr(router, "lire_rapport", leve)
    app.dependency_overrides[get_db] = _sans_base
    reponse = await client.get(f"/v1/adresse/{BAN_ID}/rapport")
    assert reponse.status_code == statut
    corps = reponse.json()
    assert corps["code"] == code and BAN_ID not in reponse.text
    assert reponse.headers["cache-control"] == "no-store"


async def test_route_serves_the_report_and_is_never_stored(
    client, monkeypatch, journal
):
    definitions = [_definition("a", ton="rassurant")]

    async def lire(db, ban_id, hosting, scope, as_of):
        assert "dpe" in hosting.hostings and "errial" in hosting.hostings
        return await service.lire_rapport(
            _Session(journal), ban_id, hosting, scope, as_of, definitions
        )

    monkeypatch.setattr(router, "lire_rapport", lire)
    app.dependency_overrides[get_db] = _sans_base
    reponse = await client.get(f"/v1/adresse/{BAN_ID}/rapport")
    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    donnees = reponse.json()["donnees"]
    assert donnees["themes"][0]["id"] == "a"
    assert [p["theme"] for p in donnees["resume"]["rassurants"]] == ["a"]
    assert len(donnees["resume"]["chiffres_cles"]) == 4


async def test_route_refuses_a_malformed_key(client):
    app.dependency_overrides[get_db] = _sans_base
    reponse = await client.get("/v1/adresse/pas-une-cle/rapport")
    assert reponse.status_code == 422


@pytest.mark.parametrize(
    "motif", ["aucun_indice_du_jour", "indices_contradictoires", "indice_absent"]
)
async def test_a_gap_of_the_day_is_never_kept(journal, motif):
    # The ATMO index of the day may be published, or computed, later on.
    async def manque(contexte):
        return sans_reponse(DEFINITIONS["a"], "manque", motif)

    definitions = [_definition("a", lire=manque)]
    await _rapport(journal, definitions)
    await _rapport(journal, definitions)
    assert journal.count("identite") == 2

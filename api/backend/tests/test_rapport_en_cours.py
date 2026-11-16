"""The first view of an address answers fast and says « read in progress » (R-07).

Measured 2026-11-10: a first view waited for its slowest upstream read, up to
the deadline (5 s), then wrote « indisponible » on sources that answered two
seconds later. With ``attente="courte"`` the report is served after a short
wait: a theme whose upstream read has not ended is ``en_cours``, never an
outage; the reads go on, shared by key, and ``attente="complete"`` waits for
them without calling a producer twice. Without the parameter nothing changes.
"""

import asyncio
import time

import pytest
from pydantic import ValidationError

from app.database import get_db
from app.main import app
from app.rapport import cache, partage, router, service, suite
from app.rapport.schemas import Theme
from app.rapport.socle import PanneAmont, sans_reponse, theme
from tests.test_rapport_service import (
    AS_OF,
    BAN_ID,
    DEFINITIONS,
    HOSTING,
    _definition,
    _fiche,
    _sans_base,
    _Session,
)

pytestmark = pytest.mark.unit

ATTENTE_S = 0.2
LENT_S = 0.6  # answers after the short wait, well before the end of the wait
FIN_S = 1.5
MARGE_S = 0.25
# asyncio arms its deadlines on the monotonic clock (15.6 ms steps on Windows)
# and the tests time with perf_counter: a wait may read one step short.
PAS_HORLOGE_S = time.get_clock_info("monotonic").resolution


@pytest.fixture(autouse=True)
def _delais_courts(monkeypatch):
    cache.vider()
    monkeypatch.setattr(suite, "ATTENTE_COURTE_S", ATTENTE_S)
    monkeypatch.setattr(suite, "ECHEANCE_SUITE_S", FIN_S)
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


class _Amont:
    """An upstream reader that answers after *delai_s*, bounded as the readers
    of ``partage`` are: by the deadline of the view that started it."""

    def __init__(self, delai_s: float, en_panne: bool = False) -> None:
        self.delai_s, self.en_panne, self.appels = delai_s, en_panne, 0

    async def __call__(self, contexte):
        self.appels += 1
        try:
            async with asyncio.timeout(partage.echeance_s()):
                await asyncio.sleep(self.delai_s)
        except TimeoutError:
            return PanneAmont()
        if self.en_panne:
            raise RuntimeError("producer down")
        return "lu"


def _lire_amont(id_):
    async def lire(contexte):
        lu = contexte.amonts[id_]
        if isinstance(lu, PanneAmont):
            return sans_reponse(DEFINITIONS[id_], "panne", "amont_indisponible")
        return theme(DEFINITIONS[id_], etat="ok", ton="neutre", verdict=f"{lu}.")

    return lire


def _definitions(lent: _Amont, rapide: _Amont | None = None):
    return [
        _definition("base"),
        _definition("rapide", amont=rapide or _Amont(0.0), lire=_lire_amont("rapide")),
        _definition("lent", amont=lent, lire=_lire_amont("lent")),
    ]


async def _vue(journal, definitions, attente=None):
    debut = time.perf_counter()
    rapport = await service.lire_rapport(
        _Session(journal), BAN_ID, HOSTING, None, AS_OF, definitions, attente=attente
    )
    return rapport, time.perf_counter() - debut


def _etats(rapport) -> dict[str, str]:
    return {t.id: t.etat for t in rapport.themes}


async def test_a_short_wait_serves_the_slow_read_as_in_progress(journal):
    rapport, duree = await _vue(journal, _definitions(_Amont(LENT_S)), "courte")
    assert duree < ATTENTE_S + MARGE_S, f"{duree:.2f} s"
    assert _etats(rapport) == {"base": "ok", "rapide": "ok", "lent": "en_cours"}
    assert rapport.lectures_en_cours == ["lent"]
    lent = rapport.themes[2]
    assert lent.ton == "info" and lent.chiffres == []
    assert lent.verdict == "La source n\u2019a pas encore répondu."
    assert lent.accroche == "Lecture en cours"
    # Neither an outage, nor an answer, nor a gap.
    resume = rapport.resume
    assert [p.theme for p in resume.en_cours] == ["lent"]
    assert resume.pannes == [] and resume.sources_en_panne == []
    assert resume.sans_valeur == [] and resume.lus == 2
    assert resume.total_criteres == 3


async def test_the_follow_up_waits_for_the_read_and_calls_no_producer_twice(journal):
    lent, rapide = _Amont(LENT_S), _Amont(0.0)
    definitions = _definitions(lent, rapide)
    premier, _ = await _vue(journal, definitions, "courte")
    assert premier.lectures_en_cours == ["lent"]
    suite_, duree = await _vue(journal, definitions, "complete")
    assert _etats(suite_) == {"base": "ok", "rapide": "ok", "lent": "ok"}
    assert suite_.lectures_en_cours == [] and suite_.resume.en_cours == []
    # It ends with the read, not with a deadline of its own.
    assert duree < LENT_S, f"{duree:.2f} s"
    assert (lent.appels, rapide.appels) == (1, 1)


async def test_a_report_with_a_read_in_progress_is_never_kept(journal):
    lent = _Amont(LENT_S)
    definitions = _definitions(lent)
    premier, _ = await _vue(journal, definitions, "courte")
    assert premier.lectures_en_cours == ["lent"]
    await asyncio.sleep(LENT_S)  # the read ends while nobody waits
    # A reload (no JavaScript) finds the answer of the read it left.
    second, duree = await _vue(journal, definitions, "courte")
    assert _etats(second)["lent"] == "ok" and second.lectures_en_cours == []
    assert duree < 0.1, f"{duree:.2f} s"
    assert lent.appels == 1
    assert journal.count("identite") == 2


async def test_a_read_that_passes_the_end_of_the_wait_is_an_outage(journal):
    muet = _Amont(60.0)
    definitions = _definitions(muet)
    premier, _ = await _vue(journal, definitions, "courte")
    assert _etats(premier)["lent"] == "en_cours"
    suite_, duree = await _vue(journal, definitions, "complete")
    assert _etats(suite_)["lent"] == "panne"
    assert suite_.lectures_en_cours == []
    assert duree < FIN_S + MARGE_S, f"{duree:.2f} s"
    assert muet.appels == 1


async def test_a_producer_that_fails_fast_is_an_outage_at_once(journal):
    rapport, _ = await _vue(journal, _definitions(_Amont(0.0, en_panne=True)), "courte")
    assert _etats(rapport)["lent"] == "panne"
    assert rapport.lectures_en_cours == []


async def test_two_first_views_share_the_reads(journal):
    lent = _Amont(LENT_S)
    definitions = _definitions(lent)
    (une, _), (autre, _) = await asyncio.gather(
        _vue(journal, definitions, "courte"), _vue(journal, definitions, "courte")
    )
    assert une.lectures_en_cours == autre.lectures_en_cours == ["lent"]
    assert lent.appels == 1


async def test_a_follow_up_without_first_view_reads_everything(journal):
    # The process restarted, or the reads were forgotten: nothing is lost.
    lent = _Amont(LENT_S)
    rapport, _ = await _vue(journal, _definitions(lent), "complete")
    assert _etats(rapport)["lent"] == "ok" and lent.appels == 1


async def test_without_the_parameter_nothing_changes(journal, monkeypatch):
    # The deadline of the page, the wait for every read, no new state.
    monkeypatch.setattr(partage, "DEADLINE_S", 0.4)
    lent = _Amont(LENT_S)
    rapport, duree = await _vue(journal, _definitions(lent))
    assert _etats(rapport)["lent"] == "panne"
    assert 0.4 - PAS_HORLOGE_S <= duree < 0.4 + MARGE_S
    assert rapport.lectures_en_cours == []
    servi, duree = await _vue(journal, _definitions(_Amont(0.3)))
    assert _etats(servi)["lent"] == "ok" and duree >= 0.3 - PAS_HORLOGE_S


async def test_a_mandatory_attachment_is_served_while_in_progress(journal):
    definitions = [
        _definition(
            "lie", amont=_Amont(LENT_S), lire=_lire_amont("lie"), entite="parcelle"
        )
    ]
    rapport, _ = await _vue(journal, definitions, "courte")
    lie = rapport.themes[0]
    assert lie.etat == "en_cours"
    assert lie.rattachement is not None
    assert lie.rattachement.confiance == "incertaine"


def test_in_progress_carries_the_tone_info_and_no_figure():
    base = {
        "id": "a",
        "section": "marche",
        "titre": "a",
        "granularite": {"niveau": "adresse"},
        "verdict": "v",
        "sources": [
            {
                "cle": "ban",
                "nom": "n",
                "producteur": "p",
                "url": "u",
                "licence": "l",
                "mode": "import",
            }
        ],
    }
    assert Theme(**base, etat="en_cours", ton="info").etat == "en_cours"
    with pytest.raises(ValidationError):
        Theme(**base, etat="en_cours", ton="rassurant")


async def test_the_route_relays_the_wait_and_refuses_another(
    client, monkeypatch, journal
):
    vues = []

    async def lire(db, ban_id, hosting, scope, as_of, **options):
        vues.append(options)
        return await service.lire_rapport(
            _Session([]), ban_id, hosting, scope, as_of, [_definition("a")], **options
        )

    monkeypatch.setattr(router, "lire_rapport", lire)
    app.dependency_overrides[get_db] = _sans_base
    for attente in ("courte", "complete"):
        reponse = await client.get(
            f"/v1/adresse/{BAN_ID}/rapport", params={"attente": attente}
        )
        assert reponse.status_code == 200
        assert reponse.headers["cache-control"] == "no-store"
        assert reponse.json()["donnees"]["lectures_en_cours"] == []
    await client.get(f"/v1/adresse/{BAN_ID}/rapport")
    assert vues == [{"attente": "courte"}, {"attente": "complete"}, {}]
    refus = await client.get(
        f"/v1/adresse/{BAN_ID}/rapport", params={"attente": "longue"}
    )
    assert refus.status_code == 422

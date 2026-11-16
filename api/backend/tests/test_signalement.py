"""Error report of the site (lot 1): ``POST /v1/signalements``.

The route reopens a public write, so its bounds are the subject: off by
default (404), per-visitor buckets, a daily ceiling counted in the database,
bounded sizes, a honeypot that answers like a success and stores nothing, plain
text only, no address of the visitor stored. Real limiter and middleware; only
the two database calls of the service are replaced.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import settings
from app.database import get_db
from app.main import app
from app.signalement import router as signalement_router
from app.signalement.schemas import SignalementDepot

pytestmark = pytest.mark.unit

VALIDE = {
    "commentaire": "Le prix médian des maisons paraît dix fois trop bas.",
    "page": "/commune/44109",
    "titre": "Prix médian des maisons",
    "lignes": "dvf.prix_m2_median_maison=312",
    "source": "DVF",
    "version_site": "81d3926f",
    "largeur": 1280,
    "user_agent": "Mozilla/5.0",
}


async def _mock_db():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    yield session


@pytest.fixture()
def actif(monkeypatch):
    monkeypatch.setattr(settings, "signalement_enabled", True)


@pytest.fixture()
def base():
    """The two database calls of the route: the 24 h count and the insert."""
    with (
        patch.object(
            signalement_router, "compter_24h", new_callable=AsyncMock, return_value=0
        ) as compter,
        patch.object(
            signalement_router, "enregistrer", new_callable=AsyncMock
        ) as enregistrer,
    ):
        yield compter, enregistrer


@pytest.fixture()
async def client():
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_off_by_default_the_route_answers_404_and_stores_nothing(client, base):
    assert settings.signalement_enabled is False

    reponse = await client.post("/v1/signalements", json=VALIDE)

    assert reponse.status_code == 404
    base[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_valid_report_is_stored_and_answered_with_a_random_reference(
    client, base, actif
):
    reponse = await client.post("/v1/signalements", json=VALIDE)

    assert reponse.status_code == 201
    assert reponse.headers["Cache-Control"] == "no-store"
    reference = reponse.json()["reference"]
    assert set(reponse.json()) == {"reference"}
    assert len(reference) == 10
    depot, reference_stockee = base[1].await_args.args[1:]
    assert reference_stockee == reference
    assert depot.commentaire == VALIDE["commentaire"]
    assert depot.email is None

    autre = await client.post("/v1/signalements", json=VALIDE)
    assert autre.json()["reference"] != reference


@pytest.mark.asyncio
async def test_the_address_of_the_visitor_is_never_handed_to_the_storage(
    client, base, actif
):
    await client.post(
        "/v1/signalements", json=VALIDE, headers={"X-Forwarded-For": "203.0.113.9"}
    )

    stocke = base[1].await_args.args[1].model_dump()
    assert "203.0.113.9" not in repr(stocke)
    assert not {"ip", "client_ip", "adresse_ip"} & set(stocke)


@pytest.mark.asyncio
async def test_a_filled_honeypot_gets_the_answer_of_a_success_and_stores_nothing(
    client, base, actif
):
    reponse = await client.post(
        "/v1/signalements", json={**VALIDE, "piege": "https://spam.example"}
    )

    assert reponse.status_code == 201
    assert len(reponse.json()["reference"]) == 10
    base[0].assert_not_awaited()
    base[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_the_4th_report_of_ten_minutes_from_one_visitor_is_refused(
    client, base, actif
):
    statuts = [
        (await client.post("/v1/signalements", json=VALIDE)).status_code
        for _ in range(4)
    ]

    assert statuts == [201, 201, 201, 429]
    assert base[1].await_count == 3


@pytest.mark.asyncio
async def test_an_invalid_payload_spends_the_bucket_like_a_valid_one(
    client, base, actif
):
    statuts = [
        (await client.post("/v1/signalements", json={"commentaire": ""})).status_code
        for _ in range(4)
    ]

    assert statuts == [422, 422, 422, 429]


@pytest.mark.asyncio
async def test_one_visitor_at_its_limit_does_not_block_another(
    client, base, actif, monkeypatch
):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    premier = {"CF-Connecting-IP": "203.0.113.1"}
    for _ in range(3):
        await client.post("/v1/signalements", json=VALIDE, headers=premier)
    assert (
        await client.post("/v1/signalements", json=VALIDE, headers=premier)
    ).status_code == 429

    autre = await client.post(
        "/v1/signalements", json=VALIDE, headers={"CF-Connecting-IP": "203.0.113.2"}
    )

    assert autre.status_code == 201


@pytest.mark.asyncio
async def test_the_daily_bucket_of_a_visitor_holds_beyond_the_ten_minutes(
    client, base, actif, monkeypatch
):
    # The short bucket is opened wide: only the daily one can refuse.
    monkeypatch.setattr(settings, "signalement_par_ip_10_min", 1000)

    statuts = [
        (await client.post("/v1/signalements", json=VALIDE)).status_code
        for _ in range(11)
    ]

    assert statuts == [201] * 10 + [429]


@pytest.mark.asyncio
async def test_the_daily_ceiling_of_the_whole_site_refuses_and_stores_nothing(
    client, base, actif
):
    base[0].return_value = 200

    reponse = await client.post("/v1/signalements", json=VALIDE)

    assert reponse.status_code == 429
    assert reponse.headers["Cache-Control"] == "no-store"
    base[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_database_failure_is_a_constant_503_that_reflects_nothing(
    client, base, actif
):
    base[1].side_effect = RuntimeError("connection refused to db:5432")

    reponse = await client.post("/v1/signalements", json=VALIDE)

    assert reponse.status_code == 503
    assert "db:5432" not in reponse.text
    assert VALIDE["commentaire"] not in reponse.text


@pytest.mark.asyncio
async def test_a_refusal_of_the_schema_never_echoes_what_was_sent(client, base, actif):
    reponse = await client.post(
        "/v1/signalements", json={**VALIDE, "email": "secret-pas-un-email"}
    )

    assert reponse.status_code == 422
    assert "secret-pas-un-email" not in reponse.text


@pytest.mark.parametrize(
    "champs",
    [
        {"commentaire": "court"},
        {"commentaire": "x" * 2001},
        {"email": "pas-un-email"},
        {"email": "a@" + "b" * 250 + ".fr"},
        {"page": "https://ailleurs.example/commune/44109"},
        {"page": "//ailleurs.example"},
        {"page": "/" + "a" * 300},
        {"titre": "t" * 201},
        {"lignes": "l" * 601},
        {"source": "s" * 201},
        {"version_site": "v" * 65},
        {"version_site": "<script>"},
        {"largeur": -1},
        {"largeur": 100_000},
        {"user_agent": "u" * 401},
        {"inconnu": "champ en trop"},
    ],
)
def test_out_of_bounds_fields_are_refused(champs):
    with pytest.raises(ValidationError):
        SignalementDepot(**{**VALIDE, **champs})


def test_text_is_kept_as_written_but_for_control_characters():
    depot = SignalementDepot(
        **{
            **VALIDE,
            "commentaire": "  <b>gras</b> @ianis\x00\x07 ligne 1\r\nligne 2  ",
            "email": "Lecteur@Example.FR",
            "page": "/adresse/44109_1234_00012?x=1#section",
        }
    )

    # Markup is not interpreted anywhere: it is stored as the characters typed.
    assert depot.commentaire == "<b>gras</b> @ianis ligne 1\nligne 2"
    assert depot.email == "lecteur@example.fr"
    # The path alone: no query string, no fragment.
    assert depot.page == "/adresse/44109_1234_00012"


def test_empty_optional_fields_are_absent_rather_than_empty():
    depot = SignalementDepot(
        commentaire=VALIDE["commentaire"], email="  ", page="", titre=" ", largeur=None
    )

    assert depot.email is None
    assert depot.page is None
    assert depot.titre is None


# -- Deployment: the table must survive a national switch and be backed up ----

_INFRA_PUBLIC = Path(__file__).resolve().parents[3] / "infra" / "public"


@pytest.mark.parametrize(
    ("script", "liste"),
    [("public-db-restore.sh", "APP_TABLES"), ("accounts-dump.sh", "TABLES")],
)
def test_the_table_is_among_the_application_tables_kept_by_the_scripts(script, liste):
    """A national switch replaces the public database by a clone of the work
    base and reloads ``APP_TABLES`` only; the daily backup carries ``TABLES``
    only. Left out of either, the reports are lost."""
    texte = (_INFRA_PUBLIC / script).read_text(encoding="utf-8")
    trouve = re.search(rf"^readonly {liste}=\(([^)]*)\)", texte, re.MULTILINE)

    assert trouve, f"{liste} not found in {script}"
    assert "signalements" in trouve.group(1).split()

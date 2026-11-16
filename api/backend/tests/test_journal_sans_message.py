"""C60: the log of an unhandled error carries no exception message.

Outside DEBUG, ``log_unhandled_exception`` writes the qualified types, the
route template (never the real path), the method, a request id returned in
``X-Request-ID`` and the traceback frames; no message of any exception of the
chain (cause or context), nor any note. With DEBUG the full message is kept
for local debugging. ``settings.debug`` is set explicitly by each test
(``monkeypatch``): the unit runs default to ``DEBUG=true``.

Also C60: the cursor error raised detached from its decoding error, the
aggregated connection failure of asyncio counted as an outage, and the 401
of the address routes rendered with the route template and ``no-store``.
"""

import asyncio
import errno
import logging
import re
import socket
from base64 import urlsafe_b64encode
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import check_quota_hybrid
from app.config import settings
from app.database import get_db
from app.main import app
from tests.test_geocodage_erreurs import (
    _handler_json,
    _journal,
    _session_defaillante,
)

pytestmark = pytest.mark.unit

SECRET = "1 rue confidentielle nantes"
CLE = "44109_7777_00042"
# Words of the inputs that must appear in no log record (lower case).
MOTS = ("confidentielle", "nantes", "7777_00042")


def _defaut_chaine(*args, **kwargs):
    """A defect whose message, note, cause and context all quote the input.

    The values come from variables: the traceback prints source lines, which
    are code, never the value a variable holds.
    """
    saisie, cle = SECRET, CLE
    try:
        try:
            raise KeyError(f"context quoting {saisie} {cle}")
        except KeyError:
            # Implicit context: no ``from``.
            raise ValueError(f"cause quoting {saisie} {cle}")  # noqa: B904
    except ValueError as cause:
        erreur = RuntimeError(f"defect quoting {saisie} {cle}")
        erreur.add_note(f"note quoting {saisie}")
        raise erreur from cause


@pytest.fixture
def db_factice():
    async def db():
        yield MagicMock()

    app.dependency_overrides[get_db] = db


async def _requete_journalisee(client, caplog, chemin, params=None):
    """The response and every log output (records, JSON as in production)."""
    handler, flux = _handler_json()
    racine = logging.getLogger()
    racine.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        # ASGITransport raises the app exceptions it receives: a response
        # proves the middleware absorbed the error, which the ASGI server
        # (uvicorn's error logger) therefore never sees.
        response = await client.get(chemin, params=params)
    finally:
        racine.removeHandler(handler)
    records = [
        record
        for record in caplog.records
        if record.name == "app.common.exceptions" and record.levelno >= logging.ERROR
    ]
    return response, records, _journal(caplog), flux.getvalue().lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chemin", "params", "gabarit", "cible"),
    [
        pytest.param(
            "/v1/dvf/adresse",
            {"q": SECRET},
            "/v1/dvf/adresse",
            "app.dvf.router.geocoder",
            id="dvf-query",
        ),
        pytest.param(
            f"/v1/adresse/{CLE}",
            None,
            "/v1/adresse/{ban_id}",
            "app.adresse.router.lire_fiche_adresse",
            id="address-key",
        ),
    ],
)
async def test_outside_debug_the_500_log_carries_no_exception_message(
    client, caplog, monkeypatch, db_factice, chemin, params, gabarit, cible
):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(cible, _defaut_chaine)

    response, records, journal, sortie_json = await _requete_journalisee(
        client, caplog, chemin, params
    )

    assert response.status_code == 500, response.text
    request_id = response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    corps = response.json()
    assert corps["title"] == "Erreur interne du serveur"
    assert corps["status"] == 500
    assert corps["instance"] == gabarit
    # Logged once, with the metadata only (the capture works).
    assert len(records) == 1
    record = records[0]
    assert record.exc_info is None
    # Both outputs compared in lower case (the JSON one is lowered above).
    for sortie in (record.getMessage().lower(), sortie_json):
        assert "builtins.runtimeerror" in sortie
        assert "builtins.valueerror" in sortie
        assert "builtins.keyerror" in sortie
        assert gabarit in sortie
        assert "method=get" in sortie
        assert request_id in sortie
        # The frames are kept: file and function of the defect.
        assert "test_journal_sans_message.py" in sortie
        assert "_defaut_chaine" in sortie
    fuites = [
        (mot, sortie)
        for mot in MOTS
        for sortie, texte in (("records", journal), ("json", sortie_json))
        if mot in texte
    ]
    assert not fuites


@pytest.mark.asyncio
async def test_with_debug_the_500_log_keeps_the_message(
    client, caplog, monkeypatch, db_factice
):
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr("app.dvf.router.geocoder", _defaut_chaine)

    response, records, journal, _ = await _requete_journalisee(
        client, caplog, "/v1/dvf/adresse", {"q": SECRET}
    )

    assert response.status_code == 500, response.text
    assert "x-request-id" in response.headers
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert "defect quoting 1 rue confidentielle nantes" in journal
    assert "cause quoting 1 rue confidentielle nantes" in journal


def test_the_redacted_traceback_follows_the_chain_without_messages():
    from app.common.exceptions import _exception_chain, _traceback_without_messages

    with pytest.raises(RuntimeError) as raised:
        _defaut_chaine()
    chaine = _exception_chain(raised.value)
    assert [type(e) for e in chaine] == [RuntimeError, ValueError, KeyError]

    trace = _traceback_without_messages(raised.value)

    assert trace.count("Traceback (most recent call last):") == 3
    assert "builtins.KeyError: <message redacted>" in trace
    assert "builtins.RuntimeError: <message redacted>" in trace
    assert all(mot not in trace.lower() for mot in MOTS)


def test_a_suppressed_context_is_not_followed():
    from app.common.exceptions import _exception_chain

    try:
        try:
            raise KeyError("hidden")
        except KeyError:
            raise ValueError("outer") from None
    except ValueError as exc:
        assert _exception_chain(exc) == [exc]


# --- C60: the cursor error is detached from its decoding error -------------


def _curseur(valeur: str) -> str:
    return urlsafe_b64encode(valeur.encode()).decode().rstrip("=")


def test_an_invalid_cursor_is_raised_detached():
    from app.common.exceptions import ErreurApiError
    from app.common.pagination import decode_cursor, decode_cursor_str

    with pytest.raises(ErreurApiError) as raised:
        decode_cursor(_curseur(SECRET))
    assert raised.value.code == 400
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

    with pytest.raises(ErreurApiError) as raised:
        decode_cursor_str(urlsafe_b64encode(b"\xff\xfe").decode())
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_an_address_in_the_cursor_then_a_failing_close_logs_no_input(
    client, caplog, monkeypatch
):
    """Review sol-G, blocking: the scenario reproduced by gpt-6.1-sol.

    Under DEBUG (messages kept) too: the cursor error carries no decoding
    error, so the chain the failing close drags along quotes nothing.
    """
    from app import database

    monkeypatch.setattr(settings, "debug", True)

    async def geocoder(*args, **kwargs):
        return [
            {
                "ban_id": CLE,
                "code_commune": "44109",
                "latitude": 47.2,
                "longitude": -1.55,
            }
        ]

    monkeypatch.setattr("app.dvf.router.geocoder", geocoder)
    monkeypatch.setattr(
        database, "async_session", lambda: _session_defaillante(lambda sql: False)
    )
    assert get_db not in app.dependency_overrides  # the real dependency

    response, records, journal, sortie_json = await _requete_journalisee(
        client,
        caplog,
        "/v1/dvf/adresse",
        {"q": "12 rue de la paix", "curseur": _curseur(SECRET)},
    )

    assert response.status_code == 500, response.text
    assert len(records) == 1
    assert "synthetic close failure" in journal  # DEBUG keeps the message
    assert "curseur invalide" in journal
    assert "confidentielle" not in journal
    assert "confidentielle" not in sortie_json


class _SessionCitante:
    """Every statement fails with a driver error quoting its parameters."""

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        from sqlalchemy.exc import DBAPIError

        cite = " ".join(str(value) for value in (params or {}).values())
        raise DBAPIError("SELECT", params, Exception(f'invalid input "{cite}"'))


@pytest.mark.asyncio
async def test_the_dpe_readers_raise_their_outage_detached():
    from app.common.schemas import DatasetHosting
    from app.dpe import document_public
    from app.scope.dependencies import RouteHosting

    hosting = RouteHosting({"dpe": DatasetHosting(etat="heberge", departements=["44"])})
    with pytest.raises(document_public.DocumentIndisponibleError) as raised:
        await document_public.lire_document_public(
            _SessionCitante(), "2344E0123456A", hosting
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

    with pytest.raises(document_public.BanIndisponibleError) as raised:
        await document_public.lire_lignes_ban(_SessionCitante(), CLE, "44")
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


# --- C60: the aggregated connection failure of asyncio is an outage --------


def _ports_refuses(nombre: int) -> list[int]:
    sockets = [socket.socket() for _ in range(nombre)]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    return ports


def _echec_agrege_d_asyncio() -> OSError:
    """The real error of ``create_connection`` when every address is refused.

    A host with an IPv6 and an IPv4 address, both refused: asyncio raises one
    bare ``OSError("Multiple exceptions: ...")``, errno None. Reproduced with
    two refused local ports behind one name, on the selector loop of Linux
    (the proactor of Windows words both refusals alike and raises one).
    """
    ports = _ports_refuses(2)

    async def connecter():
        loop = asyncio.get_running_loop()

        async def getaddrinfo(host, port, *, family=0, type=0, proto=0, flags=0):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (h, p))
                for h, p in (("127.0.0.1", ports[0]), ("127.0.0.1", ports[1]))
            ]

        loop.getaddrinfo = getaddrinfo
        await loop.create_connection(asyncio.Protocol, "db.invalid", 5432)

    try:
        asyncio.run(connecter(), loop_factory=asyncio.SelectorEventLoop)
    except OSError as exc:
        return exc
    raise AssertionError("the connection was not refused")


@pytest.fixture(scope="module")
def echec_agrege() -> OSError:
    return _echec_agrege_d_asyncio()


def test_the_reproduced_failure_has_the_asyncio_shape(echec_agrege):
    assert type(echec_agrege) is OSError
    assert echec_agrege.errno is None
    assert str(echec_agrege).startswith("Multiple exceptions: ")


def test_the_aggregated_connection_failure_is_an_outage(echec_agrege):
    from app.ban.queries import _base_indisponible

    assert _base_indisponible(echec_agrege)
    # The same shape as worded on Linux, IPv6 then IPv4.
    assert _base_indisponible(
        OSError(
            "Multiple exceptions: "
            "[Errno 111] Connect call failed ('::1', 5432, 0, 0), "
            "[Errno 111] Connect call failed ('127.0.0.1', 5432)"
        )
    )


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError(errno.ENOENT, "root.crt"),
        PermissionError(errno.EACCES, "client.key"),
        OSError(errno.EINVAL, "invalid argument"),
        OSError("no errno"),
        # The prefix alone, on another type or with an errno, proves nothing.
        FileNotFoundError("Multiple exceptions: root.crt"),
        OSError(errno.EINVAL, "Multiple exceptions: invalid argument"),
    ],
    ids=repr,
)
def test_other_os_errors_stay_defects(exc):
    from app.ban.queries import _base_indisponible

    assert not _base_indisponible(exc)


@pytest.mark.asyncio
async def test_the_aggregated_connection_failure_is_the_geocoding_503(
    client, echec_agrege
):
    class _Session:
        def begin_nested(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, statement, params=None):
            raise echec_agrege

    async def db():
        yield _Session()

    app.dependency_overrides[get_db] = db

    response = await client.get("/v1/dvf/adresse", params={"q": SECRET})

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "geocodage_indisponible"


# --- C60: the 401 of the real authentication, on the address routes -------


@pytest.fixture
async def client_authentifie(full_scope_loader):
    """The ``client`` of conftest without its authentication bypass."""
    from app.common.publication_readiness import require_publication
    from app.scope.dependencies import get_instance_scope

    async def db():
        yield MagicMock()

    scope = full_scope_loader("")
    app.dependency_overrides[require_publication] = lambda: None
    app.dependency_overrides[get_instance_scope] = lambda: scope
    app.dependency_overrides[get_db] = db
    assert check_quota_hybrid not in app.dependency_overrides
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chemin", "gabarit"),
    [
        pytest.param(f"/v1/adresse/{CLE}", "/v1/adresse/{ban_id}", id="key"),
        pytest.param(f"/v1/adresse/{SECRET}", "/v1/adresse/{ban_id}", id="address"),
        pytest.param(
            f"/v1/adresse/{CLE}/ventes",
            "/v1/adresse/{ban_id}/ventes",
            id="key-subroute",
        ),
    ],
)
async def test_a_401_of_an_address_route_names_the_template_and_is_never_stored(
    client_authentifie, chemin, gabarit
):
    response = await client_authentifie.get(chemin)

    assert response.status_code == 401, response.text
    assert response.json()["instance"] == gabarit
    corps = response.text.lower()
    assert all(mot not in corps for mot in MOTS)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_a_401_elsewhere_names_the_template_without_no_store(
    client_authentifie,
):
    response = await client_authentifie.get("/v1/dvf/adresse", params={"q": SECRET})

    assert response.status_code == 401, response.text
    assert response.json()["instance"] == "/v1/dvf/adresse"
    assert "cache-control" not in response.headers


@pytest.mark.asyncio
async def test_no_store_is_merged_with_the_headers_of_the_error():
    from types import SimpleNamespace

    from app.common.exceptions import ErreurApiError, _handle_exception_api

    request = MagicMock()
    request.scope = {"route": SimpleNamespace(path="/v1/adresse/{ban_id}")}
    exc = ErreurApiError(503, "Indisponible", headers={"Retry-After": "60"})

    response = await _handle_exception_api(request, exc)

    assert response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"

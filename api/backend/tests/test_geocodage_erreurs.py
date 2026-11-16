"""C58: an unexpected error of the geocoding reads never logs the input (CR-01).

PRIV-01 (``app.resolve.telemetry``): the address typed by a visitor never
reaches a log record. An unexpected SQL error of the geocoder or of the
referential reader of ``/v1/adresses/recherche`` follows the 500 path, whose
middleware logs the exception: that exception must carry no input, neither
in its message (SQLAlchemy writes the bound parameters and the driver message
there) nor in a chained exception. The failing session raises, for every
statement, an error built like the worst real case: the statement parameters
and a driver message quoting the value (22P02 quotes it).

Only the session is replaced; the whole catalogue is hosted (``client``).
"""

import logging
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit

AVEC_CODE_POSTAL = "1 rue confidentielle 44000 Nantes"
# Without a postal code the search first looks the commune up in
# geo_communes (referential reader), with the input words as parameters.
SANS_CODE_POSTAL = "1 rue confidentielle Nantes"
# Words of the input that must appear in no log record (lower case).
MOTS_SAISIS = ("confidentielle", "nantes")


class _Session:
    """A session whose statements matching *echoue* fail with a defect."""

    def __init__(self, echoue) -> None:
        self.echoue = echoue

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if self.echoue(sql):
            raise _defaut(sql, params)
        result = MagicMock()
        result.first.return_value = None
        result.__iter__.return_value = iter([])
        return result


def _defaut(sql: str, params: dict) -> DBAPIError:
    """22P02 as the driver words it: the parameters and the value quoted."""

    class DriverError(Exception):
        pass

    cite = " ".join(str(value) for value in params.values())
    original = DriverError(f'invalid input syntax for type integer: "{cite}"')
    original.sqlstate = "22P02"
    return DBAPIError(sql, params, original)


def _journal(caplog) -> str:
    """Every server-side log record, as text: message, args, traceback, extras."""
    formatter = logging.Formatter()
    parts = []
    for record in caplog.records:
        # httpx logs the request URL of the test client itself, not the API.
        if record.name.startswith(("httpx", "httpcore")):
            continue
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        if record.exc_info:
            parts.append(formatter.formatException(record.exc_info))
        if record.exc_text:
            parts.append(record.exc_text)
        parts.append(repr(vars(record)))
    return "\n".join(parts).lower()


def _toutes(sql: str) -> bool:
    return True


def _sauf_detection_de_ville(sql: str) -> bool:
    return "communes_lookup" not in sql


def _referentiel(sql: str) -> bool:
    return "geo_communes" in sql


SCENARIOS = [
    pytest.param(
        "/v1/dvf/adresse", {"q": AVEC_CODE_POSTAL}, _toutes, id="dvf-city-detection"
    ),
    pytest.param(
        "/v1/dvf/adresse",
        {"q": AVEC_CODE_POSTAL},
        _sauf_detection_de_ville,
        id="dvf-strategy",
    ),
    pytest.param(
        "/v1/adresses/recherche",
        {"q": AVEC_CODE_POSTAL},
        _toutes,
        id="search-city-detection",
    ),
    pytest.param(
        "/v1/adresses/recherche",
        {"q": AVEC_CODE_POSTAL},
        _sauf_detection_de_ville,
        id="search-strategy",
    ),
    # WR-03: the referential reader of the search (geo_communes lookup).
    pytest.param(
        "/v1/adresses/recherche",
        {"q": SANS_CODE_POSTAL},
        _referentiel,
        id="search-referential",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "params", "echoue"), SCENARIOS)
async def test_an_unexpected_sql_error_is_a_500_that_logs_no_input(
    client, caplog, route, params, echoue
):
    async def db():
        yield _Session(echoue)

    app.dependency_overrides[get_db] = db
    caplog.set_level(logging.DEBUG)

    response = await client.get(route, params=params)

    assert response.status_code == 500, response.text
    assert "geocodage_indisponible" not in response.text
    # The 500 was logged (the capture works), and without the input.
    assert any(
        record.name == "app.common.exceptions" and record.levelno == logging.ERROR
        for record in caplog.records
    )
    journal = _journal(caplog)
    for mot in MOTS_SAISIS:
        assert mot not in journal, mot


def test_the_engine_hides_sql_parameters_in_its_errors():
    # Global defence (CR-01 a): an error raised by the API engine never
    # writes its bound parameters, whatever the route.
    from app.database import engine

    assert engine.sync_engine.hide_parameters is True


# --- WR-01: availability failures by the SQLSTATE asyncpg actually carries ---


def _asyncpg(cls) -> DBAPIError:
    """*cls* raised by asyncpg, as SQLAlchemy hands it over.

    The asyncpg adapter of SQLAlchemy translates the driver error (its own
    translation table, never to ``OperationalError``) and SQLAlchemy wraps
    it, as on a real statement.
    """
    import asyncpg
    from sqlalchemy.dialects.postgresql.asyncpg import (
        AsyncAdapt_asyncpg_connection,
        AsyncAdapt_asyncpg_dbapi,
    )

    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    adapter = object.__new__(AsyncAdapt_asyncpg_connection)
    adapter.dbapi = dbapi
    adapter._connection = MagicMock(**{"is_closed.return_value": False})
    try:
        adapter._handle_exception(cls("synthetic"))
    except dbapi.Error as translated:
        return DBAPIError.instance(
            "SELECT synthetic", {"query": AVEC_CODE_POSTAL}, translated, dbapi.Error
        )
    raise AssertionError(f"{cls.__name__} not translated")


def _pannes():
    import asyncpg.exceptions as e

    return [
        e.ClientCannotConnectError,  # 08001
        e.ConnectionDoesNotExistError,  # 08003
        e.ConnectionFailureError,  # 08006
        e.DiskFullError,  # 53100
        e.OutOfMemoryError,  # 53200
        e.TooManyConnectionsError,  # 53300
        e.AdminShutdownError,  # 57P01
        e.CrashShutdownError,  # 57P02
        e.CannotConnectNowError,  # 57P03
        e.QueryCanceledError,  # 57014
        e.UndefinedTableError,  # 42P01
    ]


def _defauts():
    import asyncpg.exceptions as e

    return [
        e.InvalidTextRepresentationError,  # 22P02
        e.InFailedSQLTransactionError,  # 25P02
        e.DeadlockDetectedError,  # 40P01
        e.UndefinedColumnError,  # 42703
        e.UndefinedFunctionError,  # 42883
        e.OperatorInterventionError,  # 57000, not one of the three 57P0x
        e.InternalServerError,  # XX000
    ]


@pytest.mark.parametrize("cls", _pannes(), ids=lambda cls: cls.sqlstate)
def test_an_availability_sqlstate_is_an_outage(cls):
    from app.ban.queries import _base_indisponible

    assert _base_indisponible(_asyncpg(cls))


@pytest.mark.parametrize("cls", _defauts(), ids=lambda cls: cls.sqlstate)
def test_another_sqlstate_is_a_defect(cls):
    from app.ban.queries import _base_indisponible

    assert not _base_indisponible(_asyncpg(cls))


def test_an_unreachable_server_is_an_outage():
    # Observed on this stack: a refused connection reaches the caller as the
    # raw OSError of asyncpg, never wrapped by SQLAlchemy; a pool exhausted
    # raises SQLAlchemy's own TimeoutError.
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from app.ban.queries import _base_indisponible

    assert _base_indisponible(ConnectionRefusedError(22, "refused"))
    assert _base_indisponible(TimeoutError())
    assert _base_indisponible(PoolTimeoutError("QueuePool limit reached"))


def test_a_disconnection_detected_by_sqlalchemy_is_an_outage():
    import asyncpg.exceptions as e

    from app.ban.queries import _base_indisponible

    error = _asyncpg(e.InterfaceError)
    assert not _base_indisponible(error)
    error.connection_invalidated = True
    assert _base_indisponible(error)


def test_python_errors_and_an_operational_error_without_sqlstate_are_defects():
    # asyncpg never yields OperationalError through SQLAlchemy (its adapter
    # has no such translation): the class alone proves no outage.
    from sqlalchemy.exc import OperationalError

    from app.ban.queries import _base_indisponible

    assert not _base_indisponible(RuntimeError("synthetic"))
    assert not _base_indisponible(KeyError("latitude"))
    assert not _base_indisponible(OperationalError("SELECT 1", {}, Exception()))


# --- WR-03: the referential reader of /v1/adresses/recherche ----------------


class _Referentiel:
    """A session whose geo_communes read fails with *erreur*."""

    def __init__(self, erreur: Exception) -> None:
        self.erreur = erreur

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        if "geo_communes" in str(statement):
            raise self.erreur
        return MagicMock()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls", ["QueryCanceledError", "OutOfMemoryError"], ids=["57014", "53200"]
)
async def test_an_outage_of_the_referential_reader_is_the_geocoding_503(client, cls):
    import asyncpg.exceptions

    async def db():
        yield _Referentiel(_asyncpg(getattr(asyncpg.exceptions, cls)))

    app.dependency_overrides[get_db] = db

    response = await client.get(
        "/v1/adresses/recherche", params={"q": SANS_CODE_POSTAL}
    )

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "geocodage_indisponible"


@pytest.mark.asyncio
async def test_a_python_error_of_the_referential_reader_is_never_the_503(client):
    # A defect while reading the rows (here, iterating the result) is the
    # normal 500 path, never presented as a temporary outage (C55 fix 4).
    class _Resultat:
        def __iter__(self):
            raise TypeError("synthetic bug")

    class _Session(_Referentiel):
        async def execute(self, statement, params=None):
            return _Resultat()

    async def db():
        yield _Session(RuntimeError())

    app.dependency_overrides[get_db] = db

    response = await client.get(
        "/v1/adresses/recherche", params={"q": SANS_CODE_POSTAL}
    )

    assert response.status_code == 500, response.text
    assert "geocodage_indisponible" not in response.text


# --- C59 B1: an outage, then a failing session close (real get_db) ---------
#
# Review sol-F (B1): a 53200 whose driver message quotes the input becomes the
# typed 503, then ``AsyncSession.close()`` fails on the way out of ``get_db``.
# The close error escapes to ErrorHandlingMiddleware with, as its context,
# whatever the route raised: no log record may carry the input, neither the
# message, the arguments, the formatted traceback, nor the JSON handler of
# the application.


def _panne_citant(sql: str, params: dict) -> DBAPIError:
    """53200 whose driver message quotes the parameters, as 22P02 does.

    Built as the API engine does (``hide_parameters=True``): only the driver
    message can carry the value.
    """

    class DriverError(Exception):
        pass

    cite = " ".join(str(value) for value in params.values())
    original = DriverError(f'out of memory while reading "{cite}"')
    original.sqlstate = "53200"
    return DBAPIError(sql, params, original, hide_parameters=True)


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_defaillante(echoue):
    """A real AsyncSession: *echoue* statements fail with a cited 53200 and
    ``close()`` fails, as the real ``get_db`` closes it (``__aexit__``)."""
    from sqlalchemy.ext.asyncio import AsyncSession

    class _SessionDefaillante(AsyncSession):
        def begin_nested(self):
            return _Savepoint()

        async def execute(self, statement, params=None, *args, **kwargs):
            sql = str(statement)
            params = params or {}
            if echoue(sql):
                raise _panne_citant(sql, params)
            result = MagicMock()
            result.first.return_value = None
            result.__iter__.return_value = iter([])
            result.all.return_value = []
            return result

        async def rollback(self):
            return None

        async def close(self):
            raise RuntimeError("synthetic close failure")

    return _SessionDefaillante()


def _handler_json():
    """A handler formatting exactly as the application in production.

    ``configure_logging(json_output=True)`` replaces the root handlers: its
    formatter is taken, then the root logger is restored (pytest's capture
    handler included) and structlog reset.
    """
    import io

    import structlog

    from app.logging_config import configure_logging

    racine = logging.getLogger()
    handlers, niveau = list(racine.handlers), racine.level
    try:
        configure_logging(json_output=True)
        formatter = racine.handlers[0].formatter
    finally:
        racine.handlers[:] = handlers
        racine.setLevel(niveau)
        structlog.reset_defaults()
    flux = io.StringIO()
    handler = logging.StreamHandler(flux)
    handler.setFormatter(formatter)
    return handler, flux


# The outage is raised through the dependency only where the route raises
# its problem (``/v1/dvf/adresse``, ErreurApiError); the address routes return
# theirs, so the failing close happens after the response, with no context.
SCENARIOS_FERMETURE = [
    pytest.param(_toutes, id="dvf-city-detection"),
    pytest.param(_sauf_detection_de_ville, id="dvf-strategy"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("echoue", SCENARIOS_FERMETURE)
async def test_an_outage_then_a_failing_close_logs_no_input(
    client, caplog, monkeypatch, echoue
):
    from app import database

    monkeypatch.setattr(database, "async_session", lambda: _session_defaillante(echoue))
    assert get_db not in app.dependency_overrides  # the real dependency
    handler, flux = _handler_json()
    racine = logging.getLogger()
    racine.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        response = await client.get("/v1/dvf/adresse", params={"q": AVEC_CODE_POSTAL})
    finally:
        racine.removeHandler(handler)

    assert response.status_code == 500, response.text
    # The failing close was logged (the capture works), by both outputs.
    assert any(
        record.name == "app.common.exceptions" and record.levelno == logging.ERROR
        for record in caplog.records
    )
    sortie_json = flux.getvalue().lower()
    assert "runtimeerror" in sortie_json
    journal = _journal(caplog)
    fuites = [
        (mot, sortie)
        for mot in MOTS_SAISIS
        for sortie, texte in (("records", journal), ("json", sortie_json))
        if mot in texte
    ]
    assert not fuites


# --- C59 B1: every outage error is raised detached from the DB error -------
#
# The typed outage errors are handled by their routes (503), but whatever
# chains to them reaches a log as soon as a later error wraps them (the
# failing close above). Each one is raised outside its ``except`` block: no
# ``__cause__``, no ``__context__``, so the driver message never travels on.


class _SessionEnPanne(_Session):
    """``_Session`` whose failing statements raise a cited 53200."""

    def __init__(self, echoue, lignes=None) -> None:
        super().__init__(echoue)
        self.lignes = lignes or {}

    async def rollback(self):
        return None

    async def execute(self, statement, params=None):
        sql = str(statement)
        if self.echoue(sql):
            raise _panne_citant(sql, params or {})
        for fragment, lignes in self.lignes.items():
            if fragment in sql:
                result = MagicMock()
                result.__iter__.return_value = iter(lignes)
                return result
        return await super().execute(statement, params)


def _chaine(exc: BaseException | None):
    """*exc* and every exception chained to it (cause or context)."""
    vues = []
    while exc is not None and exc not in vues:
        vues.append(exc)
        exc = exc.__cause__ or exc.__context__
    return vues


def _assert_detachee(exc: BaseException) -> None:
    assert exc.__cause__ is None, repr(exc.__cause__)
    assert exc.__context__ is None, repr(exc.__context__)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "echoue",
    [_toutes, _sauf_detection_de_ville],
    ids=["city-detection", "strategy"],
)
async def test_the_geocoder_outage_is_detached(echoue):
    from app.ban.queries import GeocodageIndisponibleError, geocoder

    with pytest.raises(GeocodageIndisponibleError) as raised:
        await geocoder(
            _SessionEnPanne(echoue), AVEC_CODE_POSTAL, limit=1, erreurs="lever"
        )
    _assert_detachee(raised.value)


@pytest.mark.asyncio
async def test_the_search_reader_outage_is_detached():
    from app.adresse.recherche import noms_departements
    from app.ban.queries import GeocodageIndisponibleError

    with pytest.raises(GeocodageIndisponibleError) as raised:
        await noms_departements(_SessionEnPanne(_toutes), ["44"])
    _assert_detachee(raised.value)


@pytest.mark.asyncio
async def test_the_identity_reader_outage_is_detached():
    from app.adresse.service import _COMMUNE, AdresseIndisponibleError, _lire

    with pytest.raises(AdresseIndisponibleError) as raised:
        await _lire(_SessionEnPanne(_toutes), _COMMUNE, {"codes": ["44109"]})
    _assert_detachee(raised.value)


@pytest.mark.asyncio
async def test_the_identity_outage_of_the_departement_names_is_detached():
    from app.adresse.service import AdresseIndisponibleError, lire_identite

    commune = MagicMock(
        _mapping={"code": "44109", "nom": "Nantes", "departement_code": "44"}
    )
    session = _SessionEnPanne(
        lambda sql: "geo_departements" in sql, lignes={"geo_communes": [commune]}
    )
    with pytest.raises(AdresseIndisponibleError) as raised:
        await lire_identite(session, "44109_7777_00042", MagicMock())
    _assert_detachee(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("lecteur", ["_point", "_cle_de_l_adresse"])
async def test_the_sales_outage_is_detached(lecteur):
    from app.adresse import ventes

    with pytest.raises(ventes.VentesIndisponiblesError) as raised:
        await getattr(ventes, lecteur)(_SessionEnPanne(_toutes), "44109_7777_00042")
    _assert_detachee(raised.value)


@pytest.mark.asyncio
async def test_the_documents_outage_is_detached():
    from datetime import date

    from app.adresse.documents import (
        DocumentsIndisponiblesError,
        lire_documents_adresse,
    )

    hosting = MagicMock()
    heberge = MagicMock(etat="heberge")
    hosting.hostings = {"ban": heberge, "dpe": heberge}
    with pytest.raises(DocumentsIndisponiblesError) as raised:
        await lire_documents_adresse(
            _SessionEnPanne(_toutes),
            "44109_7777_00042",
            1,
            None,
            hosting,
            date(2026, 10, 12),
        )
    _assert_detachee(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "echoue",
    [_toutes, _sauf_detection_de_ville],
    ids=["city-detection", "strategy"],
)
async def test_the_dvf_problem_carries_no_db_error_into_the_session_dependency(
    client, echoue
):
    # What the route raises is thrown into get_db: nothing chained to it may
    # be the driver error (review sol-F, B1: app/dvf/router.py).
    recues: list[BaseException] = []

    async def db():
        try:
            yield _SessionEnPanne(echoue)
        except Exception as exc:
            recues.append(exc)
            raise

    app.dependency_overrides[get_db] = db

    response = await client.get("/v1/dvf/adresse", params={"q": AVEC_CODE_POSTAL})

    assert response.status_code == 503, response.text
    assert recues, "the problem is raised through the dependency"
    from app.common.exceptions import ErreurApiError

    assert isinstance(recues[0], ErreurApiError)
    assert _chaine(recues[0]) == [recues[0]]  # nothing chained at all


# --- C59 I1: only identified connection errors are an outage ---------------


def _pannes_de_connexion():
    import errno
    import socket

    return [
        ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
        ConnectionResetError(errno.ECONNRESET, "reset"),
        ConnectionAbortedError(errno.ECONNABORTED, "aborted"),
        socket.gaierror(socket.EAI_NONAME, "name not known"),
        TimeoutError(),
        OSError(errno.EHOSTUNREACH, "no route to host"),
        OSError(errno.ENETUNREACH, "network unreachable"),
    ]


def _defauts_systeme():
    import errno

    return [
        FileNotFoundError(errno.ENOENT, "root.crt"),
        PermissionError(errno.EACCES, "client.key"),
        OSError(errno.EINVAL, "invalid argument"),
        OSError("no errno"),
    ]


@pytest.mark.parametrize("exc", _pannes_de_connexion(), ids=repr)
def test_an_identified_connection_error_is_an_outage(exc):
    from app.ban.queries import _base_indisponible

    assert _base_indisponible(exc)


@pytest.mark.parametrize("exc", _defauts_systeme(), ids=repr)
def test_another_os_error_is_a_defect(exc):
    # A missing or unreadable certificate is a lasting configuration defect,
    # never announced as a temporary outage (review sol-F, I1).
    from app.ban.queries import _base_indisponible

    assert not _base_indisponible(exc)


@pytest.mark.asyncio
async def test_a_missing_certificate_on_the_geocoder_is_a_500(client):
    class _Session(_SessionEnPanne):
        async def execute(self, statement, params=None):
            raise FileNotFoundError(2, "No such file or directory", "root.crt")

    async def db():
        yield _Session(_toutes)

    app.dependency_overrides[get_db] = db

    response = await client.get("/v1/dvf/adresse", params={"q": AVEC_CODE_POSTAL})

    assert response.status_code == 500, response.text
    assert "geocodage_indisponible" not in response.text


# --- C59 I3: the identity reader classifies as the fixed readers -----------

CLE_TESTEE = "44109_7777_00042"


def _classe(nom: str):
    import asyncpg.exceptions

    return getattr(asyncpg.exceptions, nom)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls", ["OutOfMemoryError", "QueryCanceledError"], ids=["53200", "57014"]
)
async def test_an_outage_of_the_identity_reader_is_the_address_503(client, cls):
    import asyncpg.exceptions

    class _Session(_SessionEnPanne):
        async def execute(self, statement, params=None):
            raise _asyncpg(getattr(asyncpg.exceptions, cls))

    async def db():
        yield _Session(_toutes)

    app.dependency_overrides[get_db] = db

    response = await client.get("/v1/adresse/44109_7777_00042")

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "adresse_indisponible"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "erreur",
    [
        pytest.param(lambda: _asyncpg(_classe("UndefinedColumnError")), id="42703"),
        pytest.param(lambda: _defaut("SELECT", {"ban_id": CLE_TESTEE}), id="22P02"),
        pytest.param(lambda: TypeError("synthetic bug"), id="python"),
    ],
)
async def test_a_defect_of_the_identity_reader_is_a_bounded_500(client, caplog, erreur):
    class _Session(_SessionEnPanne):
        async def execute(self, statement, params=None):
            raise erreur()

    async def db():
        yield _Session(_toutes)

    app.dependency_overrides[get_db] = db
    caplog.set_level(logging.DEBUG)

    response = await client.get("/v1/adresse/44109_7777_00042")

    assert response.status_code == 500, response.text
    assert "adresse_indisponible" not in response.text
    assert "7777_00042" not in _journal(caplog)

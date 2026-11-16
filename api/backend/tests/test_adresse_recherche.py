"""Address search and identity contract, without a database (04-02).

``/v1/adresses/recherche`` serves the type order (D-106) and
``/v1/adresse/{ban_id}`` the identity of one BAN address: no model of
``app.adresse`` carries a coordinate, and the path pattern refuses a
coordinate pair or a path traversal before any read.
"""

import re

import pytest
from pydantic import BaseModel

from app.adresse import schemas
from app.adresse.recherche import ordre_des_types
from app.adresse.router import BAN_ID_PATTERN

pytestmark = pytest.mark.unit

ADRESSES_D_ABORD = ["adresse", "commune"]
COMMUNES_D_ABORD = ["commune", "adresse"]

_COORDINATE_FIELDS = re.compile(
    r"^(lat|lon|lng|latitude|longitude|x|y|position_geo|point|geometrie|"
    r"geometry|geojson|centroide.*|coordonnees?)$",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    ("saisie", "ordre"),
    [("12 rue des Lilas", ADRESSES_D_ABORD), ("Nantes", COMMUNES_D_ABORD)],
)
def test_ordre_des_types_tracer(saisie, ordre):
    assert ordre_des_types(saisie) == ordre


def _all_models() -> list[type[BaseModel]]:
    return [
        value
        for value in vars(schemas).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == schemas.__name__
    ]


def _field_names(schema: dict) -> set[str]:
    names: set[str] = set()
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return names


def test_no_address_model_carries_a_coordinate():
    models = _all_models()
    assert {model.__name__ for model in models} >= {
        "RechercheAdresses",
        "ResultatRechercheAdresse",
        "FicheAdresse",
        "IdentiteAdresse",
        "CouvertureAdresse",
        "RelationParcelle",
    }
    for model in models:
        names = _field_names(model.model_json_schema())
        assert not {name for name in names if _COORDINATE_FIELDS.match(name)}, (
            model.__name__
        )


@pytest.mark.parametrize(
    "refused",
    ["48.85,2.35", "../", "..%2F", "85191_0420_00012/../x", "", "abc"],
)
def test_path_pattern_refuses_coordinates_and_traversal(refused):
    assert re.fullmatch(BAN_ID_PATTERN, refused) is None


def test_path_pattern_accepts_the_tracer_key():
    assert re.fullmatch(BAN_ID_PATTERN, "85191_0420_00012")


# --- Task 2: served order, street grouping, SQL filter and outages ---------


@pytest.mark.parametrize(
    "saisie",
    [
        "12 rue des Lilas",
        "rue de la Paix",
        "allée des Pins Nantes",
        "12bis rue",
        "Rond-point des Lilas",
    ],
)
def test_adresses_d_abord(saisie):
    assert ordre_des_types(saisie) == ADRESSES_D_ABORD


@pytest.mark.parametrize("saisie", ["Nantes", "44000", "Saint-Philbert", "12"])
def test_communes_d_abord(saisie):
    assert ordre_des_types(saisie) == COMMUNES_D_ABORD


def _row(numero, voie, cp, commune, ban_id=None, rep=None):
    return {
        "ban_id": ban_id,
        "numero": numero,
        "rep": rep,
        "nom_voie": voie,
        "code_postal": cp,
        "code_commune": commune,
        "nom_commune": "Commune " + commune,
    }


def test_street_rows_are_grouped_without_number_or_key():
    from app.adresse.recherche import regrouper_par_voie

    rows = [
        _row("1", "Rue de la Paix", "85000", "85191", "85191_0001_00001"),
        _row("3", "Rue de la Paix", "85000", "85191", "85191_0001_00003"),
        _row("2", "Rue de la Paix", "44000", "44109", "44109_0001_00002"),
        _row("5", "Rue de la Paix", "85000", "85191", "85191_0001_00005"),
    ]

    voies = regrouper_par_voie(rows, {"85": "Vendée", "44": "Loire-Atlantique"})

    assert [(v.nom_voie, v.code_postal, v.code_commune) for v in voies] == [
        ("Rue de la Paix", "85000", "85191"),
        ("Rue de la Paix", "44000", "44109"),
    ]
    for voie in voies:
        assert voie.type_resultat == "voie"
        assert voie.numero is None and voie.ban_id is None and voie.rep is None
    assert voies[0].departement.model_dump() == {"code": "85", "nom": "Vendée"}


async def _search_response(monkeypatch, query, rows, *, limit=8):
    from unittest.mock import AsyncMock, MagicMock

    from app.adresse import recherche
    from app.scope.model import InstanceScope

    async def limited_geocoder(_session, _query, requested_limit, **_kwargs):
        return rows[:requested_limit]

    monkeypatch.setattr(recherche, "geocoder", limited_geocoder)
    monkeypatch.setattr(
        recherche,
        "noms_departements",
        AsyncMock(return_value={"85": "Vendée"}),
    )
    return await recherche.servir_recherche(
        MagicMock(),
        query,
        departement="85",
        limite=limit,
        hosting=_hosting(),
        scope=InstanceScope(frozenset({"ban"}), ("85",)),
    )


@pytest.mark.parametrize(
    ("query", "numero", "rep"),
    [
        ("1 rue de la Paix", "1", None),
        ("1 bis rue de la Paix", "1", "bis"),
        ("1 ter rue de la Paix", "1", "ter"),
        ("1 B rue de la Paix", "1", "B"),
    ],
)
async def test_number_match_is_exact_only_for_number_and_suffix_in_returned_results(
    monkeypatch, query, numero, rep
):
    rows = [
        _row(numero, "Rue de la Paix", "85000", "85191", "exact", rep),
        _row("11", "Rue de la Paix", "85000", "85191", "near"),
    ]

    response = await _search_response(monkeypatch, query, rows)

    assert [result.ban_id for result in response.resultats] == ["exact", "near"]
    assert response.correspondance_numero.model_dump() == {
        "etat": "exacte",
        "numero_demande": 1,
        "suffixe_demande": rep.casefold() if rep else None,
        "portee": "resultats_retournes",
    }


@pytest.mark.parametrize(
    ("query", "rows"),
    [
        (
            "1 rue de la Paix",
            [_row("11", "Rue de la Paix", "85000", "85191", "eleven")],
        ),
        (
            "1 bis rue de la Paix",
            [_row("1", "Rue de la Paix", "85000", "85191", "bare")],
        ),
        (
            "1 bis rue de la Paix",
            [_row("1", "Rue de la Paix", "85000", "85191", "ter", "ter")],
        ),
    ],
)
async def test_number_match_does_not_confuse_1_11_or_suffixes(monkeypatch, query, rows):
    response = await _search_response(monkeypatch, query, rows)

    assert response.correspondance_numero.etat == "proches"
    assert response.correspondance_numero.portee == "resultats_retournes"


async def test_number_match_is_absent_for_unparsed_input_and_scoped_for_empty_results(
    monkeypatch,
):
    no_result = await _search_response(monkeypatch, "1 rue de la Paix", [])
    assert no_result.correspondance_numero.model_dump() == {
        "etat": "aucune_dans_resultats",
        "numero_demande": 1,
        "suffixe_demande": None,
        "portee": "resultats_retournes",
    }

    for query in ("rue de la Paix", "12"):
        response = await _search_response(monkeypatch, query, [])
        assert response.correspondance_numero is None


async def test_exact_number_after_limit_does_not_change_returned_match_or_order(
    monkeypatch,
):
    rows = [
        _row("11", "Rue de la Paix", "85000", "85191", "first"),
        _row("13", "Rue de la Paix", "85000", "85191", "second"),
        _row("1", "Rue de la Paix", "85000", "85191", "exact-after-limit"),
    ]
    assert rows[2]["numero"] == "1"

    response = await _search_response(monkeypatch, "1 rue de la Paix", rows, limit=2)

    assert [result.ban_id for result in response.resultats] == ["first", "second"]
    assert response.correspondance_numero.etat == "proches"
    assert response.correspondance_numero.portee == "resultats_retournes"


async def test_out_of_coverage_does_not_claim_an_empty_executed_search(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.adresse import recherche
    from app.common.schemas import DatasetHosting
    from app.scope.dependencies import RouteHosting
    from app.scope.model import InstanceScope

    search = AsyncMock()
    monkeypatch.setattr(recherche, "rechercher_adresses", search)
    monkeypatch.setattr(
        recherche,
        "noms_departements",
        AsyncMock(return_value={"85": "Vendée"}),
    )
    response = await recherche.servir_recherche(
        MagicMock(),
        "1 rue de la Paix",
        departement="85",
        limite=8,
        hosting=RouteHosting(
            {"ban": DatasetHosting(etat="hors_couverture", motif="dataset_non_heberge")}
        ),
        scope=InstanceScope(frozenset(), ("85",)),
    )

    search.assert_not_awaited()
    assert response.resultats == []
    assert response.correspondance_numero is None


@pytest.mark.parametrize(
    "strategy", ["_STRATEGY_A", "_STRATEGY_B", "_STRATEGY_C", "_STRATEGY_D"]
)
async def test_departement_filter_precedes_order_and_limit(strategy):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.ban import queries

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=[SimpleNamespace(_mapping={"ban_id": "x"})]
    )
    params = {"query": "q", "limit": 8, "departements": ["85"]}

    await queries._execute_query(
        session, getattr(queries, strategy), params, "", "", "", departements=True
    )

    sql = str(session.execute.await_args.args[0])
    clause = "code_departement = ANY(CAST(:departements AS text[]))"
    assert clause in sql
    assert sql.index(clause) < sql.index("ORDER BY") < sql.index("LIMIT :limit")


async def test_geocoder_passes_the_hosted_departements_to_every_strategy():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.ban import queries

    execute = AsyncMock(return_value=[])
    city = AsyncMock(return_value=("rue x", "ville", ["85191"]))
    with (
        patch("app.ban.queries._detect_city", city),
        patch("app.ban.queries._execute_query", execute),
    ):
        await queries.geocoder(
            MagicMock(), "rue x ville", 8, departements_heberges=["85"], erreurs="lever"
        )

    assert execute.await_count == 3  # A, B, C
    for call in execute.await_args_list:
        assert call.args[2]["departements"] == ["85"]
        assert call.kwargs == {"departements": True, "erreurs": "lever"}
    assert city.await_args.kwargs == {"erreurs": "lever"}


async def test_geocoder_reads_nothing_without_a_hosted_departement():
    from unittest.mock import AsyncMock, MagicMock

    from app.ban import queries

    session = MagicMock()
    session.execute = AsyncMock()

    assert await queries.geocoder(session, "1 rue x", 8, departements_heberges=[]) == []
    session.execute.assert_not_awaited()


class _FailingSession:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("synthetic failure")

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        raise self.error


def _db_error(cls, sqlstate: str | None, **kwargs):
    """A SQLAlchemy *cls* wrapping a driver error that carries *sqlstate*."""

    class DriverError(Exception):
        pass

    original = DriverError("synthetic driver failure")
    original.sqlstate = sqlstate
    return cls("SELECT synthetic", {}, original, **kwargs)


def _expected_db_errors():
    from sqlalchemy.exc import DBAPIError, InterfaceError, ProgrammingError

    # The asyncpg adapter yields a plain DBAPIError for 53xxx (C58, WR-01);
    # OperationalError, never produced on this stack, is no longer a case.
    return [
        _db_error(DBAPIError, "57014"),
        _db_error(ProgrammingError, "42P01"),
        _db_error(DBAPIError, "53200"),
        _db_error(InterfaceError, None, connection_invalidated=True),
    ]


def _unexpected_errors():
    from sqlalchemy.exc import InterfaceError, ProgrammingError

    return [
        RuntimeError("synthetic failure"),
        KeyError("latitude"),
        _db_error(ProgrammingError, "42883"),
        _db_error(InterfaceError, None),
    ]


@pytest.mark.parametrize(
    "error",
    _expected_db_errors(),
    ids=["57014", "42P01", "out_of_memory", "disconnected"],
)
async def test_raise_mode_turns_expected_db_errors_into_a_typed_outage(error):
    from sqlalchemy import text as sql_text

    from app.ban import queries

    with pytest.raises(queries.GeocodageIndisponibleError):
        await queries._execute_query(
            _FailingSession(error), sql_text("SELECT 1"), {}, "", erreurs="lever"
        )
    with pytest.raises(queries.GeocodageIndisponibleError):
        await queries._detect_city(
            _FailingSession(error), "1 rue x ville", erreurs="lever"
        )


@pytest.mark.parametrize(
    "error",
    _unexpected_errors(),
    ids=["python", "key", "undefined_function", "interface"],
)
async def test_raise_mode_lets_any_other_error_reach_the_500_path(error):
    # C55 (fix 4): only an expected database failure is the geocoding 503;
    # a Python error or an SQL defect reaches the normal 500 path, as a
    # bounded error raised from None (C58, CR-01): its message names the
    # class, never the driver message nor the parameters.
    from sqlalchemy import text as sql_text

    from app.ban import queries

    with pytest.raises(queries.GeocodageDefautError) as excinfo:
        await queries._execute_query(
            _FailingSession(error), sql_text("SELECT 1"), {}, "", erreurs="lever"
        )
    _assert_bounded(excinfo.value, error)
    with pytest.raises(queries.GeocodageDefautError) as excinfo:
        await queries._detect_city(
            _FailingSession(error), "1 rue x ville", erreurs="lever"
        )
    _assert_bounded(excinfo.value, error)


def _assert_bounded(raised: Exception, error: Exception) -> None:
    assert str(raised).split()[0] == type(error).__name__
    assert "synthetic" not in str(raised) and "SELECT" not in str(raised)
    assert raised.__cause__ is None and raised.__context__ is None


async def test_default_mode_still_returns_nothing_on_any_failure():
    from sqlalchemy import text as sql_text

    from app.ban import queries

    # Defaults unchanged for /v1/geocode and /v1/resolve.
    assert (
        await queries._execute_query(_FailingSession(), sql_text("SELECT 1"), {}, "")
        == []
    )
    assert await queries._detect_city(_FailingSession(), "1 rue x ville") is None


# --- Task 3: key profiles (block E) and parcel steps -----------------------

# Synthetic keys of every observed profile: 5-4-5, 5-6-5, suffixes of one to
# nine characters, a suffix carrying a character outside [0-9A-Za-z_], and a
# Corsican INSEE code.
KEY_PROFILES = [
    "85191_0420_00012",
    "44109_ab12cd_00012",
    "44109_abcdef_00012",
    "44109_0420_00012_bis",
    "44109_04a0b2_00001_1",
    "44109_0420_00012_123456789",
    "44109_0420_00014_a-1",
    "44109_0420_00014_l'x",
    "2A004_0420_00012",
]
VOIE_FORMS = ["85191_0420", "85191_ab12cd"]


def _pydantic_accepts(value: str) -> bool:
    from typing import Annotated

    from pydantic import StringConstraints, TypeAdapter, ValidationError

    adapter = TypeAdapter(
        Annotated[str, StringConstraints(pattern=BAN_ID_PATTERN, max_length=40)]
    )
    try:
        adapter.validate_python(value)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize("key", KEY_PROFILES + VOIE_FORMS)
def test_every_observed_key_profile_is_accepted_unchanged(key):
    assert re.fullmatch(BAN_ID_PATTERN, key)
    assert _pydantic_accepts(key)  # the engine FastAPI uses for Path(pattern=)


@pytest.mark.parametrize(
    "refused",
    [
        "48.85,2.35",
        "-1.43,46.67",
        "../",
        "85191_0420_00012/../x",
        "85191_0420_00012_a/b",
        "85191_0420_00012_a\\b",
        "85191_0420_00012_a\x00",
        "85191_0420_00012_a b",
        "85191_0420_00012_12345678901",
        "85191_042_00012",
        "85191_0420_0012",
        "85191",
        "8519_0420_00012",
    ],
)
def test_malformed_keys_are_refused_by_both_engines(refused):
    assert re.fullmatch(BAN_ID_PATTERN, refused) is None
    assert not _pydantic_accepts(refused)


def test_voie_form_is_recognized():
    from app.adresse.service import forme_de_voie

    assert all(forme_de_voie(key) for key in VOIE_FORMS)
    assert not any(forme_de_voie(key) for key in KEY_PROFILES)


class _Savepoint:
    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _hosting(cadastre="heberge"):
    from app.common.schemas import DatasetHosting
    from app.scope.dependencies import RouteHosting

    motif = None if cadastre == "heberge" else "dataset_non_heberge"
    return RouteHosting(
        {
            "ban": DatasetHosting(etat="heberge", departements=["85"]),
            "cadastre": DatasetHosting(etat=cadastre, motif=motif),
        }
    )


async def test_an_added_step_is_consulted_only_on_no_candidate(monkeypatch):
    from app.adresse import relations
    from app.adresse.schemas import RelationParcelle

    calls = []

    async def no_candidate(session, point):
        calls.append("point")
        return None

    async def ambiguous(session, point):
        calls.append("point")
        return RelationParcelle(etat="ambigue", nombre_candidats=2)

    async def extra(session, point):
        calls.append("extra")
        return RelationParcelle(etat="unique", id_parcelle="X", nombre_candidats=1)

    point = relations.PointBan(lon=0.0, lat=0.0)

    monkeypatch.setattr(relations, "ETAPES_PARCELLE", (no_candidate, extra))
    relation = await relations.resoudre_parcelle(_Savepoint(), point, _hosting())
    assert calls == ["point", "extra"] and relation.id_parcelle == "X"

    calls.clear()
    monkeypatch.setattr(relations, "ETAPES_PARCELLE", (ambiguous, extra))
    relation = await relations.resoudre_parcelle(_Savepoint(), point, _hosting())
    assert calls == ["point"] and relation.etat == "ambigue"

    calls.clear()
    monkeypatch.setattr(relations, "ETAPES_PARCELLE", (no_candidate,))
    relation = await relations.resoudre_parcelle(_Savepoint(), point, _hosting())
    assert relation.etat == "point_hors_parcelle" and relation.id_parcelle is None


async def test_strict_rule_has_one_step_and_no_nearest_fallback():
    import inspect

    from app.adresse import relations

    assert (relations.etape_point,) == relations.ETAPES_PARCELLE
    source = inspect.getsource(relations)
    assert "parcelles_proches(" not in source
    assert "select_snapped_candidates(" not in source


async def test_cadastre_out_of_coverage_is_never_read(monkeypatch):
    from app.adresse import relations

    async def must_not_run(session, point):
        raise AssertionError("cadastre read while out of coverage")

    monkeypatch.setattr(relations, "ETAPES_PARCELLE", (must_not_run,))
    relation = await relations.resoudre_parcelle(
        _Savepoint(), relations.PointBan(lon=0.0, lat=0.0), _hosting("hors_couverture")
    )
    assert (relation.etat, relation.motif) == ("hors_couverture", "dataset_non_heberge")


async def test_cadastre_failure_is_unavailable_not_absent(monkeypatch):
    from app.adresse import relations

    async def failing(session, point):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(relations, "ETAPES_PARCELLE", (failing,))
    relation = await relations.resoudre_parcelle(
        _Savepoint(), relations.PointBan(lon=0.0, lat=0.0), _hosting()
    )
    assert relation.etat == "indisponible" and relation.id_parcelle is None


def test_search_422_is_declared_with_its_typed_code():
    """Review of 261007-crf WR-06: the 422 of the search is in the contract.

    ``departement_invalide`` is a code of ProblemeAdresse; a parameter out of
    bounds (q, departement, limite) is a validation problem without ``code``.
    """
    from app.main import app

    spec = app.openapi()
    reponse = spec["paths"]["/v1/adresses/recherche"]["get"]["responses"]["422"]
    variantes = reponse["content"]["application/problem+json"]["schema"]["anyOf"]
    titres = {variante["title"] for variante in variantes}
    assert titres == {"ProblemeAdresse", "ProblemDetail"}
    probleme = next(v for v in variantes if v["title"] == "ProblemeAdresse")
    assert "departement_invalide" in probleme["properties"]["code"]["enum"]
    assert (
        "departement_invalide"
        in schemas.ProblemeAdresse.model_json_schema()["properties"]["code"]["enum"]
    )

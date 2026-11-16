"""Paris, Lyon, Marseille on /v1/commune and the comparator (plan 04.7-09, D3 and D6).

``/v1/commune?code_insee=75056`` used to expand to 21 codes in no order, and the
page read ``donnees[0]``: it could show an arrondissement as Paris. The code is
now read exactly; an arrondissement carries ``type_zone`` and ``commune_parent``.
The comparator refuses a whole against its own part and takes two parts.

No database: the synthesis and the referential are replaced by doubles. The SQL
against PostGIS is proven by ``tests/integration/test_plm_arrondissements.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common import geo
from app.common.exceptions import ErreurApiError
from app.common.geo import (
    commune_and_its_arrondissement,
    resolve_location,
    zone_identity,
)
from app.commune import router as commune_router
from app.commune.router import _commune_identities, comparison_codes
from app.database import get_db
from app.main import app
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit

ROWS = {
    code: {"code_insee": code, "nom_commune": nom, "population": population}
    for code, nom, population in (
        ("75056", "Paris", 2100000),
        ("75112", "Paris 12e Arrondissement", 140000),
        ("75101", "Paris 1er Arrondissement", 16000),
        ("44109", "Nantes", 320000),
    )
}


@pytest.fixture
def synthese_reads(monkeypatch):
    """Records the codes read; answers every known row among them."""
    reads: list[list[str]] = []

    async def rows(_db, codes, _sources):
        reads.append(list(codes))
        return [dict(ROWS[code]) for code in codes if code in ROWS]

    async def no_window(_db):
        return None

    async def no_facts(_db, _names):
        return {}

    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", rows)

    async def other_mesh(_db, codes, _sources):
        # The other mesh of R-09 (tests/test_commune_autre_maille.py): not a read
        # of the requested codes, so not recorded here.
        return [dict(ROWS[code]) for code in codes if code in ROWS]

    monkeypatch.setattr(commune_router, "synthese_autre_maille", other_mesh)
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", no_window)
    # The yearly price series is another read of the route (migration 115).
    monkeypatch.setattr(
        commune_router, "tendances_prix_par_codes", AsyncMock(return_value={})
    )
    monkeypatch.setattr(commune_router, "get_provenance_facts", no_facts)

    async def no_session():
        yield object()

    app.dependency_overrides[get_db] = no_session
    yield reads
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def scope_44_75(client, full_scope_loader) -> InstanceScope:
    scope = InstanceScope(full_scope_loader("").datasets, ("44", "75"))
    app.dependency_overrides[get_instance_scope] = lambda: scope
    return scope


# ------------------------------------------------------- /v1/commune (D3) --


async def test_paris_is_read_at_its_own_code_and_gives_one_row(
    client, scope_44_75, synthese_reads
):
    response = await client.get("/v1/commune", params={"code_insee": "75056"})

    assert response.status_code == 200, response.text
    assert synthese_reads == [["75056"]]
    (row,) = response.json()["donnees"]
    assert row["code_insee"] == "75056"
    assert (row["type_zone"], row["commune_parent"]) == ("commune", None)


async def test_an_arrondissement_says_what_it_is_and_whose_part(
    client, scope_44_75, synthese_reads
):
    response = await client.get("/v1/commune", params={"code_insee": "75112"})

    (row,) = response.json()["donnees"]
    assert synthese_reads == [["75112"]]
    assert row["nom_commune"] == "Paris 12e Arrondissement"
    assert (row["type_zone"], row["commune_parent"]) == ("arrondissement", "75056")


async def test_an_ordinary_commune_is_a_commune_without_parent(
    client, scope_44_75, synthese_reads
):
    response = await client.get("/v1/commune", params={"code_insee": "44109"})

    (row,) = response.json()["donnees"]
    assert (row["type_zone"], row["commune_parent"]) == ("commune", None)


async def test_the_gps_path_is_unchanged(
    client, scope_44_75, synthese_reads, monkeypatch
):
    async def arrondissement_at_point(_session, _lat, _lon):
        return "75112"

    monkeypatch.setattr(geo, "commune_at_point", arrondissement_at_point)

    response = await client.get("/v1/commune", params={"lat": 48.84, "lon": 2.39})

    assert synthese_reads == [["75112"]]
    (row,) = response.json()["donnees"]
    assert row["code_insee"] == "75112"


async def test_resolve_location_still_expands_by_default_for_the_other_routers():
    # Eleven routers read a PLM commune on all its codes (DVF, DPE... are
    # stored at the arrondissement): only /v1/commune asks for the exact code.
    expanded = await resolve_location(object(), code_insee="75056")  # type: ignore[arg-type]
    exact = await resolve_location(object(), code_insee="75056", expand=False)  # type: ignore[arg-type]

    assert len(expanded.codes) == 21
    assert exact.codes == ["75056"]


def test_the_identity_fields_are_declared_non_semantic():
    # They carry no value, unit or source: the semantic audit skips them.
    from app.commune.schemas import COMMUNE_FIELD_SOURCES, NON_SEMANTIC_COMMUNE_COLUMNS

    assert {"type_zone", "commune_parent"} <= NON_SEMANTIC_COMMUNE_COLUMNS
    assert not {"type_zone", "commune_parent"} & set(COMMUNE_FIELD_SOURCES)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("75056", ("commune", None)),
        ("75112", ("arrondissement", "75056")),
        ("69389", ("arrondissement", "69123")),
        ("13216", ("arrondissement", "13055")),
        ("44109", ("commune", None)),
    ],
)
def test_zone_identity(code, expected):
    assert zone_identity(code) == expected


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        (["75056", "75112"], ("75056", "75112")),
        (["75112", "75056"], ("75056", "75112")),
        (["44109", "75112", "75056"], ("75056", "75112")),
        (["75112", "75101"], None),
        (["75112", "69123"], None),
        (["44109", "85047"], None),
    ],
)
def test_commune_and_its_arrondissement(codes, expected):
    assert commune_and_its_arrondissement(codes) == expected


# ------------------------------------------------------- comparator (D6) --


def test_a_commune_against_one_of_its_arrondissements_is_refused():
    with pytest.raises(ErreurApiError) as refused:
        comparison_codes("75056,75112")

    assert refused.value.code == 422
    assert "75056" in refused.value.detail
    assert "75112" in refused.value.detail


def test_the_refusal_does_not_depend_on_the_order_nor_on_the_other_codes():
    with pytest.raises(ErreurApiError):
        comparison_codes("44109,75112,75056")


def test_two_arrondissements_are_accepted():
    assert comparison_codes("75112,75101") == ["75112", "75101"]


def test_arrondissements_of_two_cities_and_their_communes_are_accepted():
    assert comparison_codes("75112,69383") == ["75112", "69383"]
    assert comparison_codes("75056,69123") == ["75056", "69123"]


async def test_the_comparison_route_answers_422_as_a_problem(
    client, scope_44_75, synthese_reads
):
    response = await client.get(
        "/v1/communes/comparaison", params={"codes": "75056,75112"}
    )

    assert response.status_code == 422
    assert synthese_reads == []  # refused before any read
    assert "arrondissement" in response.text


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Referentiel:
    """A session answering the identity statement from two tables."""

    def __init__(self, communes, arrondissements):
        self.communes = communes
        self.arrondissements = arrondissements
        self.statements: list[str] = []

    async def execute(self, statement, params):
        sql = str(statement)
        self.statements.append(sql)
        rows = []
        if "geo_communes" in sql:
            rows += self.communes
        if "geo_arrondissements" in sql:
            rows += self.arrondissements
        return _Rows([row for row in rows if row.code in params["codes"]])


async def test_identities_are_read_in_communes_then_in_arrondissements():
    session = _Referentiel(
        communes=[SimpleNamespace(code="75056", nom="Paris", departement_code="75")],
        arrondissements=[
            SimpleNamespace(
                code="75112",
                nom="Paris 12e Arrondissement",
                departement_code="75",
            )
        ],
    )

    identities = await _commune_identities(session, ["75056", "75112", "99999"])  # type: ignore[arg-type]

    assert identities == {
        "75056": {"nom": "Paris", "departement": "75"},
        # The name is the one of geo.api.gouv.fr, never composed from the code.
        "75112": {"nom": "Paris 12e Arrondissement", "departement": "75"},
    }
    assert all(
        "geo_communes" in sql and "geo_arrondissements" in sql
        for sql in session.statements
    )

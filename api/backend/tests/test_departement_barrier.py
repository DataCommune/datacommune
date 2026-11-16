"""The departement view is read behind the barrier of the commune chain.

Plan 04.7-10, decision 1: the readiness check of ``commune_synthese_par_codes``
(reader lock, latest publications, ``matview_metadata`` of the chain, view
edges) is shared, and ``v_departement_synthese`` brings its own edges to
``mutations_grain``, ``diagnostics_dpe`` and ``demographie``. Only the hosted
sources gate the read, as for the commune synthesis. No database: a recording
session; the SQL itself is proven on PostGIS
(``tests/integration/test_departement_synthese.py``).
"""

import pytest
from fastapi import HTTPException

from app.commune import queries
from app.commune.dependencies import DEPARTEMENT_EDGES

pytestmark = pytest.mark.unit


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _Mappings(self.rows)


@pytest.fixture(autouse=True)
def no_lock(monkeypatch):
    async def acquire(_session):
        return None

    monkeypatch.setattr(queries, "acquire_publication_read_lock", acquire)


def test_the_view_has_an_edge_to_each_of_its_three_sources():
    assert DEPARTEMENT_EDGES == (
        ("v_departement_synthese", "mutations_grain"),
        ("v_departement_synthese", "diagnostics_dpe"),
        ("v_departement_synthese", "demographie"),
    )


async def test_no_hosted_source_is_a_programming_error():
    session = _Session([])
    with pytest.raises(ValueError, match="hosted sources"):
        await queries.departement_synthese(session, "44", [])
    assert session.calls == []


async def test_every_hosted_source_gates_the_read_with_its_edge():
    session = _Session([{"ready": True, "document": {"code_departement": "44"}}])

    row = await queries.departement_synthese(
        session, "44", ["dvf", "dpe", "demographie"]
    )

    assert row == {"code_departement": "44"}
    ((sql, params),) = session.calls
    for view, source in DEPARTEMENT_EDGES:
        assert f"('{view}', '{source}')" in sql
    assert "LEFT JOIN v_departement_synthese v" in sql
    assert "matview_name = 'v_commune_synthese'" in sql
    assert params["code"] == "44"
    assert sorted(params["datasets"]) == ["demographie", "dpe", "dvf"]


async def test_a_source_out_of_coverage_does_not_gate_the_read():
    session = _Session([{"ready": True, "document": None}])

    row = await queries.departement_synthese(session, "29", ["demographie"])

    assert row is None  # no row for the departement: an absence, not an outage
    ((sql, params),) = session.calls
    assert "('v_departement_synthese', 'demographie')" in sql
    assert "mutations_grain')" not in sql
    assert "diagnostics_dpe')" not in sql
    assert params["datasets"] == ["demographie"]


@pytest.mark.parametrize("rows", [[], [{"ready": False, "document": None}]])
async def test_a_barrier_not_ready_is_a_503(rows):
    with pytest.raises(HTTPException) as raised:
        await queries.departement_synthese(_Session(rows), "44", ["dvf"])
    assert raised.value.status_code == 503


async def test_the_commune_read_keeps_its_own_edges(monkeypatch):
    monkeypatch.setattr(queries.settings, "commune_indicators_enabled", False)
    session = _Session([{"ready": True, "document": {"code_insee": "44109"}}])

    await queries.commune_synthese_par_codes(session, ["44109"], ["demographie"])

    ((sql, _),) = session.calls
    assert "('v_commune_synthese', 'demographie')" in sql
    assert "v_departement_synthese" not in sql

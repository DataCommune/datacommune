"""GET /v1/communes/annuaire -- the directory of communes with their prices.

04.6-02 (PROPOSITION § 4 and § 9): the page is ``list_communes`` with the
directory filters applied before pagination, then the prices of the hosted
codes of the page only, read by ``commune_synthese_par_codes`` and projected by
``apply_commune_coverage``. The served states are kept: a NULL count is
``not_reported``, a count under 5 (zero included) is ``insufficient_sample``,
a commune out of coverage carries its reason without any read, and a 503 of
the synthesis keeps the list with prices ``indisponible`` -- never a zero.

No database: the list and the synthesis are replaced by recording doubles,
and ``list_communes`` itself is checked on the statements it sends to a fake
session (count and page carry the same filters). The SQL against PostGIS is
proven by ``tests/integration/test_commune_annuaire_contract.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import HTTPException

from app.database import get_db
from app.main import app
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from app.zone import queries as zone_queries
from app.zone import router as zone_router

pytestmark = pytest.mark.unit

URL = "/v1/communes/annuaire"


def _row(code: str, nom: str, population: int | None = 1000) -> dict:
    return {
        "code_insee": code,
        "nom": nom,
        "departement": code[:2],
        "population": population,
        "population_annee": 2022 if population is not None else None,
        "population_source": "demographie" if population is not None else None,
    }


def _synthese(code: str, **dvf: Any) -> dict:
    """A synthesis row: demographie present, the given DVF columns, rest null."""
    return {"code_insee": code, "annee_demographie": 2022, "population": 1000, **dvf}


@dataclass
class Doubles:
    """Recording doubles of the list, the synthesis and the DVF window."""

    rows: list[dict] = field(default_factory=list)
    total: int | None = None
    syntheses: list[dict] = field(default_factory=list)
    synthese_error: HTTPException | None = None
    window: str | None = "2024-12-31 → 2025-12-31"
    window_error: HTTPException | None = None
    list_calls: list[dict] = field(default_factory=list)
    synthese_calls: list[tuple[list[str], list[str]]] = field(default_factory=list)
    window_calls: int = 0


@pytest.fixture
def doubles(monkeypatch) -> Doubles:
    state = Doubles()

    async def list_communes(_db, **kwargs):
        state.list_calls.append(kwargs)
        total = len(state.rows) if state.total is None else state.total
        return list(state.rows), total

    async def commune_synthese_par_codes(_db, codes, sources):
        state.synthese_calls.append((list(codes), list(sources)))
        if state.synthese_error is not None:
            raise state.synthese_error
        return [dict(row) for row in state.syntheses if row["code_insee"] in codes]

    async def fenetre_mediane_commune(_db):
        state.window_calls += 1
        if state.window_error is not None:
            raise state.window_error
        return state.window

    monkeypatch.setattr(zone_router, "list_communes", list_communes)
    monkeypatch.setattr(
        zone_router, "commune_synthese_par_codes", commune_synthese_par_codes
    )
    monkeypatch.setattr(zone_router, "fenetre_mediane_commune", fenetre_mediane_commune)

    async def no_session():
        yield object()

    app.dependency_overrides[get_db] = no_session
    yield state
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def scope_44_85(client, full_scope_loader) -> InstanceScope:
    """The whole catalogue hosted on two departements (the 44/85 instance)."""
    scope = InstanceScope(full_scope_loader("").datasets, ("44", "85"))
    app.dependency_overrides[get_instance_scope] = lambda: scope
    return scope


async def _get(client, **params) -> dict:
    response = await client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()["donnees"]


def _by_code(donnees: dict) -> dict[str, dict]:
    return {row["code_insee"]: row for row in donnees["results"]}


# --------------------------------------------------------------- validation --


@pytest.mark.parametrize(
    "params",
    [
        {"population_min": 5000, "population_max": 4999},
        {"population_min": -1},
        {"population_min": 2_147_483_648},
        {"population_max": 2_147_483_648},
        {"departement": "00"},
        {"couverture": "hebergees"},
        {"sort_by": "departement"},
        {"order": "up"},
        {"per_page": 101},
        {"per_page": 0},
        {"page": 0},
    ],
)
async def test_invalid_parameters_are_refused_before_any_read(
    client, scope_44_85, doubles, params
):
    response = await client.get(URL, params=params)

    assert response.status_code == 422, response.text
    assert doubles.list_calls == []
    assert doubles.synthese_calls == []


async def test_the_integer_ceiling_is_a_valid_bound(client, scope_44_85, doubles):
    await _get(client, population_min=2_147_483_647, population_max=2_147_483_647)

    (call,) = doubles.list_calls
    assert call["population_min"] == call["population_max"] == 2_147_483_647


async def test_equal_bounds_are_a_valid_inclusive_range(client, scope_44_85, doubles):
    await _get(client, population_min=5000, population_max=5000)

    (call,) = doubles.list_calls
    assert (call["population_min"], call["population_max"]) == (5000, 5000)


# ------------------------------------------------- filters, sort, coverage --


async def test_default_lists_the_served_departements_by_population(
    client, scope_44_85, doubles
):
    donnees = await _get(client)

    (call,) = doubles.list_calls
    assert call["departements"] == ["44", "85"]
    assert (call["sort_by"], call["order"]) == ("population", "desc")
    assert (call["page"], call["per_page"]) == (1, 50)
    assert call["departement"] is None and call["q"] is None
    assert donnees["couverture"] == "servies"
    assert donnees["departements_servis"] == ["44", "85"]


async def test_toutes_reads_the_whole_referential(client, scope_44_85, doubles):
    donnees = await _get(client, couverture="toutes")

    (call,) = doubles.list_calls
    assert call["departements"] is None
    assert donnees["couverture"] == "toutes"
    # The served departements stay said, so a screen can name them.
    assert donnees["departements_servis"] == ["44", "85"]


async def test_every_filter_reaches_the_list(client, scope_44_85, doubles):
    await _get(
        client,
        nom="saint",
        departement="85",
        population_min=1000,
        population_max=20000,
        sort_by="nom",
        order="asc",
        page=3,
        per_page=20,
    )

    (call,) = doubles.list_calls
    assert call["q"] == "saint"
    assert call["departement"] == "85"
    assert (call["population_min"], call["population_max"]) == (1000, 20000)
    assert (call["sort_by"], call["order"]) == ("nom", "asc")
    assert (call["page"], call["per_page"]) == (3, 20)
    assert call["demographie_departements"] == ["44", "85"]


async def test_served_departements_follow_the_commune_hosting(
    client, doubles, full_scope_loader
):
    # Without demographie (structural source), the synthesis is hosted nowhere.
    datasets = full_scope_loader("").datasets - {"demographie"}
    scope = InstanceScope(datasets, ("44", "85"))
    app.dependency_overrides[get_instance_scope] = lambda: scope

    response = await client.get(URL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert doubles.list_calls[0]["departements"] == []
    assert body["donnees"]["departements_servis"] == []
    # The route says which of its keys is out, with the sources it attributes.
    assert body["meta"]["hebergement"]["commune"]["etat"] == "hors_couverture"
    assert body["meta"]["source"]


async def test_total_and_pagination_are_those_of_the_filtered_list(
    client, scope_44_85, doubles
):
    doubles.rows = [_row("44109", "Nantes")]
    doubles.total = 120
    doubles.syntheses = [_synthese("44109")]

    first = await _get(client, per_page=50, page=2)
    doubles.rows = []
    beyond = await _get(client, per_page=50, page=9)

    assert (first["total_count"], first["page"], first["has_next"]) == (120, 2, True)
    # Past the total: an empty page that still says the total, never capped.
    assert beyond["results"] == []
    assert (beyond["total_count"], beyond["page"], beyond["has_next"]) == (
        120,
        9,
        False,
    )
    assert beyond["prix"] == {
        "etat": "sans_lecture",
        "motif": None,
        "fenetre_mediane_dvf": None,
    }


# ----------------------------------------------------------- price states --


async def test_price_states_are_the_served_ones_never_a_zero(
    client, scope_44_85, doubles
):
    doubles.rows = [
        _row("44109", "Nantes"),
        _row("44001", "Peu De Ventes"),
        _row("85001", "Sans Vente"),
        _row("85002", "Aucune Ligne Dvf"),
    ]
    doubles.syntheses = [
        _synthese(
            "44109",
            prix_m2_median_maison=3650,
            nb_mutations_12m_maison=412,
            prix_m2_median_appartement=3980,
            nb_mutations_12m_appartement=1630,
        ),
        _synthese(
            "44001",
            prix_m2_median_maison=None,
            nb_mutations_12m_maison=3,
            prix_m2_median_appartement=None,
            nb_mutations_12m_appartement=1,
        ),
        # A row in v_commune_dvf for houses only: the other type counts 0.
        _synthese(
            "85001",
            prix_m2_median_maison=2100,
            nb_mutations_12m_maison=9,
            prix_m2_median_appartement=None,
            nb_mutations_12m_appartement=0,
        ),
        # No row in v_commune_dvf: NULL counts, the LEFT JOIN without COALESCE.
        _synthese("85002"),
    ]

    donnees = await _get(client)
    rows = _by_code(donnees)

    nantes = rows["44109"]["prix"]
    assert nantes["maison"] == {"mediane_m2": 3650, "effectif": 412, "etat": None}
    assert nantes["appartement"] == {
        "mediane_m2": 3980,
        "effectif": 1630,
        "etat": None,
    }

    peu = rows["44001"]["prix"]
    assert peu["maison"]["mediane_m2"] is None
    assert peu["maison"]["effectif"] == 3
    assert peu["maison"]["etat"]["etat"] == "insufficient_sample"
    assert peu["appartement"]["etat"]["etat"] == "insufficient_sample"

    zero = rows["85001"]["prix"]["appartement"]
    assert zero["effectif"] == 0
    assert zero["mediane_m2"] is None
    assert zero["etat"]["etat"] == "insufficient_sample"

    sans_ligne = rows["85002"]["prix"]
    for kind in ("maison", "appartement"):
        assert sans_ligne[kind]["effectif"] is None
        assert sans_ligne[kind]["mediane_m2"] is None
        assert sans_ligne[kind]["etat"]["etat"] == "not_reported"

    assert donnees["prix"] == {
        "etat": "servi",
        "motif": None,
        "fenetre_mediane_dvf": "2024-12-31 → 2025-12-31",
    }
    # One synthesis read for the page, one window read.
    assert len(doubles.synthese_calls) == 1
    assert sorted(doubles.synthese_calls[0][0]) == ["44001", "44109", "85001", "85002"]
    assert doubles.window_calls == 1


async def test_only_the_hosted_codes_are_read(client, scope_44_85, doubles):
    doubles.rows = [_row("44109", "Nantes"), _row("79191", "Niort")]
    doubles.syntheses = [_synthese("44109", nb_mutations_12m_maison=10)]

    rows = _by_code(await _get(client, couverture="toutes"))

    ((codes, sources),) = doubles.synthese_calls
    assert codes == ["44109"]
    assert "dvf" in sources and "demographie" in sources
    niort = rows["79191"]
    assert niort["hebergement"]["etat"] == "hors_couverture"
    for kind in ("maison", "appartement"):
        assert niort["prix"][kind] == {
            "mediane_m2": None,
            "effectif": None,
            "etat": {"etat": "out_of_coverage", "motif": "territoire_hors_perimetre"},
        }


async def test_no_hosted_code_no_synthesis_read(client, scope_44_85, doubles):
    doubles.rows = [_row("79191", "Niort"), _row("33063", "Bordeaux")]

    donnees = await _get(client, couverture="toutes")

    assert doubles.synthese_calls == []
    assert doubles.window_calls == 0
    assert donnees["prix"]["etat"] == "sans_lecture"
    assert all(
        row["prix"]["maison"]["etat"]["etat"] == "out_of_coverage"
        for row in donnees["results"]
    )


async def test_a_hosted_code_without_synthesis_row_is_not_reported(
    client, scope_44_85, doubles
):
    doubles.rows = [_row("44999", "Absente De La Vue")]

    (row,) = (await _get(client))["results"]

    for kind in ("maison", "appartement"):
        assert row["prix"][kind]["mediane_m2"] is None
        assert row["prix"][kind]["effectif"] is None
        assert row["prix"][kind]["etat"]["etat"] == "not_reported"
    # No served count: the window is not read.
    assert doubles.window_calls == 0


async def test_dvf_out_of_coverage_for_a_served_departement(
    client, doubles, full_scope_loader
):
    # The synthesis is hosted for 57 (demographie covers it), DVF is not.
    scope = InstanceScope(full_scope_loader("").datasets, ("44", "57"))
    app.dependency_overrides[get_instance_scope] = lambda: scope
    doubles.rows = [_row("57463", "Metz")]
    doubles.syntheses = [_synthese("57463", nb_mutations_12m_maison=99)]

    donnees = await _get(client)
    (row,) = donnees["results"]

    assert row["hebergement"]["etat"] == "heberge"
    for kind in ("maison", "appartement"):
        # Known from the hosting alone: neither the median nor the count.
        assert row["prix"][kind] == {
            "mediane_m2": None,
            "effectif": None,
            "etat": {"etat": "out_of_coverage", "motif": "source_sans_couverture"},
        }
    # DVF out for every code of the page: nothing is read.
    assert doubles.synthese_calls == []
    assert doubles.window_calls == 0
    assert donnees["prix"]["etat"] == "sans_lecture"


@pytest.mark.parametrize("scenario", ["missing_row", "outage"])
async def test_dvf_out_of_coverage_survives_a_missing_row_and_an_outage(
    client, doubles, full_scope_loader, scenario
):
    scope = InstanceScope(full_scope_loader("").datasets, ("44", "57"))
    app.dependency_overrides[get_instance_scope] = lambda: scope
    doubles.rows = [_row("44109", "Nantes"), _row("57463", "Metz")]
    if scenario == "outage":
        doubles.synthese_error = HTTPException(503, "indisponible")
    # missing_row: the synthesis has no row for either code.

    donnees = await _get(client)
    rows = _by_code(donnees)

    for kind in ("maison", "appartement"):
        assert rows["57463"]["prix"][kind]["etat"] == {
            "etat": "out_of_coverage",
            "motif": "source_sans_couverture",
        }
    ((codes, _sources),) = doubles.synthese_calls
    assert codes == ["44109"]
    if scenario == "outage":
        assert donnees["prix"]["etat"] == "indisponible"
        assert rows["44109"]["prix"] is None
    else:
        assert rows["44109"]["prix"]["maison"]["etat"]["etat"] == "not_reported"


# ----------------------------------------------------------------- outage --


@pytest.mark.parametrize("failing", ["synthese", "window"])
async def test_an_outage_keeps_the_list_with_prices_unavailable(
    client, scope_44_85, doubles, failing
):
    outage = HTTPException(503, "Synthèse communale temporairement indisponible.")
    doubles.rows = [_row("44109", "Nantes"), _row("79191", "Niort")]
    doubles.syntheses = [_synthese("44109", nb_mutations_12m_maison=10)]
    if failing == "synthese":
        doubles.synthese_error = outage
    else:
        doubles.window_error = outage

    response = await client.get(URL, params={"couverture": "toutes"})

    assert response.status_code == 200, response.text
    donnees = response.json()["donnees"]
    assert donnees["prix"] == {
        "etat": "indisponible",
        "motif": "Synthèse communale temporairement indisponible.",
        "fenetre_mediane_dvf": None,
    }
    rows = _by_code(donnees)
    assert [row["nom"] for row in donnees["results"]] == ["Nantes", "Niort"]
    # The hosted commune has no price at all, never zeros nor silent nulls.
    assert rows["44109"]["prix"] is None
    # Out of coverage is known without the synthesis: its reason stays.
    assert rows["79191"]["prix"]["maison"]["etat"]["etat"] == "out_of_coverage"


async def test_another_synthesis_error_is_not_swallowed(client, scope_44_85, doubles):
    doubles.rows = [_row("44109", "Nantes")]
    doubles.synthese_error = HTTPException(500, "defaut")

    response = await client.get(URL)

    assert response.status_code == 500


# ------------------------------------------------- list_communes statements --


class _Result:
    def __init__(self, scalar: int | None = None, rows: list | None = None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def __iter__(self):
        return iter(self._rows)


class _RecordingSession:
    """Answers the count with *total* and the page with no row; records SQL."""

    def __init__(self, total: int):
        self.total = total
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, dict(params)))
        if sql.lstrip().startswith("SELECT COUNT(*)"):
            return _Result(scalar=self.total)
        return _Result(rows=[])


async def test_count_and_page_carry_the_same_population_and_departement_filters():
    session = _RecordingSession(total=80)

    await zone_queries.list_communes(
        session,
        departements=["44", "85"],
        population_min=1000,
        population_max=5000,
        demographie_departements=["44", "85"],
    )

    (count_sql, count_params), (page_sql, page_params) = session.statements
    for sql, params in ((count_sql, count_params), (page_sql, page_params)):
        # The served population (lateral join on demographie) bounds both.
        assert "LEFT JOIN LATERAL" in sql
        assert "COALESCE(dl.population, gc.population) >= :population_min" in sql
        assert "COALESCE(dl.population, gc.population) <= :population_max" in sql
        assert "gc.departement_code = ANY(CAST(:departements AS text[]))" in sql
        assert params["population_min"] == 1000
        assert params["population_max"] == 5000
        assert params["departements"] == ["44", "85"]


async def test_an_unbounded_count_reads_geo_communes_only():
    session = _RecordingSession(total=10)

    await zone_queries.list_communes(session, demographie_departements=["44"])

    count_sql, _ = session.statements[0]
    assert "demographie" not in count_sql
    assert "LATERAL" not in count_sql


async def test_a_page_past_the_total_is_empty_with_its_total():
    session = _RecordingSession(total=120)

    rows, total = await zone_queries.list_communes(session, page=4, per_page=50)

    assert (rows, total) == ([], 120)
    # The page reads with the real offset: 150 is past 120, never capped to it.
    assert session.statements[1][1]["offset"] == 150


async def test_an_empty_departement_list_matches_nothing_but_is_bound():
    session = _RecordingSession(total=0)

    rows, total = await zone_queries.list_communes(session, departements=[])

    assert (rows, total) == ([], 0)
    for sql, params in session.statements:
        assert "ANY(CAST(:departements AS text[]))" in sql
        assert params["departements"] == []

"""Paris, Lyon, Marseille in the zone queries, the referential and the DVF view.

Plan 04.7-09, decisions 4 and 5. DVF and DPE are coded at the arrondissement:

- a PLM commune is read on its own code and all its arrondissements (D4), each
  sale and each document once; any other zone is read as before;
- ``inclure_arrondissements`` (D5) adds the arrondissements to the list, the
  search and the directory; off, the statements read ``geo_communes`` alone.

No database: the statements sent to a recording session are checked, and the DDL
of ``v_commune_dvf`` as text. The same rules against PostGIS are proven by
``tests/integration/test_plm_arrondissements.py``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from app.common.geo import ARRONDISSEMENT_TO_COMMUNE, COMMUNE_TO_ARRONDISSEMENTS
from app.commune.matview_ddl import INTERMEDIATE_MATVIEWS
from app.database import get_db
from app.main import app
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope
from app.zone import queries as zq
from app.zone import router as zone_router

pytestmark = pytest.mark.unit


class _Row:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._mapping = values
        self.__dict__.update(values)


class _Mappings:
    def first(self):
        return {}


class _Result:
    def __init__(self, scalar: int | None = None) -> None:
        self._scalar = scalar

    def scalar(self) -> int | None:
        return self._scalar

    def mappings(self) -> _Mappings:
        return _Mappings()

    def __iter__(self) -> Iterator[_Row]:
        return iter(())


class _Session:
    """Records every statement; counts are 0, pages are empty."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        return _Result(scalar=0)


def _compact(sql: str) -> str:
    return " ".join(sql.split())


PARIS = ["75056", *[f"751{i:02d}" for i in range(1, 21)]]


# ------------------------------------------------- DVF and DPE of a zone (D4) --


async def test_dvf_stats_of_paris_read_the_commune_and_its_arrondissements():
    session = _Session()

    await zq.zone_stats_dvf(session, "commune", "75056")  # type: ignore[arg-type]

    sql, params = session.calls[0]
    assert sorted(params["codes"]) == sorted(PARIS)
    assert "code_commune = ANY(CAST(:codes AS text[]))" in sql
    # A sale on two arrondissements stays in, a sale reaching another commune
    # does not: the rule of v_commune_dvf, on the lines of the mutation.
    assert "NOT EXISTS" in sql
    assert "m.code_commune <> ALL(CAST(:codes AS text[]))" in sql


async def test_dvf_stats_of_an_arrondissement_or_a_commune_keep_the_old_rule():
    for code in ("75112", "44109"):
        session = _Session()

        await zq.zone_stats_dvf(session, "commune", code)  # type: ignore[arg-type]

        sql, params = session.calls[0]
        assert params["codes"] == [code]
        assert "AND nb_communes = 1" in sql
        assert "NOT EXISTS" not in sql


async def test_dvf_stats_of_a_departement_are_unchanged():
    session = _Session()

    await zq.zone_stats_dvf(session, "departement", "75")  # type: ignore[arg-type]

    sql, params = session.calls[0]
    assert params["code"] == "75"
    assert "codes" not in params
    assert "AND nb_communes = 1" in sql


async def test_dvf_list_of_paris_counts_each_mutation_once_on_all_its_codes():
    session = _Session()

    await zq.zone_resolve_dvf(session, zone_type="commune", code="13055")  # type: ignore[arg-type]

    for sql, params in session.calls:
        assert sorted(params["codes"]) == sorted(
            ["13055", *COMMUNE_TO_ARRONDISSEMENTS["13055"]]
        )
        assert params["dept"] == "13"
        assert "code_commune = ANY(CAST(:codes AS text[]))" in sql


@pytest.mark.parametrize("call", ["resolve", "stats"])
async def test_dpe_of_a_plm_commune_reads_the_commune_and_its_arrondissements(call):
    session = _Session()

    if call == "resolve":
        await zq.zone_resolve_dpe(session, zone_type="commune", code="69123")  # type: ignore[arg-type]
    else:
        await zq.zone_stats_dpe(session, "commune", "69123")  # type: ignore[arg-type]

    for sql, params in session.calls:
        assert sorted(params["codes"]) == sorted(
            ["69123", *COMMUNE_TO_ARRONDISSEMENTS["69123"]]
        )
        # numero_dpe is unique: one code_insee per document, each read once.
        assert "code_insee = ANY(CAST(:codes AS text[]))" in sql


async def test_dpe_of_an_ordinary_commune_is_one_code():
    session = _Session()

    await zq.zone_stats_dpe(session, "commune", "44109")  # type: ignore[arg-type]

    assert session.calls[0][1] == {"codes": ["44109"]}


# ------------------------------------------------- the view, as DDL text (D4) --


def _dvf_ddl() -> str:
    return _compact(dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"])


def test_the_view_maps_the_45_arrondissements_and_the_three_communes():
    ddl = _dvf_ddl()
    for code, commune in ARRONDISSEMENT_TO_COMMUNE.items():
        assert f"('{code}', '{commune}')" in ddl
    for commune in COMMUNE_TO_ARRONDISSEMENTS:
        assert f"('{commune}', '{commune}')" in ddl


def test_the_view_keeps_the_ordinary_rule_and_adds_the_plm_select():
    ddl = _dvf_ddl()
    # Unchanged for communes and arrondissements ...
    assert "AND nb_communes = 1 AND surface_bati_totale > 0" in ddl
    assert "GROUP BY code_commune UNION ALL" in ddl
    # ... and one extra select per PLM commune, medians by the same expressions.
    assert "GROUP BY plm.commune_code" in ddl
    assert ddl.count("AS prix_m2_median_appartement") == 2
    assert ddl.count("percentile_cont(0.5)") == 6  # 3 medians in each select


def test_a_sale_on_several_arrondissements_is_in_only_if_every_line_is_in_the_commune():
    ddl = _dvf_ddl()
    assert "g.nb_communes = 1 OR ( g.nb_communes > 1 AND NOT EXISTS (" in ddl
    assert "other.commune_code IS DISTINCT FROM plm.commune_code" in ddl
    assert "m.id_mutation = g.id_mutation" in ddl
    assert "m.code_departement = g.code_departement" in ddl


def test_a_plm_commune_is_never_also_a_group_of_the_ordinary_select():
    # A line coded at the commune itself would otherwise give two rows of the
    # same code_insee, and break the unique index of the view.
    assert (
        "AND (code_commune IS NULL OR code_commune NOT IN ('75056', '69123', '13055'))"
    ) in _dvf_ddl()


# --------------------------------------------- referential, opt-in (D5) --


async def test_the_list_without_the_option_reads_geo_communes_alone():
    session = _Session()

    await zq.list_communes(session, departements=["75"], per_page=20, page=3)

    for sql, _ in session.calls:
        assert "geo_arrondissements" not in sql
        assert "FROM geo_communes gc" in sql
    page_sql, page_params = session.calls[1]
    assert "'commune'::text AS type_zone, NULL::text AS commune_parent" in page_sql
    assert (page_params["limit"], page_params["offset"]) == (20, 40)


async def test_the_list_with_the_option_adds_the_arrondissements_to_count_and_page():
    plain, with_option = _Session(), _Session()

    await zq.list_communes(plain, departements=["75"])  # type: ignore[arg-type]
    await zq.list_communes(  # type: ignore[arg-type]
        with_option,
        departements=["75"],
        inclure_arrondissements=True,
        arrondissement_departements=["75"],
    )

    # Same filters and bound parameters, plus the departements whose
    # arrondissements are served: only the source of rows differs.
    assert [{**p, "arrondissement_departements": ["75"]} for _, p in plain.calls] == [
        p for _, p in with_option.calls
    ]
    for sql, _ in with_option.calls:
        assert "FROM geo_arrondissements" in sql
        assert "FROM geo_communes" in sql
        assert "UNION ALL" in sql
    assert "gc.type_zone, gc.commune_parent" in with_option.calls[1][0]


async def test_the_option_keeps_the_filters_of_the_total_and_of_the_page_together():
    session = _Session()

    await zq.list_communes(  # type: ignore[arg-type]
        session,
        inclure_arrondissements=True,
        code_insee="75112",
        population_min=10,
        demographie_departements=["75"],
    )

    (count_sql, count_params), (page_sql, page_params) = session.calls
    for sql, params in ((count_sql, count_params), (page_sql, page_params)):
        assert "gc.code = :code_insee" in sql
        assert params["code_insee"] == "75112"
        assert ">= :population_min" in sql


async def test_search_without_the_option_reads_geo_communes_alone():
    session = _Session()

    await zq.search_communes(session, "Paris")  # type: ignore[arg-type]

    sql, _ = session.calls[0]
    assert "geo_arrondissements" not in sql
    assert "m.type_zone, m.commune_parent" in sql


async def test_search_with_the_option_adds_the_arrondissements_once_each():
    session = _Session()

    await zq.search_communes(session, "Paris", inclure_arrondissements=True)  # type: ignore[arg-type]

    sql, _ = session.calls[0]
    assert "FROM geo_arrondissements" in sql
    # One select per table: a code is in one of them only, so no code twice.
    assert sql.count("FROM geo_communes") == 1
    assert sql.count("FROM geo_arrondissements") == 1
    # ORDER BY 4 (population), 9 (normalised name), 1 (code) follow the columns.
    assert "ORDER BY 4 DESC NULLS LAST, 9, 1" in sql


# ---------------------------------------- the routes pass the option (D5) --


@pytest.fixture
def scope_44_75(client, full_scope_loader) -> InstanceScope:
    scope = InstanceScope(full_scope_loader("").datasets, ("44", "75"))
    app.dependency_overrides[get_instance_scope] = lambda: scope
    return scope


@pytest.fixture
def referential(monkeypatch):
    """Doubles of the list and the search: rows as SQL gives them."""
    calls: dict[str, list[dict]] = {"list": [], "search": []}

    def row(code, nom, type_zone="commune", parent=None):
        return {
            "code_insee": code,
            "nom": nom,
            "departement": code[:2],
            "population": 1000,
            "population_annee": 2022,
            "population_source": "demographie",
            "type_zone": type_zone,
            "commune_parent": parent,
        }

    async def list_communes(_db, **kwargs):
        calls["list"].append(kwargs)
        rows = [row("75056", "Paris")]
        if kwargs["inclure_arrondissements"]:
            rows.append(
                row("75112", "Paris 12e Arrondissement", "arrondissement", "75056")
            )
        return rows, len(rows)

    async def search_communes(_db, _q, **kwargs):
        calls["search"].append(kwargs)
        return (await list_communes(_db, **kwargs))[0]

    async def no_prices(_db, _scope, _hebergements):
        from app.zone.schemas import PrixAnnuaire

        return {}, PrixAnnuaire(etat="sans_lecture")

    monkeypatch.setattr(zone_router, "list_communes", list_communes)
    monkeypatch.setattr(zone_router, "search_communes", search_communes)
    monkeypatch.setattr(zone_router, "_annuaire_prices", no_prices)

    async def no_session():
        yield object()

    app.dependency_overrides[get_db] = no_session
    yield calls
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("path", ["/v1/communes", "/v1/communes/annuaire"])
async def test_the_option_is_off_by_default_and_rows_say_commune(
    client, scope_44_75, referential, path
):
    response = await client.get(path)

    assert response.status_code == 200, response.text
    assert referential["list"][-1]["inclure_arrondissements"] is False
    (row,) = response.json()["donnees"]["results"]
    assert (row["type_zone"], row["commune_parent"]) == ("commune", None)


@pytest.mark.parametrize("path", ["/v1/communes", "/v1/communes/annuaire"])
async def test_the_option_lists_the_arrondissement_with_its_commune(
    client, scope_44_75, referential, path
):
    response = await client.get(path, params={"inclure_arrondissements": "true"})

    assert response.status_code == 200, response.text
    assert referential["list"][-1]["inclure_arrondissements"] is True
    rows = {r["code_insee"]: r for r in response.json()["donnees"]["results"]}
    assert set(rows) == {"75056", "75112"}
    assert rows["75112"]["nom"] == "Paris 12e Arrondissement"
    assert (rows["75112"]["type_zone"], rows["75112"]["commune_parent"]) == (
        "arrondissement",
        "75056",
    )
    assert rows["75056"]["type_zone"] == "commune"


async def test_search_paris_has_no_duplicate_with_the_option(
    client, scope_44_75, referential
):
    response = await client.get(
        "/v1/communes/search", params={"q": "Paris", "inclure_arrondissements": "true"}
    )

    assert response.status_code == 200, response.text
    assert referential["search"][-1]["inclure_arrondissements"] is True
    codes = [r["code_insee"] for r in response.json()["donnees"]]
    assert codes == ["75056", "75112"]
    assert len(codes) == len(set(codes))


async def test_search_without_the_option_is_the_old_search(
    client, scope_44_75, referential
):
    response = await client.get("/v1/communes/search", params={"q": "Paris"})

    assert referential["search"][-1]["inclure_arrondissements"] is False
    assert [r["code_insee"] for r in response.json()["donnees"]] == ["75056"]

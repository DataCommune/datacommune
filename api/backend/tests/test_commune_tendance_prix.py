"""Price trend of a commune: the series, its publication rule and its read.

Decision of Ianis of 2026-11-11, thresholds measured in
internal note 2026-11-11-tendance-prix-mesure. No database here:
the rows of ``v_commune_dvf_annuel`` are given as the view serves them (median
null under ``SEUIL_ND_COMMUNE`` sales). The SQL of the view against PostGIS is
proven by ``tests/integration/test_commune_dvf_annuel.py``.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy.exc import DBAPIError

from app.common.semantic_contract import load_registry
from app.commune import queries
from app.commune import router as commune_router
from app.commune.dependencies import ANNUEL_EDGES
from app.commune.matview_ddl import ANNUEL_CREATE_SQL, INTERMEDIATE_MATVIEWS
from app.commune.schemas import (
    COMMUNE_FIELD_SOURCES,
    ResultatCommuneSynthese,
    commune_field_id,
)
from app.commune.tendance_prix import TendancePrix, build_tendance_prix
from app.database import get_db
from app.dvf.couverture import (
    ANNEES_TENDANCE,
    SEUIL_EVOLUTION_COMMUNE,
    SEUIL_ND_COMMUNE,
)
from app.main import app
from app.scope.dependencies import get_instance_scope
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit

MAISON, APPARTEMENT = "residentiel_maison", "residentiel_appartement"
FENETRE = (2021, 2025)


def _serie(classe: str, effectifs_et_medianes: dict[int, tuple[int, int | None]]):
    return [
        {
            "classe_mutation": classe,
            "annee": annee,
            "nb_ventes": nb,
            "prix_m2_median": mediane,
        }
        for annee, (nb, mediane) in effectifs_et_medianes.items()
    ]


def _type(tendance: dict, type_bien: str) -> dict:
    (found,) = [t for t in tendance["types"] if t["type_bien"] == type_bien]
    return found


# -- the publication rule -----------------------------------------------------


def test_thresholds_are_the_measured_ones():
    assert (SEUIL_ND_COMMUNE, SEUIL_EVOLUTION_COMMUNE, ANNEES_TENDANCE) == (5, 30, 5)


def test_a_change_is_written_when_both_ends_reach_the_threshold():
    lignes = _serie(
        APPARTEMENT,
        {
            2021: (3996, 3500),
            2022: (3800, 3700),
            2023: (3100, 3900),
            2024: (2900, 3850),
            2025: (3200, 3920),
        },
    )
    tendance = build_tendance_prix(FENETRE, lignes)

    assert (tendance["annee_debut"], tendance["annee_fin"]) == (2021, 2025)
    assert (tendance["seuil_evolution"], tendance["seuil_point"]) == (30, 5)
    appartement = _type(tendance, "appartement")
    assert appartement["etat"] == "evolution"
    assert appartement["evolution_pct"] == 12.0  # (3920 - 3500) / 3500
    assert appartement["nb_ventes"] == 16996
    assert [a["annee"] for a in appartement["annees"]] == [2021, 2022, 2023, 2024, 2025]
    assert [a["prix_m2_median"] for a in appartement["annees"]] == [
        3500,
        3700,
        3900,
        3850,
        3920,
    ]
    TendancePrix.model_validate(tendance)


def test_a_fall_keeps_its_sign_and_one_decimal():
    lignes = _serie(MAISON, {2021: (40, 3000), 2025: (31, 2890)})
    assert (
        _type(build_tendance_prix(FENETRE, lignes), "maison")["evolution_pct"] == -3.7
    )


@pytest.mark.parametrize(
    ("debut", "fin"),
    [(29, 30), (30, 29), (5, 400), (400, 5)],
)
def test_one_end_under_the_threshold_gives_points_without_a_change(debut, fin):
    # A commune under the threshold: the medians stay, no percentage is written.
    lignes = _serie(MAISON, {2021: (debut, 2000), 2023: (12, 2100), 2025: (fin, 2400)})
    maison = _type(build_tendance_prix(FENETRE, lignes), "maison")

    assert maison["etat"] == "points"
    assert maison["evolution_pct"] is None
    assert [a["prix_m2_median"] for a in maison["annees"]] == [
        2000,
        None,
        2100,
        None,
        2400,
    ]


def test_exactly_the_threshold_at_both_ends_is_enough():
    lignes = _serie(MAISON, {2021: (30, 2000), 2025: (30, 2200)})
    assert _type(build_tendance_prix(FENETRE, lignes), "maison")["etat"] == "evolution"


def test_points_need_two_years_with_a_median():
    # One year at 5 sales draws nothing; under 5 the view serves no median.
    lignes = _serie(MAISON, {2021: (4, None), 2022: (6, 1800), 2025: (2, None)})
    maison = _type(build_tendance_prix(FENETRE, lignes), "maison")

    assert maison["etat"] == "echantillon_insuffisant"
    assert maison["evolution_pct"] is None
    # The real counts are kept, a year without a row is a true zero.
    assert [a["nb_ventes"] for a in maison["annees"]] == [4, 6, 0, 0, 2]


def test_a_commune_without_any_retained_sale_serves_both_types_insufficient():
    tendance = build_tendance_prix(FENETRE, [])

    assert [t["type_bien"] for t in tendance["types"]] == ["maison", "appartement"]
    assert {t["etat"] for t in tendance["types"]} == {"echantillon_insuffisant"}
    assert all(t["nb_ventes"] == 0 for t in tendance["types"])


def test_the_most_sold_type_comes_first():
    lignes = [
        *_serie(MAISON, {2021: (40, 2000), 2025: (40, 2200)}),
        *_serie(APPARTEMENT, {2021: (300, 3000), 2025: (310, 3300)}),
    ]
    tendance = build_tendance_prix(FENETRE, lignes)
    assert [t["type_bien"] for t in tendance["types"]] == ["appartement", "maison"]


def test_a_single_closed_year_never_gives_a_change():
    lignes = _serie(MAISON, {2025: (500, 2000)})
    maison = _type(build_tendance_prix((2025, 2025), lignes), "maison")
    assert maison["etat"] == "echantillon_insuffisant"


def test_rows_outside_the_window_or_of_another_class_are_ignored():
    lignes = [
        *_serie(MAISON, {2020: (900, 1000), 2021: (40, 2000), 2025: (40, 2200)}),
        *_serie("mixte", {2021: (900, 9000)}),
    ]
    maison = _type(build_tendance_prix(FENETRE, lignes), "maison")
    assert maison["nb_ventes"] == 80
    assert maison["evolution_pct"] == 10.0


# -- the view: the rule of v_commune_dvf, year by year ---------------------------


def _retained_clauses(sql: str) -> list[str]:
    """The clauses of the retained sales, window apart."""
    return [
        line.strip()
        for line in sql.splitlines()
        if line.strip().startswith("AND ") and "date_mutation" not in line
    ]


def test_the_yearly_view_retains_the_sales_of_the_commune_view():
    commune_dvf = dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"]
    assert _retained_clauses(ANNUEL_CREATE_SQL) == _retained_clauses(commune_dvf)


def test_the_yearly_view_serves_paris_lyon_marseille_at_the_commune():
    # The rule of migration 111: the commune row is built on its arrondissements,
    # and is never a group of the first select.
    assert "JOIN plm ON plm.code = g.code_commune" in ANNUEL_CREATE_SQL
    assert "GROUP BY plm.commune_code, g.classe_mutation" in ANNUEL_CREATE_SQL
    assert "code_commune NOT IN ('75056', '69123', '13055')" in ANNUEL_CREATE_SQL
    for code, commune in (("75112", "75056"), ("69383", "69123"), ("13208", "13055")):
        assert f"('{code}', '{commune}')" in ANNUEL_CREATE_SQL


def test_the_yearly_view_keeps_closed_years_only_and_no_year_in_dur():
    assert "date_part('year', max(date_mutation) + 1)::int - 1" in ANNUEL_CREATE_SQL
    assert f"derniere.annee - {ANNEES_TENDANCE - 1}" in ANNUEL_CREATE_SQL
    assert "INTERVAL '12 months'" not in ANNUEL_CREATE_SQL
    assert not re.search(r"\b(?:19|20)\d\d\b", ANNUEL_CREATE_SQL)


# -- the read behind the barrier -------------------------------------------------


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Savepoint:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.savepoints.append("open")

    async def __aexit__(self, exc_type, *_):
        # A failure inside rolls the savepoint back and lets the error through.
        self.session.savepoints[-1] = "rolled back" if exc_type else "released"
        return False


class _Session:
    def __init__(self, rows, error=None):
        self.rows = rows
        self.error = error
        self.calls: list[tuple[str, dict]] = []
        self.savepoints: list[str] = []

    def begin_nested(self):
        return _Savepoint(self)

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if self.error is not None:
            raise self.error
        return _Mappings(self.rows)


@pytest.fixture
def no_lock(monkeypatch):
    async def acquire(_session):
        return None

    monkeypatch.setattr(queries, "acquire_publication_read_lock", acquire)


def test_the_view_has_its_own_edge_to_the_sales():
    assert ANNUEL_EDGES == (("v_commune_dvf_annuel", "mutations_grain"),)


async def test_the_read_is_gated_by_the_edge_of_the_view(no_lock):
    lignes = _serie(MAISON, {2021: (40, 2000), 2025: (40, 2200)})
    session = _Session(
        [
            {
                "ready": True,
                "fenetre": {"debut": 2021, "fin": 2025},
                "lignes": [{"code_insee": "44109", **ligne} for ligne in lignes],
            }
        ]
    )

    tendances = await queries.tendances_prix_par_codes(
        session, ["44109", "44026"], ["demographie", "dvf"]
    )

    ((sql, params),) = session.calls
    assert "('v_commune_dvf_annuel', 'mutations_grain')" in sql
    assert "matview_name = 'v_commune_synthese'" in sql
    assert params["codes"] == ["44109", "44026"]
    assert params["datasets"] == ["dvf"]
    assert _type(tendances["44109"], "maison")["evolution_pct"] == 10.0
    # A requested commune without a row: its true zeros, never a missing key.
    assert _type(tendances["44026"], "maison")["nb_ventes"] == 0


async def test_dvf_not_hosted_reads_nothing(no_lock):
    session = _Session([])
    assert await queries.tendances_prix_par_codes(session, ["29019"], ["dpe"]) == {}
    assert session.calls == []


NOT_PUBLISHED = {"etat": "not_qualified", "motif": "tendance_prix_non_publiee"}


async def test_an_empty_view_serves_the_declared_absence(no_lock):
    session = _Session(
        [{"ready": True, "fenetre": {"debut": None, "fin": None}, "lignes": None}]
    )
    assert await queries.tendances_prix_par_codes(session, ["44109"], ["dvf"]) == {
        "44109": NOT_PUBLISHED
    }


@pytest.mark.parametrize(
    "rows", [[], [{"ready": False, "fenetre": None, "lignes": None}]]
)
async def test_a_series_not_ready_is_an_absence_never_an_outage(no_lock, rows):
    # Gates of 2026-11-11: a trend view that is missing, stale or being rebuilt
    # must never make the commune page unavailable. The synthesis has its own
    # barrier; the series is served absent, its reason declared.
    session = _Session(rows)
    assert await queries.tendances_prix_par_codes(
        session, ["44109", "44026"], ["dvf"]
    ) == {"44109": NOT_PUBLISHED, "44026": NOT_PUBLISHED}
    # Read in a savepoint: the transaction of the page stays usable after it.
    assert session.savepoints == ["rolled back"]


@pytest.mark.parametrize("sqlstate", ["42P01", "57014"])
async def test_a_missing_view_or_a_cancelled_read_is_an_absence(no_lock, sqlstate):
    class _OrigError(Exception):
        pass

    orig = _OrigError()
    orig.sqlstate = sqlstate
    session = _Session([], error=DBAPIError("select", {}, orig))

    assert await queries.tendances_prix_par_codes(session, ["44109"], ["dvf"]) == {
        "44109": NOT_PUBLISHED
    }
    assert session.savepoints == ["rolled back"]


async def test_another_sql_defect_keeps_its_error(no_lock):
    class _OrigError(Exception):
        sqlstate = "42703"

    session = _Session([], error=DBAPIError("select", {}, _OrigError()))
    with pytest.raises(DBAPIError):
        await queries.tendances_prix_par_codes(session, ["44109"], ["dvf"])


async def test_a_served_series_releases_its_savepoint(no_lock):
    session = _Session(
        [{"ready": True, "fenetre": {"debut": 2021, "fin": 2025}, "lignes": None}]
    )
    tendances = await queries.tendances_prix_par_codes(session, ["44109"], ["dvf"])
    assert tendances["44109"]["annee_fin"] == 2025
    assert session.savepoints == ["released"]


# -- the contract ------------------------------------------------------------------


def test_the_field_is_masked_with_its_source():
    assert COMMUNE_FIELD_SOURCES["tendance_prix"] == "dvf"
    assert "tendance_prix" in ResultatCommuneSynthese.model_fields


@pytest.mark.parametrize(
    ("path", "field_id"),
    [
        (("annee_debut",), "dvf.tendance.annee"),
        (("annee_fin",), "dvf.tendance.annee"),
        (("seuil_evolution",), "dvf.tendance.seuil"),
        (("seuil_point",), "dvf.tendance.seuil"),
        (("types", "[0]", "evolution_pct"), "dvf.tendance.evolution_pct"),
        (("types", "[1]", "nb_ventes"), "dvf.tendance.nb_ventes"),
        (("types", "[0]", "annees", "[4]", "annee"), "dvf.tendance.annee"),
        (("types", "[0]", "annees", "[4]", "nb_ventes"), "dvf.tendance.nb_ventes"),
        (
            ("types", "[1]", "annees", "[0]", "prix_m2_median"),
            "dvf.tendance.prix_m2_median",
        ),
    ],
)
def test_every_number_of_the_block_has_a_contract_entry(path, field_id):
    assert commune_field_id(("tendance_prix", *path)) == field_id
    assert field_id in load_registry()


# -- /v1/commune ---------------------------------------------------------------------


@pytest.fixture
def commune_http(monkeypatch):
    """/v1/commune on doubles: the synthesis row, then the trend read."""
    reads: list[tuple[list[str], list[str]]] = []

    async def rows(_db, codes, _sources):
        return [{"code_insee": code, "nom_commune": "Nantes"} for code in codes]

    async def tendances(_db, codes, sources):
        reads.append((list(codes), list(sources)))
        lignes = _serie(APPARTEMENT, {2021: (3996, 3500), 2025: (3200, 3920)})
        return {code: build_tendance_prix(FENETRE, lignes) for code in codes}

    async def no_window(_db):
        return None

    async def no_facts(_db, _names):
        return {}

    monkeypatch.setattr(commune_router, "commune_synthese_par_codes", rows)
    monkeypatch.setattr(commune_router, "tendances_prix_par_codes", tendances)
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", no_window)
    monkeypatch.setattr(commune_router, "get_provenance_facts", no_facts)
    app.dependency_overrides[get_db] = lambda: None
    yield reads
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_instance_scope, None)


async def test_commune_http_serves_the_trend_of_a_hosted_commune(client, commune_http):
    app.dependency_overrides[get_instance_scope] = lambda: InstanceScope(
        frozenset({"demographie", "dvf"}), ("44",)
    )

    response = await client.get("/v1/commune?code_insee=44109")

    assert response.status_code == 200
    (commune,) = response.json()["donnees"]
    tendance = commune["tendance_prix"]
    assert (tendance["annee_debut"], tendance["annee_fin"]) == (2021, 2025)
    assert tendance["types"][0]["type_bien"] == "appartement"
    assert tendance["types"][0]["evolution_pct"] == 12.0
    assert commune_http == [(["44109"], ["demographie", "dvf"])]


async def test_commune_http_serves_the_declared_absence_of_the_series(
    client, commune_http, monkeypatch
):
    async def absent(_db, codes, _sources):
        return {code: dict(NOT_PUBLISHED) for code in codes}

    monkeypatch.setattr(commune_router, "tendances_prix_par_codes", absent)
    app.dependency_overrides[get_instance_scope] = lambda: InstanceScope(
        frozenset({"demographie", "dvf"}), ("44",)
    )

    response = await client.get("/v1/commune?code_insee=44109")

    assert response.status_code == 200
    (commune,) = response.json()["donnees"]
    assert commune["tendance_prix"] == NOT_PUBLISHED
    assert commune["nom_commune"] == "Nantes"


async def test_commune_http_serves_null_when_dvf_is_out_of_coverage(
    client, commune_http
):
    # Null with its reason in couverture, never an empty series (SEM-01).
    app.dependency_overrides[get_instance_scope] = lambda: InstanceScope(
        frozenset({"demographie"}), ("44",)
    )

    response = await client.get("/v1/commune?code_insee=44109")

    (commune,) = response.json()["donnees"]
    assert commune["tendance_prix"] is None
    assert commune["couverture"]["dvf"]["etat"] == "hors_couverture"
    assert commune_http == []

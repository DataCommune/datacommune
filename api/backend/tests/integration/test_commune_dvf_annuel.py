"""Yearly price series of a commune against PostGIS (migration 115).

The view ``v_commune_dvf_annuel`` is proven both ways it exists: ``migration``
(the frozen statements of migration 115) and ``reconstruction`` (the
application DDL, as every commune source publication rebuilds it).

Fixture: the latest sale is a 30 June 2026, so 2026 is not closed and the
window is 2021 -> 2025. Nantes has flats over the years (6, 4, 0, 0, 5), one
sale in 2020 (before the window), one in 2026 (unclosed year), and the sales
every DVF statistic refuses (not a sale, VEFA, two communes, no surface, no
price, a class outside the statistics). Paris has sales at two arrondissements,
one straddling both (counted once at the commune), one reaching another commune
(excluded); Lyon and Marseille one arrondissement each.

    pwsh -NoProfile -Command "& ./scripts/test-postgis.ps1 -Paths
        tests/integration/test_commune_dvf_annuel.py; exit $LASTEXITCODE"
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.commune.matview_ddl import (
    annuel_statements,
    commune_view_drift,
    recreate_commune_synthese,
)
from app.commune.queries import tendances_prix_par_codes

# ``migrated`` (and the ``migrated_dsn`` it needs) are fixtures: imported for
# pytest, not called.
from tests.integration.test_commune_coverage_contract import (  # noqa: F401
    _engine,
    _migrate,
    migrated,
    migrated_dsn,
)

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
MAISON, APPARTEMENT = "residentiel_maison", "residentiel_appartement"
# Price per m2 of the Nantes flats of 2021 (valeur_fonciere / 50 m2): median 3250.
NANTES_2021 = (3000, 3100, 3200, 3300, 3400, 3500)
NANTES_2025 = (3600, 3700, 3800, 3900, 4000)


def _sale(
    conn,
    ident: str,
    code: str,
    per_m2: int,
    day: date,
    *,
    classe: str = APPARTEMENT,
    lines: tuple[str, ...] = (),
    est_vente: bool = True,
    vefa: bool = False,
    surface: int | None = 50,
    valeur: int | None = None,
) -> None:
    """One mutation of the grain, and its lines when it spans communes."""
    dept = code[:2]
    conn.execute(
        """INSERT INTO mutations_grain (
            code_departement, id_mutation, date_mutation, classe_mutation,
            nb_lignes, nb_dispositions, nb_communes, nb_parcelles,
            mono_local, est_vente, hors_stats_vefa, valeur_fonciere,
            surface_bati_totale, code_commune)
        VALUES (%s, %s, %s, %s, 1, 1, %s, 1, true, %s, %s, %s, %s, %s)""",
        (
            dept,
            ident,
            day,
            classe,
            max(1, len(lines)),
            est_vente,
            vefa,
            per_m2 * 50 if valeur is None else valeur,
            surface,
            code,
        ),
    )
    for line in lines:
        conn.execute(
            "INSERT INTO mutations (code_departement, id_mutation, date_mutation, "
            "code_commune) VALUES (%s, %s, %s, %s)",
            (dept, ident, day, line),
        )


def _seed(conn, *, latest: date = date(2026, 6, 30)) -> None:
    conn.execute("TRUNCATE mutations_grain, mutations")
    for index, per_m2 in enumerate(NANTES_2021):
        _sale(conn, f"N21-{index}", "44109", per_m2, date(2021, 3, 1 + index))
    for index in range(4):
        _sale(conn, f"N22-{index}", "44109", 3300, date(2022, 12, 31))
    for index, per_m2 in enumerate(NANTES_2025):
        _sale(conn, f"N25-{index}", "44109", per_m2, date(2025, 1, 1 + index))
    # Houses of Nantes, their own series.
    for index in range(5):
        _sale(conn, f"N25-M{index}", "44109", 2500, date(2025, 6, 1), classe=MAISON)
    # Before the window, and in the unclosed year (the latest sale of the base).
    _sale(conn, "N20", "44109", 9000, date(2020, 12, 31))
    _sale(conn, "N26", "44109", 9000, latest)
    # What no DVF statistic retains.
    _sale(conn, "N-DON", "44109", 9000, date(2021, 5, 1), est_vente=False)
    _sale(conn, "N-VEFA", "44109", 9000, date(2021, 5, 2), vefa=True)
    _sale(conn, "N-2COM", "44109", 9000, date(2021, 5, 3), lines=("44109", "44162"))
    _sale(conn, "N-SURF0", "44109", 9000, date(2021, 5, 4), surface=0)
    _sale(conn, "N-PRIX0", "44109", 0, date(2021, 5, 5), valeur=0)
    _sale(conn, "N-MIXTE", "44109", 9000, date(2021, 5, 6), classe="mixte")
    # Paris: two arrondissements, a sale on both, a sale reaching another commune.
    for index in range(5):
        _sale(conn, f"P12-{index}", "75112", 8000, date(2025, 2, 1))
    for index in range(2):
        _sale(conn, f"P1-{index}", "75101", 12000, date(2025, 2, 2))
    _sale(conn, "P-1-12", "75101", 9000, date(2025, 2, 3), lines=("75101", "75112"))
    _sale(conn, "P-AUTRE", "75112", 20000, date(2025, 2, 4), lines=("75112", "92050"))
    # Lyon and Marseille: one arrondissement each, served at the commune too.
    _sale(conn, "L3", "69383", 5000, date(2024, 7, 1))
    _sale(conn, "M8", "13208", 4000, date(2023, 7, 1))


def _migration_115():
    path = BACKEND / "migrations" / "versions" / "115_commune_dvf_annuel.py"
    spec = importlib.util.spec_from_file_location("migration_115", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(conn, construction: str) -> None:
    if construction == "reconstruction":
        recreate_commune_synthese(conn)
    else:
        for statement in _migration_115().ANNUEL_STATEMENTS:
            conn.execute(statement)


def _rows(conn) -> dict[tuple[str, str, int], tuple[int | None, int]]:
    return {
        (code, classe, annee): (mediane, nb)
        for code, classe, annee, mediane, nb in conn.execute(
            "SELECT code_insee, classe_mutation, annee, prix_m2_median, nb_ventes "
            "FROM v_commune_dvf_annuel"
        )
    }


@pytest.fixture(params=["reconstruction", "migration"])
def serie(request, migrated):  # noqa: F811
    conn, dsn, _ = migrated
    _seed(conn)
    _build(conn, request.param)
    return conn, dsn


def test_the_series_of_a_commune_year_by_year(serie):
    conn, _ = serie
    rows = _rows(conn)

    # 2021: six retained sales, none of the refused ones; the median is exact.
    assert rows[("44109", APPARTEMENT, 2021)] == (3250, 6)
    # 2022: four sales, the real count without a median (under 5).
    assert rows[("44109", APPARTEMENT, 2022)] == (None, 4)
    assert rows[("44109", APPARTEMENT, 2025)] == (3800, 5)
    assert rows[("44109", MAISON, 2025)] == (2500, 5)
    # A year without a retained sale has no row; nothing before the window,
    # nothing of the unclosed year.
    assert {annee for code, _, annee in rows if code == "44109"} == {2021, 2022, 2025}
    assert {annee for _, _, annee in rows} <= set(range(2021, 2026))
    assert not [key for key in rows if key[1] not in (MAISON, APPARTEMENT)]


def test_paris_lyon_marseille_are_served_at_the_commune(serie):
    conn, _ = serie
    rows = _rows(conn)

    # The arrondissement rows: their own sales, one commune each.
    assert rows[("75112", APPARTEMENT, 2025)] == (8000, 5)
    assert rows[("75101", APPARTEMENT, 2025)] == (None, 2)
    # Paris: 5 + 2 + the sale on two arrondissements, counted once; the sale
    # reaching another commune excluded. 8000 x5, 9000, 12000 x2: median 8000.
    assert rows[("75056", APPARTEMENT, 2025)] == (8000, 8)
    assert rows[("69123", APPARTEMENT, 2024)] == (None, 1)
    assert rows[("69383", APPARTEMENT, 2024)] == (None, 1)
    assert rows[("13055", APPARTEMENT, 2023)] == (None, 1)
    assert rows[("13208", APPARTEMENT, 2023)] == (None, 1)


def test_one_row_per_code_class_and_year(serie):
    conn, _ = serie
    indexdef = conn.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'idx_commune_dvf_annuel_code_classe_annee'"
    ).fetchone()[0]
    assert "UNIQUE" in indexdef
    assert "(code_insee, classe_mutation, annee)" in indexdef


@pytest.mark.parametrize(
    ("latest", "years"),
    [
        # A 31 December closes its own year: the window moves to 2022 -> 2026.
        (date(2026, 12, 31), {2022, 2025, 2026}),
        # A half-year delivery never enters the series.
        (date(2026, 6, 30), {2021, 2022, 2025}),
        (date(2026, 12, 30), {2021, 2022, 2025}),
    ],
)
def test_a_year_enters_the_series_only_when_closed(migrated, latest, years):  # noqa: F811
    conn, _, _ = migrated
    _seed(conn, latest=latest)
    for statement in annuel_statements():
        conn.execute(statement)

    assert {annee for code, _, annee in _rows(conn) if code == "44109"} == years


def test_an_empty_base_gives_an_empty_view(migrated):  # noqa: F811
    conn, _, _ = migrated
    conn.execute("TRUNCATE mutations_grain, mutations")
    for statement in annuel_statements():
        conn.execute(statement)
    assert _rows(conn) == {}


@pytest.mark.asyncio
async def test_the_block_read_behind_the_real_barrier(serie):
    _, dsn = serie
    engine = _engine(dsn, None)
    try:
        async with async_sessionmaker(engine)() as session:
            tendances = await tendances_prix_par_codes(
                session, ["44109", "75056", "44162"], ["demographie", "dvf"]
            )
    finally:
        await engine.dispose()

    nantes = tendances["44109"]
    assert (nantes["annee_debut"], nantes["annee_fin"]) == (2021, 2025)
    appartement, maison = nantes["types"]
    assert appartement["type_bien"] == "appartement"  # 15 sales against 5
    # 6 and 5 sales at both ends: the medians are served, no change is written.
    assert appartement["etat"] == "points"
    assert appartement["evolution_pct"] is None
    assert [(a["prix_m2_median"], a["nb_ventes"]) for a in appartement["annees"]] == [
        (3250, 6),
        (None, 4),
        (None, 0),
        (None, 0),
        (3800, 5),
    ]
    assert maison["etat"] == "echantillon_insuffisant"
    assert tendances["75056"]["types"][0]["nb_ventes"] == 8
    # A commune without any retained sale: its true zeros.
    assert all(t["nb_ventes"] == 0 for t in tendances["44162"]["types"])


@pytest.mark.asyncio
async def test_a_missing_view_is_an_absence_and_the_transaction_stays_usable(serie):
    # Gates of 2026-11-11: fixtures that build their own chain have no yearly
    # view; the commune page must still be served, the series declared absent.
    conn, dsn = serie
    conn.execute("DROP MATERIALIZED VIEW v_commune_dvf_annuel")
    engine = _engine(dsn, None)
    try:
        async with async_sessionmaker(engine)() as session:
            tendances = await tendances_prix_par_codes(session, ["44109"], ["dvf"])
            # The failed read was rolled back to its savepoint only.
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()
        for statement in annuel_statements():
            conn.execute(statement)

    assert tendances == {
        "44109": {"etat": "not_qualified", "motif": "tendance_prix_non_publiee"}
    }


@pytest.mark.asyncio
async def test_an_empty_view_is_an_absence(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    conn.execute("TRUNCATE mutations_grain, mutations")
    for statement in annuel_statements():
        conn.execute(statement)
    engine = _engine(dsn, None)
    try:
        async with async_sessionmaker(engine)() as session:
            tendances = await tendances_prix_par_codes(session, ["44109"], ["dvf"])
    finally:
        await engine.dispose()
    assert tendances["44109"]["motif"] == "tendance_prix_non_publiee"


# --------------------------------------------------- migration 115 itself --


def test_migration_115_roundtrip_and_no_drift(migrated_dsn):  # noqa: F811
    from alembic import command

    def present(conn) -> bool:
        return (
            conn.execute(
                "SELECT to_regclass('v_commune_dvf_annuel') IS NOT NULL"
            ).fetchone()[0]
            is True
        )

    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert present(conn)
        assert commune_view_drift(conn) == []

    _migrate(migrated_dsn, command.downgrade, "114_signalements")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert not present(conn)
        # Only the yearly view went: the other views of the chain are untouched.
        assert commune_view_drift(conn) == ["v_commune_dvf_annuel"]

    _migrate(migrated_dsn, command.upgrade, "head")
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        assert present(conn)
        assert commune_view_drift(conn) == []

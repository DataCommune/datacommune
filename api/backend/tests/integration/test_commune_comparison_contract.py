"""D-48 on a migrated database: the comparison served by real HTTP.

The view is the one ``alembic upgrade head`` installs; identities come from
``geo_communes``, syntheses through the same projection as ``/v1/commune``.
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.exceptions import register_exception_handlers
from app.common.semantic_audit import audit_commune_payload
from app.commune import queries
from app.commune import router as commune_router
from app.commune.indicator_reader import ActiveIndicators
from app.commune.router import router
from app.config import settings
from app.database import get_db
from app.dvf.couverture import SEUIL_ND_COMMUNE
from tests import test_commune_indicator_contract as indicator_fixtures
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.test_commune_absences_http import _apartment_sales

# ``migrated`` (and the ``migrated_dsn`` it needs) are fixtures: imported for
# pytest, not called.
from tests.integration.test_commune_coverage_contract import (  # noqa: F401
    _engine,
    documents,
    migrated,
    migrated_dsn,
)

# Baseline communes of the ``migrated`` fixture (demographie rows), plus one
# commune of the referential without any synthesis row.
REFERENTIEL = (
    ("44991", "Ancienne", "44"),
    ("44992", "Courante", "44"),
    ("85991", "Couverture inconnue", "85"),
    ("44993", "Sans synthese", "44"),
)
UNKNOWN = "44999"


def _referentiel(conn) -> None:
    for code, nom, departement in REFERENTIEL:
        conn.execute(
            "INSERT INTO geo_communes (code, nom, departement_code, geometry) "
            "VALUES (%s, %s, %s, ST_Multi(ST_Buffer(ST_SetSRID("
            "ST_MakePoint(-1.5, 47.2), 4326), 0.01))) ON CONFLICT (code) DO NOTHING",
            (code, nom, departement),
        )


def _revenus(conn, *rows) -> None:
    conn.execute("TRUNCATE revenus")
    for code, revenu, annee in rows:
        conn.execute(
            "INSERT INTO revenus (code_insee, revenu_median, annee) "
            "VALUES (%s, %s, %s)",
            (code, revenu, annee),
        )


async def _compare(
    conn, dsn, codes: str | None, statements: list[str] | None = None
) -> httpx.Response:
    """``GET /v1/communes/comparaison?codes=`` on the view refreshed from *conn*."""
    _referentiel(conn)
    documents(conn, "migration")
    engine = _engine(dsn, None)
    if statements is not None:

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

    sessions = async_sessionmaker(engine)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = database
    params = {} if codes is None else {"codes": codes}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            return await client.get("/v1/communes/comparaison", params=params)
    finally:
        await engine.dispose()


def _critere(body: dict, cle: str) -> dict:
    return next(c for c in body["donnees"]["criteres"] if c["cle"] == cle)


def _audit(body: dict) -> list[str]:
    """Semantic audit of every served synthesis, as a /v1/commune body."""
    errors = []
    for commune in body["donnees"]["communes"]:
        if commune["synthese"] is not None:
            payload = {"donnees": [commune["synthese"]], "meta": body["meta"]}
            errors += audit_commune_payload(payload)
    return errors


@pytest.mark.asyncio
async def test_same_vintage_serves_the_gap(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    _revenus(conn, ("44991", 21000, 2021), ("44992", 22480, 2021))
    response = await _compare(conn, dsn, "44991,44992")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [c["code_insee"] for c in body["donnees"]["communes"]] == [
        "44991",
        "44992",
    ]
    assert body["donnees"]["communes"][0]["nom"] == "Ancienne"
    reference, other = _critere(body, "revenu_median")["cellules"]
    assert reference["valeur"] == 21000
    assert other["valeur"] == 22480
    assert other["ecart"] == {"valeur": 22480 - 21000, "unite": "euro_per_uc_year"}
    assert other["motif_non_calcul"] is None
    assert _audit(body) == []


@pytest.mark.asyncio
async def test_different_vintages_serve_the_reason(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    _revenus(conn, ("44991", 21000, 2021), ("44992", 22480, 2019))
    response = await _compare(conn, dsn, "44991,44992")

    assert response.status_code == 200, response.text
    reference, other = _critere(response.json(), "revenu_median")["cellules"]
    assert (reference["periode"], other["periode"]) == ("2021", "2019")
    assert other["ecart"] is None
    assert other["motif_non_calcul"] == "periodes_differentes"


@pytest.mark.asyncio
async def test_out_of_coverage_commune_keeps_its_column_and_unknown_code_is_reported(
    migrated,  # noqa: F811
):
    """Three communes, one outside the hosted departements, one unknown code."""
    conn, dsn, _ = migrated
    install_instance_scope(conn, COMMUNE_SOURCES, ("44",))
    _revenus(conn, ("44991", 21000, 2021), ("44992", 21000, 2021))
    response = await _compare(conn, dsn, f"44991,85991,{UNKNOWN},44992")

    assert response.status_code == 200, response.text
    body = response.json()
    donnees = body["donnees"]
    assert [c["code_insee"] for c in donnees["communes"]] == ["44991", "85991", "44992"]
    assert donnees["codes_inconnus"] == [UNKNOWN]
    outside = donnees["communes"][1]
    assert outside["nom"] == "Couverture inconnue"
    assert outside["synthese"] is None
    assert outside["hebergement"] == {
        "etat": "hors_couverture",
        "motif": "territoire_hors_perimetre",
        "departements": [],
    }
    assert donnees["criteres"]
    for critere in donnees["criteres"]:
        cellule = critere["cellules"][1]
        assert (cellule["etat"], cellule["valeur"]) == ("out_of_coverage", None)
        assert cellule["motif_non_calcul"] == "commune_hors_couverture"
        assert cellule["ecart"] is None
        assert critere["identiques"] is False
    # Column 3 is still compared to column 1: equal revenus, a served 0.
    third = _critere(body, "revenu_median")["cellules"][2]
    assert third["ecart"] == {"valeur": 0, "unite": "euro_per_uc_year"}
    assert third["identique_a_la_reference"] is True
    assert _audit(body) == []


@pytest.mark.asyncio
async def test_communes_all_out_of_coverage_read_only_the_referential(
    migrated,  # noqa: F811
):
    conn, dsn, _ = migrated
    install_instance_scope(conn, COMMUNE_SOURCES, ("85",))
    statements: list[str] = []
    response = await _compare(conn, dsn, "44991,44992", statements)

    assert response.status_code == 200, response.text
    communes = response.json()["donnees"]["communes"]
    assert [c["hebergement"]["motif"] for c in communes] == [
        "territoire_hors_perimetre"
    ] * 2
    assert all(c["synthese"] is None for c in communes)
    assert any("geo_communes" in sql for sql in statements)
    read = ("v_commune_synthese", "etl_", "dataset_metadata", "revenus")
    assert not [sql for sql in statements if any(name in sql for name in read)]


@pytest.mark.asyncio
async def test_an_absent_value_under_the_threshold_is_donnee_absente(
    migrated,  # noqa: F811
):
    conn, dsn, _ = migrated
    conn.execute("TRUNCATE mutations_grain")
    _apartment_sales(conn, "44991", SEUIL_ND_COMMUNE)
    _apartment_sales(conn, "44992", SEUIL_ND_COMMUNE - 1)
    response = await _compare(conn, dsn, "44991,44992")

    assert response.status_code == 200, response.text
    reference, other = _critere(response.json(), "prix_m2_median_appartement")[
        "cellules"
    ]
    assert reference["etat"] == "value"
    assert (other["etat"], other["valeur"]) == ("insufficient_sample", None)
    assert other["ecart"] is None
    assert other["motif_non_calcul"] == "donnee_absente"


# C28 (Ianis, 2026-10-08, WINDOWS n° 89): a source without a vintage column is
# dated by its provenance; an empty label with a known import day falls back to
# that day (Paris), as C14 on the address page. A true label wins. Proved on
# nb_monuments: since C34 every flat dvf column is dated by its 12 months.
def _run(dataset: str) -> str:
    return (
        "INSERT INTO etl_runs (dataset_name, status, started_at, completed_at) "
        f"VALUES ('{dataset}', 'success', TIMESTAMPTZ '2026-09-25 21:30:00+00', "
        "TIMESTAMPTZ '2026-09-25 22:30:00+00')"
    )


def _metadata(conn, label: str | None, dataset: str = "dvf") -> None:
    conn.execute(
        "INSERT INTO dataset_metadata (dataset_name, status, source_period_label) "
        "VALUES (%s, 'disponible', %s) ON CONFLICT (dataset_name) "
        "DO UPDATE SET source_period_label = EXCLUDED.source_period_label",
        (dataset, label),
    )


def _monuments(conn, *codes: str) -> None:
    conn.execute("TRUNCATE monuments_historiques")
    for code in codes:
        conn.execute(
            "INSERT INTO monuments_historiques (reference, code_insee) VALUES (%s, %s)",
            (f"PA{code}00", code),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "attendue"),
    [(None, "2026-09-26"), ("2026-03-01", "2026-03-01")],
)
async def test_a_source_without_label_is_dated_by_its_import_day(
    migrated,  # noqa: F811
    label,
    attendue,
):
    conn, dsn, _ = migrated
    _monuments(conn, "44991", "44992")
    _metadata(conn, label, "monuments")
    conn.execute(_run("monuments"))
    try:
        response = await _compare(conn, dsn, "44991,44992")
    finally:
        conn.execute("DELETE FROM etl_runs WHERE dataset_name = 'monuments'")
        conn.execute("DELETE FROM dataset_metadata WHERE dataset_name = 'monuments'")

    assert response.status_code == 200, response.text
    cellules = _critere(response.json(), "nb_monuments")["cellules"]
    assert [(c["etat"], c["periode"]) for c in cellules] == [("value", attendue)] * 2


@pytest.mark.asyncio
async def test_a_source_without_label_nor_import_serves_no_period(
    migrated,  # noqa: F811
):
    """Nothing known, nothing invented (C14, C28)."""
    conn, dsn, _ = migrated
    _monuments(conn, "44991")
    _metadata(conn, None, "monuments")
    try:
        response = await _compare(conn, dsn, "44991,44992")
    finally:
        conn.execute("DELETE FROM dataset_metadata WHERE dataset_name = 'monuments'")

    assert response.status_code == 200, response.text
    reference = _critere(response.json(), "nb_monuments")["cellules"][0]
    assert (reference["etat"], reference["periode"]) == ("value", None)


# C32 (Ianis, 2026-10-10, WINDOWS n° 92) and C34 (WINDOWS n° 95): the six flat
# dvf columns of v_commune_dvf are computed on the same 12 months; they are
# dated by those months, with the same rule and the same query as landmark ④
# (C29), else by the period of the source (C14).
_COLONNES_12_MOIS = (
    "prix_m2_median_maison",
    "prix_m2_median_appartement",
    "prix_m2_median",
    "nb_mutations_12m",
    "nb_mutations_12m_maison",
    "nb_mutations_12m_appartement",
)
_LIBELLE_DVF = "2021-01-02 → 2025-12-31"
_FENETRE_DVF = "2024-06-01 → 2025-06-01"


def _row_with_dvf_counts(code: str, counts: tuple[int | None, ...]) -> dict:
    return {
        "code_insee": code,
        "nom_commune": next(
            nom for candidate, nom, _ in REFERENTIEL if candidate == code
        ),
        "code_departement": code[:2],
        "population": 100,
        "annee_demographie": 2025,
        "nb_mutations_12m": counts[0],
        "nb_mutations_12m_maison": counts[1],
        "nb_mutations_12m_appartement": counts[2],
    }


@pytest.mark.asyncio
async def test_dvf_window_is_read_once_for_a_mixed_batch_and_attached_per_commune(
    migrated,  # noqa: F811
    monkeypatch,
):
    conn, dsn, _ = migrated
    rows = [
        _row_with_dvf_counts("44991", (7, None, None)),
        _row_with_dvf_counts("44992", (0, 0, 0)),
        _row_with_dvf_counts("85991", (None, None, None)),
    ]
    read_window = AsyncMock(return_value=_FENETRE_DVF)
    monkeypatch.setattr(
        commune_router, "commune_synthese_par_codes", AsyncMock(return_value=rows)
    )
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", read_window)
    monkeypatch.setattr(
        commune_router, "get_provenance_facts", AsyncMock(return_value={})
    )

    response = await _compare(conn, dsn, "44991,44992,85991")

    assert response.status_code == 200, response.text
    body = response.json()
    syntheses = [commune["synthese"] for commune in body["donnees"]["communes"]]
    assert [synthese["fenetre_mediane_dvf"] for synthese in syntheses] == [
        _FENETRE_DVF,
        _FENETRE_DVF,
        None,
    ]
    assert [
        cell["periode"] for cell in _critere(body, "nb_mutations_12m")["cellules"][:2]
    ] == [_FENETRE_DVF, _FENETRE_DVF]
    read_window.assert_awaited_once()


@pytest.mark.asyncio
async def test_dvf_window_is_not_read_when_every_served_count_is_absent(
    migrated,  # noqa: F811
    monkeypatch,
):
    conn, dsn, _ = migrated
    rows = [
        _row_with_dvf_counts("44991", (None, None, None)),
        _row_with_dvf_counts("44992", (None, None, None)),
    ]
    read_window = AsyncMock(return_value=_FENETRE_DVF)
    monkeypatch.setattr(
        commune_router, "commune_synthese_par_codes", AsyncMock(return_value=rows)
    )
    monkeypatch.setattr(commune_router, "fenetre_mediane_commune", read_window)
    monkeypatch.setattr(
        commune_router, "get_provenance_facts", AsyncMock(return_value={})
    )

    response = await _compare(conn, dsn, "44991,44992")

    assert response.status_code == 200, response.text
    syntheses = [
        commune["synthese"] for commune in response.json()["donnees"]["communes"]
    ]
    assert [synthese["fenetre_mediane_dvf"] for synthese in syntheses] == [None, None]
    read_window.assert_not_awaited()


@pytest.mark.asyncio
async def test_flat_dvf_columns_are_dated_by_their_twelve_months(
    migrated,  # noqa: F811
):
    conn, dsn, _ = migrated
    conn.execute("TRUNCATE mutations_grain")
    _apartment_sales(conn, "44991", SEUIL_ND_COMMUNE)
    _apartment_sales(conn, "44992", SEUIL_ND_COMMUNE)
    _metadata(conn, _LIBELLE_DVF)
    try:
        response = await _compare(conn, dsn, "44991,44992")
    finally:
        conn.execute("DELETE FROM dataset_metadata WHERE dataset_name = 'dvf'")

    assert response.status_code == 200, response.text
    body = response.json()
    for cle in _COLONNES_12_MOIS:
        periodes = [c["periode"] for c in _critere(body, cle)["cellules"]]
        assert periodes == ["2024-06-01 → 2025-06-01"] * 2, cle


@pytest.mark.asyncio
async def test_flat_dvf_columns_without_any_sale_keep_the_period_of_the_source(
    migrated,  # noqa: F811
):
    conn, dsn, _ = migrated
    conn.execute("TRUNCATE mutations_grain")
    _metadata(conn, _LIBELLE_DVF)
    try:
        response = await _compare(conn, dsn, "44991,44992")
    finally:
        conn.execute("DELETE FROM dataset_metadata WHERE dataset_name = 'dvf'")

    assert response.status_code == 200, response.text
    body = response.json()
    for cle in _COLONNES_12_MOIS:
        periodes = [c["periode"] for c in _critere(body, cle)["cellules"]]
        assert periodes == [_LIBELLE_DVF] * 2, cle


def _dvf_block(shift_years: int) -> dict:
    block = indicator_fixtures.dvf()

    def moved(day: str) -> str:
        return f"{int(day[:4]) + shift_years}{day[4:]}"

    for kind in ("residentiel_maison", "residentiel_appartement"):
        for window in ("precedente", "courante"):
            fenetre = block[kind][window]["fenetre"]
            for bound in ("debut_inclus", "fin_exclue"):
                fenetre[bound] = moved(fenetre[bound])
    anchor = block["ancrage_temporel"]
    for bound in ("periode_debut_inclus", "periode_fin_exclue"):
        anchor[bound] = moved(anchor[bound])
    return block


def _indicators(monkeypatch, blocks: dict) -> None:
    """Serve *blocks* through the indicator reader seam (the view stays real)."""

    class Policy:
        @staticmethod
        def from_json(_raw):
            return None

    async def reader(_session, *, policy, environment, codes):
        return ActiveIndicators(blocks, frozenset())

    monkeypatch.setattr(settings, "commune_indicators_enabled", True)
    monkeypatch.setattr(queries, "RuntimeIndicatorPolicy", Policy)
    monkeypatch.setattr(queries, "read_active_preproduction_build", reader)


@pytest.mark.asyncio
async def test_dvf_medians_of_different_windows_are_not_compared(
    migrated,  # noqa: F811
    monkeypatch,
):
    """Blocks come from the indicator reader seam; the view is the real one."""
    conn, dsn, _ = migrated
    _indicators(
        monkeypatch,
        {
            "44991": {"indicateurs_dvf": _dvf_block(0), "indicateurs_dpe": None},
            "44992": {"indicateurs_dvf": _dvf_block(-1), "indicateurs_dpe": None},
        },
    )
    response = await _compare(conn, dsn, "44991,44992")

    assert response.status_code == 200, response.text
    body = response.json()
    reference, other = _critere(
        body, "indicateurs_dvf.residentiel_maison.courante.mediane"
    )["cellules"]
    assert reference["periode"] == "[2024-01-01, 2025-01-01["
    assert other["periode"] == "[2023-01-01, 2024-01-01["
    assert reference["valeur"] == other["valeur"] == 2200
    assert other["ecart"] is None
    assert other["motif_non_calcul"] == "periodes_differentes"
    assert _audit(body) == []


@pytest.mark.asyncio
async def test_four_communes_are_served(migrated):  # noqa: F811
    conn, dsn, _ = migrated
    response = await _compare(conn, dsn, "44991,44992,85991,44993")

    assert response.status_code == 200, response.text
    communes = response.json()["donnees"]["communes"]
    assert [c["code_insee"] for c in communes] == ["44991", "44992", "85991", "44993"]
    # Hosted, but no synthesis row: no column is invented, nothing reads 0.
    assert communes[3]["hebergement"]["etat"] == "heberge"
    assert communes[3]["synthese"] is None
    cellule = _critere(response.json(), "population")["cellules"][3]
    assert (cellule["etat"], cellule["valeur"]) == ("not_reported", None)
    assert cellule["motif_non_calcul"] == "donnee_absente"


@pytest.mark.parametrize(
    "codes",
    [
        "44991",
        "44991,44992,85991,44993,44001",
        "44991,44991",
        "44991,4499",
        "44991,44992;DROP",
        "44991," + "4" * 64,
        None,
    ],
)
@pytest.mark.asyncio
async def test_bad_codes_are_refused_before_any_read(
    migrated,  # noqa: F811
    codes,
):
    conn, dsn, _ = migrated
    statements: list[str] = []
    response = await _compare(conn, dsn, codes, statements)

    assert response.status_code == 422, response.text
    assert statements == []


@pytest.mark.asyncio
async def test_an_unavailable_synthesis_fails_the_whole_response(
    migrated,  # noqa: F811
):
    conn, dsn, _ = migrated
    _referentiel(conn)
    documents(conn, "migration")
    conn.execute(
        "INSERT INTO matview_metadata (matview_name, last_refresh_status) "
        "VALUES ('v_commune_synthese', 'failed') ON CONFLICT (matview_name) "
        "DO UPDATE SET last_refresh_status = 'failed'"
    )
    try:
        response = await _compare(conn, dsn, "44991,44992")
    finally:
        conn.execute(
            "UPDATE matview_metadata SET last_refresh_status = 'success' "
            "WHERE matview_name = 'v_commune_synthese'"
        )
    assert response.status_code == 503, response.text


@pytest.mark.asyncio
async def test_frozen_responses_for_the_frontend(migrated, monkeypatch):  # noqa: F811
    """Harness responses the frontend and the 03-10 specs replay as served.

    Written to ``COMPARAISON_API_FIXTURE_OUTPUT`` when set. Two responses of
    one database: three communes with one out of coverage and an unknown code
    (no criterion can be ``identiques`` there), and the two hosted communes.
    """
    conn, dsn, _ = migrated
    install_instance_scope(conn, COMMUNE_SOURCES, ("44",))
    conn.execute("TRUNCATE revenus, mutations_grain")
    conn.execute(
        "INSERT INTO revenus (code_insee, revenu_median, taux_pauvrete, annee) "
        "VALUES ('44991', 21000, 15.2, 2021), ('44992', 22480, 15.2, 2021)"
    )
    _apartment_sales(conn, "44991", SEUIL_ND_COMMUNE)
    _apartment_sales(conn, "44992", SEUIL_ND_COMMUNE - 1)
    dpe = indicator_fixtures.dpe()
    maison = _dvf_block(0)
    maison["residentiel_maison"]["courante"]["mediane"]["valeur"] = 2300.0
    _indicators(
        monkeypatch,
        {
            "44991": {"indicateurs_dvf": _dvf_block(0), "indicateurs_dpe": dpe},
            "44992": {"indicateurs_dvf": maison, "indicateurs_dpe": dpe},
        },
    )
    bodies = {}
    for name, codes in (
        ("trois_communes_dont_hors_couverture", f"44991,44992,85991,{UNKNOWN}"),
        ("deux_communes_hebergees", "44991,44992"),
    ):
        response = await _compare(conn, dsn, codes)
        assert response.status_code == 200, response.text
        bodies[name] = response.json()
        assert _audit(bodies[name]) == []

    three = bodies["trois_communes_dont_hors_couverture"]
    assert three["donnees"]["codes_inconnus"] == [UNKNOWN]
    assert three["donnees"]["communes"][2]["synthese"] is None
    motifs = {
        cellule["motif_non_calcul"]
        for critere in three["donnees"]["criteres"]
        for cellule in critere["cellules"][1:2]
    }
    assert {"periodes_differentes", "donnee_absente", "non_numerique"} <= motifs
    two = bodies["deux_communes_hebergees"]
    assert _critere(two, "revenu_median")["cellules"][1]["ecart"] == {
        "valeur": 1480,
        "unite": "euro_per_uc_year",
    }
    assert _critere(two, "taux_pauvrete")["identiques"] is True
    assert _critere(two, "population")["cellules"][1]["motif_non_calcul"] == (
        "periodes_differentes"
    )
    assert _critere(two, "prix_m2_median_appartement")["cellules"][1][
        "motif_non_calcul"
    ] == ("donnee_absente")

    if target := os.environ.get("COMPARAISON_API_FIXTURE_OUTPUT"):
        Path(target).write_text(
            json.dumps(bodies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

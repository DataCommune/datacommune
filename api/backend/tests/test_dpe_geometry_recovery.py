"""Measured no-recovery branch and separation of spatial/documentary readers."""

import csv
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.dpe.queries import dpe_par_identifiant_ban, dpe_par_point
from app.etl_dpe.pipeline import DpePipeline
from app.logements.queries import dpe_appartements_par_parcelle
from tests.test_etl_dpe_sources import install_source_api

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    bind_departements(("44", "85"), DpePipeline)


@pytest.mark.parametrize("value", [None, "", "bad", "NaN", "inf", "-inf", 0])
@pytest.mark.parametrize("identity", [None, "44999", "44999_0001", "44999_0001_00001"])
def test_unproven_recovery_never_invents_position(value, identity):
    # Even a plausible key is not the measured exact-address recovery proof.
    row = {
        "coordonnee_cartographique_x_ban": value,
        "coordonnee_cartographique_y_ban": value,
        "identifiant_ban": identity,
    }
    assert DpePipeline._invalid_geometry(row)


@pytest.mark.parametrize("value", [None, 0])
@pytest.mark.parametrize("identity", [None, "44999_0001_00001"])
def test_row_without_geometry_is_kept_without_position(
    monkeypatch, tmp_path, value, identity
):
    # Plan 02-16 (DATA-02): kept with empty coordinates (null position at
    # swap), never skipped and never given the position of its BAN key.
    install_source_api(
        monkeypatch,
        tmp_path,
        row_override={
            "coordonnee_cartographique_x_ban": value,
            "coordonnee_cartographique_y_ban": value,
            "identifiant_ban": identity,
        },
    )
    pipeline = DpePipeline()
    try:
        path = pipeline.transform(pipeline.download()[0])
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    finally:
        pipeline.close()
    assert len(rows) == 1
    assert rows[0]["longitude"] == rows[0]["latitude"] == ""
    assert rows[0]["identifiant_ban"] == (identity or "")
    assert rows[0]["source_state"] == "present"


async def test_spatial_reader_excludes_null_and_removed_documents():
    session = AsyncMock()
    session.execute.return_value = MagicMock(__iter__=lambda _: iter(()))
    assert await dpe_par_point(session, lat=47.2, lon=-1.5) == []
    sql = str(session.execute.call_args.args[0])
    assert "position IS NOT NULL" in sql
    assert "NOT is_tombstone" in sql
    assert "'absent_from_complete_scope', 'explicitly_deactivated'" in sql


@pytest.mark.parametrize("seulement_sans_cle", [False, True])
async def test_parcel_fallback_excludes_documents_without_position(
    seulement_sans_cle,
):
    # Plan 02-16: a document kept without source geometry never enters a
    # spatial search, explicitly rather than through a NULL comparison.
    session = AsyncMock()
    session.execute.return_value = MagicMock(__iter__=lambda _: iter(()))
    await dpe_appartements_par_parcelle(
        session, "44109000AB0001", seulement_sans_cle=seulement_sans_cle
    )
    sql = str(session.execute.call_args.args[0])
    assert "d.position IS NOT NULL" in sql


async def test_exact_identity_reader_does_not_require_geometry_or_neighbour():
    session = AsyncMock()
    session.execute.return_value = MagicMock(__iter__=lambda _: iter(()))
    await dpe_par_identifiant_ban(session, "44999_0001_00001")
    sql = str(session.execute.call_args.args[0])
    assert "d.identifiant_ban = :ban_id" in sql
    assert "position" not in sql
    assert "ST_DWithin" not in sql


async def test_gps_or_cadastre_without_ban_identity_cannot_fetch_identity():
    session = AsyncMock()
    assert await dpe_par_identifiant_ban(session, "") == ([], False)
    session.execute.assert_not_called()

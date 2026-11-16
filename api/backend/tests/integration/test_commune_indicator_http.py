"""Async commune indicator reader contracts on disposable PostgreSQL 17."""

from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.commune import indicator_activation, indicator_binding
from app.commune.indicator_policy import RuntimeIndicatorPolicy
from app.commune.indicator_projector import ProjectionRequest
from app.commune.indicator_reader import read_active_preproduction_build
from app.commune.indicator_results import ResultStoreRefused
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.test_commune_indicator_projector import NOW, prepare

pytest_plugins = ("tests.integration.test_commune_indicator_results",)


def build_preproduction(conn, tmp_path, *, trend=False):
    inputs, _ = prepare(conn, tmp_path, scope="preproduction", trend=trend)
    receipt = indicator_binding.rebuild_and_activate_indicator_results(
        conn,
        trust=inputs,
        request=ProjectionRequest(NOW + timedelta(minutes=1), "preproduction"),
    )
    conn.commit()
    policy = RuntimeIndicatorPolicy(
        scope="preproduction",
        cog=inputs.cog,
        evidence_root=inputs.evidence_root,
        checked_at=inputs.checked_at,
        dpe=inputs.dpe,
        dvf=inputs.dvf,
    )
    return receipt, policy


@pytest.mark.asyncio
async def test_async_reader_returns_exact_blocks_without_hidden_connection(
    store, tmp_path
):
    conn, dsn = store
    _, policy = build_preproduction(conn, tmp_path)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with AsyncSession(engine) as session:
            loaded = await read_active_preproduction_build(
                session,
                policy=policy,
                environment="preproduction",
                codes=["44001", "85084"],
            )
            assert set(loaded.blocks) == {"44001", "85084"}
            assert not loaded.hors_build
            assert set(loaded.blocks["44001"]) == {
                "code_insee",
                "indicateurs_dvf",
                "indicateurs_dpe",
            }
            dvf = loaded.blocks["85084"]["indicateurs_dvf"]
            assert dvf["etat"] == "not_qualified"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_async_store_preserves_closed_trend_version(store, tmp_path):
    conn, dsn = store
    _, policy = build_preproduction(conn, tmp_path, trend=True)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with AsyncSession(engine) as session:
            loaded = await read_active_preproduction_build(
                session, policy=policy, environment="preproduction", codes=["44001"]
            )
        group = loaded.blocks["44001"]["indicateurs_dvf"]["residentiel_maison"]
        assert group["variation_pct"]["etat"] == "not_reported"
        assert group["qualification_tendance"] is None
        assert group["courante"]["quantiles"]["etat"] == "not_qualified"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_commune_http_reads_same_postgres_session_and_maps_revoke_503(
    store, tmp_path, client, monkeypatch
):
    from app.common.publication_readiness import require_publication
    from app.commune import queries
    from app.database import get_db
    from app.main import app

    conn, dsn = store
    _, policy = build_preproduction(conn, tmp_path, trend=True)
    # Projector qualification deliberately holds the publication session lock.
    # The HTTP reader must run after that maintenance owner has released it.
    conn.execute("SELECT pg_advisory_unlock_all()")
    conn.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    conn.execute(
        """CREATE MATERIALIZED VIEW v_commune_synthese AS
        SELECT c.code_insee, c.nom_commune
        FROM (VALUES ('44001', 'Commune 44001'), ('75056', 'Paris'))
            AS c(code_insee, nom_commune)
        CROSS JOIN (SELECT 1 FROM diagnostics_dpe LIMIT 1) d
        CROSS JOIN (SELECT 1 FROM mutations_grain LIMIT 1) m"""
    )
    conn.execute(
        "CREATE TABLE matview_metadata("
        "matview_name text PRIMARY KEY,last_refresh_status text)"
    )
    conn.execute("INSERT INTO matview_metadata VALUES ('v_commune_synthese','success')")
    conn.commit()
    # /v1/commune reads the scope first (DATA-03): host its thirteen sources,
    # in 75 too: a hosted commune outside the qualified 44/85 build.
    install_instance_scope(conn, COMMUNE_SOURCES, ("44", "85", "75"))
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    # The generic publication guard has its own COR-20 coverage. Keep this route
    # test focused on the real commune query and indicator reader transaction.
    app.dependency_overrides[require_publication] = lambda: None
    monkeypatch.setattr(
        queries, "COMMUNE_SOURCES", {"dpe": "diagnostics_dpe", "dvf": "mutations_grain"}
    )
    monkeypatch.setattr(
        queries,
        "COMMUNE_EDGES",
        (
            ("v_commune_synthese", "diagnostics_dpe"),
            ("v_commune_synthese", "mutations_grain"),
        ),
    )
    monkeypatch.setattr(queries.settings, "app_environment", "preproduction")
    monkeypatch.setattr(
        queries.settings, "commune_indicator_policy", policy.model_dump_json()
    )
    try:
        # Gate closed (todo 2026-11-01): both blocks carry an explicit absence,
        # never a bare null without a state, even with a valid policy.
        monkeypatch.setattr(queries.settings, "commune_indicators_enabled", False)
        closed = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert closed.status_code == 200, closed.text
        (gated,) = closed.json()["donnees"]
        for block in ("indicateurs_dvf", "indicateurs_dpe"):
            assert gated[block] == {
                "etat": "not_qualified",
                "motif": "indicateurs_communaux_non_publies",
            }
        monkeypatch.setattr(queries.settings, "commune_indicators_enabled", True)
        response = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert response.status_code == 200, response.text
        assert response.json()["donnees"][0]["indicateurs_dvf"]
        unknown = await client.get("/v1/commune", params={"code_insee": "44999"})
        assert unknown.status_code == 200 and unknown.json()["donnees"] == []
        # Hosted, outside the qualified build: served with its own information
        # and an explicit absence of indicators, never a 503 (T-04.1-29).
        outside = await client.get("/v1/commune", params={"code_insee": "75056"})
        assert outside.status_code == 200, outside.text
        (paris,) = outside.json()["donnees"]
        assert paris["nom_commune"] == "Paris"
        for block in ("indicateurs_dvf", "indicateurs_dpe"):
            assert paris[block] == {
                "etat": "not_qualified",
                "motif": "commune_hors_build_qualifie",
            }

        monkeypatch.setattr(queries.settings, "commune_indicator_policy", "")
        invalid_policy = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert invalid_policy.status_code == 503
        monkeypatch.setattr(
            queries.settings, "commune_indicator_policy", policy.model_dump_json()
        )

        saved_cell = conn.execute(
            "SELECT dataset_name,code_insee,public_payload,private_binding "
            "FROM commune_indicator_result_cells "
            "WHERE build_id=(SELECT build_id FROM commune_indicator_build_activations "
            "ORDER BY id DESC LIMIT 1) "
            "AND dataset_name='dpe' AND code_insee='44001'"
        ).fetchone()
        from tests.integration.test_commune_indicator_results import allow_corruption

        allow_corruption(conn)
        conn.execute(
            "DELETE FROM commune_indicator_result_cells "
            "WHERE build_id=(SELECT build_id FROM commune_indicator_build_activations "
            "ORDER BY id DESC LIMIT 1) "
            "AND dataset_name='dpe' AND code_insee='44001'"
        )
        conn.commit()
        missing_cell = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert missing_cell.status_code == 503
        # A qualified commune without its cell stays an outage; the commune
        # outside the build is not served from a build now known corrupt.
        outside_corrupt = await client.get(
            "/v1/commune", params={"code_insee": "75056"}
        )
        assert outside_corrupt.status_code == 503
        conn.execute(
            "INSERT INTO commune_indicator_result_cells "
            "(build_id,dataset_name,code_insee,public_payload,private_binding) "
            "SELECT build_id,%s,%s,%s,%s FROM commune_indicator_build_activations "
            "ORDER BY id DESC LIMIT 1",
            (*saved_cell[:2], Jsonb(saved_cell[2]), Jsonb(saved_cell[3])),
        )
        conn.commit()
        restored = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert restored.status_code == 200, restored.text
        from tests.integration import test_commune_indicator_storage as registry

        registry.lock(conn)
        indicator_activation.revoke_active_build(conn)
        conn.commit()
        refused = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert refused.status_code == 503
        refused_outside = await client.get(
            "/v1/commune", params={"code_insee": "75056"}
        )
        assert refused_outside.status_code == 503
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_publication, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_latest_revoke_and_synthetic_build_never_fallback(store, tmp_path):
    conn, dsn = store
    _, policy = build_preproduction(conn, tmp_path)
    from tests.integration import test_commune_indicator_storage as registry

    registry.lock(conn)
    indicator_activation.revoke_active_build(conn)
    conn.commit()
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with AsyncSession(engine) as session:
            with pytest.raises(ResultStoreRefused, match="active_build_revoked"):
                await read_active_preproduction_build(
                    session,
                    policy=policy,
                    environment="preproduction",
                    codes=["44001"],
                )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "fault", ["header", "cell", "missing_cell", "manifest", "evidence"]
)
@pytest.mark.asyncio
async def test_async_reader_fails_closed_on_corruption(store, tmp_path, fault):
    conn, dsn = store
    receipt, policy = build_preproduction(conn, tmp_path)
    from tests.integration.test_commune_indicator_results import allow_corruption

    allow_corruption(conn)
    if fault == "header":
        conn.execute(
            "UPDATE commune_indicator_result_builds SET header='{}' WHERE id=%s",
            (receipt.build_id,),
        )
    elif fault == "cell":
        conn.execute(
            "UPDATE commune_indicator_result_cells SET public_payload='{}' "
            "WHERE build_id=%s AND dataset_name='dpe' AND code_insee='44001'",
            (receipt.build_id,),
        )
    elif fault == "missing_cell":
        conn.execute(
            "DELETE FROM commune_indicator_result_cells WHERE build_id=%s "
            "AND dataset_name='dpe' AND code_insee='44001'",
            (receipt.build_id,),
        )
    elif fault == "manifest":
        conn.execute(
            "ALTER TABLE commune_indicator_manifests DISABLE TRIGGER "
            "immutable_indicator_registry"
        )
        conn.execute(
            "UPDATE commune_indicator_manifests "
            "SET manifest=manifest || '{\"unexpected\":true}'::jsonb "
            "WHERE dataset_name='dpe' AND source_generation=%s",
            (policy.dpe.source_generation,),
        )
    else:
        (tmp_path / "dpe" / "review.json").unlink()
    conn.commit()
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with AsyncSession(engine) as session:
            with pytest.raises(ResultStoreRefused):
                await read_active_preproduction_build(
                    session,
                    policy=policy,
                    environment="preproduction",
                    codes=["44001"],
                )
    finally:
        await engine.dispose()

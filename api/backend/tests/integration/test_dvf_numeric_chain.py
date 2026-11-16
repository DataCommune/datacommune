"""Seven raw DVF lines through native reconstruction, SQL and commune HTTP.

Qualification receipts and publication identities are synthetic test inputs.
They do not attest an actual supplier delivery or production qualification.
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.commune import indicator_binding, queries
from app.commune.indicator_policy import RuntimeIndicatorPolicy
from app.commune.indicator_projector import ProjectionRequest
from app.commune.indicator_sql import query_dvf_indicators
from app.commune.matview_ddl import recreate_commune_synthese
from app.config import settings
from app.database import get_db
from app.etl_dvf.reconstruction import reconstruit_mutations_grain
from tests.integration import test_commune_indicator_storage as registry
from tests.integration.instance_scope_fixture import (
    COMMUNE_SOURCES,
    install_instance_scope,
)
from tests.integration.test_commune_indicator_context import trust
from tests.integration.test_commune_indicator_projector import NOW, register

pytestmark = pytest.mark.destructive
BACKEND = Path(__file__).resolve().parents[2]
FIXTURE = (
    BACKEND.parents[1] / "frontend/src/lib/data/__fixtures__/dvf-numeric-chain.json"
)


def assert_oracle(count, median):
    """Independent hand-calculated sorted prices: 2000,3000,4000,5000,6000."""
    assert (count, median) == (5, Fraction(4000))


def current_house(conn):
    cells = query_dvf_indicators(
        conn,
        communes=("44001",),
        departments=("44", "85"),
        end_exclusive=date(2026, 1, 1),
        observed_at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    return next(
        c for c in cells if c.kind == "residentiel_maison" and c.period == "current"
    )


@pytest.mark.asyncio
async def test_raw_reconstruction_sql_and_http_share_independent_oracle(
    isolated_pg_dsn, monkeypatch, tmp_path, client
):
    fixture = json.loads(FIXTURE.read_bytes())
    assert fixture["synthetic"] is True
    assert fixture["expected"]["eligible_mutations"] == 5
    assert fixture["expected"]["median_eur_m2"] == 4000
    monkeypatch.setenv("DATABASE_URL", isolated_pg_dsn)
    monkeypatch.setattr(settings, "database_url", isolated_pg_dsn)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    command.upgrade(cfg, "head")

    with psycopg.connect(isolated_pg_dsn) as conn:
        install_instance_scope(conn, COMMUNE_SOURCES, ("44", "85"))
        for row in fixture["raw_rows"]:
            conn.execute(
                sql.SQL("INSERT INTO mutations ({}) VALUES ({})").format(
                    sql.SQL(",").join(map(sql.Identifier, row)),
                    sql.SQL(",").join(sql.Placeholder() for _ in row),
                ),
                list(row.values()),
            )
        conn.execute(
            "INSERT INTO demographie(code_insee,nom_commune,code_departement,"
            "population,annee) VALUES('44001','Commune synthetique DVF','44',100,2025)"
        )
        assert conn.execute("SELECT count(*) FROM mutations").fetchone() == (7,)

    reconstruit_mutations_grain(isolated_pg_dsn, ("44", "85"))
    with psycopg.connect(isolated_pg_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM mutations_grain").fetchone() == (6,)
        assert conn.execute(
            "SELECT classe_mutation,nb_lignes,nb_locaux_maison,nb_locaux_commercial,"
            "surface_bati_totale,valeur_fonciere FROM mutations_grain "
            "WHERE id_mutation='SYN-MIXED'"
        ).fetchone() == ("mixte", 2, 1, 1, 150, 600000)
        good = current_house(conn)
        assert_oracle(good.count, good.median_eur_m2)
        recreate_commune_synthese(conn)
        assert conn.execute(
            "SELECT nb_mutations_12m,prix_m2_median FROM v_commune_dvf"
        ).fetchone() == (5, 4000)
        # Only these durable receipt inputs are synthetic; all numeric source
        # rows above are reconstructed from raw detail by the application.
        registry.publish(conn, "dpe", registry.G1, row_count=0)
        registry.publish(conn, "dvf", registry.G2, row_count=7)
        registry.lock(conn)
        inputs = trust(tmp_path, scope="preproduction")
        dpe_pins = register(conn, tmp_path, inputs, "dpe")
        dvf_pins = register(conn, tmp_path, inputs, "dvf")
        inputs = inputs.model_copy(update={"dpe": dpe_pins, "dvf": dvf_pins})
        indicator_binding.rebuild_and_activate_indicator_results(
            conn,
            trust=inputs,
            request=ProjectionRequest(NOW + timedelta(minutes=1), "preproduction"),
        )
        conn.commit()
        conn.execute("SELECT pg_advisory_unlock_all()")
        conn.commit()

    engine = create_async_engine(
        isolated_pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    # The middleware imports this engine separately from the route dependency.
    # Bind both to the migrated child instead of the harness parent database.
    monkeypatch.setattr("app.database.engine", engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    from app.main import app

    previous_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = db
    policy = RuntimeIndicatorPolicy(
        scope="preproduction",
        cog=inputs.cog,
        evidence_root=inputs.evidence_root,
        checked_at=inputs.checked_at,
        dpe=inputs.dpe,
        dvf=inputs.dvf,
    )
    try:
        monkeypatch.setattr(queries.settings, "commune_indicators_enabled", False)
        legacy = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert legacy.status_code == 200, legacy.text
        document = legacy.json()["donnees"][0]
        expected_legacy = fixture["http_projection"]["legacy"]
        assert {key: document[key] for key in expected_legacy} == expected_legacy
        monkeypatch.setattr(queries.settings, "commune_indicators_enabled", True)
        monkeypatch.setattr(queries.settings, "app_environment", "preproduction")
        monkeypatch.setattr(
            queries.settings, "commune_indicator_policy", policy.model_dump_json()
        )
        response = await client.get("/v1/commune", params={"code_insee": "44001"})
        assert response.status_code == 200, response.text
        document = response.json()["donnees"][0]
        block = document["indicateurs_dvf"]
        projection = {"code_insee": document["code_insee"], "indicateurs_dvf": block}
        output = os.environ.get("DC_DVF_PROOF_DIR")
        if output:
            root = Path(output)
            root.mkdir(parents=True, exist_ok=True)
            (root / "http-legacy.json").write_bytes(legacy.content)
            (root / "http-indicators.json").write_bytes(response.content)
            (root / "stable-indicator-projection.json").write_text(
                json.dumps(projection, indent=2), encoding="utf-8"
            )
        assert projection == fixture["http_projection"]["indicators"]
        assert block["generation"] == str(registry.G2)
        assert block["ancrage_temporel"]["date_observation"] == "2026-09-09T00:00:00Z"
        window = block["residentiel_maison"]["courante"]["fenetre"]
        assert window == {
            "debut_inclus": fixture["period"]["start_inclusive"],
            "fin_exclue": fixture["period"]["end_exclusive"],
        }
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_db
        await engine.dispose()

    # Mutate the raw input, not the grain or its SQL: reproduce the historical
    # premature residential filter and prove the independent oracle rejects it.
    with psycopg.connect(isolated_pg_dsn) as conn:
        conn.execute("DELETE FROM mutations WHERE code_type_local='4'")
    reconstruit_mutations_grain(isolated_pg_dsn, ("44", "85"))
    with psycopg.connect(isolated_pg_dsn) as conn:
        wrong = current_house(conn)
        assert (wrong.count, wrong.median_eur_m2) == (6, Fraction(4500))
        with pytest.raises(AssertionError):
            assert_oracle(wrong.count, wrong.median_eur_m2)
        recreate_commune_synthese(conn)
        assert conn.execute(
            "SELECT nb_mutations_12m,prix_m2_median FROM v_commune_dvf"
        ).fetchone() == (6, 4500)
    if output:
        (Path(output) / "numeric-oracle.json").write_text(
            json.dumps(
                {
                    "synthetic": True,
                    "raw_rows": 7,
                    "reconstructed_mutations": 6,
                    "eligible_mutations": good.count,
                    "median_eur_m2": int(good.median_eur_m2),
                    "premature_filter": {
                        "eligible_mutations": wrong.count,
                        "median_eur_m2": int(wrong.median_eur_m2),
                        "independent_oracle_rejected": True,
                    },
                    "qualification_and_publication_receipts": "synthetic",
                    "legacy_period": "12 months anchored to max raw mutation date",
                    "indicator_period": fixture["period"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

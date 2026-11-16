"""Actual ASGI routes, documentary services, and disposable publication SQL."""

import importlib.util
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.common.publication_readiness import require_publication
from app.database import get_db
from app.dpe.inventory_service import load_dpe_inventory
from app.dpe.schemas import DpeConsultedDocument, DpeInventoryPage
from app.logements import router as logements
from app.main import app
from app.middleware import RequestLoggingMiddleware
from tests.integration.instance_scope_fixture import install_instance_scope
from tests.integration.publication_fixture import install_publication_journal
from tests.integration.test_dpe_inventory_generation import (
    _CREATE_RELATIONS,
    _DROP_RELATIONS,
)

# The documentary routes read the scope first (DATA-03): DPE, and the DVF and
# copropriete sources of /v1/bien/logements.
# The documentary routes also read the BAN address of their inventory.
_HOSTED = ("dpe", "dvf", "copropriete", "ban")
JOURNEY_BAN_ID = "44999_5803_00001"
FOREIGN_BAN_ID = "44999_5803_00002"
PRIVATE_NUMBER_CANARY = "B-PRIVATE-NUMBER-58-3-06"
PRIVATE_COMPLEMENT_CANARY = "PRIVATE-COMPLEMENT-58-3-06"


@dataclass(frozen=True, slots=True)
class RealDocumentaryApp:
    """Real service bindings over one disposable PostGIS database."""

    dsn: str
    client: object
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    request_log: AsyncMock


def _insert_real_journey_rows(dsn: str) -> None:
    """Insert a synthetic oracle whose private identifiers never reach HTTP."""
    rows = [
        (
            580301,
            "A-COMPATIBLE",
            JOURNEY_BAN_ID,
            "maison",
            82.5,
            "C",
            "individuel",
            "2024-02-03",
            None,
        ),
        (
            580302,
            PRIVATE_NUMBER_CANARY,
            JOURNEY_BAN_ID,
            None,
            None,
            None,
            None,
            "2023-09-14",
            PRIVATE_COMPLEMENT_CANARY,
        ),
        (
            580303,
            "C-APPARTEMENT",
            JOURNEY_BAN_ID,
            "appartement",
            40,
            "E",
            "collectif",
            "2022-06-01",
            None,
        ),
        (
            580304,
            "D-AUTRE-BAN",
            FOREIGN_BAN_ID,
            "maison",
            82.5,
            "A",
            "individuel",
            "2024-02-03",
            "porte-privee-etrangere",
        ),
    ]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(_DROP_RELATIONS)
        conn.execute(_CREATE_RELATIONS)
        install_publication_journal(conn)
        install_instance_scope(conn, _HOSTED, ("44", "85"))
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO public.adresses (id, code_departement, ban_id)
                VALUES (%s, '44', %s)
                """,
                [(58031, JOURNEY_BAN_ID), (58032, FOREIGN_BAN_ID)],
            )
            cursor.executemany(
                """
            INSERT INTO public.diagnostics_dpe (
                id, numero_dpe, identifiant_ban, code_departement,
                type_batiment, surface_habitable, etiquette_dpe, etiquette_ges,
                date_etablissement_dpe, date_fin_validite_dpe,
                type_installation_chauffage, type_energie_principale_chauffage,
                regime_dpe, source_state, source_snapshot_at, lifecycle_facts,
                source_observations, complement_adresse_logement
            ) VALUES (
                %s, %s, %s, '44', %s, %s, %s, 'D', %s::date,
                DATE '2034-02-02', %s, 'electricite', 'existant', 'present',
                %s, jsonb_build_object(
                    'replacement_state', 'no_successor_known',
                    'graph_issues', '[]'::jsonb,
                    'date_issues', '[]'::jsonb
                ),
                '[]', %s
            )
            """,
                [
                    (
                        row_id,
                        number,
                        ban_id,
                        building_type,
                        surface,
                        energy,
                        established,
                        heating,
                        datetime(2026, 9, 5, 22, 30, tzinfo=UTC),
                        complement,
                    )
                    for (
                        row_id,
                        number,
                        ban_id,
                        building_type,
                        surface,
                        energy,
                        heating,
                        established,
                        complement,
                    ) in rows
                ],
            )


def _publish(dsn: str, status: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO etl_publications
                (generation, dataset_name, table_name, status, relation_oids)
            VALUES (gen_random_uuid(), 'dpe', 'diagnostics_dpe', %s,
                jsonb_build_object('diagnostics_dpe', 'diagnostics_dpe'::regclass::oid))
            """,
            (status,),
        )


def _swap_diagnostics_generation(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE diagnostics_dpe_new (LIKE diagnostics_dpe INCLUDING ALL)"
        )
        conn.execute("INSERT INTO diagnostics_dpe_new SELECT * FROM diagnostics_dpe")
        conn.execute("ALTER TABLE diagnostics_dpe RENAME TO diagnostics_dpe_retired")
        conn.execute("ALTER TABLE diagnostics_dpe_new RENAME TO diagnostics_dpe")
        conn.execute("DROP TABLE diagnostics_dpe_retired")
    _publish(dsn, "published")


@pytest.fixture
async def real_documentary_app(isolated_pg_dsn, client, monkeypatch):
    """Bind both FastAPI dependencies and imported service factory to PostGIS."""
    _insert_real_journey_rows(isolated_pg_dsn)
    _publish(isolated_pg_dsn, "published")
    engine = create_async_engine(
        isolated_pg_dsn.replace("postgresql://", "postgresql+asyncpg://"),
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    request_log = AsyncMock()
    app.dependency_overrides.pop(require_publication, None)
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(logements, "async_session", sessions)
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", request_log)
    try:
        yield RealDocumentaryApp(isolated_pg_dsn, client, engine, sessions, request_log)
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
        with psycopg.connect(isolated_pg_dsn, autocommit=True) as conn:
            conn.execute(_DROP_RELATIONS)
            conn.execute("DROP TABLE IF EXISTS etl_publications")


def _assert_private_response(response) -> None:
    """Every documentary result is non-cacheable and contains no private canary."""
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE_NUMBER_CANARY not in response.text
    assert PRIVATE_COMPLEMENT_CANARY not in response.text


async def test_real_sql_journey_preserves_candidates_without_attribution(
    real_documentary_app,
    caplog,
):
    lab = real_documentary_app
    seeded = await load_dpe_inventory(lab.factory, ban_id=JOURNEY_BAN_ID, limit=2)

    first_response = await lab.client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": seeded.context_ref, "limite": 2},
    )
    assert first_response.status_code == 200
    _assert_private_response(first_response)
    first = first_response.json()
    assert first["total"] == 3
    assert first["distribution"] == {
        "A": 0,
        "B": 0,
        "C": 1,
        "D": 0,
        "E": 1,
        "F": 0,
        "G": 0,
        "unknown": 1,
    }
    assert first["has_more"] is True
    assert len(first["items"]) == 2
    assert first["items"][0]["type_batiment"] == "maison"
    assert first["items"][0]["surface_habitable"] == 82.5
    assert first["items"][0]["etiquette_dpe"] == "C"
    assert first["items"][1]["type_batiment"] is None
    assert first["items"][1]["surface_habitable"] is None
    assert first["items"][1]["etiquette_dpe"] is None

    target = {
        "type_bien": "maison",
        "surface": {"value": 82.5, "nature": "habitable_dpe"},
    }
    comparison_response = await lab.client.post(
        "/v1/bien/dpe/comparaison",
        json={"contexte": seeded.context_ref, "limite": 2, "cible": target},
    )
    assert comparison_response.status_code == 200
    _assert_private_response(comparison_response)
    comparison = comparison_response.json()
    assert comparison["attribution"] == {
        "version": 2,
        "status": "unassigned",
        "reason": "dwelling_identity_unproven",
        "diagnostic": None,
        "candidates": [],
        "building_context": None,
        "graph_state": "not_checked",
    }
    assert [item["document_ref"] for item in comparison["comparisons"]] == [
        item["document_ref"] for item in comparison["inventory"]["items"]
    ]
    assert comparison["comparisons"][0]["criteria"][0]["state"] == "matching"
    assert comparison["comparisons"][0]["criteria"][1]["state"] == "matching"
    assert comparison["comparisons"][1]["criteria"][0]["state"] == "unknown"
    assert comparison["comparisons"][1]["criteria"][1]["state"] == "unknown"
    assert "candidates_outside_page" in comparison["limits"]

    second_response = await lab.client.get(
        "/v1/bien/dpe/inventaire",
        params={
            "contexte": seeded.context_ref,
            "curseur": first["next_cursor"],
            "limite": 2,
        },
    )
    assert second_response.status_code == 200
    _assert_private_response(second_response)
    second = second_response.json()
    assert first["total"] == second["total"] == 3
    assert len(second["items"]) == 1
    assert second["items"][0]["type_batiment"] == "appartement"
    assert second["items"][0]["surface_habitable"] == 40.0
    assert second["items"][0]["etiquette_dpe"] == "E"
    assert second["has_more"] is False

    document_response = await lab.client.get(
        "/v1/bien/dpe/document",
        params={
            "contexte": seeded.context_ref,
            "reference": first["items"][0]["document_ref"],
        },
    )
    assert document_response.status_code == 200
    _assert_private_response(document_response)
    document = document_response.json()
    assert document["type_batiment"] == "maison"
    assert document["surface_habitable"] == 82.5
    assert document["date_etablissement_dpe"] == "2024-02-03"
    assert document["type_installation_chauffage"] == "individuel"

    corrected_response = await lab.client.post(
        "/v1/bien/dpe/comparaison",
        json={
            "contexte": seeded.context_ref,
            "curseur": first["next_cursor"],
            "limite": 2,
            "cible": {
                "type_bien": "appartement",
                "surface": {"value": 40.0, "nature": "habitable_dpe"},
            },
        },
    )
    assert corrected_response.status_code == 200
    _assert_private_response(corrected_response)
    corrected = corrected_response.json()
    assert corrected["attribution"]["status"] == "unassigned"
    assert corrected["attribution"]["diagnostic"] is None
    assert corrected["comparisons"][0]["criteria"][0]["state"] == "matching"
    assert corrected["comparisons"][0]["criteria"][1]["state"] == "matching"

    reread_response = await lab.client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": seeded.context_ref, "limite": 2},
    )
    assert reread_response.status_code == 200
    _assert_private_response(reread_response)
    reread = reread_response.json()
    for payload in (first, reread):
        payload.pop("context_ref")
        payload.pop("next_cursor")
        for item in payload["items"]:
            item.pop("document_ref")
    assert reread == first
    logged_arguments = " ".join(
        repr(call.args) + repr(call.kwargs) for call in lab.request_log.await_args_list
    )
    assert PRIVATE_NUMBER_CANARY not in logged_arguments
    assert PRIVATE_COMPLEMENT_CANARY not in logged_arguments
    assert PRIVATE_NUMBER_CANARY not in caplog.text
    assert PRIVATE_COMPLEMENT_CANARY not in caplog.text


async def test_real_sql_rejects_foreign_reference_and_changed_generation(
    real_documentary_app,
):
    lab = real_documentary_app
    own = await load_dpe_inventory(lab.factory, ban_id=JOURNEY_BAN_ID, limit=2)
    foreign = await load_dpe_inventory(lab.factory, ban_id=FOREIGN_BAN_ID, limit=2)

    foreign_response = await lab.client.get(
        "/v1/bien/dpe/document",
        params={
            "contexte": own.context_ref,
            "reference": foreign.items[0].document_ref,
        },
    )
    assert foreign_response.status_code == 400
    _assert_private_response(foreign_response)

    _swap_diagnostics_generation(lab.dsn)
    stale_response = await lab.client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": own.context_ref, "limite": 2},
    )
    assert stale_response.status_code == 409
    _assert_private_response(stale_response)
    stale_comparison = await lab.client.post(
        "/v1/bien/dpe/comparaison",
        json={"contexte": own.context_ref, "limite": 2},
    )
    assert stale_comparison.status_code == 409
    _assert_private_response(stale_comparison)


async def test_real_sql_distinguishes_empty_inventory_from_invalid_publication(
    real_documentary_app,
):
    lab = real_documentary_app
    page = await load_dpe_inventory(lab.factory, ban_id=JOURNEY_BAN_ID, limit=2)
    with psycopg.connect(lab.dsn, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM diagnostics_dpe WHERE identifiant_ban = %s",
            (JOURNEY_BAN_ID,),
        )

    absent_response = await lab.client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": page.context_ref, "limite": 2},
    )
    assert absent_response.status_code == 200
    _assert_private_response(absent_response)
    assert absent_response.json()["state"] == "absent"
    assert absent_response.json()["total"] == 0
    absent_comparison = await lab.client.post(
        "/v1/bien/dpe/comparaison",
        json={"contexte": page.context_ref, "limite": 2},
    )
    assert absent_comparison.status_code == 200
    _assert_private_response(absent_comparison)
    assert absent_comparison.json()["attribution"]["status"] == "absent"

    _publish(lab.dsn, "failed")
    unavailable_response = await lab.client.get(
        "/v1/bien/dpe/inventaire",
        params={"contexte": page.context_ref, "limite": 2},
    )
    assert unavailable_response.status_code == 503
    _assert_private_response(unavailable_response)
    assert "publication non validée" in unavailable_response.text
    unavailable_comparison = await lab.client.post(
        "/v1/bien/dpe/comparaison",
        json={"contexte": page.context_ref, "limite": 2},
    )
    assert unavailable_comparison.status_code == 503
    _assert_private_response(unavailable_comparison)


@pytest.fixture
async def documentary_app(isolated_pg_dsn, client, monkeypatch):
    with psycopg.connect(isolated_pg_dsn) as conn:
        # The existing harness owns this isolated child database.
        conn.execute(
            "DROP TABLE IF EXISTS etl_publications, diagnostics_dpe, retired_dpe"
        )
        conn.execute("CREATE TABLE diagnostics_dpe (id bigint)")
        path = (
            Path(__file__).parents[2]
            / "migrations/versions/094_publication_generations.py"
        )
        spec = importlib.util.spec_from_file_location("dpe_route_migration094", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = SimpleNamespace(execute=conn.execute)
        migration.upgrade()
        conn.commit()
        install_instance_scope(conn, _HOSTED, ("44", "85"))
    engine = create_async_engine(
        isolated_pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
    )
    sessions = async_sessionmaker(engine)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides.pop(require_publication, None)
    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    inventory = AsyncMock(
        return_value=DpeInventoryPage(
            state="absent",
            total=0,
            distribution={},
            page_size=20,
            has_more=False,
            read_state="complete",
        )
    )
    document = AsyncMock(
        return_value=DpeConsultedDocument(
            context_ref="synthetic-context",
            document_ref="synthetic-document",
            validity_by_date="unknown",
        )
    )
    monkeypatch.setattr(logements, "load_dpe_inventory", inventory)
    monkeypatch.setattr(logements, "load_dpe_document", document)
    try:
        yield isolated_pg_dsn, client, inventory, document
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.parametrize(
    "path",
    ["/v1/bien/dpe/inventaire", "/v1/bien/dpe/document", "/v1/bien/dpe/comparaison"],
)
@pytest.mark.parametrize(
    "state", ["legacy", "published", "failed", "pending", "wrong_oid"]
)
async def test_document_routes_enforce_real_publication_sql(
    documentary_app, path, state
):
    dsn, client, inventory, document = documentary_app
    if state != "legacy":
        with psycopg.connect(dsn) as conn:
            conn.execute(
                """
                INSERT INTO etl_publications
                    (generation,dataset_name,table_name,status,relation_oids)
                VALUES ('11111111-1111-1111-1111-111111111111',
                    'dpe','diagnostics_dpe',%s,
                    jsonb_build_object('diagnostics_dpe',
                        'diagnostics_dpe'::regclass::oid))
            """,
                ("published" if state == "wrong_oid" else state,),
            )
            if state == "wrong_oid":
                # Actual OID replacement at unchanged logical table name.
                conn.execute("ALTER TABLE diagnostics_dpe RENAME TO retired_dpe")
                conn.execute("CREATE TABLE diagnostics_dpe (id bigint)")
    if path.endswith("comparaison"):
        response = await client.post(path, json={"contexte": "synthetic-context"})
    else:
        response = await client.get(
            path,
            params={
                "contexte": "synthetic-context",
                "reference": "synthetic-document",
            },
        )
    allowed = state in {"legacy", "published"}
    assert response.status_code == (200 if allowed else 503)
    assert response.headers["cache-control"] == "no-store"
    if allowed:
        (document if path.endswith("document") else inventory).assert_awaited_once()
    else:
        inventory.assert_not_awaited()
        document.assert_not_awaited()
        assert "publication non validée" in response.text
    if state == "wrong_oid":
        with psycopg.connect(dsn) as conn:
            conn.execute("DROP TABLE retired_dpe")


@pytest.mark.parametrize(
    "state", ["legacy", "published", "failed", "pending", "wrong_oid"]
)
async def test_logements_also_refuses_unpublished_dvf(documentary_app, state):
    dsn, client, inventory, document = documentary_app
    with psycopg.connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS mutations, retired_mutations")
        conn.execute("CREATE TABLE mutations (id bigint)")
        if state != "legacy":
            conn.execute(
                """
                INSERT INTO etl_publications
                    (generation,dataset_name,table_name,status,relation_oids)
                VALUES ('22222222-2222-2222-2222-222222222222',
                    'dvf','mutations',%s,
                    jsonb_build_object('mutations','mutations'::regclass::oid))
                """,
                ("published" if state == "wrong_oid" else state,),
            )
            if state == "wrong_oid":
                conn.execute("ALTER TABLE mutations RENAME TO retired_mutations")
                conn.execute("CREATE TABLE mutations (id bigint)")
    # No parcel parameter: 422 proves admission reached parameter validation.
    # A failed DVF publication must be rejected first, even with legacy DPE.
    response = await client.get("/v1/bien/logements")
    allowed = state in {"legacy", "published"}
    assert response.status_code == (422 if allowed else 503)
    if not allowed:
        assert "publication non validée" in response.text
        assert response.headers["cache-control"] == "no-store"
    inventory.assert_not_awaited()
    document.assert_not_awaited()

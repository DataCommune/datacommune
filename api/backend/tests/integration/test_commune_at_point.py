"""A GPS point in Paris resolves its arrondissement, not 21 codes (WINDOWS 39).

``geo_communes`` carries 75056/69123/13055 only; ``expand_code_insee`` widens
75056 to 21 codes. Migration 099 adds ``geo_arrondissements`` (loaded by
``scripts/import_geo_boundaries.py``) and ``commune_at_point`` prefers the
arrondissement covering the point, as the former resolution by ``parcelles``
did. Proven against a real PostGIS at migration head, with synthetic squares:

1. a point in the 1st arrondissement → ``resolve_location`` gives ``['75101']``;
2. a point in Nantes → ``['44109']``, unchanged;
3. the commune list (``/v1/communes``) counts no arrondissement.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 -Paths
        tests/integration/test_commune_at_point.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.geo import resolve_location
from app.config import settings
from app.zone.queries import list_communes
from tests.integration.test_instance_scope import BACKEND


def _square(lon: float, lat: float, size: float) -> str:
    x2, y2 = lon + size, lat + size
    return (
        f"SRID=4326;MULTIPOLYGON((({lon} {lat},{x2} {lat},{x2} {y2},"
        f"{lon} {y2},{lon} {lat})))"
    )


# Paris commune: a 0.2° square; its 1st arrondissement: the lower-left 0.05°.
PARIS = _square(2.25, 48.80, 0.2)
PARIS_1 = _square(2.25, 48.80, 0.05)
NANTES = _square(-1.65, 47.15, 0.2)


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn: str) -> str:
    # Own database: the shared one of this CI group is left at another
    # revision by earlier modules (DuplicateTable etl_publications on upgrade).
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", isolated_pg_dsn)
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(cfg, "head")
    return isolated_pg_dsn


@pytest.fixture()
async def session_geo(migrated: str) -> AsyncIterator[AsyncSession]:
    """Synthetic referential rows inside one transaction, rolled back after."""
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM geo_communes WHERE code IN ('75056', '44109')")
            )
            await session.execute(
                text("DELETE FROM geo_arrondissements WHERE code = '75101'")
            )
            for code, nom, dept, geom in (
                ("75056", "Paris", "75", PARIS),
                ("44109", "Nantes", "44", NANTES),
            ):
                await session.execute(
                    text(
                        "INSERT INTO geo_communes "
                        "(code, nom, departement_code, population, geometry) "
                        "VALUES (:c, :n, :d, 1000, ST_GeomFromEWKT(:g))"
                    ),
                    {"c": code, "n": nom, "d": dept, "g": geom},
                )
            await session.execute(
                text(
                    "INSERT INTO geo_arrondissements "
                    "(code, nom, departement_code, commune_code, population, geometry) "
                    "VALUES ('75101', 'Paris 1er Arrondissement', '75', '75056', 10, "
                    "ST_GeomFromEWKT(:g))"
                ),
                {"g": PARIS_1},
            )
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def test_point_in_the_first_arrondissement_resolves_it_alone(session_geo):
    resolved = await resolve_location(session_geo, lat=48.82, lon=2.27)
    assert resolved.codes == ["75101"]


async def test_point_in_paris_outside_any_arrondissement_keeps_the_commune(
    session_geo,
):
    # No arrondissement polygon here in this synthetic referential: the
    # commune (and its expansion) is still the answer.
    resolved = await resolve_location(session_geo, lat=48.95, lon=2.40)
    assert resolved.codes[0] == "75056"
    assert len(resolved.codes) == 21


async def test_point_in_nantes_is_unchanged(session_geo):
    resolved = await resolve_location(session_geo, lat=47.25, lon=-1.55)
    assert resolved.codes == ["44109"]


async def test_the_commune_list_counts_no_arrondissement(session_geo):
    rows, total = await list_communes(session_geo, departement="75")
    assert [r["code_insee"] for r in rows] == ["75056"]
    assert total == 1

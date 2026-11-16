"""Raw SQL queries for ANFR antennes lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_COLS = """
    operateur,
    generation,
    bande_frequence,
    numero_station,
    numero_support,
    code_insee,
    adresse,
    latitude,
    longitude,
    date_mise_en_service::text AS date_mise_en_service,
    statut
"""


async def antennes_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: int = 1000,
    limit: int = 50,
) -> list[dict]:
    """Find antennas within radius of a GPS point."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM antennes
        WHERE geom IS NOT NULL
          AND ST_DWithin(
              geom::geography,
              ST_SetSRID(
                  ST_MakePoint(:lon, :lat), 4326
              )::geography,
              :rayon_m
          )
        ORDER BY geom <-> ST_SetSRID(
            ST_MakePoint(:lon, :lat), 4326
        )
        LIMIT :lim
    """)

    result = await session.execute(
        sql,
        {
            "lat": lat,
            "lon": lon,
            "rayon_m": rayon_m,
            "lim": limit,
        },
    )
    return [dict(row._mapping) for row in result]


async def antennes_par_code_insee(
    session: AsyncSession,
    codes: list[str],
    limit: int = 100,
) -> list[dict]:
    """Find antennas by commune codes (pre-expanded by caller)."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM antennes
        WHERE code_insee = ANY(:codes)
        ORDER BY generation, operateur
        LIMIT :lim
    """)

    result = await session.execute(
        sql,
        {"codes": codes, "lim": limit},
    )
    return [dict(row._mapping) for row in result]

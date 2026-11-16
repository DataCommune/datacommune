"""Raw SQL queries for BD TOPO batiments lookups.

Uses PostGIS ST_DWithin for spatial filtering and KNN (<-> operator)
for distance-ordered results. Default 200m radius (buildings are dense).
Deduplicates on cleabs via DISTINCT ON subquery (buildings near department
borders appear in multiple partitions).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def batiments_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 200.0,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Find BD TOPO buildings near a GPS point.

    Spatial filter uses ST_DWithin on the GIST index. Inner query
    deduplicates on cleabs (picks closest instance per building).
    Outer query applies final distance ordering and pagination.
    """
    sql = text("""
        SELECT cleabs, nature, usage_1, nombre_d_etages,
               hauteur, altitude_minimale_sol, altitude_maximale_toit
        FROM (
            SELECT DISTINCT ON (cleabs)
                   cleabs, nature, usage_1, nombre_d_etages,
                   hauteur, altitude_minimale_sol, altitude_maximale_toit,
                   ST_Distance(
                       geometrie::geography,
                       ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
                   ) AS dist
            FROM batiments
            WHERE ST_DWithin(
                geometrie::geography,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                :rayon_m
            )
            ORDER BY cleabs, dist
        ) sub
        ORDER BY dist
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(
        sql,
        {
            "lat": lat,
            "lon": lon,
            "rayon_m": rayon_m,
            "limit": limit,
            "offset": offset,
        },
    )
    return [dict(row._mapping) for row in result]

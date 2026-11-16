"""Raw SQL queries for Monuments Historiques lookups.

Uses PostGIS ST_DWithin for spatial filtering and KNN (<-> operator)
for distance-ordered results. Default 1000m radius (monuments are sparse).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def monuments_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 1000.0,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Find Monuments Historiques near a GPS point.

    Spatial filter uses ST_DWithin on the GIST index. Results are
    ordered by KNN distance (<-> operator) for index-assisted sort.
    """
    sql = text("""
        SELECT reference, denomination, adresse, commune,
               code_insee, protection, date_protection,
               ST_X(position) AS longitude,
               ST_Y(position) AS latitude
        FROM monuments_historiques
        WHERE ST_DWithin(
            position::geography,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
            :rayon_m
        )
        ORDER BY position <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
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

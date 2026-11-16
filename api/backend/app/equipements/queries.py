"""Raw SQL queries for BPE equipements lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_COLS = """
    type_equipement,
    code_commune,
    code_departement,
    code_iris,
    domaine,
    sous_domaine,
    nb_equipements,
    latitude,
    longitude,
    annee
"""


async def equipements_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: int = 1000,
    domaine: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Find facilities within radius of a GPS point."""
    where_domaine = "AND domaine = :domaine" if domaine else ""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM equipements
        WHERE geom IS NOT NULL
          AND ST_DWithin(
              geom::geography,
              ST_SetSRID(
                  ST_MakePoint(:lon, :lat), 4326
              )::geography,
              :rayon_m
          )
          {where_domaine}
        ORDER BY geom <-> ST_SetSRID(
            ST_MakePoint(:lon, :lat), 4326
        )
        LIMIT :lim
    """)

    params: dict = {
        "lat": lat,
        "lon": lon,
        "rayon_m": rayon_m,
        "lim": limit,
    }
    if domaine:
        params["domaine"] = domaine

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]


async def equipements_par_code_insee(
    session: AsyncSession,
    codes: list[str],
    domaine: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Find facilities by commune codes (pre-expanded by caller)."""
    where_domaine = "AND domaine = :domaine" if domaine else ""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM equipements
        WHERE code_commune = ANY(:codes)
          {where_domaine}
        ORDER BY type_equipement
        LIMIT :lim
    """)

    params: dict = {
        "codes": codes,
        "lim": limit,
    }
    if domaine:
        params["domaine"] = domaine

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]

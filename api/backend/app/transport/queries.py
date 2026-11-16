"""Raw SQL queries for Transport proximity lookups.

Uses PostGIS ST_DWithin for spatial filtering on arrets_transport and
gares via UNION ALL of the hosted branches, ordered by distance.
"""

import math
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_DISTANCE = """ST_Distance(
                       position::geography,
                       ST_MakePoint(:lon, :lat)::geography
                   ) AS distance_m"""
# Bounding box on the geometry GIST index (idx_arrets_position,
# idx_gares_position) first, then the geodesic distance: the geography
# predicate alone cannot use those indexes and read every row (k6 firing of
# 2026-11-02, 35 % of the PostgreSQL time). The box only over-selects
# (:func:`marge_en_degres`), so the rows kept are the same.
_WITHIN = """position && ST_Expand(
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :marge
            )
            AND ST_DWithin(
                position::geography,
                ST_MakePoint(:lon, :lat)::geography,
                :rayon
            )"""

# WGS84: metres per degree of latitude at the equator (the shortest), and of
# longitude on the equatorial radius a, rounded down.
_METRES_PAR_DEGRE_NORD_MIN = 110_574.0
_METRES_PAR_DEGRE_EST_EQUATEUR = 111_319.0


def marge_en_degres(lon: float, lat: float, rayon_m: float) -> float:
    """Half side, in degrees, of a box around (*lon*, *lat*) holding every
    point within *rayon_m* metres on the WGS84 ellipsoid.

    A path of length r changes the latitude by at most r / 110 574 m per
    degree, and the longitude by at most r / (a cos φ) where φ is the largest
    latitude it reaches. When the disc reaches a pole or the antimeridian the
    box spans the whole plane: the geometry box does not wrap.
    """
    nord = rayon_m / _METRES_PAR_DEGRE_NORD_MIN
    lat_max = abs(lat) + nord
    if lat_max >= 90:
        return 360.0
    est = rayon_m / (_METRES_PAR_DEGRE_EST_EQUATEUR * math.cos(math.radians(lat_max)))
    if abs(lon) + est >= 180:
        return 360.0
    return max(nord, est)


# One branch per catalogue source of the ``transports`` aggregate: a source
# the instance does not host is not in the query at all (DATA-03).
_BRANCHES: dict[str, str] = {
    "transports": f"""
            SELECT stop_name, 'arret' AS type_transport,
                   ST_Y(position) AS latitude,
                   ST_X(position) AS longitude,
                   {_DISTANCE},
                   location_type, agency_name
            FROM arrets_transport
            WHERE {_WITHIN}""",
    "gares": f"""
            SELECT nom_gare AS stop_name, 'gare' AS type_transport,
                   ST_Y(position) AS latitude,
                   ST_X(position) AS longitude,
                   {_DISTANCE},
                   NULL::integer AS location_type,
                   NULL::text AS agency_name
            FROM gares
            WHERE {_WITHIN}""",
}


async def transports_a_proximite(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 1000.0,
    limit: int = 50,
    *,
    sources: Sequence[str],
) -> list[dict]:
    """Find transport stops and train stations near a GPS point.

    Mounts only the branches of the hosted *sources* (``transports`` ->
    arrets_transport, ``gares`` -> gares), each filtered by ST_DWithin,
    ordered by distance ascending. An empty *sources* is a programming
    error: the scope guard has already decided that nothing is read.
    Note: ST_MakePoint uses lon,lat order per PostGIS convention.
    """
    if not sources:
        raise ValueError("transports_a_proximite needs at least one hosted source")
    branches = [_BRANCHES[source] for source in dict.fromkeys(sources)]
    sql = text(f"""
        SELECT * FROM ({" UNION ALL ".join(branches)}
        ) combined
        ORDER BY distance_m
        LIMIT :limit
    """)

    result = await session.execute(
        sql,
        {
            "lat": lat,
            "lon": lon,
            "rayon": rayon_m,
            "marge": marge_en_degres(lon, lat, rayon_m),
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in result]


async def gare_la_plus_proche(
    session: AsyncSession, lon: float, lat: float, rayon_m: float
) -> dict | None:
    """The SNCF station nearest to the point within *rayon_m*, at the
    geodesic distance (04.7-07); None when there is none."""
    result = await session.execute(
        text(f"""
            SELECT nom_gare, {_DISTANCE}
            FROM gares
            WHERE {_WITHIN}
            ORDER BY distance_m, nom_gare
            LIMIT 1
        """),
        {
            "lon": lon,
            "lat": lat,
            "rayon": rayon_m,
            "marge": marge_en_degres(lon, lat, rayon_m),
        },
    )
    ligne = result.first()
    return dict(ligne._mapping) if ligne is not None else None

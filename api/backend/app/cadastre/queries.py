"""Raw SQL queries for cadastre lookups.

Uses PostGIS ST_Intersects for point-in-polygon spatial join and
B-tree index on id_parcelle for reference lookups.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def parcelle_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
) -> list[dict]:
    """Return ALL cadastre parcels intersecting the given GPS point.

    Deterministic candidates API (Phase 45, D-04): parcels do not overlap,
    but a point exactly on a shared boundary intersects several parcels.
    A silent ``LIMIT 1`` would pick one arbitrarily — instead every candidate
    is returned in a stable order (``id_parcelle``) and callers inspect the
    cardinality: zero = typed absence, one = exact, several = ambiguous.
    Uses ST_Intersects which leverages the GIST spatial index.
    """
    sql = text("""
        SELECT id_parcelle, commune, prefixe, section, numero,
               contenance,
               ST_Y(ST_Centroid(geometrie)) AS centroide_lat,
               ST_X(ST_Centroid(geometrie)) AS centroide_lon,
               ST_AsGeoJSON(geometrie) AS geometrie_geojson
        FROM parcelles
        WHERE ST_Intersects(
            geometrie,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
        )
        ORDER BY id_parcelle
    """)

    result = await session.execute(sql, {"lat": lat, "lon": lon})
    return [dict(row._mapping) for row in result]


async def parcelles_proches(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 10.0,
) -> list[dict]:
    """Return parcels within ``rayon_m`` metres of the point, closest first.

    Fallback companion of :func:`parcelle_par_point` for entrance points that
    land just outside every parcel (a BAN point on the roadway is 0.0 m from
    the abutting parcel yet strictly outside it).

    Two-stage filter, on purpose: ``idx_parcelles_geometrie`` is a GIST index
    on the 4326 GEOMETRY column (migration 003), so a ``geometrie::geography``
    predicate could not use it and would seq-scan a 94M-row partitioned table.
    Stage 1 is therefore an index-assisted ``ST_DWithin`` in degrees, padded by
    ``1/cos(lat)`` so it can only over-select; stage 2 re-filters exactly in
    metres via ``geography``.

    Same columns as the strict lookup plus ``distance_m``; ties are broken on
    ``id_parcelle`` so the order is deterministic. The caller applies the
    decision rule (:func:`app.resolve.snap.select_snapped_candidates`) — this
    query never picks a winner on its own.
    """
    sql = text("""
        SELECT id_parcelle, commune, prefixe, section, numero,
               contenance,
               ST_Y(ST_Centroid(geometrie)) AS centroide_lat,
               ST_X(ST_Centroid(geometrie)) AS centroide_lon,
               ST_AsGeoJSON(geometrie) AS geometrie_geojson,
               ST_Distance(
                   geometrie::geography,
                   ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
               ) AS distance_m
        FROM parcelles
        WHERE ST_DWithin(
            geometrie,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
            :rayon_m / (111320.0 * GREATEST(COS(RADIANS(:lat)), 0.01))
        )
        AND ST_Distance(
            geometrie::geography,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
        ) <= :rayon_m
        ORDER BY distance_m, id_parcelle
    """)

    result = await session.execute(sql, {"lat": lat, "lon": lon, "rayon_m": rayon_m})
    return [dict(row._mapping) for row in result]


async def parcelle_par_reference(
    session: AsyncSession,
    ref_cadastrale: str,
) -> list[dict]:
    """Look up a cadastre parcel by its 14-character reference.

    Uses the B-tree index on id_parcelle for fast exact match.
    Returns an empty list when the reference is unknown.
    """
    sql = text("""
        SELECT id_parcelle, commune, prefixe, section, numero,
               contenance,
               ST_Y(ST_Centroid(geometrie)) AS centroide_lat,
               ST_X(ST_Centroid(geometrie)) AS centroide_lon,
               ST_AsGeoJSON(geometrie) AS geometrie_geojson
        FROM parcelles
        WHERE id_parcelle = :ref
    """)

    result = await session.execute(sql, {"ref": ref_cadastrale})
    return [dict(row._mapping) for row in result]

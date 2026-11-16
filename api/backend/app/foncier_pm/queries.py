"""Raw SQL queries for MAJIC personnes morales (legal entity parcels).

Supports commune-code and GPS-radius query modes with optional
SIREN and denomination filters.  GPS mode resolves communes via the
geo_communes referential since foncier_pm has no geometry column (the
cadastre parcels are a source the instance may not host, D-23).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.sql_escape import escape_like

_SELECT_COLS = """
    fp.code_parcelle,
    fp.code_commune,
    fp.nom_commune,
    fp.adresse,
    fp.contenance_parcelle,
    fp.nature_culture,
    fp.code_droit,
    fp.numero_siren,
    fp.groupe_personne,
    fp.forme_juridique_abregee,
    fp.denomination
"""


def _build_filters(
    siren: str | None,
    denomination: str | None,
    params: dict,
) -> list[str]:
    """Return optional WHERE clauses and update params dict in-place."""
    clauses: list[str] = []
    if siren is not None:
        clauses.append("fp.numero_siren = :siren")
        params["siren"] = siren
    if denomination is not None:
        clauses.append("fp.denomination ILIKE :denomination ESCAPE '\\'")
        params["denomination"] = f"%{escape_like(denomination)}%"
    return clauses


async def foncier_pm_par_codes(
    session: AsyncSession,
    codes: list[str],
    siren: str | None = None,
    denomination: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Find legal entity parcels for the given commune/arrondissement codes."""
    params: dict = {
        "codes": codes,
        "limit": limit,
        "offset": offset,
    }

    where_parts = ["fp.code_commune = ANY(:codes)"]
    where_parts.extend(_build_filters(siren, denomination, params))
    where_sql = " AND ".join(where_parts)

    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM foncier_pm fp
        WHERE {where_sql}
        ORDER BY fp.code_parcelle, fp.numero_siren
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]


async def foncier_pm_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 500.0,
    siren: str | None = None,
    denomination: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Find legal entity parcels near a GPS point.

    Since foncier_pm has no geometry column, we first resolve all
    communes whose referential polygon (geo_communes) falls within the
    radius, then filter foncier_pm by those commune codes.
    """
    params: dict = {
        "lat": lat,
        "lon": lon,
        "rayon_m": rayon_m,
        "limit": limit,
        "offset": offset,
    }

    where_parts = ["fp.code_commune IN (SELECT commune FROM communes_in_radius)"]
    where_parts.extend(_build_filters(siren, denomination, params))
    where_sql = " AND ".join(where_parts)

    sql = text(f"""
        WITH communes_in_radius AS (
            SELECT g.code AS commune
            FROM geo_communes g
            WHERE ST_DWithin(
                g.geometry::geography,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                :rayon_m
            )
        )
        SELECT {_SELECT_COLS}
        FROM foncier_pm fp
        WHERE {where_sql}
        ORDER BY fp.code_parcelle, fp.numero_siren
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]

"""Raw SQL queries for Zonage ABC housing tension zone lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def zonage_par_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict]:
    """Find ABC zones for the given commune/arrondissement codes."""
    sql = text("""
        SELECT
            code_insee,
            nom_commune,
            code_departement,
            zone,
            reclassement,
            date_arrete::text AS date_arrete
        FROM zonage_abc
        WHERE code_insee = ANY(:codes)
    """)

    result = await session.execute(sql, {"codes": codes})
    return [dict(row._mapping) for row in result]


async def zonage_par_departement(
    session: AsyncSession,
    code_departement: str,
) -> list[dict]:
    """Find all ABC zones for a department."""
    sql = text("""
        SELECT
            code_insee,
            nom_commune,
            code_departement,
            zone,
            reclassement,
            date_arrete::text AS date_arrete
        FROM zonage_abc
        WHERE code_departement = :code_departement
        ORDER BY nom_commune
    """)

    result = await session.execute(sql, {"code_departement": code_departement})
    return [dict(row._mapping) for row in result]

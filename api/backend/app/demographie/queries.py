"""Raw SQL queries for Recensement demographie lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_COLS = """
    d.code_insee,
    d.nom_commune,
    d.code_departement,
    d.code_region,
    d.population,
    d.population_hommes,
    d.population_femmes,
    d.nb_menages,
    d.nb_logements,
    d.nb_residences_principales,
    d.nb_logements_vacants,
    d.nb_residences_secondaires,
    d.superficie,
    d.densite,
    d.annee
"""


async def demographie_par_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict]:
    """Return the latest observation for each exact commune/arrondissement code."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM demographie d
        WHERE d.code_insee = ANY(:codes)
          AND d.annee = (
              SELECT MAX(history.annee) FROM demographie history
              WHERE history.code_insee = d.code_insee
          )
        ORDER BY d.code_insee
    """)

    result = await session.execute(sql, {"codes": codes})
    return [dict(row._mapping) for row in result]

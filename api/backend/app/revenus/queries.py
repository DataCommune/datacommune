"""Raw SQL queries for Filosofi revenus lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_COLS = """
    r.code_insee,
    r.nom_commune,
    r.code_departement,
    r.nom_departement,
    r.nom_region,
    r.nb_menages_fiscaux,
    r.revenu_median,
    r.revenu_q1,
    r.revenu_q3,
    r.revenu_d1,
    r.revenu_d9,
    r.indice_gini,
    r.taux_pauvrete,
    r.part_imposee,
    r.annee
"""


async def revenus_par_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict]:
    """Return the latest observation for each exact commune/arrondissement code."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM revenus r
        WHERE r.code_insee = ANY(:codes)
          AND r.annee = (
              SELECT MAX(history.annee) FROM revenus history
              WHERE history.code_insee = r.code_insee
          )
        ORDER BY r.code_insee
    """)

    result = await session.execute(sql, {"codes": codes})
    return [dict(row._mapping) for row in result]

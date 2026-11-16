"""Raw SQL queries for LOVAC vacance lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_COLS = """
    v.code_commune,
    v.nom_commune,
    v.code_departement,
    v.nom_departement,
    v.nom_region,
    v.nb_logements_prives,
    v.nb_logements_vacants,
    v.nb_vacants_moins_2ans,
    v.nb_vacants_plus_2ans,
    CASE WHEN v.nb_logements_prives > 0 AND v.nb_logements_vacants >= 0
        THEN v.taux_vacance END AS taux_vacance,
    CASE WHEN v.nb_logements_prives > 0 AND v.nb_vacants_moins_2ans >= 0
        THEN v.taux_vacance_moins_2ans END AS taux_vacance_moins_2ans,
    CASE WHEN v.nb_logements_prives > 0 AND v.nb_vacants_plus_2ans >= 0
        THEN v.taux_vacance_plus_2ans END AS taux_vacance_plus_2ans,
    v.annee
"""


async def vacance_par_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict]:
    """Latest observation per code; suppress undecidable historical rates."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM vacance v
        WHERE v.code_commune = ANY(:codes)
          AND v.annee = (
              SELECT MAX(latest.annee) FROM vacance latest
              WHERE latest.code_commune = v.code_commune
          )
    """)

    result = await session.execute(sql, {"codes": codes})
    return [dict(row._mapping) for row in result]

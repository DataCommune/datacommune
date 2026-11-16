"""Raw SQL queries for Loyers rental price indicator lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def loyers_par_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict]:
    """Find rental indicators for the given commune/arrondissement codes.

    Returns the latest observation for each exact code and housing type.
    """
    sql = text("""
        SELECT
            code_insee,
            nom_commune,
            type_habitat,
            loyer_m2,
            loyer_m2_bas,
            loyer_m2_haut,
            r2,
            nb_observations,
            annee,
            id_zone, type_prediction, nb_observations_commune,
            nb_observations_maille, r2_ajuste, trimestre_reference,
            charges_comprises, meuble, surface_reference_m2,
            surface_piece_reference_m2
        FROM indicateurs_loyers current_row
        WHERE current_row.code_insee = ANY(:codes)
          AND current_row.annee = (
              SELECT MAX(history.annee) FROM indicateurs_loyers history
              WHERE history.code_insee = current_row.code_insee
                AND history.type_habitat = current_row.type_habitat
          )
        ORDER BY current_row.type_habitat, current_row.code_insee
    """)

    result = await session.execute(sql, {"codes": codes})
    return [dict(row._mapping) for row in result]

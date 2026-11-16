"""Raw SQL queries for Fiscalite locale tax rate lookups."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def fiscalite_par_codes(
    session: AsyncSession,
    codes: list[str],
) -> list[dict]:
    """Return the latest observation for each exact commune/arrondissement code."""
    sql = text("""
        SELECT
            code_insee,
            nom_commune,
            code_departement,
            nom_departement,
            nom_region,
            nom_epci,
            taux_tfpb,
            taux_tfpnb,
            taux_th_rs,
            taux_teom,
            -- 04.7-08: NULL until the first import that reads them.
            majoration_thrs,
            taux_majoration_thrs,
            -- D6: base_tfpb / produit_tfpb deliberately not selected. The DGFiP
            -- source has no imposition base, the ETL writes both columns empty
            -- on every run; serving them would be an empty promise.
            annee
        FROM fiscalite_locale f
        WHERE f.code_insee = ANY(:codes)
          AND f.annee = (
              SELECT MAX(history.annee) FROM fiscalite_locale history
              WHERE history.code_insee = f.code_insee
          )
        ORDER BY f.code_insee
    """)

    result = await session.execute(sql, {"codes": codes})
    return [dict(row._mapping) for row in result]


async def moyenne_departement(
    session: AsyncSession, code_departement: str, annee: int
) -> dict:
    """The plain mean of the global built-property rates of the communes of
    *code_departement* for *annee*, how many communes the year holds and
    how many of them have a rate.

    A plain mean of rates, not the DGFiP departmental mean (weighted by the
    bases, which the source does not give).
    """
    sql = text("""
        SELECT AVG(taux_tfpb) AS moyenne, COUNT(*) AS communes,
               COUNT(taux_tfpb) AS taux
        FROM fiscalite_locale
        WHERE code_departement = :departement AND annee = :annee
    """)
    result = await session.execute(
        sql, {"departement": code_departement, "annee": annee}
    )
    return dict(result.one()._mapping)

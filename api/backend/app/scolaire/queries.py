"""Raw SQL queries for Carte Scolaire lookups.

Implements the sector college matching logic: code_insee + voie name
normalization + street number range + parity check (P=pair, I=impair,
PI=tous).  Falls back to unique-sector lookup when voie/numero missing.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.geo import expand_code_insee
from app.common.sql_escape import escape_like


async def college_de_secteur(
    session: AsyncSession,
    code_insee: str,
    nom_voie: str | None = None,
    numero: int | None = None,
) -> list[dict]:
    """Find the sector college for an address.

    If nom_voie or numero are missing, fall back to unique-sector
    lookup (communes where secteur_unique = 'O').

    Full matching applies: code_insee + unaccent(lower(voie)) LIKE +
    number range [n_debut..n_fin] + parity check.
    """
    codes = expand_code_insee(code_insee)
    if nom_voie is None or numero is None:
        # Fallback: return unique-sector college if exists
        sql = text("""
            SELECT DISTINCT code_rne, code_insee, libelle_commune,
                   NULL::text AS type_et_libelle,
                   NULL::real AS n_de_voie_debut,
                   NULL::real AS n_de_voie_fin,
                   NULL::text AS parite,
                   secteur_unique
            FROM carte_scolaire
            WHERE code_insee = ANY(:codes) AND secteur_unique = 'O'
        """)
        result = await session.execute(sql, {"codes": codes})
        return [dict(row._mapping) for row in result]

    # Full matching: code_insee + voie + numero + parite
    sql = text("""
        SELECT DISTINCT code_rne, code_insee, libelle_commune,
               type_et_libelle, n_de_voie_debut, n_de_voie_fin,
               parite, secteur_unique
        FROM carte_scolaire
        WHERE code_insee = ANY(:codes)
          AND unaccent(lower(type_et_libelle))
              LIKE '%' || unaccent(lower(:nom_voie)) || '%' ESCAPE '\'
          AND (n_de_voie_debut IS NULL OR :numero >= n_de_voie_debut)
          AND (n_de_voie_fin IS NULL OR :numero <= n_de_voie_fin)
          AND (parite = 'PI'
               OR (parite = 'P' AND mod(CAST(:numero AS int), 2) = 0)
               OR (parite = 'I' AND mod(CAST(:numero AS int), 2) = 1))
        ORDER BY code_rne
        LIMIT 5
    """)
    result = await session.execute(
        sql,
        {
            "codes": codes,
            "nom_voie": escape_like(nom_voie),
            "numero": numero,
        },
    )
    return [dict(row._mapping) for row in result]


async def colleges_du_secteur(
    session: AsyncSession, code_insee: str, nom_voie: str, numero: int
) -> list[str]:
    """Distinct UAI of the colleges whose sector holds the address, on the
    full matching of :func:`college_de_secteur`; at most two, enough to tell
    one college from several (the rows of one college are not counted)."""
    sql = text(r"""
        SELECT DISTINCT code_rne
        FROM carte_scolaire
        WHERE code_insee = ANY(:codes)
          AND unaccent(lower(type_et_libelle))
              LIKE '%' || unaccent(lower(:nom_voie)) || '%' ESCAPE '\'
          AND (n_de_voie_debut IS NULL OR :numero >= n_de_voie_debut)
          AND (n_de_voie_fin IS NULL OR :numero <= n_de_voie_fin)
          AND (parite = 'PI'
               OR (parite = 'P' AND mod(CAST(:numero AS int), 2) = 0)
               OR (parite = 'I' AND mod(CAST(:numero AS int), 2) = 1))
        ORDER BY code_rne
        LIMIT 2
    """)
    result = await session.execute(
        sql,
        {
            "codes": expand_code_insee(code_insee),
            "nom_voie": escape_like(nom_voie),
            "numero": numero,
        },
    )
    return [row.code_rne for row in result]

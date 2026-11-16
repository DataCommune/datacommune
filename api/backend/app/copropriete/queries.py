"""Raw SQL queries for RNIC copropriete lookups.

Every row leaves with the name of its manager only when it is a professional
(RGPD: a volunteer or cooperative manager is a co-owner, a person), so that
no route or export that reads the registry through these queries can serve
it: ``GET /v1/copropriete`` and ``/v1/resolve`` alike.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.copropriete.syndic import nom_syndic_public

_SELECT_COLS = """
    cp.numero_immatriculation,
    cp.nom_copropriete,
    cp.adresse,
    cp.code_commune,
    cp.code_postal,
    cp.nb_lots_total,
    cp.nb_lots_habitation,
    cp.nb_lots_commerciaux,
    cp.nb_lots_stationnement,
    cp.nom_syndic,
    cp.type_syndic,
    cp.periode_construction,
    cp.annee_reglement
"""


def _publique(row) -> dict:
    ligne = dict(row._mapping)
    ligne["nom_syndic"] = nom_syndic_public(ligne["type_syndic"], ligne["nom_syndic"])
    return ligne


async def copropriete_par_codes(
    session: AsyncSession,
    codes: list[str],
    limit: int = 50,
) -> list[dict]:
    """Find coproprietes for the given commune/arrondissement codes."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM copropriete cp
        WHERE cp.code_commune = ANY(:codes)
        ORDER BY cp.nb_lots_total DESC NULLS LAST
        LIMIT :lim
    """)

    result = await session.execute(sql, {"codes": codes, "lim": limit})
    return [_publique(row) for row in result]


async def copropriete_par_numero(
    session: AsyncSession,
    numero: str,
) -> list[dict]:
    """Find a copropriete by registration number."""
    sql = text(f"""
        SELECT {_SELECT_COLS}
        FROM copropriete cp
        WHERE cp.numero_immatriculation = :numero
    """)

    result = await session.execute(sql, {"numero": numero})
    return [_publique(row) for row in result]

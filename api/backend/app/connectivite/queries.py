"""Raw SQL queries for broadband eligibility lookups.

Uses text() SQL with bind params, querying eligibilite_fixe by
code_insee with optional voie/numero filtering. Pattern from BAN/DVF queries.

One stored row per ARCEP building and its address, its offers coded in
``offres`` (plan 04.1-16): ``unnest(offres)`` joined to the dictionary
``eligibilite_offres`` gives back the offer rows of the source, served as
before. The order defined by the routes (technology, speed) ends with
``operateur, id_imb`` so that tied rows come out in one order.
"""

import unicodedata
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def eligibilite_par_adresse(
    session: AsyncSession,
    code_insee: str,
    nom_voie: str | None = None,
    numero: str | None = None,
) -> list[dict]:
    """Find broadband eligibility records for an address.

    Base filter: code_insee (required).
    Optional: nom_voie (fuzzy LIKE with unaccent/lower), numero (exact match).
    Returns up to 50 rows ordered by technology and speed.
    """
    clauses = ["e.code_insee = :code_insee"]
    params: dict = {"code_insee": code_insee}

    if nom_voie is not None:
        clauses.append(
            "unaccent(lower(e.voie)) LIKE '%' || unaccent(lower(:nom_voie)) || '%'"
        )
        params["nom_voie"] = nom_voie

    if numero is not None:
        clauses.append("e.numero = :numero")
        params["numero"] = numero

    where = " AND ".join(clauses)

    sql = text(f"""
        SELECT
            e.id_imb,
            e.code_insee,
            e.voie,
            e.numero,
            o.technologie,
            o.debit_max,
            o.operateur
        FROM eligibilite_fixe e
        CROSS JOIN LATERAL unnest(e.offres) AS u(code)
        JOIN eligibilite_offres o ON o.code = u.code
        WHERE {where}
        ORDER BY o.technologie, o.debit_max DESC NULLS LAST, o.operateur, e.id_imb
        LIMIT 50
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]


# --- 04-06: eligibility published for ONE address (R06-09/4) -------------------

# Rows read for the candidates of one number in one commune; reaching it
# leaves the identity undetermined (never a partial read taken as exact).
CANDIDATS_MAX = 500

_CANDIDATS_ADRESSE = text("""
    SELECT e.id_imb, e.voie, e.numero, e.complement, o.technologie, o.debit_max
    FROM eligibilite_fixe e
    CROSS JOIN LATERAL unnest(e.offres) AS u(code)
    JOIN eligibilite_offres o ON o.code = u.code
    WHERE e.code_insee = :code_insee
      AND e.numero = :numero
      AND unaccent(lower(e.voie)) LIKE '%' || unaccent(lower(:nom_voie)) || '%'
    ORDER BY e.id_imb, o.technologie, o.debit_max, o.operateur
    LIMIT :limite
""")


def _norme(texte: str | None) -> str:
    """Accents, case and repeated spaces removed; ``None`` is the empty text."""
    sans_accents = unicodedata.normalize("NFKD", texte or "")
    lettres = "".join(c for c in sans_accents if not unicodedata.combining(c))
    return " ".join(lettres.lower().split())


@dataclass(frozen=True)
class EligibiliteAdresse:
    """Eligibility rows of one address, or why none is served.

    ``unique``: the rows of the one building whose number, suffix, street and
    commune are exactly the address's. ``aucune``: no candidate in the commune
    for this number and street. ``indeterminee``: candidates exist but none,
    or several buildings, match exactly, or the read was bounded.
    """

    etat: Literal["unique", "aucune", "indeterminee"]
    lignes: tuple[dict, ...] = ()


async def eligibilite_adresse_exacte(
    session: AsyncSession,
    code_insee: str,
    *,
    numero: str,
    rep: str | None,
    nom_voie: str,
) -> EligibiliteAdresse:
    """Eligibility published for exactly this address (additive to
    :func:`eligibilite_par_adresse`, which keeps its other callers).

    The ``LIKE`` only bounds the candidates: « rue Victor Hugo » also brings
    « rue Victor Hugo prolongée ». The identity is then checked exactly on
    the number, the suffix and the whole street; technologies are aggregated
    only once every row is proven to describe the same building.
    """
    result = await session.execute(
        _CANDIDATS_ADRESSE,
        {
            "code_insee": code_insee,
            "numero": numero,
            "nom_voie": nom_voie,
            "limite": CANDIDATS_MAX,
        },
    )
    candidats = [dict(row._mapping) for row in result]
    if not candidats:
        return EligibiliteAdresse(etat="aucune")
    if len(candidats) >= CANDIDATS_MAX:
        return EligibiliteAdresse(etat="indeterminee")
    exactes = [
        ligne
        for ligne in candidats
        if _norme(ligne["voie"]) == _norme(nom_voie)
        and _norme(ligne["numero"]) == _norme(numero)
        and _norme(ligne["complement"]) == _norme(rep)
    ]
    if len({ligne["id_imb"] for ligne in exactes}) != 1:
        return EligibiliteAdresse(etat="indeterminee")
    return EligibiliteAdresse(etat="unique", lignes=tuple(exactes))

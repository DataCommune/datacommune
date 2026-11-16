"""Parcel relation of an address: the parcel that contains the BAN point.

One rule shared by the identity and every enrichment of the address page
(Ianis, 2026-10-02, N-02): the parcel is the one containing the BAN point,
read through :func:`app.cadastre.queries.parcelle_par_point` (every
candidate, no ``LIMIT 1``). The nearest-parcel fallback of ``/v1/resolve``
is never used here.

``ETAPES_PARCELLE`` is an ordered tuple of step functions. A step returns a
conclusive relation, or ``None`` for « no candidate »; when every step
returns ``None`` the relation is ``point_hors_parcelle``.

P-PARCELLE (open, Ianis): a step « parcel declared by the BAN » would be
appended after :func:`etape_point` with its own ``rattachement`` value;
nothing of it is coded in phase 4.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.schemas import RelationParcelle
from app.cadastre.queries import parcelle_par_point
from app.resolve import telemetry
from app.scope.dependencies import RouteHosting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PointBan:
    """Internal BAN point of an address; never serialized."""

    lon: float
    lat: float


EtapeParcelle = Callable[[AsyncSession, PointBan], Awaitable[RelationParcelle | None]]


async def etape_point(
    session: AsyncSession, point: PointBan
) -> RelationParcelle | None:
    """Parcels containing the BAN point: one, several, or no candidate."""
    candidats = await parcelle_par_point(session, point.lat, point.lon)
    if not candidats:
        return None
    if len(candidats) == 1:
        return RelationParcelle(
            etat="unique",
            id_parcelle=candidats[0]["id_parcelle"],
            nombre_candidats=1,
            rattachement="parcelle_du_point",
        )
    return RelationParcelle(
        etat="ambigue", nombre_candidats=len(candidats), motif="parcelles_multiples"
    )


ETAPES_PARCELLE: tuple[EtapeParcelle, ...] = (etape_point,)


async def resoudre_parcelle(
    session: AsyncSession, point: PointBan, hosting: RouteHosting
) -> RelationParcelle:
    """Parcel relation of *point*, cadastre read only where it is hosted.

    The steps run in a savepoint: a cadastre failure gives ``indisponible``
    and never breaks the identity.
    """
    cadastre = hosting.hostings["cadastre"]
    if cadastre.etat != "heberge":
        return RelationParcelle(etat="hors_couverture", motif=cadastre.motif)
    try:
        async with session.begin_nested():
            for etape in ETAPES_PARCELLE:
                relation = await etape(session, point)
                if relation is not None:
                    return relation
    except Exception as exc:  # noqa: BLE001
        telemetry.emit_resolve_event(
            logger,
            "address_parcel_read_failed",
            level=logging.WARNING,
            error_code=telemetry.error_code_for(exc),
        )
        return RelationParcelle(etat="indisponible")
    return RelationParcelle(etat="point_hors_parcelle", motif="point_hors_parcelle")


@dataclass(frozen=True)
class BatimentsParcelle:
    """Buildings on one parcel: all of them, and those not declared annexes."""

    total: int
    hors_annexe: int


# A building is on the parcel when the parcel covers its point on surface
# (same rule as block J of ``scripts/mesure_dpe_documents.py``). Every
# building is counted, without LIMIT; an unknown use (NULL) is not an annex
# (P-D110-NULL, Ianis 2026-10-02). ``batiments_par_point`` (nearest, LIMIT)
# is never used for a count. The buildings partition is taken from the bound
# ``:department``, never from the CTE: through the CTE the planner keeps the
# 103 partitions of ``batiments`` (and locks them with their indexes), which
# saturated the CPU and the lock table in the k6 firing of 2026-11-02.
_BATIMENTS_SUR_PARCELLE = text("""
    WITH parcelle AS (
        SELECT p.geometrie
        FROM parcelles AS p
        WHERE p.id_parcelle = :id_parcelle
          AND p.code_departement = :department
    )
    SELECT (SELECT count(*) FROM parcelle) AS parcelles,
           count(b.cleabs) AS total,
           count(b.cleabs) FILTER (
               WHERE b.usage_1 IS DISTINCT FROM 'Annexe'
           ) AS hors_annexe
    FROM parcelle AS p
    JOIN batiments AS b
      ON b.code_departement = :department
     AND ST_Intersects(b.geometrie, p.geometrie)
     AND ST_Covers(p.geometrie, ST_PointOnSurface(b.geometrie))
""")


def departement_de_parcelle(id_parcelle: str) -> str:
    """Partition of a parcel id: three characters overseas, else two."""
    return id_parcelle[:3] if id_parcelle.startswith("97") else id_parcelle[:2]


async def batiments_sur_parcelle(
    session: AsyncSession, id_parcelle: str
) -> BatimentsParcelle | None:
    """Count of the buildings on *id_parcelle*; None when the parcel is not
    one row of the cadastre (unknown, never zero).

    Call it inside its own savepoint (``app.adresse.blocs.lire_bloc``): a
    failure of the buildings relation must leave the other blocks served.
    Reused by 04-06 (« {n} bâtiments sur la parcelle »).
    """
    result = await session.execute(
        _BATIMENTS_SUR_PARCELLE,
        {
            "id_parcelle": id_parcelle,
            "department": departement_de_parcelle(id_parcelle),
        },
    )
    rows = result.mappings().all()
    if len(rows) != 1 or rows[0]["parcelles"] != 1:
        return None
    return BatimentsParcelle(
        total=int(rows[0]["total"]), hors_annexe=int(rows[0]["hors_annexe"])
    )

"""One guarded read per block of the address page (04-03, N-01, N-10, N-14).

Every block of ``/v1/adresse/{ban_id}`` reads its source in its own
savepoint: under PostgreSQL a failed statement aborts the whole transaction,
so a block without savepoint would take the identity and the other blocks
down with it. A journaled source (``dpe``, ``dvf``, ``connectivite``) is
guarded by :func:`guard_publication` inside that savepoint, never by a
route-wide ``require_publication``: a refused publication turns only its
block into ``temporary_error``.

Reused by 04-05, 04-06, 04-07 and 04-11.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.schemas import SourceValeur
from app.common.exceptions import UpstreamUnavailableError
from app.common.imported_tables import IMPORTED_TABLES, publication_actee
from app.common.publication_readiness import (
    PUBLICATION_DATASETS,
    PUBLICATION_NOT_VALIDATED,
    guard_publication,
)
from app.common.schemas import OutOfCoverageReason
from app.common.source_catalog import DATASET_CATALOG

# C14 period helpers live with the facts, shared with the commune comparator
# (C28); re-exported here for the address readers.
from app.datasets.provenance import (
    ProvenanceFacts,
)
from app.datasets.provenance import (
    jour_d_import as jour_d_import,
)
from app.datasets.provenance import (
    periode_de_source as periode_de_source,
)
from app.datasets.queries import get_provenance_facts
from app.resolve import telemetry

logger = logging.getLogger(__name__)

# Sources whose publication is journaled in ``etl_publications``: the same
# set the source routes guard (``require_publication``). The address page
# reads ``dpe``, ``dvf`` and ``connectivite`` of them; the address report
# also reads the communal ones (04.7-01). The BAN, the cadastre, Géorisques
# and the commune synthesis (which carries its own barrier) take no lock.
SOURCES_JOURNALISEES = PUBLICATION_DATASETS

CausePanne = Literal[
    "publication_non_validee", "lecture_indisponible", "amont_indisponible"
]


# A disc around the BAN point that reaches a departement this instance does not
# host (C16, final review API WR-02): the hosted tables only hold the hosted
# departements, so a list or a count read in that disc is partial. It carries
# ``liste_incomplete`` and this coverage reason (D-24), never a complete count
# nor a zero. The bounding-box margin (degrees) covers the radius up to 60° of
# latitude; the distance itself is geodesic.
MOTIF_HORS_PERIMETRE = OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE.value
_RAYON_DEBORDE_SQL = text("""
    SELECT EXISTS (
        SELECT 1 FROM geo_departements g
        WHERE g.code <> ALL(CAST(:heberges AS text[]))
          AND g.geometry && ST_Expand(
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :marge
          )
          AND ST_DWithin(
              g.geometry::geography,
              ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
              :rayon
          )
    )
""")


async def rayon_deborde(
    session: AsyncSession,
    lon: float,
    lat: float,
    rayon_m: float,
    heberges: Sequence[str],
) -> bool:
    """Whether the disc of *rayon_m* around the point reaches a departement
    outside *heberges* (C16). Called inside the reader's block: a failed read
    is the outage of that block, never a complete list."""
    resultat = await session.execute(
        _RAYON_DEBORDE_SQL,
        {
            "lon": lon,
            "lat": lat,
            "rayon": float(rayon_m),
            "marge": float(rayon_m) / 50_000,
            "heberges": list(heberges),
        },
    )
    return bool(resultat.scalar_one())


def source_valeur(cle: str) -> SourceValeur:
    """Source of a served value, from the catalogue entry *cle*."""
    entry = DATASET_CATALOG[cle]
    return SourceValeur(
        cle=cle, nom=entry.nom, producteur=entry.producteur, url=entry.url
    )


async def faits_de_provenance(
    session: AsyncSession, cle: str
) -> ProvenanceFacts | None:
    """Period and import facts of *cle*; None when unknown or unreadable.

    Read in its own savepoint: a metadata failure degrades to an unknown
    period, never to an outage of the value it qualifies.
    """
    try:
        async with session.begin_nested():
            facts = await get_provenance_facts(session, [cle])
    except SQLAlchemyError:
        return None
    return facts.get(cle)


@dataclass(frozen=True)
class Bloc[T]:
    """Outcome of one block: its value, or the bounded cause of its outage."""

    valeur: T | None = None
    panne: CausePanne | None = None

    @property
    def en_panne(self) -> bool:
        return self.panne is not None


async def lire_bloc[T](
    session: AsyncSession,
    sources: Sequence[str],
    lecteur: Callable[[], Awaitable[T]],
) -> Bloc[T]:
    """Run *lecteur* in its own savepoint, after the guard of its sources.

    A refused publication (503 of the guard), a SQL error or an upstream
    outage gives a block in outage; nothing propagates to the other blocks.
    Only bounded codes are logged (PRIV-01): never a key, a point or a
    message of the database.
    """
    journalisees = [source for source in sources if source in SOURCES_JOURNALISEES]
    importees = [IMPORTED_TABLES[s] for s in sources if s in IMPORTED_TABLES]
    dataset = sources[0] if sources else "aucune"
    try:
        async with session.begin_nested():
            if journalisees:
                await guard_publication(session, journalisees)
            if importees and not await publication_actee(session, importees):
                raise HTTPException(503, PUBLICATION_NOT_VALIDATED)
            return Bloc(valeur=await lecteur())
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        cause: CausePanne = "publication_non_validee"
        error = "publication_refused"
    except SQLAlchemyError as exc:
        cause = "lecture_indisponible"
        error = telemetry.error_code_for(exc)
    except UpstreamUnavailableError as exc:
        cause = "amont_indisponible"
        error = telemetry.error_code_for(exc)
    telemetry.emit_resolve_event(
        logger,
        "address_block_unavailable",
        level=logging.WARNING,
        dataset=dataset,
        outcome=cause,
        error_code=error,
    )
    return Bloc(panne=cause)

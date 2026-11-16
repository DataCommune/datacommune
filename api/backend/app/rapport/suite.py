"""Upstream reads of a view served in two steps (R-07, 2026-11-10).

A first view of an address used to wait for its slowest upstream read, up to
the deadline of the page (5 s), then wrote an outage on a source that
answered two seconds later (ERRIAL: 7 s cold). The page now asks for a short
wait: the report is served after ``ATTENTE_COURTE_S`` with the themes still
being read said ``en_cours``, the reads go on here, and the follow-up of the
page waits for them. Nothing is asked twice to a producer: the follow-up
joins the tasks of the first view.

``ATTENTE_COURTE_S`` is the knee of the measured first views (four cold
addresses, 29 readers: 16 to 20 have ended at 1.5 s, 17 to 22 at 2 s, then
nothing until 2.4 s). ``ECHEANCE_SUITE_S`` is the end of the wait, above the
slowest read measured cold (ERRIAL, 7.3 s) and under the background bound of
``CacheAmont`` (15 s): past it, a read is an outage.

The reads of an address are kept ``DUREE_S`` at most, by the key of the
report cache, in this process (one process serves the API, audit PERF-06).
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from cachetools import TTLCache  # type: ignore[import-untyped]

from app.rapport import partage
from app.rapport.socle import ContexteAmont, DefinitionTheme, PanneAmont
from app.resolve import telemetry

logger = logging.getLogger(__name__)

ATTENTE_COURTE_S = 1.5
ECHEANCE_SUITE_S = 12.0
DUREE_S = 30.0
TAILLE_MAX = 1024


@dataclass(frozen=True)
class EnCours:
    """The upstream read of a theme has not ended yet."""


EN_COURS = EnCours()


async def _lire(definition: DefinitionTheme, contexte: ContexteAmont) -> object:
    """The answer of one reader; an error is its outage, as ``lire_amonts``."""
    assert definition.amont is not None
    try:
        return await definition.amont(contexte)
    except Exception as exc:  # noqa: BLE001 - one row in outage, never the report
        # Bounded code only (PRIV-01): never the key, the point or a message.
        telemetry.emit_resolve_event(
            logger,
            "address_report_theme_failed",
            level=logging.WARNING,
            dataset=definition.id,
            outcome="amont_indisponible",
            error_code=telemetry.error_code_for(exc),
        )
        return PanneAmont()


class Lectures:
    """The upstream reads of one address, started once, awaited by its views."""

    def __init__(
        self, definitions: Sequence[DefinitionTheme], contexte: ContexteAmont
    ) -> None:
        # The tasks copy the context at their creation: the readers of
        # ``partage`` bound themselves by the end of the wait, not by the
        # deadline of a page that no longer waits for them.
        jeton = partage.ECHEANCE.set(ECHEANCE_SUITE_S)
        try:
            self._taches = {
                d.id: asyncio.ensure_future(_lire(d, contexte))
                for d in definitions
                if d.amont is not None
            }
        finally:
            partage.ECHEANCE.reset(jeton)

    def de_cette_boucle(self) -> bool:
        boucle = asyncio.get_running_loop()
        return all(t.get_loop() is boucle for t in self._taches.values())

    async def attendre(self, delai_s: float | None) -> dict[str, object]:
        """The answers by theme id after *delai_s* at most (None: all of
        them); a read that has not ended is ``EN_COURS``, and goes on."""
        en_vol = [t for t in self._taches.values() if not t.done()]
        if en_vol:
            # ``wait`` never cancels its tasks, even when its caller is.
            await asyncio.wait(en_vol, timeout=delai_s)
        return {
            id_: (
                EN_COURS
                if not tache.done()
                else PanneAmont()
                if tache.cancelled()
                else tache.result()
            )
            for id_, tache in self._taches.items()
        }

    def annuler(self) -> None:
        for tache in self._taches.values():
            if not tache.get_loop().is_closed():
                tache.cancel()


_lectures: TTLCache = TTLCache(maxsize=TAILLE_MAX, ttl=DUREE_S)


def obtenir(
    cle: object, definitions: Sequence[DefinitionTheme], contexte: ContexteAmont
) -> Lectures:
    """The reads in flight for *cle*, started if no view left any."""
    lectures = _lectures.get(cle)
    if lectures is None or not lectures.de_cette_boucle():
        lectures = _lectures[cle] = Lectures(definitions, contexte)
    return lectures


def oublier(cle: object) -> None:
    """Every view has its answers: the next one reads again (an outage never
    outlives its view)."""
    _lectures.pop(cle, None)


def vider() -> None:
    lectures = list(_lectures.values())
    _lectures.clear()
    for lecture in lectures:
        lecture.annuler()

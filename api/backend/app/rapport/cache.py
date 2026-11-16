"""In-process cache of the address reports (04.7-01, audit C18).

A report is kept ten minutes, keyed by the BAN key, the day it is computed
for and the hosting the route served: a change of scope or of day is a new
key. A report carrying an outage, even of one sub-source of a theme, is
never kept, so an outage never outlives its source; nor is a report
carrying a gap of the day (no ATMO index yet), nor one served while a read
was in progress (R-07). A new publication shows
after the TTL at worst.

The HTTP answer stays ``no-store``: the route is behind the API key, and a
shared cache (Cloudflare) would serve the report without it. One process
serves the API (audit PERF-06); a second worker would hold its own cache.
"""

from datetime import date

from cachetools import TTLCache  # type: ignore[import-untyped]

from app.rapport import suite
from app.rapport.schemas import RapportAdresse
from app.rapport.themes.qualite_air import MANQUES_DU_JOUR
from app.scope.dependencies import RouteHosting

TTL_S = 600
TAILLE_MAX = 2048

_cache: TTLCache = TTLCache(maxsize=TAILLE_MAX, ttl=TTL_S)

type CleCache = tuple[str, str, tuple]


def cle_cache(ban_id: str, as_of: date, hosting: RouteHosting) -> CleCache:
    signature = tuple(
        (cle, h.etat, h.motif, tuple(h.departements or ()))
        for cle, h in sorted(hosting.hostings.items())
    )
    return ban_id, as_of.isoformat(), signature


def lire(cle: CleCache) -> RapportAdresse | None:
    return _cache.get(cle)


def ecrire(cle: CleCache, rapport: RapportAdresse) -> None:
    """Keep *rapport* unless one of its themes is, even partly, an outage,
    a gap of the day, or a read in progress."""
    if any(
        theme.etat in ("panne", "en_cours")
        or theme.pannes_partielles
        or (theme.etat == "manque" and theme.motif in MANQUES_DU_JOUR)
        for theme in rapport.themes
    ):
        return
    _cache[cle] = rapport


def vider() -> None:
    _cache.clear()
    suite.vider()

"""Base map of the interactive map: Plan IGN tiles relayed by the server (04.10-04).

The visitor never talks to the IGN (T-01 of :mod:`app.carte.proxy`): the tile
is asked by the server, with a fresh client and the fixed headers of the static
map, and served on our origin.

This is an explicit relaxation of T-02 (« no free extent »): the visitor
chooses ``z``, ``x`` and ``y``. It is bounded four ways, and by nothing else:

1. the layer is fixed, with its host, style, format and matrix set: only the
   three indices vary in the outbound request;
2. the zoom is 16 to 19;
3. the indices lie inside the grid of their zoom (``0 <= x, y < 2**z``);
4. the tile lies in the hosted territory: its envelope, widened by the margin
   of the shore (``carte_fond_marge_deg``), touches a departement that hosts
   the BAN (:func:`dans_le_territoire`, asked by the route before the tile).

Bounds 2 and 3 are checked again here, where the request is built: no caller
can make this module ask for anything else. Bound 4 is the route's to ask: it
needs the scope and a connection, which the relay itself never holds.

Upstream parameters, copied from ``04.10-AMONT-IGN.md`` (plan 04.10-01, read on
2026-11-11 in the GetCapabilities of the service and in the terms of use)::

    RELAIS=autorise                  deduced from the terms and the licence
    LIMITE_PAR_SECONDE=non_publiee   CGU, annex 3, article 3.2
    URL_SERVICE=https://data.geopf.fr/wmts
    JEU_DE_MATRICES=PM_0_19          EPSG:3857, tiles of 256 x 256
    NIVEAUX=0-19

Layer ``GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2``, style ``normal``, format
``image/png`` (the only one announced), KVP encoding, no access key.
``TILEMATRIX`` is the zoom, ``TILECOL`` the x and ``TILEROW`` the y, origin at
the top left: no inversion of the y axis. The service address is a setting
(``carte_wmts_url``); the rest is fixed below.
"""

import asyncio
from typing import Final

from cachetools import TTLCache  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.carte.parcelles import _ENVELOPPE_SQL
from app.carte.proxy import (
    COUCHES,
    CarteAmontIndisponibleError,
    lire_png_borne,
    nouveau_cache,
    png_valide,
)
from app.config import settings
from app.scope.dependencies import route_hosting
from app.scope.model import InstanceScope

# The four fixed parameters of the layer (GetCapabilities, 04.10-AMONT-IGN.md § 4).
COUCHE: Final = COUCHES["plan"]
STYLE: Final = "normal"
FORMAT: Final = "image/png"
JEU_DE_MATRICES: Final = "PM_0_19"
# Zooms the map serves, inside the 0 to 19 of the matrix set.
ZOOM_MIN: Final = 16
ZOOM_MAX: Final = 19
# Side of a tile of the matrix set, in pixels.
TAILLE_TUILE_PX: Final = 256

# Global bucket on the calls actually made to the WMTS, apart from the one of
# the WMS (``carte:amont``): the two services have their own terms.
AMONT_BUCKET: Final = "carte:fond:amont"

Tuile = tuple[int, int, int]


class BudgetAmontEpuiseError(Exception):
    """The budget of calls to the WMTS is spent; ``reset`` seconds to wait."""

    def __init__(self, reset: int) -> None:
        super().__init__(reset)
        self.reset = reset


class TerritoireIndisponibleError(Exception):
    """The departements of a tile could not be read: an outage, not a refusal."""


# Departements the tile reaches once widened by the margin, from the
# referential (never from a dataset table). The envelope is the one of the
# parcel tiles; in degrees its edges follow meridians and parallels, so
# widening its box by the margin widens the tile itself. The widened box is a
# constant of the statement: the spatial index of the referential answers.
_DEPARTEMENTS_DU_FOND = text(f"""
    SELECT g.code
    FROM geo_departements g
    WHERE ST_Intersects(
        g.geometry,
        ST_Expand({_ENVELOPPE_SQL}, CAST(:marge AS double precision)))
    ORDER BY g.code
""")


async def dans_le_territoire(
    session: AsyncSession, scope: InstanceScope, z: int, x: int, y: int
) -> bool:
    """Whether the tile, widened by the margin, touches a departement hosting the BAN.

    One hosted departement among those reached is enough: a tile astride a
    boundary is served, and so is a tile of a neighbouring departement lying
    inside the margin. A tile reaching no departement at all (the open sea) is
    outside, unlike a tile of parcels, which is then served empty: here the
    question is whether to call the IGN, and nothing asks for it out there.
    Raises :class:`TerritoireIndisponibleError` when the referential cannot be
    read; the caller answers an outage, never a refusal nor a tile.
    """
    try:
        codes = (
            (
                await session.execute(
                    _DEPARTEMENTS_DU_FOND,
                    {"z": z, "x": x, "y": y, "marge": settings.carte_fond_marge_deg},
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        raise TerritoireIndisponibleError from exc
    return any(route_hosting(("ban",), scope, code).hosts("ban") for code in codes)


# Tiles by (z, x, y), bounded in bytes and apart from the cache of the static
# images: scrolling a map evicts tiles, never an image of an address page.
# Single event loop, no await between a lookup and a store: no lock needed.
CACHE: TTLCache = nouveau_cache(
    settings.carte_fond_cache_octets, settings.carte_cache_ttl_secondes
)
# Tiles that just failed: the same failure is answered for a short while
# without calling the IGN again. Never a tile, never for long.
ECHECS: TTLCache = TTLCache(
    maxsize=settings.carte_cache_negatif_entrees,
    ttl=settings.carte_fond_echec_ttl_secondes,
)
# Tiles being read right now: concurrent requests for one tile wait for the
# first one instead of each calling the IGN. Removed when it ends.
EN_COURS: dict[Tuile, asyncio.Future[bytes]] = {}


def parametres_wmts(z: int, x: int, y: int) -> dict[str, str]:
    """The whole GetTile query: fixed, but for the three bounded indices.

    Raises ``ValueError`` for a zoom outside 16 to 19 or an index outside the
    grid of the zoom: such a tile is never asked.
    """
    if not (ZOOM_MIN <= z <= ZOOM_MAX and 0 <= x < 2**z and 0 <= y < 2**z):
        raise ValueError("tuile hors de la grille servie")
    return {
        "SERVICE": "WMTS",
        "REQUEST": "GetTile",
        "VERSION": "1.0.0",
        "LAYER": COUCHE,
        "STYLE": STYLE,
        "FORMAT": FORMAT,
        "TILEMATRIXSET": JEU_DE_MATRICES,
        "TILEMATRIX": str(z),
        "TILEROW": str(y),
        "TILECOL": str(x),
    }


async def telecharger_tuile(z: int, x: int, y: int) -> bytes:
    """One GetTile, accepted only as a complete PNG of 256 x 256 and bounded size.

    Raises :class:`~app.carte.proxy.CarteAmontIndisponibleError` for whatever
    :func:`~app.carte.proxy.lire_png_borne` refuses (status, type, encoding,
    size, network) and for a body that is not that PNG.
    """
    corps = await lire_png_borne(
        settings.carte_wmts_url,
        parametres_wmts(z, x, y),
        settings.carte_fond_tuile_max_octets,
    )
    if not png_valide(corps, TAILLE_TUILE_PX):
        raise CarteAmontIndisponibleError
    return corps


async def obtenir_tuile(z: int, x: int, y: int) -> bytes:
    """The tile, from the cache or from one call shared by concurrent requests.

    In order: the cache; the memory of a recent failure
    (:class:`~app.carte.proxy.CarteAmontIndisponibleError`, without a call); a
    read already under way, whose outcome is shared; else one token of the
    upstream budget (:class:`BudgetAmontEpuiseError` when it is spent, which is
    not remembered as a failure) and one call. A read tile is kept; a failed
    one is remembered briefly. The caller holds no database connection while
    it waits here.
    """
    cle = (z, x, y)
    parametres_wmts(z, x, y)  # the bounds, before anything is looked up or kept
    image = CACHE.get(cle)
    if image is not None:
        return image
    if cle in ECHECS:
        raise CarteAmontIndisponibleError
    en_cours = EN_COURS.get(cle)
    if en_cours is not None:
        return await asyncio.shield(en_cours)
    futur: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    EN_COURS[cle] = futur
    try:
        image = await _produire(cle)
    except BaseException as exc:
        futur.set_exception(
            exc if isinstance(exc, Exception) else CarteAmontIndisponibleError()
        )
        futur.exception()  # nobody may be waiting: mark the error as retrieved
        raise
    else:
        futur.set_result(image)
        return image
    finally:
        EN_COURS.pop(cle, None)


async def _produire(cle: Tuile) -> bytes:
    """Spend one upstream token, call the IGN, keep the tile or its failure."""
    # Imported here: ``app.rate_limit`` and ``app.auth`` import each other, so a
    # module importing it at the top does not load on its own, as the registry
    # of explained statements loads this one.
    from app.rate_limit import _check_rate

    allowed, _, reset = _check_rate(AMONT_BUCKET, settings.carte_fond_rate_limit_amont)
    if not allowed:
        raise BudgetAmontEpuiseError(reset)
    try:
        image = await telecharger_tuile(*cle)
    except CarteAmontIndisponibleError:
        ECHECS[cle] = True
        raise
    CACHE[cle] = image
    return image

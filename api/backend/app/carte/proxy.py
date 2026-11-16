"""Outbound side of the map proxy: the WMS request, its acceptance, the cache.

The visitor never talks to the IGN. The server builds the whole request from
a BAN point it read itself, with a fresh client and fixed headers, so nothing
of the visitor (address, cookies, referrer, user agent) can reach the IGN and
nobody can steer the request (no free layer, extent, size or host: T-01, T-02).
"""

import asyncio
import math
import struct
import zlib
from typing import Final

import httpx
from cachetools import TTLCache  # type: ignore[import-untyped]

from app.config import settings

# Public layer name -> Geoplateforme layer (allowlist; probe of 2026-10-31).
COUCHES: Final[dict[str, str]] = {
    "plan": "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2",
    "parcelles": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS",
}

# Ground side of the extent, in metres, and the fixed size of the image.
COTE_M: Final = 300
TAILLE_PX: Final = 448
# WGS84 sphere radius of EPSG:3857.
_RAYON: Final = 6378137.0

# Fixed headers: the only ones sent to the IGN. httpx adds Host, Accept-Encoding
# and Connection itself; nothing is copied from the visitor's request.
# ``identity``: a compressed answer would be inflated in memory before any size check.
_EN_TETES: Final = {
    "User-Agent": "DataCommune (+https://datacommune.fr)",
    "Accept": "image/png",
    "Accept-Encoding": "identity",
}
# Short, like the other terrain services (app/rapport/partage.py).
TIMEOUT: Final = httpx.Timeout(4.0, connect=2.0)
_SIGNATURE_PNG: Final = b"\x89PNG\r\n\x1a\n"


class CarteAmontIndisponibleError(Exception):
    """The IGN did not answer with an acceptable image (outage, refusal, junk)."""


def nouveau_cache(octets: int, ttl_secondes: int) -> TTLCache:
    """A cache bounded by the total size of its images, not their number."""
    return TTLCache(maxsize=octets, ttl=ttl_secondes, getsizeof=len)


# Single event loop, no await between a lookup and a store: no lock needed.
CACHE: TTLCache = nouveau_cache(
    settings.carte_cache_octets, settings.carte_cache_ttl_secondes
)
# Unknown keys and street keys (ban_id -> problem code): a short, bounded 404 memory so
# that enumerating keys does not become one database read per request. Never an outage.
NEGATIF: TTLCache = TTLCache(
    maxsize=settings.carte_cache_negatif_entrees,
    ttl=settings.carte_cache_negatif_ttl_secondes,
)
# Misses being served right now, by (ban_id, couche): concurrent requests on one key
# wait for the first one instead of each calling the IGN. Removed when it ends.
EN_COURS: dict[tuple[str, str], asyncio.Future[bytes]] = {}


def emprise_3857(lon: float, lat: float) -> tuple[float, float, float, float]:
    """300 ground metres centred on the point, as an EPSG:3857 box.

    Web Mercator stretches distances by 1/cos(lat), so the half side is
    150/cos(lat) projected metres: the same 300 m on the ground in Challans and
    in Lille. Returns (xmin, ymin, xmax, ymax); EPSG:3857 is easting first, so
    the WMS 1.3.0 axis order is the plain one.
    """
    lat_rad = math.radians(lat)
    x = _RAYON * math.radians(lon)
    y = _RAYON * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    demi = (COTE_M / 2) / math.cos(lat_rad)
    return (x - demi, y - demi, x + demi, y + demi)


def parametres_wms(couche: str, lon: float, lat: float) -> dict[str, str]:
    """The whole GetMap query, from the allowlisted layer and the BAN point."""
    xmin, ymin, xmax, ymax = emprise_3857(lon, lat)
    parametres = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": COUCHES[couche],
        "STYLES": "",
        "CRS": "EPSG:3857",
        "BBOX": f"{xmin:.1f},{ymin:.1f},{xmax:.1f},{ymax:.1f}",
        "WIDTH": str(TAILLE_PX),
        "HEIGHT": str(TAILLE_PX),
        "FORMAT": "image/png",
    }
    if couche == "parcelles":
        # The layer is already transparent (probe of 31/10: identical bytes with and
        # without); the parameter makes the request say what the overlay needs.
        parametres["TRANSPARENT"] = "TRUE"
    return parametres


def _est_png(reponse: httpx.Response) -> bool:
    type_media = reponse.headers.get("content-type", "").split(";")[0].strip().lower()
    codage = reponse.headers.get("content-encoding", "identity").strip().lower()
    return (
        reponse.status_code == 200
        and type_media == "image/png"
        and codage == "identity"
    )


# Highest row filter of PNG (None, Sub, Up, Average, Paeth).
_FILTRE_MAX = 4
# Samples per pixel by PNG colour type (grey, RGB, palette, grey+alpha, RGBA).
_CANAUX = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _ligne(ihdr: bytes, taille: int) -> int | None:
    """Size of an inflated row the header implies; None for a header of
    another size than *taille* squared, an unknown colour type or depth, or an
    interlaced image."""
    if len(ihdr) != 13:
        return None
    largeur, hauteur, profondeur, couleur, _, _, entrelace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    canaux = _CANAUX.get(couleur)
    if (largeur, hauteur) != (taille, taille) or canaux is None:
        return None
    if profondeur not in (1, 2, 4, 8, 16) or entrelace != 0:
        return None
    # One filter byte, then the packed samples, per row: the size of a row.
    return 1 + -(-largeur * canaux * profondeur // 8)


def _pixels_complets(flux: bytes, ligne: int, lignes: int) -> bool:
    """The zlib stream inflates to exactly the *lignes* rows of the image, never
    more, and each row starts with a known filter (0 to 4)."""
    taille = lignes * ligne
    decompresseur = zlib.decompressobj()
    try:
        pixels = decompresseur.decompress(flux, taille + 1)
    except zlib.error:
        return False
    if len(pixels) != taille or not decompresseur.eof:
        return False
    return all(pixels[i] <= _FILTRE_MAX for i in range(0, taille, ligne))


def png_valide(corps: bytes, taille: int = TAILLE_PX) -> bool:
    """A complete PNG of the fixed size: nothing else is worth keeping 21 days.

    *taille* is the side the caller expects, in pixels: 448 for the static map,
    256 for a tile of the base map (:mod:`app.carte.tuiles`). Never taken from
    the answer itself.

    Signature, then well-bounded chunks with a right CRC: ``IHDR`` first (square
    of *taille*, not interlaced), at least one ``IDAT``, ``IEND`` last, nothing
    after it. The pixels are then inflated **bounded** to the exact size the
    header implies: a stream that does not decode, ends short or goes beyond is
    refused, and a bomb never expands past that size.
    """
    if not corps.startswith(_SIGNATURE_PNG):
        return False
    position = len(_SIGNATURE_PNG)
    fin = len(corps)
    premier = True
    idat = bytearray()
    ligne = 0
    while position + 12 <= fin:
        (longueur,) = struct.unpack_from(">I", corps, position)
        type_ = corps[position + 4 : position + 8]
        debut, suite = position + 8, position + 8 + longueur
        if suite + 4 > fin:
            return False
        (crc,) = struct.unpack_from(">I", corps, suite)
        if zlib.crc32(corps[position + 4 : suite]) != crc:
            return False
        if premier:
            rangee = _ligne(corps[debut:suite], taille) if type_ == b"IHDR" else None
            if rangee is None:
                return False
            ligne, premier = rangee, False
        elif type_ == b"IDAT":
            idat += corps[debut:suite]
        elif type_ == b"IEND":
            if longueur != 0 or not idat or suite + 4 != fin:
                return False
            return _pixels_complets(bytes(idat), ligne, taille)
        position = suite + 4
    return False


async def lire_png_borne(url: str, parametres: dict[str, str], limite: int) -> bytes:
    """One GET to the IGN with the fixed headers, read up to *limite* bytes.

    The only outbound call of the map (static images and tiles): a fresh client
    each time, so nothing of a previous request nor of the visitor travels with
    it. Raises :class:`CarteAmontIndisponibleError` on a non-200 answer, another
    content type or encoding, a body over the limit (read as a stream and
    abandoned at the limit, so a hostile answer never fills the memory), a
    timeout or any network error. A redirect is an answer, not something to
    follow. The body is not validated here: the caller knows the size it expects.
    """
    corps = bytearray()
    try:
        async with (
            httpx.AsyncClient(
                timeout=TIMEOUT, headers=_EN_TETES, follow_redirects=False
            ) as client,
            client.stream("GET", url, params=parametres) as reponse,
        ):
            if not _est_png(reponse):
                raise CarteAmontIndisponibleError
            async for morceau in reponse.aiter_bytes():
                # Before appending: an oversized chunk is never copied.
                if len(corps) + len(morceau) > limite:
                    raise CarteAmontIndisponibleError
                corps += morceau
    except httpx.HTTPError as exc:
        raise CarteAmontIndisponibleError from exc
    return bytes(corps)


async def telecharger_carte(couche: str, lon: float, lat: float) -> bytes:
    """One GetMap, accepted only as a complete PNG of 448 x 448 and bounded size.

    Raises :class:`CarteAmontIndisponibleError` for whatever
    :func:`lire_png_borne` refuses, and for a body that is not that PNG.
    """
    corps = await lire_png_borne(
        settings.carte_wms_url,
        parametres_wms(couche, lon, lat),
        settings.carte_image_max_octets,
    )
    if not png_valide(corps):
        raise CarteAmontIndisponibleError
    return corps

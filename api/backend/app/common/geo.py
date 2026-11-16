"""Geographic utilities for French administrative codes.

Handles arrondissement-to-commune normalization for Paris, Lyon, and
Marseille -- the three cities that use sub-municipal codes (arrondissements)
which differ from the commune INSEE code used in national datasets.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Paris 75101-75120 -> 75056
# Lyon 69381-69389 -> 69123
# Marseille 13201-13216 -> 13055
ARRONDISSEMENT_TO_COMMUNE: dict[str, str] = {
    **{f"751{i:02d}": "75056" for i in range(1, 21)},
    **{f"6938{i}": "69123" for i in range(1, 10)},
    **{f"132{i:02d}": "13055" for i in range(1, 17)},
}


def normalize_code_insee(code_insee: str) -> str:
    """Return the commune code for an arrondissement, or passthrough.

    Maps the 45 arrondissement codes (Paris 20, Lyon 9, Marseille 16)
    to their parent commune INSEE code.  All other codes are returned
    unchanged.
    """
    return ARRONDISSEMENT_TO_COMMUNE.get(code_insee, code_insee)


# Reverse mapping: commune code -> list of arrondissement codes
COMMUNE_TO_ARRONDISSEMENTS: dict[str, list[str]] = {
    "75056": [f"751{i:02d}" for i in range(1, 21)],
    "69123": [f"6938{i}" for i in range(1, 10)],
    "13055": [f"132{i:02d}" for i in range(1, 17)],
}


# Canonical commune codes for PLM cities (Paris, Lyon, Marseille).
# Used to short-circuit _detect_city when a user types the bareword
# city name without postal code. Required because communes_lookup
# stores only arrondissement rows ("Paris 1er Arrondissement", ...)
# and no plain "Paris" row — without this, Strategy A misses and
# Strategy B trigram-matches "Le Touquet-Paris-Plage" (62826).
PLM_CITY_ALIASES: dict[str, str] = {
    "paris": "75056",
    "lyon": "69123",
    "marseille": "13055",
}


def resolve_plm_alias(candidate: str) -> str | None:
    """Return the canonical commune code for a PLM bareword, else None.

    Case-insensitive, accent-insensitive enough for the 3 target tokens
    (none of "paris"/"lyon"/"marseille" carry accents). Returns None for
    anything else including "paris 1er", "lyon 3e", "le touquet-paris-plage",
    etc. -- those legitimately need the communes_lookup path.
    """
    return PLM_CITY_ALIASES.get(candidate.strip().lower())


def expand_code_insee(code_insee: str) -> list[str]:
    """Expand a commune code to include all arrondissement codes.

    For Paris (75056), Lyon (69123), and Marseille (13055), returns
    both the commune code and all arrondissement codes so queries
    match data stored at either level.  Arrondissement codes (e.g.
    75101) are returned as-is without expansion.  For all other
    codes, returns a single-element list.
    """
    arrondissements = COMMUNE_TO_ARRONDISSEMENTS.get(code_insee)
    if arrondissements:
        return [code_insee, *arrondissements]
    return [code_insee]


def zone_identity(code_insee: str) -> tuple[str, str | None]:
    """``(type_zone, commune_parent)`` of a code, from the static PLM mapping.

    ``("arrondissement", "75056")`` for 75112, ``("commune", None)`` for every
    other code, the commune 75056 included.
    """
    parent = ARRONDISSEMENT_TO_COMMUNE.get(code_insee)
    if parent is None:
        return "commune", None
    return "arrondissement", parent


def commune_and_its_arrondissement(codes: list[str]) -> tuple[str, str] | None:
    """First ``(commune, arrondissement)`` pair of *codes*, a whole and its part."""
    for code in codes:
        parent = ARRONDISSEMENT_TO_COMMUNE.get(code)
        if parent is not None and parent in codes:
            return parent, code
    return None


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    """Result of location resolution -- always a list of codes."""

    codes: list[str]
    lat: float | None
    lon: float | None
    mode: str  # "gps" or "code_insee"


def require_location_params(
    lat: float | None, lon: float | None, code_insee: str | None
) -> None:
    """Refuse a request naming no location (422), without any read."""
    if (lat is None or lon is None) and code_insee is None:
        raise HTTPException(
            status_code=422,
            detail="Au moins un parametre requis: (lat + lon) ou code_insee",
        )


async def resolve_location(
    session: AsyncSession,
    lat: float | None = None,
    lon: float | None = None,
    code_insee: str | None = None,
    *,
    expand: bool = True,
) -> ResolvedLocation:
    """Resolve GPS or code_insee into a list of commune/arrondissement codes.

    Raises HTTPException 422 if neither GPS nor code_insee is provided.
    When both are provided, code_insee takes precedence.

    ``expand=False`` reads a ``code_insee`` at its exact code (75056 stays
    ``['75056']``, not the 21 codes of Paris): a profile names one zone, and the
    first of 21 unordered rows could be an arrondissement (plan 04.7-09). The
    GPS path is the same in both modes.
    """
    require_location_params(lat, lon, code_insee)
    has_code = code_insee is not None

    # code_insee mode (preferred when both provided)
    if has_code:
        codes = (
            expand_code_insee(code_insee)  # type: ignore[arg-type]
            if expand
            else [code_insee]  # type: ignore[list-item]
        )
        return ResolvedLocation(codes=codes, lat=None, lon=None, mode="code_insee")

    resolved_code = await commune_at_point(session, lat, lon)  # type: ignore[arg-type]
    if resolved_code is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune commune trouvee pour ces coordonnees GPS",
        )
    codes = expand_code_insee(resolved_code)
    return ResolvedLocation(codes=codes, lat=lat, lon=lon, mode="gps")


# Commune of a point: the referential polygon covering it, never a dataset
# table (cadastre parcels are a source the instance may not host, D-23). The
# municipal arrondissement of Paris, Lyon or Marseille (geo_arrondissements,
# migration 099) wins over its commune: the point names one arrondissement,
# not the 21 codes expand_code_insee gives for 75056 (WINDOWS 39). ORDER BY
# makes a point on a shared border deterministic.
_COMMUNE_AT_POINT_SQL = text(
    "SELECT code FROM ("
    "SELECT a.code, 0 AS rank FROM geo_arrondissements a "
    "WHERE ST_Covers(a.geometry, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) "
    "UNION ALL "
    "SELECT g.code, 1 AS rank FROM geo_communes g "
    "WHERE ST_Covers(g.geometry, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"
    ") covering ORDER BY rank, code LIMIT 1"
)


async def commune_at_point(session: AsyncSession, lat: float, lon: float) -> str | None:
    """INSEE code of the arrondissement, else commune, covering the point."""
    row = (
        await session.execute(_COMMUNE_AT_POINT_SQL, {"lon": lon, "lat": lat})
    ).first()
    return None if row is None else row[0]

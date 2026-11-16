"""Quarantined query entrypoints; no SQL is executed (COR-09/13)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.unqualified_surfaces import refuse


async def encadrement_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
) -> list[dict]:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    refuse("encadrement_loyers")


async def encadrement_par_quartier(
    session: AsyncSession,
    ville: str,
    numero_quartier: int,
) -> list[dict]:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    refuse("encadrement_loyers")

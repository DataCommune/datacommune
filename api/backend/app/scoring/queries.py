"""Quarantined query entrypoints; no SQL is executed (COR-09/13)."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.unqualified_surfaces import refuse


async def scoring_renov_par_departement(
    session: AsyncSession,
    code_departement: str,
    etiquette_dpe: str | None = None,
    commune: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    refuse("scoring_renov")


async def query_scoring_export(
    session: AsyncSession,
    code_departement: str,
    etiquette_dpe: list[str] | None = None,
    code_commune: str | None = None,
    date_achat_min: date | None = None,
    date_achat_max: date | None = None,
    surface_min: float | None = None,
    limit: int = 5000,
) -> list[dict]:
    """Retired surface: preserve call compatibility, publish no unqualified values."""
    refuse("scoring_renov")

"""Public reads are quarantined, including legacy generations."""

from sqlalchemy.ext.asyncio import AsyncSession


async def permis_par_codes(
    session: AsyncSession, codes: list[str], limit: int = 50
) -> list[dict]:
    """Return no unqualified rows; callers MUST expose the quarantine state.
    This guard deliberately does not touch the table: historical date_depot
    values and missing source manifests cannot become public evidence.
    """
    return []

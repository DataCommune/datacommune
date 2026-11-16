"""Storage of the error reports: the table ``signalements`` (migration 114)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.signalement.schemas import SignalementDepot

_COMPTER_24H = text(
    "SELECT count(*) FROM signalements WHERE created_at > now() - interval '24 hours'"
)
# Retention is enforced on the write path too, so it never depends on a timer
# being installed where the table lives.
_PURGER = text(
    "DELETE FROM signalements WHERE created_at < now() - make_interval(days => :jours)"
)
_INSERER = text(
    """
    INSERT INTO signalements
        (reference, page, titre, lignes, source, version_site, largeur,
         user_agent, commentaire, email)
    VALUES
        (:reference, :page, :titre, :lignes, :source, :version_site, :largeur,
         :user_agent, :commentaire, :email)
    """
)


async def compter_24h(db: AsyncSession) -> int:
    """Reports received by the whole site in the last 24 hours."""
    return int((await db.execute(_COMPTER_24H)).scalar_one())


async def enregistrer(
    db: AsyncSession, depot: SignalementDepot, reference: str
) -> None:
    """Store one report and drop those past their retention, in one transaction."""
    await db.execute(_PURGER, {"jours": settings.signalement_conservation_jours})
    await db.execute(
        _INSERER, {"reference": reference, **depot.model_dump(exclude={"piege"})}
    )
    await db.commit()

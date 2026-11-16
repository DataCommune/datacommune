"""Boot-time schema drift gate (ADR-0003 D2).

Compares the DB's applied Alembic revision to the head embedded in the running
image. The semantics are deliberately ASYMMETRIC:

- DB == head            -> boot normally (nominal).
- DB behind / table     -> RuntimeError, fail fast. This is the /v1/dpe 500
  absent                   incident class (code ahead of an un-migrated DB):
                           turn a silent multi-day partial-500 into a franc,
                           dated boot failure that the liveness probe surfaces.
- DB ahead / unknown    -> WARNING + boot. Legitimate rollback path (restore a
  revision                 recent PBS snapshot under an older image): migrations
                           are additive, blocking this would make rollback
                           impossible.

Escape hatch: `ALLOW_SCHEMA_DRIFT=true` (never set in a compose file).
"""

import logging
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import settings

logger = logging.getLogger(__name__)

# app/common/migration_check.py -> parents[2] is the backend root that holds
# alembic.ini and migrations/ (both in the image at /app after `COPY . .`).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _load_script_directory() -> ScriptDirectory:
    """Build a ScriptDirectory from the embedded alembic config.

    script_location is forced to an absolute path so head resolution is
    independent of the process CWD (container runs from /app, tests from the
    repo root).
    """
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg)


async def _read_db_revisions(engine: AsyncEngine) -> set[str]:
    """Return the revision(s) stored in alembic_version, or an empty set.

    An absent table (fresh or incompletely migrated DB) is reported as an empty
    set, which _evaluate_revisions treats as "behind".
    """
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        except ProgrammingError:
            return set()
        return set(result.scalars().all())


def _evaluate_revisions(db_revs: set[str], script: ScriptDirectory) -> None:
    """Apply the ADR-0003 D2 decision table. Raises on blocking drift."""
    heads = set(script.get_heads())
    if db_revs and db_revs == heads:
        return  # nominal: DB at head

    known = {rev.revision for rev in script.walk_revisions()}
    head_str = ",".join(sorted(heads))
    db_str = ",".join(sorted(db_revs)) if db_revs else "(aucune)"

    # DB ahead / unknown revision -> legitimate rollback path (additive migrations).
    if db_revs and not (db_revs <= known):
        logger.warning(
            "Derive de schema TOLEREE (boot autorise): base a la revision %s, "
            "inconnue de l'historique embarque (head=%s). Chemin rollback probable "
            "(restore d'un snapshot recent sous une image plus ancienne). "
            "Verifiez que c'est intentionnel.",
            db_str,
            head_str,
        )
        return

    # DB behind (known revision != head) or alembic_version absent -> fail fast.
    raise RuntimeError(
        f"Derive de schema BLOQUANTE: la base est a la revision {db_str} mais le "
        f"code attend {head_str}. Jouez les migrations avant de demarrer l'API:\n"
        "  docker compose -p datacommune-api -f docker-compose.yml "
        "-f docker-compose.vps.yml run --rm api alembic upgrade head\n"
        "Override exceptionnel (rollback/maintenance): ALLOW_SCHEMA_DRIFT=true."
    )


async def check_migrations_current(engine: AsyncEngine) -> None:
    """Fail fast at boot if the DB schema is behind the code (ADR-0003 D2)."""
    if settings.allow_schema_drift:
        logger.warning(
            "ALLOW_SCHEMA_DRIFT=true — gate migrations desactive (operation "
            "exceptionnelle). Ne jamais poser ce flag dans un fichier compose."
        )
        return
    db_revs = await _read_db_revisions(engine)
    _evaluate_revisions(db_revs, _load_script_directory())

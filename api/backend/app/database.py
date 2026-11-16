from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# Server settings of every connection of the API.
# Hard cap on every query served by get_db(). A single slow query (unselective
# department filter, dense-table radius, stale post-ETL plan) would otherwise
# hold a pool connection until the client gives up; a few in parallel exhaust
# the pool and take the whole API down. 15s is well above any healthy read; a
# route that needs a tighter bound overrides statement_timeout on its own
# session.
# No JIT: an API read never runs long enough to repay its LLVM compilation
# (k6 firing of 2026-11-02: 0.35 s per call on the transports, 2.2 s on the
# buildings of a parcel, 0.12 s on the isolated sales once those were fixed).
# Ianis, 2026-11-02. The ETL (psycopg, its own DSN) keeps the server default.
CONNECT_ARGS = {"server_settings": {"statement_timeout": "15000", "jit": "off"}}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    # PRIV-01 (CR-01, C58): a statement error never writes its bound
    # parameters (an address typed, a key) into its message, hence into the
    # logs of the 500 path; the SQL echo of DEBUG hides them too.
    hide_parameters=True,
    connect_args=CONNECT_ARGS,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with async_session() as session:
        yield session

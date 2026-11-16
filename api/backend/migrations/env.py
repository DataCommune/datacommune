from logging.config import fileConfig

from alembic import context
from geoalchemy2.alembic_helpers import include_object, render_item, writer
from sqlalchemy import engine_from_config, pool, text

from app.config import settings

# Alembic creates alembic_version with version_num VARCHAR(32) by default,
# but several revision ids exceed 32 chars (e.g.
# "059_commune_synthese_add_nom_dept_region" = 40), which breaks
# `alembic upgrade head` on a fresh database. Pre-creating the table with a
# wide column sidesteps the default-width creation. Idempotent: a no-op once
# the table exists.
_PRECREATE_VERSION_TABLE = text(
    "CREATE TABLE IF NOT EXISTS alembic_version ("
    "version_num VARCHAR(255) NOT NULL, "
    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
)

# Alembic Config object
config = context.config

# Override sqlalchemy.url with sync version of the DATABASE_URL
# Alembic uses sync driver, so replace asyncpg with psycopg2/default
sync_url = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql://"
)
config.set_main_option("sqlalchemy.url", sync_url)

# Setup Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No declarative Base yet -- will be set when models are defined
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        process_revision_directives=writer,
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure alembic_version is wide enough before Alembic auto-creates
        # it at VARCHAR(32) (see _PRECREATE_VERSION_TABLE).
        connection.execute(_PRECREATE_VERSION_TABLE)
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            process_revision_directives=writer,
            render_item=render_item,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

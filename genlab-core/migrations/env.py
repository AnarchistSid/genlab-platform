"""Alembic env.py — reads POSTGRES_PASSWORD from environment."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Inject POSTGRES_PASSWORD from environment into the config
# so %(POSTGRES_PASSWORD)s in alembic.ini resolves correctly.
pg_password = os.environ["POSTGRES_PASSWORD"]
config.set_section_option(config.config_ini_section, "POSTGRES_PASSWORD", pg_password)

# 2026-06-29: allow DATABASE_URL to fully override the sqlalchemy.url ini
# value. Needed for the self-hosted CI runner — prod's postgres binds the
# host's :5432 (genlab-postgres docker container), so the GH Actions
# service container has to publish on :15432 to avoid a conflict; the
# `alembic.ini` URL is hardcoded to :5432 and was attempting to connect
# to prod's DB (auth failed because the password differs). With this
# override, the CI step sets ``DATABASE_URL=postgresql+psycopg://...:15432/...``
# and the migration applies against the ephemeral CI postgres.
_db_url_override = os.environ.get("DATABASE_URL")
if _db_url_override:
    # SQLAlchemy expects the ``+psycopg`` dialect prefix (psycopg3); upgrade
    # bare ``postgresql://`` URLs from libpq-style env values transparently.
    if _db_url_override.startswith("postgresql://"):
        _db_url_override = "postgresql+psycopg://" + _db_url_override[len("postgresql://") :]
    config.set_main_option("sqlalchemy.url", _db_url_override)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata is intentionally None — GenLab uses hand-written migrations
# rather than Alembic autogenerate.  Setting this to a SQLAlchemy Base.metadata
# would enable `alembic revision --autogenerate`, but the project prefers
# explicit migration scripts for tighter control over schema changes.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Alembic environment configuration."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url() -> str:
    """Return the DB URL from the environment.

    Consumed directly (offline) or injected into the engine-config dict
    (online) -- never via ``config.set_main_option``, which routes through
    ConfigParser and would treat the ``%`` characters in an RDS IAM auth
    token as interpolation syntax (``ValueError: invalid interpolation
    syntax``). The master password used during the bastion bootstrap has no
    ``%``, which is why this only surfaced on the IAM-authenticated migrator
    Lambda.
    """
    return os.environ.get("DATABASE_URL", "postgresql://localhost/bdo")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Build the engine config from the [alembic] section, then inject the URL
    # into the plain dict so the IAM token's '%'-encoded bytes bypass
    # ConfigParser interpolation (see _database_url).
    configuration = dict(config.get_section(config.config_ini_section, {}) or {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
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

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401
from app.config import get_settings
from app.database import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig's default True disables every
    # logger created before migrations run (e.g. mangaflow.* when the API
    # process or the offline suite touches alembic), silencing app logging for
    # the rest of the process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
if config.attributes.get("connection") is None:
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_on_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Programmatic callers may supply an already isolated connection. Keep its
    # schema/transaction and lifecycle; never create a second default connection.
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_on_connection(supplied_connection)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            _run_on_connection(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Owned PostgreSQL schema lifecycle for the explicitly enabled live harness."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from scripts.acceptance_safety import validate_safe_acceptance_pg_url

ROOT = Path(__file__).resolve().parents[2]


def _drop_owned_schema(admin: Engine, schema: str, token: str) -> None:
    # Names and tokens come only from uuid4 below, not URL/user input.
    if schema != f"acceptance_{token}" or len(token) != 32:
        raise ValueError("Invalid acceptance schema ownership")
    if any(character not in "0123456789abcdef" for character in token):
        raise ValueError("Invalid acceptance schema token")
    with admin.begin() as connection:
        owner = connection.scalar(
            text(
                "SELECT obj_description(oid, 'pg_namespace') "
                "FROM pg_namespace WHERE nspname = :schema"
            ),
            {"schema": schema},
        )
        if owner != f"mangaflow-acceptance:{token}":
            raise RuntimeError("Refusing schema cleanup: ownership marker is missing or changed")
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


@contextmanager
def isolated_postgres_schema(admin: Engine) -> Iterator[tuple[Engine, str]]:
    if admin.dialect.name != "postgresql":
        raise ValueError("This live fixture requires the PostgreSQL dialect")
    validate_safe_acceptance_pg_url(admin.url.render_as_string(hide_password=False))
    token = uuid4().hex
    schema = f"acceptance_{token}"
    # Creation and ownership marking commit together. A preexisting schema fails
    # CREATE; it can never become owned through IF NOT EXISTS.
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(
            f"COMMENT ON SCHEMA \"{schema}\" IS 'mangaflow-acceptance:{token}'"
        )

    engine = None
    try:
        # Server startup options apply to every new connection, before dialect
        # initialization. No connect listener, shared pool, or public search path.
        engine = create_engine(
            admin.url,
            connect_args={
                "options": (
                    f"-csearch_path={schema},pg_catalog "
                    "-clock_timeout=5000 -cstatement_timeout=30000"
                )
            },
            pool_pre_ping=True,
        )
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_schema()")) != schema:
                raise RuntimeError("PostgreSQL schema isolation was not applied")
            config = Config(str(ROOT / "apps" / "api" / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine, schema
    finally:
        try:
            if engine is not None:
                engine.dispose()
        finally:
            # Also runs when engine construction or migration fails. Cleanup
            # errors are surfaced rather than silently reported as success.
            _drop_owned_schema(admin, schema, token)

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from scripts.acceptance_safety import (
    mask_url,
    validate_safe_acceptance_pg_url,
    validate_safe_acceptance_redis_url,
)
from tests.integration.postgres_resources import isolated_postgres_schema
from tests.integration.redis_resources import RedisAcceptanceResources


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live-integration",
        action="store_true",
        default=False,
        help="Run live integration tests against real PostgreSQL and Redis instances.",
    )
    parser.addoption(
        "--pg-url",
        action="store",
        default=os.getenv(
            "MANGAFLOW_ACCEPTANCE_PG_URL",
            "",
        ),
        help="Isolated PostgreSQL URL: loopback:55432, acceptance database, no query.",
    )
    parser.addoption(
        "--redis-url",
        action="store",
        default=os.getenv(
            "MANGAFLOW_ACCEPTANCE_REDIS_URL",
            "",
        ),
        help="Isolated Redis URL: loopback:56379, DB 1..15, no query.",
    )


@pytest.fixture(scope="session")
def live_integration_enabled(request: pytest.FixtureRequest) -> bool:
    # Live execution stays opt-in: default runs skip with NOT RUN, and an
    # explicit opt-in with unavailable services fails loudly (masked) instead
    # of silently reporting success.
    return bool(
        request.config.getoption("--run-live-integration")
        or os.getenv("MANGAFLOW_ENABLE_LIVE_INTEGRATION", "").lower() in {"1", "true", "yes"}
    )


@pytest.fixture(scope="session")
def live_pg_url(request: pytest.FixtureRequest, live_integration_enabled: bool) -> str | None:
    if not live_integration_enabled:
        return None
    url = request.config.getoption("--pg-url")
    return validate_safe_acceptance_pg_url(url)


@pytest.fixture(scope="session")
def live_redis_url(request: pytest.FixtureRequest, live_integration_enabled: bool) -> str | None:
    if not live_integration_enabled:
        return None
    url = request.config.getoption("--redis-url")
    return validate_safe_acceptance_redis_url(url)


@pytest.fixture(scope="session")
def live_postgres_admin_engine(
    live_pg_url: str | None, live_integration_enabled: bool
) -> Generator[Engine, None, None]:
    if not live_integration_enabled or not live_pg_url:
        pytest.skip("Live PostgreSQL acceptance is not requested; NOT RUN.")

    masked = mask_url(live_pg_url)
    engine = None
    try:
        engine = create_engine(
            live_pg_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        if engine is not None:
            engine.dispose()
        pytest.fail(f"Live PostgreSQL connection failed at {masked}: {type(exc).__name__}.")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def live_pg_isolated_schema(
    live_postgres_admin_engine: Engine,
) -> Generator[tuple[Engine, str], None, None]:
    with isolated_postgres_schema(live_postgres_admin_engine) as resource:
        yield resource


@pytest.fixture
def live_pg_session_factory(live_pg_isolated_schema: tuple[Engine, str]) -> sessionmaker[Session]:
    engine, _schema = live_pg_isolated_schema
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session")
def live_redis_connection(
    live_redis_url: str | None, live_integration_enabled: bool
) -> Generator[Any, None, None]:
    if not live_integration_enabled or not live_redis_url:
        pytest.skip("Live Redis/RQ acceptance is not requested; NOT RUN.")

    masked = mask_url(live_redis_url)
    client = None
    try:
        from redis import Redis

        client = Redis.from_url(
            live_redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
    except Exception as exc:
        if client is not None:
            client.close()
        pytest.fail(f"Live Redis connection failed at {masked}: {type(exc).__name__}.")

    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def live_redis_resource_tracker(
    live_redis_connection: Any,
) -> Generator[RedisAcceptanceResources, None, None]:
    resources = RedisAcceptanceResources(live_redis_connection)
    resources.claim()
    try:
        yield resources
    finally:
        resources.cleanup()

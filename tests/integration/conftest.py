from __future__ import annotations

import os
import urllib.parse
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base


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
            "postgresql+psycopg://mangaflow_test:mangaflow_test_pass@127.0.0.1:55432/mangaflow_acceptance",
        ),
        help="PostgreSQL connection URL for live acceptance testing (must be loopback 127.0.0.1:55432).",
    )
    parser.addoption(
        "--redis-url",
        action="store",
        default=os.getenv(
            "MANGAFLOW_ACCEPTANCE_REDIS_URL",
            "redis://:mangaflow_redis_test_pass@127.0.0.1:56379/15",
        ),
        help="Redis connection URL for live acceptance testing (must be loopback 127.0.0.1:56379/15).",
    )


def validate_safe_acceptance_pg_url(url: str) -> str:
    """Validate that the PostgreSQL URL strictly targets an isolated local loopback endpoint."""
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            f"Security Violation: Integration acceptance PostgreSQL URL must target local loopback (127.0.0.1), got {hostname}"
        )
    if parsed.port == 5432:
        # Default port 5432 warning: prefer isolated port 55432 to avoid colliding with default dev/prod DB
        pass
    return url


def validate_safe_acceptance_redis_url(url: str) -> str:
    """Validate that the Redis URL strictly targets an isolated local loopback endpoint with dedicated DB index."""
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            f"Security Violation: Integration acceptance Redis URL must target local loopback (127.0.0.1), got {hostname}"
        )
    # Check DB index from path: path is e.g. "/15"
    path_db = (parsed.path or "").strip("/")
    if not path_db or path_db == "0":
        raise ValueError(
            "Security Violation: Integration acceptance Redis must use an isolated non-zero DB index (e.g. /15) to prevent accidental data loss in DB 0."
        )
    return url


@pytest.fixture(scope="session")
def live_integration_enabled(request: pytest.FixtureRequest) -> bool:
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
def live_postgres_engine(
    live_pg_url: str | None, live_integration_enabled: bool
) -> Generator[Engine, None, None]:
    if not live_integration_enabled or not live_pg_url:
        pytest.skip(
            "Live PostgreSQL acceptance is skipped (pass --run-live-integration or set MANGAFLOW_ENABLE_LIVE_INTEGRATION=1 to run)."
        )

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
        pytest.skip(
            f"Live PostgreSQL server is not reachable at {live_pg_url}: {exc}. Ensure Docker/PostgreSQL is running on 127.0.0.1:55432."
        )

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        try:
            Base.metadata.drop_all(engine)
        except Exception:
            pass
        engine.dispose()


@pytest.fixture
def live_pg_session_factory(live_postgres_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=live_postgres_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def live_pg_db(live_pg_session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = live_pg_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session")
def live_redis_connection(
    live_redis_url: str | None, live_integration_enabled: bool
) -> Generator[Any, None, None]:
    if not live_integration_enabled or not live_redis_url:
        pytest.skip(
            "Live Redis/RQ acceptance is skipped (pass --run-live-integration or set MANGAFLOW_ENABLE_LIVE_INTEGRATION=1 to run)."
        )

    try:
        from redis import Redis

        client = Redis.from_url(
            live_redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
    except Exception as exc:
        pytest.skip(
            f"Live Redis server is not reachable at {live_redis_url}: {exc}. Ensure Redis is running on 127.0.0.1:56379."
        )

    try:
        yield client
    finally:
        try:
            # Only flush the isolated acceptance DB (e.g. DB 15), never flushall
            client.flushdb()
            client.close()
        except Exception:
            pass
from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from app.database import Base
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from scripts.acceptance_safety import (
    mask_url,
    validate_safe_acceptance_pg_url,
    validate_safe_acceptance_redis_url,
)


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
    enabled = bool(
        request.config.getoption("--run-live-integration")
        or os.getenv("MANGAFLOW_ENABLE_LIVE_INTEGRATION", "").lower() in {"1", "true", "yes"}
    )
    if enabled:
        pytest.fail(
            "BLOCKED: live harness is under lead repair; resource ownership and "
            "process acceptance are not ready. No service was connected.",
            pytrace=False,
        )
    return False


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
        pytest.fail(f"Live PostgreSQL connection failed at {masked}: {type(exc).__name__}.")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def live_pg_isolated_schema(
    live_postgres_admin_engine: Engine,
) -> Generator[tuple[Engine, str], None, None]:
    """Existing schema fixture; migration and failure cleanup still need repair."""
    schema_name = f"acceptance_{uuid.uuid4().hex[:8]}"
    with live_postgres_admin_engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        conn.commit()

    test_engine = create_engine(
        live_postgres_admin_engine.url,
        execution_options={"schema_translate_map": {None: schema_name}},
        pool_pre_ping=True,
    )

    try:
        Base.metadata.create_all(test_engine)
        yield test_engine, schema_name
    finally:
        test_engine.dispose()
        with live_postgres_admin_engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            conn.commit()


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
    try:
        from redis import Redis

        client = Redis.from_url(
            live_redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
    except Exception as exc:
        pytest.fail(
            f"Live Redis connection failed at {masked}: {type(exc).__name__}."
        )

    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def live_redis_resource_tracker(
    live_redis_connection: Any,
) -> Generator[dict[str, Any], None, None]:
    """Existing partial tracker; RQ resource cleanup still needs repair."""
    prefix = f"mangaflow:acceptance:{uuid.uuid4().hex[:8]}:"
    tracker = {
        "prefix": prefix,
        "queues": set(),
        "jobs": set(),
    }
    try:
        yield tracker
    finally:
        try:
            # 1. Clean up tracked queues
            for q_name in tracker["queues"]:
                live_redis_connection.delete(f"rq:queue:{q_name}")
                live_redis_connection.srem("rq:queues", q_name)
            # 2. Clean up tracked jobs
            for j_id in tracker["jobs"]:
                live_redis_connection.delete(f"rq:job:{j_id}")
            # 3. Clean up any application keys matching prefix
            app_keys = list(live_redis_connection.scan_iter(match=f"{prefix}*", count=100))
            if app_keys:
                live_redis_connection.delete(*app_keys)
        except Exception:
            pass

from __future__ import annotations

import os
import urllib.parse
import uuid
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
            "postgresql+psycopg://mangaflow_test:mangaflow_acceptance_pass_55432@127.0.0.1:55432/mangaflow_acceptance",
        ),
        help="PostgreSQL connection URL for live acceptance testing (must be loopback 127.0.0.1:55432 with mangaflow_acceptance database and NO query overrides).",
    )
    parser.addoption(
        "--redis-url",
        action="store",
        default=os.getenv(
            "MANGAFLOW_ACCEPTANCE_REDIS_URL",
            "redis://:mangaflow_acceptance_redis_pass_56379@127.0.0.1:56379/15",
        ),
        help="Redis connection URL for live acceptance testing (must be loopback 127.0.0.1:56379 with non-zero DB index /15 and NO query overrides).",
    )


def mask_url(url: str) -> str:
    """Mask credentials in URL strings to prevent sensitive credential leaks in logs or skip messages."""
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            auth, host = netloc.split("@", 1)
            if ":" in auth:
                user = auth.split(":", 1)[0]
                masked_netloc = f"{user}:***@{host}"
            else:
                masked_netloc = f"***@{host}"
        else:
            masked_netloc = netloc
        return urllib.parse.urlunparse(
            (parsed.scheme, masked_netloc, parsed.path, "", "", "")
        )
    except Exception:
        return "<masked-url>"


def validate_safe_acceptance_pg_url(url: str) -> str:
    """Validate that the PostgreSQL URL strictly targets an isolated local loopback endpoint on dedicated port 55432 and acceptance database without query overrides."""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if not (scheme == "postgresql" or scheme.startswith("postgresql+")):
        raise ValueError(f"Security Violation: Expected postgresql connection scheme, got '{scheme}'")

    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            f"Security Violation: Integration acceptance PostgreSQL URL must target local loopback (127.0.0.1/localhost), got '{hostname}'"
        )

    if parsed.port != 55432:
        raise ValueError(
            f"Security Violation: Integration acceptance PostgreSQL port must be 55432, got {parsed.port}. Default port 5432 is strictly prohibited."
        )

    dbname = (parsed.path or "").strip("/")
    if not dbname or not (dbname == "mangaflow_acceptance" or dbname.startswith("mangaflow_acceptance_")):
        raise ValueError(
            f"Security Violation: Target PostgreSQL database must start with 'mangaflow_acceptance', got '{dbname}'. Operating on arbitrary databases is prohibited."
        )

    # Strictly disallow all query parameters (prevent hostaddr, sslhost, dbname or other connection-hijacking overrides)
    if parsed.query:
        raise ValueError(
            f"Security Violation: Acceptance PostgreSQL URL must not contain query parameters, got '{parsed.query}'"
        )

    return url


def validate_safe_acceptance_redis_url(url: str) -> str:
    """Validate that the Redis URL strictly targets an isolated local loopback endpoint on dedicated port 56379 with an isolated non-zero DB index (1-15) and no query overrides."""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"redis", "rediss"}:
        raise ValueError(f"Security Violation: Expected redis connection scheme, got '{scheme}'")

    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            f"Security Violation: Integration acceptance Redis URL must target local loopback (127.0.0.1/localhost), got '{hostname}'"
        )

    if parsed.port != 56379:
        raise ValueError(
            f"Security Violation: Integration acceptance Redis port must be 56379, got {parsed.port}. Default port 6379 is strictly prohibited."
        )

    # Strictly disallow all query parameters (prevent ?db=0, ?host=... or ambiguous multi-db overrides)
    if parsed.query:
        raise ValueError(
            f"Security Violation: Acceptance Redis URL must not contain query parameters, got '{parsed.query}'"
        )

    path_str = (parsed.path or "").strip("/")
    if not path_str:
        raise ValueError("Security Violation: Redis URL must explicitly specify a database index in path (e.g. /15).")

    # Reject /00, /0, etc.
    if path_str == "0" or path_str == "00" or set(path_str) == {"0"}:
        raise ValueError("Security Violation: Redis DB 0 is strictly forbidden to prevent data loss.")

    try:
        db_index = int(path_str)
    except ValueError:
        raise ValueError(f"Security Violation: Invalid Redis database index '{path_str}'.")

    if db_index <= 0 or db_index > 15:
        raise ValueError(
            f"Security Violation: Redis acceptance database index must be between 1 and 15, got {db_index}."
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
def live_postgres_admin_engine(
    live_pg_url: str | None, live_integration_enabled: bool
) -> Generator[Engine, None, None]:
    if not live_integration_enabled or not live_pg_url:
        pytest.skip(
            "Live PostgreSQL acceptance is skipped (pass --run-live-integration or set MANGAFLOW_ENABLE_LIVE_INTEGRATION=1 to run)."
        )

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
        pytest.fail(
            f"Live PostgreSQL acceptance FAILED: Server is unreachable or driver missing at {masked}: {exc}."
        )

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def live_pg_isolated_schema(
    live_postgres_admin_engine: Engine,
) -> Generator[tuple[Engine, str], None, None]:
    """Create a random isolated schema per test function, run metadata DDL within it, and drop schema on finish."""
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
def live_pg_session_factory(
    live_pg_isolated_schema: tuple[Engine, str]
) -> sessionmaker[Session]:
    engine, _schema = live_pg_isolated_schema
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session")
def live_redis_connection(
    live_redis_url: str | None, live_integration_enabled: bool
) -> Generator[Any, None, None]:
    if not live_integration_enabled or not live_redis_url:
        pytest.skip(
            "Live Redis/RQ acceptance is skipped (pass --run-live-integration or set MANGAFLOW_ENABLE_LIVE_INTEGRATION=1 to run)."
        )

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
            f"Live Redis acceptance FAILED: Server is unreachable at {masked}: {exc}."
        )

    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:
            pass


@pytest.fixture
def live_redis_resource_tracker(
    live_redis_connection: Any,
) -> Generator[dict[str, Any], None, None]:
    """Track created queues and jobs specifically, cleaning up all corresponding RQ keys on teardown without flushdb."""
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
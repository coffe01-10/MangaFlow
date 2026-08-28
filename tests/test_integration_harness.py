from __future__ import annotations

import pytest

from tests.integration.conftest import (
    validate_safe_acceptance_pg_url,
    validate_safe_acceptance_redis_url,
)


def test_safe_acceptance_pg_url_allows_loopback():
    """Verify loopback PostgreSQL URLs are accepted."""
    valid_urls = [
        "postgresql+psycopg://user:pass@127.0.0.1:55432/test_db",
        "postgresql://user:pass@localhost:55432/test_db",
        "postgresql+psycopg://user:pass@[::1]:55432/test_db",
    ]
    for url in valid_urls:
        assert validate_safe_acceptance_pg_url(url) == url


def test_safe_acceptance_pg_url_blocks_remote_hosts():
    """Verify remote hosts are strictly rejected for PostgreSQL acceptance testing."""
    invalid_urls = [
        "postgresql://user:pass@192.168.1.100:55432/test_db",
        "postgresql://user:pass@db.production.internal:5432/mangaflow",
        "postgresql://user:pass@8.8.8.8:55432/test_db",
    ]
    for url in invalid_urls:
        with pytest.raises(ValueError, match="Security Violation"):
            validate_safe_acceptance_pg_url(url)


def test_safe_acceptance_redis_url_allows_isolated_loopback():
    """Verify loopback Redis URLs on non-zero DB index are accepted."""
    valid_urls = [
        "redis://:pass@127.0.0.1:56379/15",
        "redis://localhost:56379/1",
        "redis://:pass@[::1]:56379/9",
    ]
    for url in valid_urls:
        assert validate_safe_acceptance_redis_url(url) == url


def test_safe_acceptance_redis_url_blocks_remote_hosts():
    """Verify remote Redis hosts are strictly rejected."""
    invalid_urls = [
        "redis://192.168.1.50:6379/15",
        "redis://cache.production.internal:6379/15",
    ]
    for url in invalid_urls:
        with pytest.raises(ValueError, match="Security Violation"):
            validate_safe_acceptance_redis_url(url)


def test_safe_acceptance_redis_url_blocks_default_db_zero():
    """Verify Redis DB 0 is rejected to prevent colliding with development/default data."""
    invalid_urls = [
        "redis://127.0.0.1:56379/0",
        "redis://127.0.0.1:56379",
        "redis://localhost:56379/",
    ]
    for url in invalid_urls:
        with pytest.raises(ValueError, match="Security Violation"):
            validate_safe_acceptance_redis_url(url)
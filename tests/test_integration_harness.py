from __future__ import annotations

import pytest

from tests.integration.conftest import (
    mask_url,
    validate_safe_acceptance_pg_url,
    validate_safe_acceptance_redis_url,
)


def test_mask_url_hides_sensitive_credentials():
    """Verify password masking in URL strings."""
    pg_url = "postgresql+psycopg://myuser:secret_pass_123@127.0.0.1:55432/mangaflow_acceptance"
    masked_pg = mask_url(pg_url)
    assert "secret_pass_123" not in masked_pg
    assert "myuser:***@127.0.0.1:55432/mangaflow_acceptance" in masked_pg

    redis_url = "redis://:super_secret_token@127.0.0.1:56379/15"
    masked_redis = mask_url(redis_url)
    assert "super_secret_token" not in masked_redis
    assert "***@127.0.0.1:56379/15" in masked_redis


def test_safe_acceptance_pg_url_allows_valid_acceptance_endpoints():
    """Verify strictly valid acceptance PostgreSQL URLs are accepted."""
    valid_urls = [
        "postgresql+psycopg://user:pass@127.0.0.1:55432/mangaflow_acceptance",
        "postgresql+psycopg2://user:pass@localhost:55432/mangaflow_acceptance_sub",
        "postgresql://user:pass@[::1]:55432/mangaflow_acceptance",
    ]
    for url in valid_urls:
        assert validate_safe_acceptance_pg_url(url) == url


def test_safe_acceptance_pg_url_blocks_standard_and_remote_and_invalid_dbs():
    """Verify default port 5432, arbitrary DB names, remote hosts and query parameter overrides are strictly rejected."""
    invalid_cases = [
        # Standard development/production port 5432
        ("postgresql://user:pass@127.0.0.1:5432/mangaflow_acceptance", "must be 55432"),
        # Arbitrary or production database names
        ("postgresql://user:pass@127.0.0.1:55432/mangaflow", "must start with 'mangaflow_acceptance'"),
        ("postgresql://user:pass@127.0.0.1:55432/postgres", "must start with 'mangaflow_acceptance'"),
        ("postgresql://user:pass@127.0.0.1:55432/production_db", "must start with 'mangaflow_acceptance'"),
        # Remote hosts
        ("postgresql://user:pass@192.168.1.100:55432/mangaflow_acceptance", "must target local loopback"),
        ("postgresql://user:pass@db.production.internal:55432/mangaflow_acceptance", "must target local loopback"),
        # Query parameter host overrides
        ("postgresql://user:pass@127.0.0.1:55432/mangaflow_acceptance?host=outside.invalid", "forbidden override"),
    ]
    for url, err_msg in invalid_cases:
        with pytest.raises(ValueError, match=err_msg):
            validate_safe_acceptance_pg_url(url)


def test_safe_acceptance_redis_url_allows_valid_isolated_endpoints():
    """Verify strictly valid acceptance Redis URLs on port 56379 and DB 1..15 are accepted."""
    valid_urls = [
        "redis://:pass@127.0.0.1:56379/15",
        "redis://localhost:56379/1",
        "redis://:pass@[::1]:56379/9",
        "redis://:pass@127.0.0.1:56379?db=14",
    ]
    for url in valid_urls:
        assert validate_safe_acceptance_redis_url(url) == url


def test_safe_acceptance_redis_url_blocks_standard_ports_and_db_zero():
    """Verify default port 6379, DB 0 (/0, /00, ?db=0), remote hosts, and query parameter overrides are strictly rejected."""
    invalid_cases = [
        # Standard port 6379
        ("redis://127.0.0.1:6379/15", "must be 56379"),
        # DB 0 variants
        ("redis://127.0.0.1:56379/0", "DB 0 is strictly forbidden"),
        ("redis://127.0.0.1:56379/00", "DB 0 is strictly forbidden"),
        ("redis://127.0.0.1:56379?db=0", "DB 0 is strictly forbidden"),
        ("redis://127.0.0.1:56379/15?db=0", "DB 0 is strictly forbidden"),
        # Remote hosts
        ("redis://192.168.1.50:56379/15", "must target local loopback"),
        ("redis://cache.production.internal:56379/15", "must target local loopback"),
        # Query parameter overrides
        ("redis://127.0.0.1:56379/15?host=outside.invalid", "forbidden override"),
    ]
    for url, err_msg in invalid_cases:
        with pytest.raises(ValueError, match=err_msg):
            validate_safe_acceptance_redis_url(url)
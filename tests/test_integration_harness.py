from __future__ import annotations

from pathlib import Path

import pytest

from scripts.acceptance_safety import (
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
    """Verify strictly valid acceptance PostgreSQL URLs without query parameters are accepted."""
    valid_urls = [
        "postgresql+psycopg://user:pass@127.0.0.1:55432/mangaflow_acceptance",
        "postgresql+psycopg2://user:pass@localhost:55432/mangaflow_acceptance_sub",
        "postgresql://user:pass@[::1]:55432/mangaflow_acceptance",
    ]
    for url in valid_urls:
        assert validate_safe_acceptance_pg_url(url) == url


def test_safe_acceptance_pg_url_blocks_standard_and_remote_and_invalid_dbs_and_queries():
    """Reject default ports, foreign databases, remote hosts and driver overrides."""
    invalid_cases = [
        # Standard development/production port 5432
        ("postgresql://user:pass@127.0.0.1:5432/mangaflow_acceptance", "must be 55432"),
        # Arbitrary or production database names
        (
            "postgresql://user:pass@127.0.0.1:55432/mangaflow",
            "must start with 'mangaflow_acceptance'",
        ),
        (
            "postgresql://user:pass@127.0.0.1:55432/postgres",
            "must start with 'mangaflow_acceptance'",
        ),
        (
            "postgresql://user:pass@127.0.0.1:55432/production_db",
            "must start with 'mangaflow_acceptance'",
        ),
        # Remote hosts
        (
            "postgresql://user:pass@192.168.1.100:55432/mangaflow_acceptance",
            "must target local loopback",
        ),
        (
            "postgresql://user:pass@db.production.internal:55432/mangaflow_acceptance",
            "must target local loopback",
        ),
        # Query parameter hostaddr / host overrides
        (
            "postgresql+psycopg://test:test@127.0.0.1:55432/mangaflow_acceptance?hostaddr=203.0.113.1",
            "must not contain query parameters",
        ),
        (
            "postgresql://user:pass@127.0.0.1:55432/mangaflow_acceptance?host=outside.invalid",
            "must not contain query parameters",
        ),
        (
            "postgresql://user:pass@127.0.0.1:55432/mangaflow_acceptance?sslmode=disable",
            "must not contain query parameters",
        ),
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
    ]
    for url in valid_urls:
        assert validate_safe_acceptance_redis_url(url) == url


def test_safe_acceptance_redis_url_blocks_standard_ports_and_db_zero_and_queries():
    """Reject default ports, DB zero, remote hosts and driver overrides."""
    invalid_cases = [
        # Standard port 6379
        ("redis://127.0.0.1:6379/15", "must be 56379"),
        # DB 0 variants in path
        ("redis://127.0.0.1:56379/0", "DB 0 is strictly forbidden"),
        ("redis://127.0.0.1:56379/00", "DB 0 is strictly forbidden"),
        # Query parameter attempts
        ("redis://127.0.0.1:56379/15?db=0", "must not contain query parameters"),
        ("redis://127.0.0.1:56379/15?db=0&db=15", "must not contain query parameters"),
        ("redis://127.0.0.1:56379/15?host=outside.invalid", "must not contain query parameters"),
        # Remote hosts
        ("redis://192.168.1.50:56379/15", "must target local loopback"),
        ("redis://cache.production.internal:56379/15", "must target local loopback"),
    ]
    for url, err_msg in invalid_cases:
        with pytest.raises(ValueError, match=err_msg):
            validate_safe_acceptance_redis_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "redis://127.0.0.1:56379/15?db=0&db=15",
        "redis://127.0.0.1:56379/15?password=review-secret",
        "redis://127.0.0.1:56379/%31",
        "redis://127.0.0.1:56379/+1",
        "redis://127.0.0.1:56379/01",
        "redis://127.0.0.1:56379/1#review-secret",
        "\\nredis://127.0.0.1:56379/1",
    ],
)
def test_redis_guard_rejects_ambiguous_driver_inputs(url):
    with pytest.raises(ValueError) as error:
        validate_safe_acceptance_redis_url(url)
    assert "review-secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@127.0.0.1:55432/mangaflow_acceptance?hostaddr=203.0.113.1",
        "postgresql+psycopg://u:p@127.0.0.1:55432/mangaflow_acceptance_%2fpublic",
        "postgresql+unexpected://u:p@127.0.0.1:55432/mangaflow_acceptance",
        "postgresql://u:p@127.0.0.1:55432/mangaflow_acceptance#review-secret",
    ],
)
def test_pg_guard_rejects_ambiguous_driver_inputs(url):
    with pytest.raises(ValueError):
        validate_safe_acceptance_pg_url(url)


def test_mask_url_removes_query_and_fragment_secrets():
    url = "redis://:first-secret@127.0.0.1:56379/15?password=second-secret#third-secret"
    masked = mask_url(url)
    assert all(secret not in masked for secret in ("first-secret", "second-secret", "third-secret"))


def test_acceptance_entry_dry_run_does_not_load_app_or_dotenv(tmp_path, monkeypatch, capsys):
    import importlib.util

    entry = Path(__file__).resolve().parents[1] / "scripts" / "run_phase2_acceptance.py"
    spec = importlib.util.spec_from_file_location("review_acceptance_entry", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=not-a-database", encoding="utf-8")
    monkeypatch.setenv("MANGAFLOW_ACCEPTANCE_PG_URL", "")
    monkeypatch.setenv("MANGAFLOW_ACCEPTANCE_REDIS_URL", "")
    assert module.main(["--dry-run", "--start-containers", "--run-live"]) == 0
    assert "no connection" in capsys.readouterr().out
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env"]


def test_incomplete_live_entry_is_blocked_before_any_service_operation(monkeypatch, capsys):
    import importlib.util

    entry = Path(__file__).resolve().parents[1] / "scripts" / "run_phase2_acceptance.py"
    spec = importlib.util.spec_from_file_location("review_blocked_entry", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("MANGAFLOW_ACCEPTANCE_PG_URL", "")
    monkeypatch.setenv("MANGAFLOW_ACCEPTANCE_REDIS_URL", "")
    assert module.main(["--run-live", "--start-containers", "--stop-containers"]) == 2
    assert "No service was connected" in capsys.readouterr().err


@pytest.mark.parametrize("explicit, inherited", [(True, "0"), (False, "1")])
def test_live_fixture_blocks_all_opt_ins_before_connect(explicit, inherited, monkeypatch):
    from types import SimpleNamespace

    from tests.integration.conftest import live_integration_enabled

    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda _: explicit))
    monkeypatch.setenv("MANGAFLOW_ENABLE_LIVE_INTEGRATION", inherited)
    with pytest.raises(pytest.fail.Exception, match="BLOCKED"):
        live_integration_enabled.__wrapped__(request)


def test_postgres_cleanup_refuses_changed_ownership():
    from unittest.mock import MagicMock

    from tests.integration.postgres_resources import _drop_owned_schema

    admin = MagicMock()
    connection = admin.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "belongs-to-someone-else"
    token = "a" * 32
    with pytest.raises(RuntimeError, match="ownership marker"):
        _drop_owned_schema(admin, f"acceptance_{token}", token)
    connection.exec_driver_sql.assert_not_called()


@pytest.mark.parametrize("stage", ["engine", "migration", "body"])
def test_postgres_schema_cleanup_runs_after_each_failure(stage, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from tests.integration import postgres_resources as resources

    token = "b" * 32
    schema = f"acceptance_{token}"
    admin = MagicMock()
    admin.dialect.name = "postgresql"
    admin.url.render_as_string.return_value = (
        "postgresql://test:test@127.0.0.1:55432/mangaflow_acceptance"
    )
    admin_connection = admin.begin.return_value.__enter__.return_value
    admin_connection.scalar.return_value = f"mangaflow-acceptance:{token}"
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value.scalar.return_value = schema
    monkeypatch.setattr(resources, "uuid4", lambda: SimpleNamespace(hex=token))
    create = MagicMock(return_value=engine)
    migrate = MagicMock()
    if stage == "engine":
        create.side_effect = RuntimeError("injected lifecycle failure")
    elif stage == "migration":
        migrate.side_effect = RuntimeError("injected lifecycle failure")
    monkeypatch.setattr(resources, "create_engine", create)
    monkeypatch.setattr(resources.command, "upgrade", migrate)
    with (
        pytest.raises(RuntimeError, match="injected lifecycle failure"),
        resources.isolated_postgres_schema(admin),
    ):
        raise RuntimeError("injected lifecycle failure")
    commands = [call.args[0] for call in admin_connection.exec_driver_sql.call_args_list]
    assert commands[0] == f'CREATE SCHEMA "{schema}"'
    assert commands[-1] == f'DROP SCHEMA "{schema}" CASCADE'
    if stage != "engine":
        engine.dispose.assert_called_once()

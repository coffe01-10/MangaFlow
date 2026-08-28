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


@pytest.fixture
def redis_cleanup_mock():
    """Only protocol/control-flow verification; no Redis server is simulated."""
    from unittest.mock import MagicMock

    from tests.integration.redis_resources import RedisAcceptanceResources

    client = MagicMock()
    client.connection_pool.connection_kwargs = {"host": "127.0.0.1", "port": 56379, "db": 15}
    client.set.return_value = True
    client.exists.return_value = 0
    client.sismember.return_value = False
    client.type.return_value = b"set"
    client.hget.side_effect = lambda key, field: b"done" if field == "death" else None
    client.smembers.return_value = set()
    client.lrange.return_value = []
    client.zrange.return_value = []
    client.scan_iter.return_value = []
    client.pipeline.return_value.__enter__.return_value = client
    client.execute.return_value = [1]
    resources = RedisAcceptanceResources(client, token="c" * 32)
    client.get.side_effect = lambda key: (
        resources.token.encode() if key == resources.owner_key else None
    )
    resources.claim()
    return resources, client


def test_redis_cleanup_covers_rq_resource_families_and_exact_global_members(redis_cleanup_mock):
    from rq.job import Job
    from rq.registry import StartedJobRegistry

    resources, client = redis_cleanup_mock
    queue = resources.queue_name()
    job_id = "test-job"
    resources.track_job(job_id)
    worker = resources.worker_name("one")
    execution = f"rq:execution:{job_id}:execution-one"
    app_key = resources.app_key("control")
    first_seen = f"rq:queue:{queue}:intermediate:first_seen:{job_id}"
    families = {
        f"rq:execution:{job_id}:*": [execution.encode()],
        resources.prefix + "app:*": [app_key.encode()],
        f"rq:queue:{queue}:intermediate:first_seen:*": [first_seen.encode()],
    }
    client.scan_iter.side_effect = lambda **kwargs: families.get(kwargs["match"], [])
    client.hget.side_effect = lambda key, field: (
        queue.encode() if field == "origin" else b"done" if field == "death" else None
    )
    resources.cleanup()
    deleted = set(client.delete.call_args_list[0].args)
    assert {
        f"rq:queue:{queue}",
        f"rq:queue:{queue}:intermediate",
        f"rq:clean_registries:{queue}",
        f"rq:workers:{queue}",
        f"rq:scheduler:{queue}",
        f"rq:scheduler-lock:{queue}",
        StartedJobRegistry.key_template.format(queue),
        f"rq:finished:{queue}",
        f"rq:failed:{queue}",
        f"rq:deferred:{queue}",
        f"rq:scheduled:{queue}",
        f"rq:canceled:{queue}",
        f"rq:job:{job_id}",
        f"rq:job:{job_id}:dependents",
        Job(id=job_id, connection=client).dependencies_key,
        f"rq:results:{job_id}",
        f"rq:executions:{job_id}",
        f"rq:worker:{worker}",
        execution,
        app_key,
        first_seen,
    } <= deleted
    assert "rq:queues" not in deleted and "rq:workers" not in deleted
    assert client.srem.call_args_list[0].args == ("rq:queues", f"rq:queue:{queue}")
    assert client.srem.call_args_list[1].args == ("rq:workers", f"rq:worker:{worker}")
    assert client.delete.call_args_list[-1].args == (resources.owner_key,)
    assert resources.cleaned


def test_redis_cleanup_finds_slot_results_after_job_hash_expired(redis_cleanup_mock):
    resources, client = redis_cleanup_mock
    resources.queue_name()
    resources.track_job("root-job")
    slot_id = "root-job-slot-" + "d" * 32
    result_key = f"rq:results:{slot_id}"
    client.scan_iter.side_effect = lambda **kwargs: (
        [result_key.encode()] if kwargs["match"] == "rq:results:root-job-slot-*" else []
    )
    resources.cleanup()
    assert result_key in client.delete.call_args_list[0].args
    assert "rq:job:foreign-job" not in client.delete.call_args_list[0].args


@pytest.mark.parametrize(
    "case", ["owner", "worker", "origin", "untracked", "registry_type", "scheduler"]
)
def test_redis_cleanup_refuses_unowned_or_active_resources(redis_cleanup_mock, case):
    resources, client = redis_cleanup_mock
    resources.queue_name()
    resources.track_job("owned-job")
    resources.worker_name("one")
    if case == "owner":
        client.get.side_effect = lambda key: b"someone-else"
    elif case == "worker":
        client.hget.side_effect = lambda key, field: None
    elif case == "origin":
        client.hget.side_effect = lambda key, field: b"foreign-queue"
    elif case == "untracked":
        client.lrange.return_value = [b"unknown-job"]
    elif case == "scheduler":
        client.get.side_effect = lambda key: (
            resources.token.encode() if key == resources.owner_key else b"123"
        )
    else:
        client.type.return_value = b"string"
    messages = {
        "owner": "ownership changed",
        "worker": "Worker has not confirmed",
        "origin": "another queue",
        "untracked": "Untracked job",
        "registry_type": "registry type",
        "scheduler": "Scheduler lock",
    }
    with pytest.raises(RuntimeError, match=messages[case]):
        resources.cleanup()
    client.delete.assert_not_called()
    assert not resources.cleaned
    with pytest.raises(RuntimeError, match="registration is closed"):
        resources.track_job("late-job")


def test_redis_cleanup_failure_keeps_owner_and_allows_retry(redis_cleanup_mock):
    resources, client = redis_cleanup_mock
    resources.queue_name()
    resources.track_job("owned-job")
    client.execute.side_effect = [ConnectionError("injected"), [1], [1]]
    with pytest.raises(ConnectionError, match="injected"):
        resources.cleanup()
    assert not resources.cleaned
    assert all(call.args != (resources.owner_key,) for call in client.delete.call_args_list)
    resources.cleanup()
    assert resources.cleaned


def test_redis_cleanup_refuses_to_adopt_existing_job(redis_cleanup_mock):
    resources, client = redis_cleanup_mock
    client.exists.return_value = 1
    with pytest.raises(RuntimeError, match="preexisting RQ job"):
        resources.track_job("someone-elses-job")
    assert resources.jobs == set()
    client.delete.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        {"host": "127.0.0.1", "port": 6379, "db": 15},
        {"host": "127.0.0.1", "port": 56379, "db": 0},
        {"host": "example.invalid", "port": 56379, "db": 15},
    ],
)
def test_redis_claim_checks_actual_connection_not_only_url(redis_cleanup_mock, options):
    resources, client = redis_cleanup_mock
    client.set.reset_mock()
    client.connection_pool.connection_kwargs = options
    with pytest.raises(ValueError, match="isolated acceptance endpoint"):
        resources.claim()
    client.set.assert_not_called()


def test_redis_cleanup_does_not_mark_success_if_owner_delete_failed(redis_cleanup_mock):
    resources, client = redis_cleanup_mock
    resources.queue_name()
    client.execute.side_effect = [[1], [0]]
    with pytest.raises(RuntimeError, match="marker was not removed"):
        resources.cleanup()
    assert not resources.cleaned

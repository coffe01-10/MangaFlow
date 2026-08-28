from __future__ import annotations

# Ruff lints tests/ outside apps/api's py312 target; import the 3.11+ builtin explicitly.
from builtins import ExceptionGroup
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


# These run real lightweight Windows processes, but no database, Redis or supplier.
@pytest.fixture
def process_tree(tmp_path):
    import os

    from tests.integration.process_resources import OwnedProcessTree

    if os.name != "nt":
        pytest.skip("Windows Job Object process verification requires Windows")
    with OwnedProcessTree(tmp_path) as tree:
        yield tree


def _wait_process_file(path, timeout=8):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if content:
                return content
        time.sleep(0.02)
    raise AssertionError("Owned test process did not produce its readiness file")


def test_owned_process_runs_in_isolated_environment_and_retains_nonzero_status(
    process_tree, monkeypatch
):
    import json

    monkeypatch.setenv("MANGAFLOW_TEST_PARENT_SECRET", "do-not-inherit")
    output = process_tree.payload / "child result 中文.json"
    child = process_tree.start_python(
        "probe",
        """import json, os, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "pid": os.getpid(), "cwd": os.getcwd(),
    "secret": os.getenv("MANGAFLOW_TEST_PARENT_SECRET"),
    "parent_env": os.getenv("DATABASE_URL"),
    "argument": sys.argv[2],
}), encoding="utf-8")
raise SystemExit(7)
""",
        [str(output), "spaces ' and \" quotes 中文"],
    )
    assert child.wait(timeout=8) == 7
    result = json.loads(output.read_text(encoding="utf-8"))
    # Windows venv python.exe may be a redirector. Both belong to the same job.
    assert isinstance(result["pid"], int) and result["pid"] > 0
    assert Path(result["cwd"]) == process_tree.payload
    assert result["secret"] is None and result["parent_env"] is None
    assert result["argument"] == "spaces ' and \" quotes 中文"
    process_tree.stop()
    record = json.loads((process_tree.directory / "owner.json").read_text(encoding="utf-8"))
    assert record["processes"][0]["exit_code"] == 7
    assert "do-not-inherit" not in json.dumps(record)
    with pytest.raises(RuntimeError, match="registration is closed"):
        process_tree.start_python("late", "pass")


def test_process_gate_does_not_execute_if_assignment_fails(process_tree, monkeypatch):
    output = process_tree.payload / "must-not-exist"
    monkeypatch.setattr(process_tree.api, "AssignProcessToJobObject", lambda *_: False)
    with pytest.raises(OSError):
        process_tree.start_python(
            "rejected",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('unsafe')",
            [str(output)],
        )
    assert not output.exists()
    assert process_tree.processes[-1].poll() is not None


def test_job_zero_active_waits_for_assigned_handles_without_terminating_twice(
    process_tree, monkeypatch
):
    child = process_tree.start_python("wait-race", "import time; time.sleep(60)")
    assert child.assigned_to_job
    monkeypatch.setattr(
        child,
        "terminate",
        lambda: (_ for _ in ()).throw(
            AssertionError("assigned member already terminated by job")
        ),
    )
    process_tree.stop()
    assert child.returncode is not None


def test_process_stop_kills_grandchild_after_direct_child_already_exited(process_tree):
    from tests.integration.process_resources import _checked

    pid_file = process_tree.payload / "grandchild.pid"
    child = process_tree.start_python(
        "parent",
        """import pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
""",
        [str(pid_file)],
    )
    descendant_pid = int(_wait_process_file(pid_file))
    assert child.wait(timeout=8) == 0
    api = process_tree.api
    handle = _checked(api.OpenProcess(0x100000, False, descendant_pid))
    try:
        assert api.WaitForSingleObject(handle, 0) == 258
        process_tree.stop()
        assert api.WaitForSingleObject(handle, 5000) == 0
    finally:
        _checked(api.CloseHandle(handle))


def test_process_cleanup_retries_real_windows_sqlite_lock(process_tree):
    import sqlite3

    connection = sqlite3.connect(process_tree.payload / "locked.sqlite")
    connection.execute("CREATE TABLE sentinel (value INTEGER)")
    connection.commit()
    try:
        with pytest.raises(PermissionError):
            process_tree.cleanup()
        assert not process_tree.cleaned
        assert (process_tree.directory / "owner.json").exists()
    finally:
        connection.close()
    process_tree.cleanup()
    assert process_tree.cleaned and not process_tree.directory.exists()


def test_process_recovery_refuses_live_controller_even_without_children(process_tree):
    from tests.integration.process_resources import recover_stopped_tree

    with pytest.raises(RuntimeError, match="controller is still active"):
        recover_stopped_tree(process_tree.directory, process_tree.token)
    assert (process_tree.directory / "owner.json").exists()


def test_process_cleanup_refuses_changed_owner(process_tree):
    import json

    owner = process_tree.directory / "owner.json"
    original = owner.read_text(encoding="utf-8")
    altered = json.loads(original)
    altered["token"] = "e" * 32
    owner.write_text(json.dumps(altered), encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="ownership marker changed"):
            process_tree.cleanup()
        assert owner.exists() and not process_tree.cleaned
    finally:
        owner.write_text(original, encoding="utf-8")


def test_process_stop_failure_is_not_completion_and_can_retry(process_tree, monkeypatch):
    import json

    terminate = process_tree.api.TerminateJobObject
    monkeypatch.setattr(process_tree.api, "TerminateJobObject", lambda *_: False)
    with pytest.raises(OSError):
        process_tree.stop()
    record = json.loads((process_tree.directory / "owner.json").read_text(encoding="utf-8"))
    assert record["state"] == "stop_failed" and not process_tree.cleaned
    monkeypatch.setattr(process_tree.api, "TerminateJobObject", terminate)
    process_tree.cleanup()
    assert not process_tree.directory.exists()


@pytest.mark.parametrize("ending", ["kill", "abrupt"])
def test_controller_death_kills_tree_and_journal_can_be_recovered(tmp_path, ending):
    import json
    import os
    import subprocess
    import sys

    from tests.integration.process_resources import _checked, _kernel, recover_stopped_tree

    if os.name != "nt":
        pytest.skip("Windows process recovery test")
    repo = str(Path(__file__).resolve().parents[1])
    pointer = tmp_path / "tree.json"
    # Controller imports the actual module from this checkout. No parent patches.
    code = """import json, os, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from tests.integration.process_resources import OwnedProcessTree
parent, pointer = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
tree = OwnedProcessTree(parent)
tree.start_python("worker", '''import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
''', [str(tree.payload / "leaf.pid")])
pointer.write_text(json.dumps({"directory": str(tree.directory), "token": tree.token}),
                   encoding="utf-8")
while not (parent / "exit-now").exists():
    time.sleep(0.02)
os._exit(23)
"""
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    env.update(TEMP=str(tmp_path), TMP=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    controller = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, repo, str(tmp_path), str(pointer)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    api, handles, identity = _kernel(), [], None
    try:
        identity = json.loads(_wait_process_file(pointer))
        directory = Path(identity["directory"])
        leaf = int(_wait_process_file(directory / "payload" / "leaf.pid"))
        record = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
        for pid in [record["processes"][0]["pid"], leaf]:
            handles.append(_checked(api.OpenProcess(0x100000, False, pid)))
        if ending == "kill":
            controller.kill()  # Only the Popen handle created by this test.
        else:
            (tmp_path / "exit-now").touch()
        assert controller.wait(timeout=8) != 0
        assert all(api.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
        recover_stopped_tree(directory, identity["token"])
        assert not directory.exists()
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=8)
        for handle in handles:
            _checked(api.CloseHandle(handle))
        if identity and Path(identity["directory"]).exists():
            recover_stopped_tree(Path(identity["directory"]), identity["token"])


def test_suspended_launcher_and_execution_process_belong_to_job(process_tree, monkeypatch):
    import ctypes
    import time
    from ctypes import wintypes

    from tests.integration.process_resources import _checked

    output = process_tree.payload / "execution.pid"
    release = process_tree.payload / "release"
    original_assign = process_tree.api.AssignProcessToJobObject

    def delayed_assignment(job, handle):
        time.sleep(0.1)
        # Even the Windows venv redirector cannot execute before assignment.
        assert process_tree.api.WaitForSingleObject(handle, 0) == 258
        assert not output.exists()
        return original_assign(job, handle)

    monkeypatch.setattr(process_tree.api, "AssignProcessToJobObject", delayed_assignment)
    child = process_tree.start_python(
        "membership",
        """import os, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
while not pathlib.Path(sys.argv[2]).exists():
    time.sleep(0.02)
""",
        [str(output), str(release)],
    )
    execution_pid = int(_wait_process_file(output))
    execution = _checked(process_tree.api.OpenProcess(0x1000, False, execution_pid))
    try:
        member = wintypes.BOOL()
        _checked(
            process_tree.api.IsProcessInJob(execution, process_tree.handle, ctypes.byref(member))
        )
        assert member.value
    finally:
        _checked(process_tree.api.CloseHandle(execution))
    release.touch()
    assert child.wait(timeout=8) == 0


def test_process_body_and_cleanup_failures_both_reported(tmp_path):
    import os
    import sqlite3

    from tests.integration.process_resources import OwnedProcessTree

    if os.name != "nt":
        pytest.skip("Windows lock behavior")
    tree = OwnedProcessTree(tmp_path)
    connection = sqlite3.connect(tree.payload / "locked.sqlite")
    connection.execute("CREATE TABLE sentinel (value INTEGER)")
    connection.commit()
    try:
        with pytest.raises(ExceptionGroup) as caught, tree:
            raise ValueError("original test failure")
        assert isinstance(caught.value.exceptions[0], ValueError)
        assert isinstance(caught.value.exceptions[1], PermissionError)
        assert not tree.cleaned and (tree.directory / "owner.json").exists()
    finally:
        connection.close()
        tree.cleanup()


def test_process_stop_can_retry_after_child_handles_closed(process_tree, monkeypatch):
    child = process_tree.start_python("complete", "pass")
    assert child.wait(timeout=8) == 0
    close = process_tree.api.CloseHandle
    fail_once = True

    def close_handle(handle):
        nonlocal fail_once
        if handle == process_tree.handle and fail_once:
            fail_once = False
            return False
        return close(handle)

    monkeypatch.setattr(process_tree.api, "CloseHandle", close_handle)
    with pytest.raises(OSError):
        process_tree.stop()
    assert child.handle is None and not process_tree.cleaned
    process_tree.cleanup()
    assert process_tree.cleaned


@pytest.fixture
def offline_application_process(process_tree):
    """Real child application execution using disposable SQLite, NOT RQ/PG evidence."""
    from app.database import Base
    from app.domain.states import Resolution
    from app.model_adapters.fake_acceptance import _generate_fake_png_bytes
    from app.models import Asset, GenerationBatch, PageCandidate
    from app.services.job_service import create_job
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from tests.integration.test_postgres_acceptance import _seed_pg_project_hierarchy

    engine = create_engine(f"sqlite:///{process_tree.payload / 'probe.sqlite'}")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    try:
        seeded = _seed_pg_project_hierarchy(factory)
        with factory() as db:
            # Only newly created test assets; materialize their PNGs in the owned payload.
            for asset in db.scalars(select(Asset)):
                path = process_tree.payload / "uploads" / asset.storage_key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_generate_fake_png_bytes())
            batch = GenerationBatch(
                project_id=seeded["project_id"],
                page_id=seeded["page_id"],
                chapter_id=seeded["chapter_id"],
                ordinal=1,
                status="OPEN",
            )
            db.add(batch)
            db.flush()
            candidate = PageCandidate(
                batch_id=batch.id,
                page_id=seeded["page_id"],
                ordinal=1,
                model_alias="image.nano_banana_2",
                resolution=Resolution.DRAFT_1K,
                based_on_storyboard_version=1,
                status="QUEUED",
                prompt_snapshot={
                    "reference_selections": {
                        seeded["character_id"]: {
                            "character_asset_id": seeded["character_asset_id"],
                            "outfit_id": seeded["outfit_id"],
                            "outfit_asset_id": seeded["outfit_asset_id"],
                        }
                    }
                },
            )
            db.add(candidate)
            db.flush()
            job = create_job(
                db,
                project_id=seeded["project_id"],
                target_type="PAGE_CANDIDATE",
                target_id=candidate.id,
                job_type="PAGE_GENERATE",
                model_alias="image.nano_banana_2",
                max_attempts=3,
                auto_commit=False,
            )
            candidate.job_id = job.id
            db.commit()
            yield process_tree, factory, job.id, candidate.id
    finally:
        engine.dispose()


def _start_application_probe(tree, config, job_id, label):
    root = Path(__file__).resolve().parents[1]
    return tree.start_python(
        label,
        """import pathlib, sys
sys.path[:0] = [sys.argv[1], str(pathlib.Path(sys.argv[1]) / "apps" / "api")]
from tests.integration.worker_runtime import run_offline_application_probe
run_offline_application_probe(pathlib.Path(sys.argv[2]), sys.argv[3])
""",
        [str(root), str(config), job_id],
    )


@pytest.mark.parametrize("mode", ["ok", "terminal", "retry_once"])
def test_child_executes_actual_page_task_with_persisted_local_fixture(
    offline_application_process, mode
):
    import json

    from app.domain.states import JobStatus
    from app.models import Asset, GenerationJob, GenerationRecord, PageCandidate

    from tests.integration.worker_runtime import write_worker_config

    tree, factory, job_id, candidate_id = offline_application_process
    config = write_worker_config(tree, {job_id: mode})
    first = _start_application_probe(tree, config, job_id, "first")
    first_code = first.wait(timeout=15)
    with factory() as db:
        job = db.get(GenerationJob, job_id)
        if mode == "ok":
            assert first_code == 0, (job.status, job.error_code, job.error_message)
        else:
            assert first_code != 0
            assert job.error_code == ("RATE_LIMIT" if mode == "retry_once" else "INVALID_PROMPT")
            assert job.status == (JobStatus.WAITING if mode == "retry_once" else JobStatus.FAILED)
        assert job.attempt_count == 1

    if mode == "retry_once":
        # New OS process, not an in-memory counter. This is NOT automatic RQ retry evidence.
        second = _start_application_probe(tree, config, job_id, "second")
        assert second.wait(timeout=15) == 0

    events = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tree.payload / "events").glob("event-*.json")
    ]
    entered = [event for event in events if event["event"] == "entered"]
    assert len(entered) == (2 if mode == "retry_once" else 1)
    if mode == "retry_once":
        assert len({event["pid"] for event in entered}) == 2
    with factory() as db:
        job = db.get(GenerationJob, job_id)
        candidate = db.get(PageCandidate, candidate_id)
        if mode == "terminal":
            assert candidate.asset_id is None and job.status == JobStatus.FAILED
        else:
            assert job.status == JobStatus.COMPLETED and candidate.status == "READY"
            assert job.attempt_count == (2 if mode == "retry_once" else 1)
            asset = db.get(Asset, candidate.asset_id)
            record = db.get(GenerationRecord, candidate.generation_record_id)
            assert record.job_id == job_id and record.provider_request_id.startswith("local-")
            assert (tree.payload / "storage" / asset.storage_key).is_file()


def test_child_config_ignores_dotenv_and_inherited_application_environment(process_tree):
    import json

    from tests.integration.worker_runtime import write_worker_config

    root = Path(__file__).resolve().parents[1]
    config = write_worker_config(process_tree, {"probe": "ok"})
    (process_tree.payload / ".env").write_text(
        "GOOGLE_CLOUD_PROJECT=dotenv-sentinel\nMANGAFLOW_PROXY_URL=http://invalid.example\n",
        encoding="utf-8",
    )
    output = process_tree.payload / "probe-settings.json"
    child = process_tree.start_python(
        "config",
        """import json, pathlib, sys
sys.path[:0] = [sys.argv[1], str(pathlib.Path(sys.argv[1]) / "apps" / "api")]
from tests.integration.worker_runtime import configure_child
record, settings, engine, adapter = configure_child(pathlib.Path(sys.argv[2]), probe_job="probe")
try:
    pathlib.Path(sys.argv[3]).write_text(json.dumps({
        "project": settings.google_cloud_project,
        "credential": str(settings.google_application_credentials),
        "proxy": settings.mangaflow_proxy_url,
        "queue_enabled": settings.queue_enabled,
        "storage": str(settings.storage_root),
    }), encoding="utf-8")
finally:
    engine.dispose()
""",
        [str(root), str(config), str(output)],
        environment={
            "GOOGLE_CLOUD_PROJECT": "environment-sentinel",
            "DATABASE_URL": "not-a-database",
            "QUEUE_ENABLED": "true",
        },
    )
    assert child.wait(timeout=15) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["project"] is None and result["proxy"] is None
    assert result["credential"] == "None" and result["queue_enabled"] is False
    assert Path(result["storage"]) == process_tree.payload / "storage"


def test_worker_configuration_rejects_live_endpoint_override_before_writing(process_tree):
    from tests.integration.worker_runtime import write_worker_config

    with pytest.raises(ValueError, match="query parameters"):
        write_worker_config(
            process_tree,
            {"job": "ok"},
            pg_url="postgresql://test:test@127.0.0.1:55432/mangaflow_acceptance?host=outside",
            schema="acceptance_" + "a" * 32,
            redis_url="redis://127.0.0.1:56379/15",
            redis_token="b" * 32,
            queue_name="acceptance_" + "b" * 32 + "_main",
        )
    assert not (process_tree.payload / "worker.json").exists()


class _FakeConnection:
    """Connection stand-in exposing only the kwargs the horse environment reads."""

    class _Pool:
        connection_kwargs = {"host": "127.0.0.1", "port": 56379, "password": "sekret", "retry": "x"}

    connection_pool = _Pool()


class _FakeQueue:
    name = "acceptance_main"

    class key:  # noqa: N801 - rq workers treat this as an opaque string attribute
        def __str__(self):
            return "rq:queue:acceptance_main"


class _FakeHorse:
    """Minimal Popen stand-in for the WindowsSpawnWorker monitor loop."""

    def __init__(self, exit_code, *, alive=False):
        self.pid = 4242
        # alive=True models a running horse; otherwise it has already exited.
        self.returncode = None if alive else exit_code
        self._exit_code = exit_code
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = self._exit_code

    def wait(self):
        if self.returncode is None:
            self.returncode = self._exit_code
        return self.returncode


def _bare_windows_worker(horse, calls, monitoring_interval=3600):
    """Construct WindowsSpawnWorker without Redis for pure control-flow checks."""
    from types import SimpleNamespace

    from app.rq_windows import WindowsSpawnWorker

    worker = WindowsSpawnWorker.__new__(WindowsSpawnWorker)
    worker._horse_popen = horse
    worker._stopped_job_id = None
    worker.execution = SimpleNamespace(id="exec-1")
    worker.death_penalty_class = object()
    worker.job_monitoring_interval = monitoring_interval
    worker.current_job_working_time = 0
    worker.set_current_job_working_time = lambda value: (
        calls.append(("working_time", value)),
        setattr(worker, "current_job_working_time", value),
    )
    worker.heartbeat = lambda *args: calls.append(("heartbeat", args))
    worker.maintain_heartbeats = lambda job: calls.append(("maintain", job.id))
    worker.handle_work_horse_killed = lambda job, pid, code, rusage: calls.append(
        ("horse_killed", code)
    )
    worker.handle_job_failure = lambda job, queue, exc_string: calls.append(
        ("job_failure", exc_string)
    )
    return worker


def _fake_job():
    from types import SimpleNamespace

    from rq.job import JobStatus

    return SimpleNamespace(
        id="job-1",
        started_at=None,
        timeout=-1,
        ended_at=None,
        stopped_callback=None,
        get_status=lambda: JobStatus.STARTED,
    )


def test_horse_spawn_keeps_credentials_out_of_argv(monkeypatch):
    import json
    import sys
    from types import SimpleNamespace

    from app import rq_windows

    captured = {}

    class _RecordingPopen(_FakeHorse):
        def __init__(self, command, env=None, creationflags=None):
            captured["command"] = command
            captured["env"] = env
            super().__init__(0)

    monkeypatch.setattr(rq_windows.subprocess, "Popen", _RecordingPopen)
    worker = _bare_windows_worker(_FakeHorse(0), [])
    worker.connection = _FakeConnection()
    worker.execution = SimpleNamespace(id="exec-1")
    worker.name = "acceptance_" + "b" * 32 + "_main"
    job, queue = _fake_job(), _FakeQueue()
    worker.fork_work_horse(job, queue)
    assert captured["command"][:2] == [sys.executable, "-c"]
    # Credentials travel in the child environment, never in the command line.
    assert "sekret" not in json.dumps(captured["command"])
    horse_env = json.loads(captured["env"]["RQ_HORSE_REDIS_KWARGS"])
    assert horse_env["password"] == "sekret"
    assert "retry" not in horse_env
    assert captured["env"]["RQ_QUEUE_NAME"] == "acceptance_main"


def test_monitor_returns_cleanly_when_horse_exits_zero():
    from rq.job import JobStatus

    calls: list = []
    job = _fake_job()
    job.get_status = lambda: JobStatus.FINISHED
    worker = _bare_windows_worker(_FakeHorse(0), calls)
    worker.monitor_work_horse(job, _FakeQueue())
    # Only the post-loop working-time reset is recorded; no failure handling.
    assert calls == [("working_time", 0)]
    assert worker._horse_pid == 0


def test_monitor_reports_unexpected_horse_death_without_posix_apis():
    calls: list = []
    worker = _bare_windows_worker(_FakeHorse(1), calls)
    worker.monitor_work_horse(_fake_job(), _FakeQueue())
    assert ("horse_killed", 1) in calls
    failure = next(item for item in calls if item[0] == "job_failure")
    assert "return code 1" in failure[1]


def test_monitor_kills_horse_after_job_timeout():
    calls: list = []
    horse = _FakeHorse(1, alive=True)
    worker = _bare_windows_worker(horse, calls, monitoring_interval=0)
    worker.current_job_working_time = 0

    def set_working_time(value):
        calls.append(("working_time", value))
        # Elapsed seconds stay near zero in a fast test; inject an over-limit
        # value so the timeout branch is exercised without waiting 61s.
        worker.current_job_working_time = 999

    worker.set_current_job_working_time = set_working_time
    job = _fake_job()
    job.timeout = 1
    worker.monitor_work_horse(job, _FakeQueue())
    assert horse.killed is True
    assert any(item[0] == "job_failure" for item in calls)


def test_acceptance_worker_horse_uses_verified_runtime_entry(tmp_path):
    import sys
    from types import SimpleNamespace

    from tests.integration.worker_runtime import AcceptanceWorker

    worker = AcceptanceWorker.__new__(AcceptanceWorker)
    worker.config_path = tmp_path / "worker.json"
    worker.name = "acceptance_" + "b" * 32 + "_main"
    worker.execution = SimpleNamespace(id="exec-1")
    job, queue = _fake_job(), _FakeQueue()
    command = worker._horse_spawn_command(job, queue)
    assert command[0] == sys.executable and command[1] == "-c"
    assert "run_rq_horse" in command[2]
    from tests.integration.worker_runtime import ROOT

    # Identity travels as argv values; no connection credentials are involved.
    assert command[3] == str(ROOT)
    assert command[4:] == [str(tmp_path / "worker.json"), worker.name, "job-1", "exec-1"]

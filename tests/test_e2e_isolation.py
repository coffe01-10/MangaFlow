import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
# Ruff lints this file outside apps/api's py312 target; import the 3.11+ builtin explicitly.
from builtins import ExceptionGroup
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load_serve():
    spec = importlib.util.spec_from_file_location(
        "serve_e2e_api", ROOT / "scripts/serve_e2e_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(tmp_path):
    from e2e_runtime import new_runtime

    instance = new_runtime(tmp_path)
    try:
        yield instance
    finally:
        if not instance.cleaned:
            instance.cleanup()


def test_fake_dotenv_and_inherited_secrets_ignored(runtime, tmp_path, monkeypatch):
    serve = _load_serve()
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / ".env").write_text(
        "GOOGLE_CLOUD_PROJECT=should-not-load\nMANGAFLOW_PROXY_URL=http://127.0.0.1:9\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "also-do-not-load")
    monkeypatch.setenv("Redis_URL", "redis://example.invalid:6379/0")
    env = serve.build_isolated_env(runtime, seed=False)
    assert not any(key.lower() in {"redis_url", "google_cloud_project"} for key in env)
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from app.config import get_settings; s=get_settings(); "
            "print(s.google_cloud_project, s.mangaflow_proxy_url, s.database_url)",
        ],
        cwd=fake_root,
        env=env,
        text=True,
    )
    assert output.startswith("None None sqlite:///")
    assert runtime.path.as_posix() in output.replace("\\", "/")


def test_refuses_to_reuse_existing_database(runtime):
    (runtime.path / "mangaflow.db").write_bytes(b"old")
    with pytest.raises(RuntimeError, match="refusing to reuse"):
        _load_serve().build_isolated_env(runtime)


@pytest.mark.parametrize("suffix", ["foreign-abc123", "mangaflow-e2e-abc123"])
def test_assigned_existing_foreign_directory_is_not_adopted(tmp_path, monkeypatch, suffix):
    foreign = tmp_path / suffix
    foreign.mkdir()
    sentinel = foreign / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("MANGAFLOW_E2E_RUN_ID", "abc123")
    monkeypatch.setenv("MANGAFLOW_E2E_RUNTIME", str(foreign))
    with pytest.raises(RuntimeError, match="does not belong"):
        _load_serve().create_runtime()
    assert list(foreign.iterdir()) == [sentinel]


def test_assigned_requires_actual_process_job_not_just_owner_token(runtime, monkeypatch):
    from e2e_runtime import assigned_runtime

    monkeypatch.setenv("MANGAFLOW_E2E_RUN_ID", runtime.run_id)
    monkeypatch.setenv("MANGAFLOW_E2E_RUNTIME", str(runtime.path))
    runtime.tree.start_python("quick", "pass").wait()
    with pytest.raises(RuntimeError, match="not in the acceptance"):
        assigned_runtime()
    monkeypatch.setenv("MANGAFLOW_E2E_RUNTIME", str(runtime.path / ".." / "runtime"))
    with pytest.raises(RuntimeError, match="canonical"):
        assigned_runtime()


def test_supervised_node_verifies_membership_and_child_api_configuration(runtime):
    from run_e2e_owned import child_environment

    node = shutil.which("node")
    assert node
    output = runtime.tree.payload / "verified.json"
    script = """
import json, pathlib, subprocess, sys
root, output = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
code = ("import {assertSupervised} from './scripts/phase2_runner.mjs'; "
        "console.log(JSON.stringify(assertSupervised()));")
result = subprocess.check_output(
    [sys.argv[3], "--input-type=module", "-e", code], cwd=root, text=True)
sys.path.insert(0, str(root / "scripts"))
from serve_e2e_api import create_runtime, build_isolated_env
runtime = create_runtime()
output.write_text(json.dumps({"node": json.loads(result),
                             "env": build_isolated_env(runtime)}), encoding="utf-8")
"""
    child = runtime.tree.start_python(
        "verify-node",
        script,
        [str(ROOT), str(output), node],
        environment=child_environment(runtime, node=node),
    )
    assert child.wait(timeout=15) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["node"]["runId"] == runtime.run_id
    assert data["node"]["runtime"] == str(runtime.path)
    assert data["env"]["DATABASE_URL"] == "sqlite:///" + (runtime.path / "mangaflow.db").as_posix()


def test_final_summary_keeps_body_and_stop_failures_without_deletion(
    runtime, tmp_path, monkeypatch
):
    from run_e2e_owned import finish

    original = runtime.tree.stop
    monkeypatch.setattr(runtime.tree, "stop", lambda: (_ for _ in ()).throw(OSError("stop probe")))
    report = tmp_path / "summary.json"
    summary = {"errors": ["body failed"], "runtime_removed": False}
    assert finish(runtime, summary, report) == 1
    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["errors"][0] == "body failed"
    assert "stop probe" in written["errors"][1]
    assert runtime.path.exists() and not runtime.cleaned
    monkeypatch.setattr(runtime.tree, "stop", original)


def test_final_cleanup_failure_keeps_owner_and_recovers_after_file_unlock(runtime, tmp_path):
    import sqlite3

    from run_e2e_owned import finish

    connection = sqlite3.connect(runtime.path / "locked.sqlite")
    connection.execute("create table x(id integer)")
    connection.commit()
    summary = {"errors": [], "runtime_removed": False}
    try:
        assert finish(runtime, summary, tmp_path / "summary.json") == 1
        assert summary["process_tree_stopped"] is True
        assert summary["runtime_removed"] is False
        assert (runtime.tree.directory / "owner.json").exists()
    finally:
        connection.close()
    runtime.cleanup()
    assert not runtime.tree.directory.exists()


def test_phase2_runner_failure_paths():
    result = subprocess.run(
        [os.environ.get("MANGAFLOW_NODE", "node"), "--test", "scripts/phase2_runner.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# These run real lightweight Windows processes, but no database, Redis or supplier.
@pytest.fixture
def process_tree(tmp_path):
    import os

    from owned_processes import OwnedProcessTree

    if os.name != "nt":
        pytest.skip("Windows Job Object process verification requires Windows")
    with OwnedProcessTree(tmp_path) as tree:
        yield tree


def _wait_process_file(path, timeout=8):

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


def test_process_stop_kills_grandchild_after_direct_child_already_exited(process_tree):
    from owned_processes import _checked

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
    from owned_processes import recover_stopped_tree

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

    from owned_processes import _checked, _kernel, recover_stopped_tree

    if os.name != "nt":
        pytest.skip("Windows process recovery test")
    repo = str(Path(__file__).resolve().parents[1] / "scripts")
    pointer = tmp_path / "tree.json"
    # Controller imports the actual module from this checkout. No parent patches.
    code = """import json, os, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from owned_processes import OwnedProcessTree
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
    from ctypes import wintypes

    from owned_processes import _checked

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

    from owned_processes import OwnedProcessTree

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


def test_next_preload_never_reads_fake_dotenv(runtime, tmp_path):
    from run_e2e_owned import child_environment

    fake_root = tmp_path / "fake-next"
    fake_root.mkdir()
    (fake_root / ".env").write_text("SENTINEL=do-not-read\n", encoding="utf-8")
    node = shutil.which("node")
    env = child_environment(runtime, node=node)
    script = """
const fs = require("node:fs");
const original = fs.readFileSync;
fs.readFileSync = function(file, ...args) {
  if (String(file).includes(".env")) throw new Error("dotenv read was attempted");
  return original.call(this, file, ...args);
};
const env = require("@next/env");
const result = env.loadEnvConfig(process.argv[1], false);
if (result.loadedEnvFiles.length || process.env.SENTINEL) process.exit(7);
console.log("NO_DOTENV_READ");
"""
    result = subprocess.run(
        [node, "-e", script, str(fake_root)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "NO_DOTENV_READ" in result.stdout


def test_assigned_cleanup_cannot_delete_controller_payload(runtime, monkeypatch):
    from e2e_runtime import assigned_runtime

    monkeypatch.setenv("MANGAFLOW_E2E_RUN_ID", runtime.run_id)
    monkeypatch.setenv("MANGAFLOW_E2E_RUNTIME", str(runtime.path))
    assigned = assigned_runtime(require_job=False)
    with pytest.raises(RuntimeError, match="Only the process controller"):
        assigned.cleanup()
    assert runtime.path.exists()


def test_owned_controller_rejects_foreign_service_without_creating_runtime(monkeypatch):
    import run_e2e_owned as controller

    monkeypatch.setattr(
        controller,
        "assert_ports_free",
        lambda: (_ for _ in ()).throw(RuntimeError("Port 8000 occupied")),
    )
    monkeypatch.setattr(
        controller,
        "new_runtime",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not allocate")),
    )
    with pytest.raises(RuntimeError, match="occupied"):
        controller.run("playwright")


def test_actual_api_migration_seed_health_and_process_exit(runtime):
    import socket
    import urllib.request

    from owned_processes import _checked
    from run_e2e_owned import child_environment

    # Never reuse an unknown endpoint. The service binds loopback only; no POST
    # occurs before checking this random run's health identity.
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    log = runtime.tree.payload / "api.log"
    pid_file = runtime.tree.payload / "api.pid"
    code = """
import os, pathlib, sys
root, log, pidfile, port = sys.argv[1:]
stream = open(log, "w", encoding="utf-8")
sys.stdout = sys.stderr = stream
pathlib.Path(pidfile).write_text(str(os.getpid()), encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(root) / "scripts"))
from serve_e2e_api import main
main(port=int(port))
"""
    child = runtime.tree.start_python(
        "api-smoke",
        code,
        [str(ROOT), str(log), str(pid_file), str(port)],
        environment=child_environment(runtime, node=shutil.which("node")),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    import time

    deadline = time.monotonic() + 30
    health = None
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.fail(log.read_text(encoding="utf-8"))
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/v1/health", timeout=1) as response:
                health = json.load(response)
            break
        except (OSError, TimeoutError):
            time.sleep(0.05)
    assert health is not None, log.read_text(encoding="utf-8")
    assert health["e2e_run_id"] == runtime.run_id
    with opener.open(f"http://127.0.0.1:{port}/api/v1/projects", timeout=2) as response:
        projects = json.load(response)
    assert any(project["name"] == "e2e-lighthouse-workbench" for project in projects)
    verify_result = runtime.tree.payload / "listener-pids.json"
    verify_code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from e2e_runtime import assigned_runtime, verify_owned_listener
pids = verify_owned_listener(assigned_runtime(), int(sys.argv[2]))
pathlib.Path(sys.argv[3]).write_text(json.dumps(pids), encoding="utf-8")
"""
    verifier = runtime.tree.start_python(
        "port-owner",
        verify_code,
        [str(ROOT / "scripts"), str(port), str(verify_result)],
        environment=child_environment(runtime, node=shutil.which("node")),
    )
    assert verifier.wait(timeout=20) == 0
    actual_pid = int(pid_file.read_text(encoding="utf-8"))
    assert actual_pid in json.loads(verify_result.read_text(encoding="utf-8"))
    api = runtime.tree.api
    handle = _checked(api.OpenProcess(0x100000, False, actual_pid))
    try:
        runtime.cleanup()
        assert api.WaitForSingleObject(handle, 5000) == 0
        assert not runtime.tree.directory.exists()
    finally:
        _checked(api.CloseHandle(handle))


def test_job_zero_active_waits_for_assigned_handles_without_terminating_twice(
    process_tree, monkeypatch
):
    child = process_tree.start_python("wait-race", "import time; time.sleep(60)")
    assert child.assigned_to_job
    monkeypatch.setattr(
        child,
        "terminate",
        lambda: (_ for _ in ()).throw(AssertionError("assigned member already terminated by job")),
    )
    process_tree.stop()
    assert child.returncode is not None


def test_foreign_loopback_listener_is_rejected_by_actual_job_check(runtime):
    import socket

    from run_e2e_owned import child_environment

    result = runtime.tree.payload / "listener-check.txt"
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        port = foreign.getsockname()[1]
        code = """
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from e2e_runtime import assigned_runtime, verify_owned_listener
try:
    verify_owned_listener(assigned_runtime(), int(sys.argv[2]))
except RuntimeError as exc:
    pathlib.Path(sys.argv[3]).write_text(str(exc), encoding="utf-8")
else:
    raise AssertionError("foreign listener accepted")
"""
        child = runtime.tree.start_python(
            "foreign-port",
            code,
            [str(ROOT / "scripts"), str(port), str(result)],
            environment=child_environment(runtime, node=shutil.which("node")),
        )
        assert child.wait(timeout=20) == 0
        assert "does not belong" in result.read_text(encoding="utf-8")


@pytest.mark.parametrize("first_exit", [0, 7])
def test_outer_controller_runs_real_commands_and_preserves_failure(
    tmp_path, monkeypatch, first_exit
):
    import run_e2e_owned as controller
    from e2e_runtime import new_runtime

    fake_root = tmp_path / "controller-root"
    fake_root.mkdir()
    marker = fake_root / "second-command"
    monkeypatch.setattr(controller, "ROOT", fake_root)
    monkeypatch.setattr(controller, "assert_ports_free", lambda: None)
    monkeypatch.setattr(controller, "new_runtime", lambda _parent: new_runtime(tmp_path))
    # These are real disposable subprocesses, not Playwright or performance proof.
    monkeypatch.setattr(controller, "child_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        controller,
        "commands_for",
        lambda *_args: [
            [
                sys.executable,
                "-I",
                "-c",
                f"print('FIRST', flush=True); raise SystemExit({first_exit})",
            ],
            [
                sys.executable,
                "-I",
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('SECOND')",
                str(marker),
            ],
        ],
    )
    result = controller.run("playwright", timeout=15)
    assert result == (1 if first_exit else 0)
    assert marker.exists() is (first_exit == 0)
    report = next((fake_root / "output/playwright").glob("owned-*/summary.json"))
    summary = json.loads(report.read_text(encoding="utf-8"))
    assert summary["exit_code"] == first_exit
    assert summary["process_tree_stopped"] and summary["runtime_removed"]
    assert not Path(summary["directory"]).exists()
    assert "FIRST" in report.with_suffix(".log").read_text(encoding="utf-8")

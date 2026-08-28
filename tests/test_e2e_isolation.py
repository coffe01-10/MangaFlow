import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_serve():
    spec = importlib.util.spec_from_file_location(
        "serve_e2e_api", ROOT / "scripts" / "serve_e2e_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["serve_e2e_api"] = module
    spec.loader.exec_module(module)
    return module


def _probe(env: dict[str, str], cwd: Path) -> str:
    script = (
        "from app.config import get_settings\n"
        "settings = get_settings()\n"
        "print(f'project={settings.google_cloud_project!r}')\n"
        "print(f'proxy={settings.mangaflow_proxy_url!r}')\n"
        "print(f'db={settings.database_url}')\n"
        "print(f'run={settings.e2e_run_id!r}')\n"
    )
    return subprocess.check_output(
        [sys.executable, "-c", script],
        cwd=str(cwd),
        env=env,
        text=True,
    )


def test_fake_dotenv_is_ignored_and_runtime_is_unique(tmp_path):
    serve = _load_serve()
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / ".env").write_text(
        "GOOGLE_CLOUD_PROJECT=should-not-load\n"
        "MANGAFLOW_PROXY_URL=http://127.0.0.1:9\n",
        encoding="utf-8",
    )
    leftover = tmp_path / "old-runtime"
    leftover.mkdir()
    (leftover / "mangaflow.db").write_bytes(b"stale")

    first = serve.create_runtime(base_dir=tmp_path)
    second = serve.create_runtime(base_dir=tmp_path)
    assert first.path != second.path
    env = serve.build_isolated_env(first, seed=False)
    assert "should-not-load" not in env.get("GOOGLE_CLOUD_PROJECT", "")
    assert leftover.as_posix() not in env["DATABASE_URL"]
    assert first.run_id in env["DATABASE_URL"] or first.path.as_posix().replace("\\", "/") in env[
        "DATABASE_URL"
    ]

    probe = _probe(env, fake_root)
    assert "project=None" in probe
    assert "proxy=None" in probe
    assert f"run='{first.run_id}'" in probe
    assert first.path.resolve().as_posix() in probe.replace("\\", "/")
    first.cleanup()
    second.cleanup()
    assert not first.path.exists()


def test_refuses_to_reuse_existing_database(tmp_path):
    serve = _load_serve()
    runtime = serve.create_runtime(base_dir=tmp_path)
    (runtime.path / "mangaflow.db").write_bytes(b"old")
    try:
        serve.build_isolated_env(runtime, seed=False)
        raise AssertionError("expected reuse to fail")
    except RuntimeError as error:
        assert "refusing to reuse" in str(error)
    finally:
        runtime.cleanup()


def test_locked_sqlite_cleanup_can_retry(tmp_path):
    import sqlite3

    serve = _load_serve()
    runtime = serve.create_runtime(base_dir=tmp_path)
    connection = sqlite3.connect(runtime.path / "mangaflow.db")
    connection.execute("create table item(id integer)")
    connection.commit()
    try:
        runtime.cleanup()
    except RuntimeError:
        assert runtime.path.exists()
        assert runtime.cleaned is False
    else:
        assert not runtime.path.exists()
        assert runtime.cleaned is True
    connection.close()
    if runtime.path.exists():
        runtime.cleanup()
    assert not runtime.path.exists()
    assert runtime.cleaned is True


def test_cleanup_failure_after_retries_is_not_marked_done(tmp_path, monkeypatch):
    serve = _load_serve()
    runtime = serve.create_runtime(base_dir=tmp_path)
    (runtime.path / "mangaflow.db").write_bytes(b"data")
    monkeypatch.setattr(serve.shutil, "rmtree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve.time, "sleep", lambda *_args, **_kwargs: None)
    try:
        runtime.cleanup()
        raise AssertionError("expected cleanup to fail while the directory remains")
    except RuntimeError as error:
        assert "failed to remove" in str(error)
        assert runtime.cleaned is False
        assert runtime.path.exists()
    monkeypatch.undo()
    shutil.rmtree(runtime.path)
    assert not runtime.path.exists()


def test_forced_kill_skips_python_finally_until_controller_cleanup(tmp_path):
    serve = _load_serve()
    run_id = "forcedkill01"
    runtime_dir = tmp_path / f"mangaflow-e2e-{run_id}"
    child_script = (
        "import os, sqlite3, sys, time\n"
        f"os.environ['MANGAFLOW_E2E_RUN_ID'] = {run_id!r}\n"
        f"os.environ['MANGAFLOW_E2E_RUNTIME'] = {str(runtime_dir)!r}\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "from serve_e2e_api import create_runtime\n"
        "runtime = create_runtime()\n"
        "conn = sqlite3.connect(runtime.path / 'mangaflow.db')\n"
        "conn.execute('create table item(id integer)')\n"
        "conn.commit()\n"
        "print('READY', runtime.path, flush=True)\n"
        "try:\n"
        "    time.sleep(60)\n"
        "finally:\n"
        "    runtime.cleanup()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_script],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        ready = ""
        deadline = time.time() + 20
        while time.time() < deadline and "READY" not in ready:
            if process.poll() is not None:
                rest = (process.stdout.read() if process.stdout else b"").decode("utf-8", "replace")
                raise AssertionError(f"child exited before READY: {ready}{rest}")
            chunk = process.stdout.readline() if process.stdout else b""
            ready += chunk.decode("utf-8", "replace")
        assert "READY" in ready
        assert runtime_dir.exists()
        if sys.platform == "win32":
            killed = subprocess.run(
                ["taskkill", "/pid", str(process.pid), "/f"],
                check=False,
                capture_output=True,
            )
            assert killed.returncode in {0, 128}
        else:
            process.kill()
        process.wait(timeout=15)
        assert runtime_dir.exists(), "taskkill /F must skip Python finally so the controller can delete"
        controller = serve.IsolatedRuntime(run_id=run_id, path=runtime_dir)
        controller.cleanup()
        assert not runtime_dir.exists()
        assert controller.cleaned is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_assigned_runtime_must_match_run_id(tmp_path, monkeypatch):
    serve = _load_serve()
    monkeypatch.setenv("MANGAFLOW_E2E_RUN_ID", "abc123")
    monkeypatch.setenv("MANGAFLOW_E2E_RUNTIME", str(tmp_path / "foreign-dir"))
    try:
        serve.create_runtime(base_dir=tmp_path)
        raise AssertionError("expected foreign runtime path to fail")
    except RuntimeError as error:
        assert "does not belong" in str(error)


def test_phase2_runner_failure_paths():
    result = subprocess.run(
        [os.environ.get("MANGAFLOW_NODE", "node"), "--test", "scripts/phase2_runner.test.mjs"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

import importlib.util
import os
import subprocess
import sys
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


def test_abnormal_exit_cleans_runtime(tmp_path):
    serve = _load_serve()
    runtime = serve.create_runtime(base_dir=tmp_path)
    path = runtime.path
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        runtime.cleanup()
    assert not path.exists()


def test_phase2_runner_failure_paths():
    result = subprocess.run(
        [os.environ.get("MANGAFLOW_NODE", "node"), "--test", "scripts/phase2_runner.test.mjs"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

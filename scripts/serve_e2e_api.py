"""Start the API against a unique, disposable SQLite database.

Disables dotenv before any Settings import, ignores inherited vendor/proxy
variables, and never reuses a previous runtime directory.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"

VENDOR_ENV = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_LOCATION",
    "MANGAFLOW_CREDENTIAL_MASTER_KEY",
    "MANGAFLOW_PROXY_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "DATABASE_URL",
    "STORAGE_ROOT",
    "UPLOAD_ROOT",
)

@dataclass
class IsolatedRuntime:
    run_id: str
    path: Path
    cleaned: bool = False

    def cleanup(self) -> None:
        if not self.path.exists():
            self.cleaned = True
            return
        last_error: Exception | None = None
        for attempt in range(8):
            try:
                shutil.rmtree(self.path)
            except OSError as error:
                last_error = error
            if not self.path.exists():
                self.cleaned = True
                return
            time.sleep(0.15 * (attempt + 1))
        self.cleaned = False
        raise RuntimeError(f"failed to remove runtime {self.path}: {last_error}")


def create_runtime(base_dir: Path | None = None) -> IsolatedRuntime:
    run_id = os.environ.get("MANGAFLOW_E2E_RUN_ID") or uuid.uuid4().hex
    assigned = os.environ.get("MANGAFLOW_E2E_RUNTIME")
    if assigned:
        path = Path(assigned)
        if run_id not in path.name:
            raise RuntimeError("runtime path does not belong to this run")
        path.mkdir(parents=True, exist_ok=True)
    else:
        parent = Path(base_dir or tempfile.gettempdir())
        path = Path(tempfile.mkdtemp(prefix=f"mangaflow-e2e-{run_id}-", dir=str(parent)))
    (path / "storage").mkdir(exist_ok=True)
    (path / "uploads").mkdir(exist_ok=True)
    return IsolatedRuntime(run_id=run_id, path=path)


def build_isolated_env(
    runtime: IsolatedRuntime,
    *,
    seed: bool = True,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    for key in VENDOR_ENV:
        env.pop(key, None)
    database = runtime.path / "mangaflow.db"
    if database.exists():
        raise RuntimeError(f"refusing to reuse existing database {database}")
    env.update(
        {
            "PYTHONPATH": str(API_ROOT),
            "MANGAFLOW_DISABLE_DOTENV": "1",
            "MANGAFLOW_E2E_RUN_ID": runtime.run_id,
            "E2E_RUN_ID": runtime.run_id,
            "MANGAFLOW_E2E_SEED": "1" if seed else "0",
            "DATABASE_URL": "sqlite:///" + database.resolve().as_posix(),
            "STORAGE_ROOT": str(runtime.path / "storage"),
            "UPLOAD_ROOT": str(runtime.path / "uploads"),
            "WEB_ORIGIN": "http://127.0.0.1:3000",
            "QUEUE_ENABLED": "false",
            "ENVIRONMENT": "development",
            "GOOGLE_GENAI_USE_VERTEXAI": "false",
        }
    )
    if extra:
        env.update(extra)
    return env


def migrate(env: dict[str, str], cwd: Path) -> None:
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(API_ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(cwd),
        env=env,
    )


def _record_owned_pointer(runtime: IsolatedRuntime) -> None:
    pointer = ROOT / "output" / "playwright" / "owned-runtime.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    if pointer.exists():
        try:
            loaded = json.loads(pointer.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}
    payload.update({"runtime": str(runtime.path), "runId": runtime.run_id, "pid": os.getpid()})
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    (runtime.path / "owner.pid").write_text(str(os.getpid()), encoding="utf-8")


def _install_cleanup(runtime: IsolatedRuntime) -> None:
    atexit.register(runtime.cleanup)

    def _on_signal(_signum, _frame) -> None:
        runtime.cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _on_signal)


def main() -> None:
    sys.path.insert(0, str(API_ROOT))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    runtime = create_runtime()
    controller_owned = bool(os.environ.get("MANGAFLOW_E2E_RUNTIME"))
    _record_owned_pointer(runtime)
    if not controller_owned:
        _install_cleanup(runtime)
    env = build_isolated_env(runtime, seed=os.environ.get("MANGAFLOW_E2E_SEED", "1") != "0")
    for key in VENDOR_ENV:
        os.environ.pop(key, None)
    os.environ.update(env)
    os.chdir(ROOT)
    try:
        migrate(env, cwd=ROOT)
        if env.get("MANGAFLOW_E2E_SEED") == "1":
            from e2e_fixtures import seed_gate_projects

            seed_gate_projects(env["DATABASE_URL"], Path(env["STORAGE_ROOT"]))
        # Run uvicorn in this process so Windows taskkill /T /F (or Playwright
        # stopping the webServer PID) cannot leave an orphan child holding SQLite.
        # Do not chdir into the runtime directory; that locks the folder on Windows.
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
    finally:
        if not controller_owned:
            runtime.cleanup()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

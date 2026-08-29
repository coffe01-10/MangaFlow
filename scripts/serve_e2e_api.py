"""Serve disposable E2E data only inside the acceptance controller's Job Object."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_runtime import BrowserRuntime, assigned_runtime, new_runtime  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"


def create_runtime(base_dir: Path | None = None) -> BrowserRuntime:
    # Explicit base_dir is for isolated unit fixtures. The service never creates
    # or adopts an arbitrary assigned directory, even if its name contains a token.
    if base_dir is not None:
        return new_runtime(base_dir)
    return assigned_runtime()


def build_isolated_env(runtime: BrowserRuntime, *, seed: bool = True) -> dict[str, str]:
    database = runtime.path / "mangaflow.db"
    if database.exists():
        raise RuntimeError("refusing to reuse existing database")
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        PYTHONPATH=str(API_ROOT),
        MANGAFLOW_DISABLE_DOTENV="1",
        MANGAFLOW_E2E_RUN_ID=runtime.run_id,
        E2E_RUN_ID=runtime.run_id,
        MANGAFLOW_E2E_SEED="1" if seed else "0",
        DATABASE_URL="sqlite:///" + database.resolve().as_posix(),
        STORAGE_ROOT=str(runtime.path / "storage"),
        UPLOAD_ROOT=str(runtime.path / "uploads"),
        WEB_ORIGIN="http://127.0.0.1:3000",
        QUEUE_ENABLED="false",
        ENVIRONMENT="development",
        GOOGLE_GENAI_USE_VERTEXAI="false",
    )
    return env


def migrate(env: dict[str, str], cwd: Path) -> None:
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "-c", str(API_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=str(cwd),
        env=env,
    )


def main(*, port: int = 8000) -> None:
    runtime = create_runtime()  # Verify actual Job Object membership before writes.
    env = build_isolated_env(runtime, seed=os.environ.get("MANGAFLOW_E2E_SEED", "1") != "0")
    os.environ.clear()
    os.environ.update(env)
    sys.path.insert(0, str(API_ROOT))
    os.chdir(ROOT)
    migrate(env, cwd=ROOT)
    if env["MANGAFLOW_E2E_SEED"] == "1":
        from e2e_fixtures import seed_gate_projects

        seed_gate_projects(env["DATABASE_URL"], Path(env["STORAGE_ROOT"]))
    import uvicorn

    # No API-side deletion, atexit or shared PID pointer. The outer controller
    # stops and verifies the entire job, including this venv's redirector.
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()

"""Start the API against an isolated SQLite database for browser acceptance.

Does not read or copy a developer .env, production database, or real vendor
credentials. Migrations apply only to the temporary database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
RUNTIME = ROOT / "output" / "playwright" / "e2e-runtime"
DATABASE = RUNTIME / "mangaflow.db"
STORAGE = RUNTIME / "storage"
UPLOADS = RUNTIME / "uploads"

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
)


def _prepare() -> dict[str, str]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STORAGE.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in VENDOR_ENV:
        env.pop(key, None)

    db_url = "sqlite:///" + DATABASE.resolve().as_posix()
    env.update(
        {
            "PYTHONPATH": str(API_ROOT),
            "DATABASE_URL": db_url,
            "STORAGE_ROOT": str(STORAGE),
            "UPLOAD_ROOT": str(UPLOADS),
            "WEB_ORIGIN": "http://127.0.0.1:3000",
            "QUEUE_ENABLED": "false",
            "ENVIRONMENT": "development",
            "GOOGLE_GENAI_USE_VERTEXAI": "false",
        }
    )
    return env


def _migrate(env: dict[str, str]) -> None:
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
        cwd=str(ROOT),
        env=env,
    )


def main() -> None:
    env = _prepare()
    _migrate(env)
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(API_ROOT),
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=str(ROOT),
            env=env,
        )
    )


if __name__ == "__main__":
    main()

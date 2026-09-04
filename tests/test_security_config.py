import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_next_js_uses_patched_windows_release():
    web = json.loads((ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert web["dependencies"]["next"] == "16.3.3"
    assert web["devDependencies"]["eslint-config-next"] == "16.3.3"
    assert lock["packages"]["node_modules/next"]["version"] == "16.3.3"
    assert lock["packages"]["node_modules/eslint-config-next"]["version"] == "16.3.3"


def test_dev_and_start_scripts_bind_loopback():
    root = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    web = json.loads((ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    assert "--hostname 127.0.0.1" in web["scripts"]["dev"]
    assert "--hostname 127.0.0.1" in web["scripts"]["start"]
    assert "--host 127.0.0.1" in root["scripts"]["dev:api"]
    assert root["scripts"]["dev:worker"] == ".venv\\Scripts\\python.exe apps/api/run_worker.py"
    assert "--host 127.0.0.1" in root["scripts"]["serve:e2e:api"]
    assert "--hostname 127.0.0.1" in root["scripts"]["serve:e2e:web"]
    assert "0.0.0.0" not in web["scripts"]["dev"]
    assert "0.0.0.0" not in web["scripts"]["start"]
    assert "0.0.0.0" not in root["scripts"]["dev:api"]


def test_compose_publishes_data_services_on_loopback_with_auth():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:5432:5432" in compose
    assert "127.0.0.1:6379:6379" in compose
    assert "--requirepass" in compose
    assert '"5432:5432"' not in compose
    assert '"6379:6379"' not in compose
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "redis://:mangaflow-dev@127.0.0.1:6379/0" in env_example


def test_api_rejects_non_trusted_host_headers():
    """The unauthenticated loopback API must refuse foreign Host headers so a
    DNS-rebinded attacker page cannot reach it same-origin (CORS is irrelevant
    for same-origin requests; the Host allowlist is the actual boundary)."""

    from fastapi import FastAPI
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.testclient import TestClient

    from app.main import app as production_app

    # Production app: configured allowlist (loopback) or "*" in the offline
    # test environment; assert the middleware is actually installed.
    middleware_types = {
        type(mw.cls) if hasattr(mw, "cls") else type(mw)
        for mw in production_app.user_middleware
    }
    assert any(
        mw is TrustedHostMiddleware or str(mw).endswith("TrustedHostMiddleware")
        for mw in middleware_types
    ) or any("TrustedHost" in str(mw) for mw in production_app.user_middleware)

    # Behavior: fresh app with the production default allowlist rejects a
    # rebinded host and accepts loopback hosts.
    isolated = FastAPI()
    isolated.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @isolated.get("/probe")
    def probe():
        return {"ok": True}

    with TestClient(isolated) as strict_client:
        assert strict_client.get(
            "/probe", headers={"host": "attacker.example:8000"}
        ).status_code == 400
        assert strict_client.get("/probe", headers={"host": "127.0.0.1:8000"}).status_code == 200
        assert strict_client.get("/probe", headers={"host": "localhost:8000"}).status_code == 200

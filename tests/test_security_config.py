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


def test_api_rejects_non_trusted_host_headers(monkeypatch):
    """The unauthenticated loopback API must refuse foreign Host headers so a
    DNS-rebinded attacker page cannot reach it same-origin (CORS is irrelevant
    for same-origin requests; the Host allowlist is the actual boundary)."""

    from app.config import get_settings
    from app.main import app as production_app
    from fastapi import FastAPI
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.testclient import TestClient

    # Production app: configured allowlist (loopback) or "*" in the offline
    # test environment; assert the middleware is actually installed AND wired
    # to the live settings value — middleware presence alone would still pass
    # if main.py stopped reading api_trusted_hosts.
    wired = next(
        mw for mw in production_app.user_middleware if "TrustedHost" in str(mw)
    )
    wired_hosts = list(getattr(wired, "kwargs", {}).get("allowed_hosts") or [])
    configured = [
        host.strip()
        for host in get_settings().api_trusted_hosts.split(",")
        if host.strip()
    ]
    assert wired_hosts == configured

    # Behavior: rebuild the middleware against the production DEFAULT allowlist
    # — conftest pins API_TRUSTED_HOSTS="*" so TestClient's synthetic
    # "testserver" host works; delenv + cache_clear re-reads the real default.
    monkeypatch.delenv("API_TRUSTED_HOSTS", raising=False)
    get_settings.cache_clear()
    try:
        default_hosts = [
            host.strip()
            for host in get_settings().api_trusted_hosts.split(",")
            if host.strip()
        ]
    finally:
        get_settings.cache_clear()
    assert "*" not in default_hosts
    assert "localhost" in default_hosts and "127.0.0.1" in default_hosts

    isolated = FastAPI()
    isolated.add_middleware(TrustedHostMiddleware, allowed_hosts=default_hosts)

    @isolated.get("/probe")
    def probe():
        return {"ok": True}

    with TestClient(isolated) as strict_client:
        assert strict_client.get(
            "/probe", headers={"host": "attacker.example:8000"}
        ).status_code == 400
        assert strict_client.get("/probe", headers={"host": "127.0.0.1:8000"}).status_code == 200
        assert strict_client.get("/probe", headers={"host": "localhost:8000"}).status_code == 200

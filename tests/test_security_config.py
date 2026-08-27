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
    assert "--with-scheduler" in root["scripts"]["dev:worker"]
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

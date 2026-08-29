import os
import subprocess
import sys
from pathlib import Path

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"

_DOTENV_PROJECT = "dotenv-project-sentinel"
_DOTENV_PROXY = "http://dotenv-proxy-sentinel.example"
_ENV_PROJECT = "environment-project-sentinel"
_STRIP_NAMES = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "MANGAFLOW_CREDENTIAL_MASTER_KEY",
    "MANGAFLOW_PROXY_URL",
)


def _allowlist_env(**extra: str) -> dict[str, str]:
    allowed = {
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "PATHEXT",
        "PATH",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
        "PYTHONUTF8",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(extra)
    return env


def _write_sentinels(tmp_path: Path) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GOOGLE_CLOUD_PROJECT={_DOTENV_PROJECT}\n"
        f"MANGAFLOW_PROXY_URL={_DOTENV_PROXY}\n",
        encoding="utf-8",
    )
    credential = tmp_path / "credential-sentinel.json"
    credential.write_text('{"sentinel": true}\n', encoding="utf-8")
    return credential


def _run_fresh(tmp_path: Path, code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_offline_suite_disables_dotenv_and_uses_placeholder_provider(
    _offline_configured_provider_premise,
):
    settings = get_settings()
    creds = _offline_configured_provider_premise
    assert os.environ.get("MANGAFLOW_DISABLE_DOTENV") == "1"
    assert settings.google_cloud_project == "test-project"
    assert settings.google_application_credentials == creds
    assert creds.name == "placeholder.json"
    assert creds.read_text(encoding="utf-8") == "{}"
    assert "offline-provider" in creds.as_posix()


def test_fresh_interpreter_loads_env_sentinels_when_disable_flag_absent(tmp_path):
    credential = _write_sentinels(tmp_path)
    result = _run_fresh(
        tmp_path,
        (
            "from app.config import get_settings\n"
            "import app.config as config\n"
            "s = get_settings()\n"
            "print(config._ENV_FILE)\n"
            "print(s.google_cloud_project)\n"
            "print(s.mangaflow_proxy_url)\n"
            "print(s.google_application_credentials)\n"
        ),
        _allowlist_env(
            PYTHONPATH=str(API_ROOT),
            GOOGLE_CLOUD_PROJECT=_ENV_PROJECT,
            GOOGLE_APPLICATION_CREDENTIALS=str(credential),
        ),
    )
    assert result.returncode == 0, result.stderr
    env_file, project, proxy, creds = result.stdout.strip().splitlines()
    assert env_file == ".env"
    assert project == _ENV_PROJECT
    assert proxy == _DOTENV_PROXY
    assert "credential-sentinel.json" in creds
    assert project != _DOTENV_PROJECT


def test_fresh_interpreter_disable_dotenv_before_config_blocks_sentinels(tmp_path):
    credential = _write_sentinels(tmp_path)
    strip = ", ".join(repr(name) for name in _STRIP_NAMES)
    result = _run_fresh(
        tmp_path,
        (
            "import os\n"
            "assert os.environ.get('MANGAFLOW_DISABLE_DOTENV') == '1'\n"
            f"for name in ({strip}):\n"
            "    os.environ.pop(name, None)\n"
            "import app.config as config\n"
            "from app.config import get_settings\n"
            "s = get_settings()\n"
            "assert config._ENV_FILE is None, config._ENV_FILE\n"
            "assert s.google_cloud_project is None\n"
            "assert s.mangaflow_proxy_url is None\n"
            "assert s.google_application_credentials is None\n"
            "print('BOOT_OK', config._ENV_FILE, s.google_cloud_project, s.mangaflow_proxy_url)\n"
        ),
        _allowlist_env(
            PYTHONPATH=str(API_ROOT),
            MANGAFLOW_DISABLE_DOTENV="1",
            GOOGLE_CLOUD_PROJECT=_ENV_PROJECT,
            GOOGLE_APPLICATION_CREDENTIALS=str(credential),
            MANGAFLOW_PROXY_URL=_DOTENV_PROXY,
        ),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "BOOT_OK" in result.stdout
    assert _DOTENV_PROJECT not in result.stdout
    assert _ENV_PROJECT not in result.stdout
    assert "dotenv-proxy-sentinel" not in result.stdout

import os
from pathlib import Path

from app.config import get_settings


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
    repo_env = Path(__file__).resolve().parents[1] / ".env"
    if repo_env.exists():
        assert settings.google_application_credentials.resolve() != repo_env.resolve()
    assert settings.google_application_credentials != Path(
        os.environ.get("REAL_GOOGLE_APPLICATION_CREDENTIALS", "")
    )

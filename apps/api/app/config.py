import base64
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = None if os.environ.get("MANGAFLOW_DISABLE_DOTENV") == "1" else ".env"


class Settings(BaseSettings):
    app_name: str = "MangaFlow AI"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str = "sqlite:///./storage/mangaflow.db"
    storage_root: Path = Path("./storage")
    upload_root: Path = Path("./uploads")
    web_origin: str = "http://localhost:3000"

    google_cloud_project: str | None = None
    google_cloud_location: str = "global"
    google_application_credentials: Path | None = None
    google_genai_use_vertexai: bool = True

    vertex_text_model: str = "gemini-3.5-flash"
    vertex_image_model_nano_banana_2: str = "gemini-3.1-flash-image"
    vertex_image_model_nano_banana_pro: str = "gemini-3-pro-image-preview"

    # Write-only provider credentials are encrypted with this server-side key.
    # The value must be a URL-safe base64 encoded 32-byte key. Existing
    # environment-managed Vertex credentials keep working when it is absent.
    mangaflow_credential_master_key: str | None = None
    mangaflow_proxy_url: str | None = None
    allow_private_provider_networks: bool = False

    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "mangaflow"
    queue_enabled: bool = True
    job_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    job_lease_seconds: int = Field(default=120, ge=30, le=3600)
    max_auto_repairs: int = Field(default=3, ge=0, le=10)
    cli_run_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    cli_run_timeout_grace_seconds: int = Field(default=5, ge=0, le=60)
    cli_channel_max_concurrency: int = Field(default=1, ge=1, le=16)

    # Host-header allowlist (comma separated). Loopback-only by default: the
    # API is unauthenticated and must not answer DNS-rebinded attacker hosts.
    # Set to "*" only for isolated test environments, or add your deployment
    # host when binding beyond loopback.
    api_trusted_hosts: str = "localhost,127.0.0.1"

    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    upload_form_overhead_bytes: int = Field(default=64 * 1024, ge=0)
    max_image_pixels: int = Field(default=40_000_000, ge=1)
    max_image_side: int = Field(default=16_384, ge=1)
    max_provider_metadata_bytes: int = Field(default=1 * 1024 * 1024, ge=1)
    max_discovered_models: int = Field(default=500, ge=1)
    allowed_upload_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/webp",
        "text/plain",
        "text/markdown",
    )
    e2e_run_id: str | None = None

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def vertex_configured(self) -> bool:
        return bool(
            self.google_cloud_project
            and self.google_application_credentials
            and self.google_application_credentials.is_file()
        )

    @property
    def provider_credentials_writable(self) -> bool:
        if not self.mangaflow_credential_master_key:
            return self.environment.lower() == "development"
        try:
            return (
                len(
                    base64.urlsafe_b64decode(
                        self.mangaflow_credential_master_key.encode("ascii")
                    )
                )
                == 32
            )
        except Exception:
            return False

    def ensure_directories(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.upload_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

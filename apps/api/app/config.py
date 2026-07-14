from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    vertex_image_model_default: str = "gemini-3.1-flash-image"
    vertex_image_model_quality: str = "gemini-3-pro-image"

    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    allowed_upload_types: tuple[str, ...] = (
        "image/png",
        "image/jpeg",
        "image/webp",
        "text/plain",
        "text/markdown",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
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

    def ensure_directories(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.upload_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RuntimeSettingsRead(BaseModel):
    queue_mode: Literal["AUTO", "LOCAL", "REDIS"] = "AUTO"
    job_timeout_seconds: int
    max_auto_repairs: int
    default_concurrency: int = 4
    health_check_interval_seconds: int = 600
    ui_poll_interval_seconds: int = 3000
    workflow_autosave_ms: int = 800
    database_backend: str
    storage_root: str
    upload_root: str
    redis_configured: bool
    version: int


class RuntimeSettingsUpdate(BaseModel):
    queue_mode: Literal["AUTO", "LOCAL", "REDIS"] | None = None
    job_timeout_seconds: int | None = Field(default=None, ge=30, le=3600)
    max_auto_repairs: int | None = Field(default=None, ge=0, le=10)
    default_concurrency: int | None = Field(default=None, ge=1, le=8)
    health_check_interval_seconds: int | None = Field(default=None, ge=60, le=3600)
    ui_poll_interval_seconds: int | None = Field(default=None, ge=1000, le=60_000)
    workflow_autosave_ms: int | None = Field(default=None, ge=200, le=10_000)
    version: int = Field(default=1, ge=1)


class VertexHealthRead(BaseModel):
    configured: bool
    health_state: Literal["UNCONFIGURED", "CHECKING", "HEALTHY", "DEGRADED", "OFFLINE"]
    credential_file_present: bool
    project: str | None
    location: str
    text_model: str
    image_models: list[str]
    last_checked_at: datetime | None
    last_success_at: datetime | None
    token_expires_at: datetime | None
    consecutive_failures: int
    latency_ms: int | None
    error_code: str | None
    message: str
    text_model_access: str
    image_model_access: dict[str, Any]


class VertexVerifyRequest(BaseModel):
    level: Literal["CREDENTIALS", "TEXT_MODEL", "IMAGE_MODEL"] = "CREDENTIALS"
    image_model_alias: Literal["image.nano_banana_2", "image.nano_banana_pro"] | None = None


class DiagnosticCheckRead(BaseModel):
    id: str
    label: str
    status: Literal["OK", "WARNING", "FAILED", "NOT_CHECKED"]
    message: str
    latency_ms: int | None = None


class DiagnosticsRead(BaseModel):
    checks: list[DiagnosticCheckRead]
    checked_at: datetime


class ProjectSummaryRead(BaseModel):
    project_id: str
    chapter_count: int
    page_count: int
    asset_count: int
    pending_job_count: int
    failed_job_count: int
    active_style_name: str | None
    active_workflow_id: str | None
    active_workflow_status: str | None
    section_statuses: dict[str, str]


class AppSettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: dict[str, Any]
    version: int
    updated_at: datetime

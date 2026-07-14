from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.states import Resolution, WorkflowMode


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="zh-CN", max_length=16)
    reading_direction: str = Field(default="rtl", pattern="^(rtl|ltr)$")
    page_ratio: str = Field(default="b5_portrait", max_length=32)
    default_resolution: Resolution = Resolution.STANDARD_2K
    draft_resolution: Resolution = Resolution.DRAFT_1K
    workflow_mode: WorkflowMode = WorkflowMode.SEMI_AUTO
    default_concurrency: int = Field(default=4, ge=1, le=8)
    ocr_enabled: bool = True
    consistency_check_enabled: bool = True
    image_model_alias: str = Field(default="image.fast", pattern="^image\\.(fast|quality)$")


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_resolution: Resolution | None = None
    draft_resolution: Resolution | None = None
    workflow_mode: WorkflowMode | None = None
    default_concurrency: int | None = Field(default=None, ge=1, le=8)
    ocr_enabled: bool | None = None
    consistency_check_enabled: bool | None = None
    image_model_alias: str | None = Field(default=None, pattern="^image\\.(fast|quality)$")
    version: int = Field(ge=1)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    language: str
    reading_direction: str
    page_ratio: str
    default_resolution: Resolution
    draft_resolution: Resolution
    workflow_mode: WorkflowMode
    default_concurrency: int
    ocr_enabled: bool
    consistency_check_enabled: bool
    text_model_alias: str
    image_model_alias: str
    created_at: datetime
    updated_at: datetime
    version: int


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    original_name: str
    mime_type: str
    byte_size: int
    width: int | None
    height: int | None
    status: str
    created_at: datetime


class ModelCapabilityRead(BaseModel):
    provider: str
    model_id: str
    logical_alias: str
    display_name: str
    operations: list[str]
    resolutions: list[str]
    preview_resolutions: list[str]
    max_reference_images: int
    regions: list[str]


class VertexStatusRead(BaseModel):
    configured: bool
    credential_file_present: bool
    location: str
    text_model: str
    image_models: list[str]
    verification: str
    message: str

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.states import JobStatus, PageStatus, Resolution, WorkflowMode

IMAGE_MODEL_PATTERN = r"^image\.(nano_banana_2|nano_banana_pro)$"


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
    last_image_model_alias: str | None = Field(default=None, pattern=IMAGE_MODEL_PATTERN)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_resolution: Resolution | None = None
    draft_resolution: Resolution | None = None
    workflow_mode: WorkflowMode | None = None
    default_concurrency: int | None = Field(default=None, ge=1, le=8)
    ocr_enabled: bool | None = None
    consistency_check_enabled: bool | None = None
    last_image_model_alias: str | None = Field(default=None, pattern=IMAGE_MODEL_PATTERN)
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
    last_image_model_alias: str | None
    default_style_id: str | None
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
    content_url: str | None = None


class AssetUpdate(BaseModel):
    kind: str = Field(pattern="^(CHARACTER_REFERENCE|OUTFIT_REFERENCE|STYLE_REFERENCE)$")


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


class SourceImportRequest(BaseModel):
    title: str = Field(default="正文", min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)
    source_type: str = Field(default="PASTE", pattern="^(PASTE|TXT|MARKDOWN)$")


class SourceSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ordinal: int
    text: str
    start_offset: int
    end_offset: int


class ChapterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    ordinal: int
    status: str
    current_source_revision_id: str | None
    created_at: datetime
    updated_at: datetime
    version: int
    source_character_count: int = 0
    segment_count: int = 0
    page_count: int = 0
    coverage_ratio: float = 0


class SourceRevisionCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)
    source_type: str = Field(default="PASTE", pattern="^(PASTE|TXT|MARKDOWN)$")


class SourceRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_id: str
    revision: int
    source_type: str
    original_text: str
    character_count: int
    imported_at: datetime


class BeatRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scene_id: str
    ordinal: int
    action: str
    speaker_name: str
    dialogue: str
    narration: str
    subtext: str
    emotion: str
    importance: float
    must_visualize: bool
    mergeable: bool
    page_turn_hook: bool
    source_range: dict


class SceneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_id: str
    ordinal: int
    location: str
    time_label: str
    weather: str
    purpose: str
    emotional_arc: str
    source_range: dict
    outfit_assignments: dict
    beats: list[BeatRead] = Field(default_factory=list)


class ScriptRead(BaseModel):
    chapter_id: str
    status: str
    revision_no: int | None
    coverage: dict
    scenes: list[SceneRead]


class SourceImportRead(BaseModel):
    chapters: list[ChapterRead]
    total_characters: int


class CharacterCreate(BaseModel):
    primary_name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=40)
    canonical_description: str = Field(default="", max_length=8000)
    locked_features: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class CharacterUpdate(BaseModel):
    primary_name: str | None = Field(default=None, min_length=1, max_length=120)
    aliases: list[str] | None = Field(default=None, max_length=40)
    canonical_description: str | None = Field(default=None, max_length=8000)
    locked_features: list[str] | None = None
    forbidden_changes: list[str] | None = None
    version: int = Field(ge=1)


class CharacterReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    character_id: str
    asset_id: str
    angle: str
    is_canonical: bool
    created_at: datetime


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    primary_name: str
    aliases: list[str]
    alias_conflict: bool
    canonical_description: str
    locked_features: list[str]
    forbidden_changes: list[str]
    status: str
    version: int
    references: list[CharacterReferenceRead] = Field(default_factory=list)


class CharacterReferenceCreate(BaseModel):
    asset_id: str
    angle: str = Field(default="unspecified", max_length=32)
    is_canonical: bool = False


class OutfitCreate(BaseModel):
    character_id: str
    name: str = Field(min_length=1, max_length=120)
    components: dict = Field(default_factory=dict)
    state_rules: dict = Field(default_factory=dict)
    locked_fields: list[str] = Field(default_factory=list)
    reference_asset_ids: list[str] = Field(default_factory=list)


class OutfitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    character_id: str
    name: str
    components: dict
    state_rules: dict
    locked_fields: list
    reference_asset_ids: list[str]
    status: str
    version: int


class StyleProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color_mode: str = Field(default="monochrome", max_length=24)
    profile: dict = Field(default_factory=dict)
    reference_asset_ids: list[str] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=list)


class StyleProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    color_mode: str
    profile: dict
    locked_fields: list
    status: str
    version: int


class SceneOutfitUpdate(BaseModel):
    assignments: dict[str, str] = Field(default_factory=dict)


class CharacterSheetCreate(BaseModel):
    model_alias: str = Field(pattern=IMAGE_MODEL_PATTERN)
    resolution: Resolution = Resolution.DRAFT_1K
    variants: list[str] = Field(
        default_factory=lambda: ["FRONT", "SIDE", "BACK", "EXPRESSION"],
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_variants(self):
        allowed = {"FRONT", "SIDE", "BACK", "EXPRESSION"}
        if any(item not in allowed for item in self.variants):
            raise ValueError("角色形象只支持正面、侧面、背面和表情")
        self.variants = list(dict.fromkeys(self.variants))
        return self


class AssetBatchCreate(BaseModel):
    target_type: str = Field(pattern="^(CHARACTER|OUTFIT|STYLE)$")
    target_id: str
    generation_kind: str = Field(pattern="^(CHARACTER|OUTFIT|STYLE_TEST)$")


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_id: str
    page_number: int
    revision_no: int
    page_function: str
    panel_count: int
    reading_direction: str
    resolution: Resolution
    status: PageStatus
    estimated_text_chars: int
    estimated_bubbles: int
    source_coverage: dict
    selected_candidate_id: str | None
    continuity_status: str
    scene_ids: list
    beat_ids: list


class PlanRequest(BaseModel):
    replace_existing: bool = True
    from_page_number: int | None = Field(default=None, ge=1)


class PlanRead(BaseModel):
    chapter_id: str
    page_count: int
    source_segment_count: int
    covered_segment_count: int
    coverage_ratio: float
    pages: list[PageRead]


class GenerationBatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str | None
    page_id: str | None
    target_type: str | None
    target_id: str | None
    ordinal: int
    generation_kind: str
    status: str
    created_at: datetime
    closed_at: datetime | None


class CandidateCreate(BaseModel):
    model_alias: str = Field(pattern=IMAGE_MODEL_PATTERN)
    resolution: Resolution


class PageCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    batch_id: str
    page_id: str | None
    ordinal: int
    model_alias: str
    resolution: Resolution
    status: str
    asset_id: str | None
    job_id: str | None
    is_favorite: bool
    is_selected: bool
    created_at: datetime
    content_url: str | None = None


class CandidateQueuedRead(BaseModel):
    job_id: str
    job_status: JobStatus
    candidate: PageCandidateRead


class AssetCandidateCreate(BaseModel):
    model_alias: str = Field(pattern=IMAGE_MODEL_PATTERN)
    resolution: Resolution
    variant: str = Field(pattern="^(FRONT|SIDE|BACK|EXPRESSION|OUTFIT|STYLE_TEST)$")
    instruction: str = Field(default="", max_length=2000)


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class SelectCandidateRequest(BaseModel):
    candidate_id: str


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    target_type: str
    target_id: str
    job_type: str
    priority: int
    status: JobStatus
    progress: int
    attempt_count: int
    max_attempts: int
    model_alias: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class LibraryBatchRead(BaseModel):
    batch: GenerationBatchRead
    candidates: list[PageCandidateRead]


class LibraryRead(BaseModel):
    groups: list[LibraryBatchRead]
    total_candidates: int
    favorite_count: int


class InspectionRequest(BaseModel):
    categories: list[str] = Field(
        default_factory=lambda: [
            "TEXT",
            "SPEAKER",
            "CHARACTER",
            "OUTFIT",
            "PROP",
            "CONTINUITY",
        ]
    )


class InspectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_id: str | None
    category: str
    outcome: str
    score: float | None
    details: dict
    regions: list
    severity: str
    created_at: datetime


class RepairRequest(BaseModel):
    inspection_result_id: str
    repair_type: str = Field(pattern="^(TEXT_REGION|BUBBLE_REGION|PANEL|PAGE)$")
    target_regions: list[dict] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    model_alias: str = Field(pattern=IMAGE_MODEL_PATTERN)
    resolution: Resolution


class UpscaleRequest(BaseModel):
    model_alias: str = Field(pattern=IMAGE_MODEL_PATTERN)
    resolution: Resolution

    @model_validator(mode="after")
    def validate_resolution(self):
        if self.resolution == Resolution.DRAFT_1K:
            raise ValueError("升清目标只能选择 2K 或 4K")
        return self


class ExportRequest(BaseModel):
    export_type: str = Field(pattern="^(PNG|PDF|JSON)$")


class ExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str | None
    export_type: str
    byte_size: int
    page_count: int
    created_at: datetime
    download_url: str

    @model_validator(mode="before")
    @classmethod
    def add_download_url(cls, value):
        if hasattr(value, "id"):
            return {
                "id": value.id,
                "project_id": value.project_id,
                "chapter_id": value.chapter_id,
                "export_type": value.export_type,
                "byte_size": value.byte_size,
                "page_count": value.page_count,
                "created_at": value.created_at,
                "download_url": f"/api/v1/exports/{value.id}/download",
            }
        return value

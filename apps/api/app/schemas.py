from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.domain.states import (
    CharacterPresence,
    JobStatus,
    PageStatus,
    Resolution,
    WorkflowMode,
)

MODEL_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="zh-CN", max_length=16)
    reading_direction: str = Field(default="rtl", pattern="^(rtl|ltr)$")
    page_ratio: str = Field(default="b5_portrait", max_length=32)
    default_resolution: Resolution = Resolution.STANDARD_2K
    draft_resolution: Resolution = Resolution.DRAFT_1K
    workflow_mode: WorkflowMode = WorkflowMode.SEMI_AUTO
    default_concurrency: int = Field(default=4, ge=1, le=8)
    consistency_check_enabled: bool = True
    last_image_model_alias: str | None = Field(default=None, pattern=MODEL_REFERENCE_PATTERN)
    default_text_model_id: str | None = Field(default=None, max_length=36)
    last_image_model_id: str | None = Field(default=None, max_length=36)
    text_model_alias: str = Field(default="text.fast", pattern=MODEL_REFERENCE_PATTERN)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_resolution: Resolution | None = None
    draft_resolution: Resolution | None = None
    workflow_mode: WorkflowMode | None = None
    default_concurrency: int | None = Field(default=None, ge=1, le=8)
    consistency_check_enabled: bool | None = None
    last_image_model_alias: str | None = Field(default=None, pattern=MODEL_REFERENCE_PATTERN)
    default_text_model_id: str | None = Field(default=None, max_length=36)
    last_image_model_id: str | None = Field(default=None, max_length=36)
    text_model_alias: str | None = Field(default=None, pattern=MODEL_REFERENCE_PATTERN)
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
    consistency_check_enabled: bool
    text_model_alias: str
    last_image_model_alias: str | None
    default_text_model_id: str | None
    last_image_model_id: str | None
    default_style_id: str | None
    created_at: datetime
    updated_at: datetime
    version: int


class DashboardNextAction(BaseModel):
    section: str
    label: str
    reason: str


class ProjectDashboardItem(BaseModel):
    project: ProjectRead
    chapter_count: int
    page_count: int
    selected_page_count: int
    review_page_count: int
    stale_selected_page_count: int
    candidate_count: int
    pending_job_count: int
    failed_job_count: int
    next_action: DashboardNextAction


class DashboardTotals(BaseModel):
    project_count: int
    page_count: int
    selected_page_count: int
    review_page_count: int
    pending_job_count: int


class DashboardAIOverview(BaseModel):
    """首页徽标所需的最小模型/供应商摘要，避免额外的首屏请求。"""

    enabled_model_count: int = 0
    healthy_connection_count: int = 0
    configured_connection_count: int = 0


class ProjectDashboardRead(BaseModel):
    totals: DashboardTotals
    ai_overview: DashboardAIOverview = DashboardAIOverview()
    projects: list[ProjectDashboardItem]


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    original_name: str
    display_name: str | None
    mime_type: str
    byte_size: int
    width: int | None
    height: int | None
    status: str
    created_at: datetime
    content_url: str | None = None
    thumbnail_url: str | None = None


class AssetUpdate(BaseModel):
    kind: str | None = Field(
        default=None,
        pattern="^(CHARACTER_REFERENCE|OUTFIT_REFERENCE|STYLE_REFERENCE)$",
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("请提供素材名称或分类")
        if "display_name" in self.model_fields_set and self.display_name is not None:
            self.display_name = self.display_name.strip()
            if not self.display_name:
                raise ValueError("素材名称不能为空")
        return self


class ModelCapabilityRead(BaseModel):
    catalog_id: str | None = None
    connection_id: str | None = None
    provider: str
    protocol: str | None = None
    model_id: str
    logical_alias: str
    display_name: str
    model_type: str = "TEXT"
    input_modalities: list[str] = Field(default_factory=lambda: ["TEXT"])
    output_modalities: list[str] = Field(default_factory=lambda: ["TEXT"])
    operations: list[str]
    resolutions: list[str]
    preview_resolutions: list[str]
    max_reference_images: int
    regions: list[str]
    confidence: str = "VERIFIED"
    enabled: bool = True
    display_enabled: bool = True
    auto_eligible: bool = False
    priority: int = 50


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
    version: int


class BeatUpdate(BaseModel):
    action: str | None = Field(default=None, max_length=8000)
    speaker_name: str | None = Field(default=None, max_length=120)
    dialogue: str | None = Field(default=None, max_length=4000)
    narration: str | None = Field(default=None, max_length=4000)
    subtext: str | None = Field(default=None, max_length=4000)
    emotion: str | None = Field(default=None, max_length=120)
    importance: float | None = Field(default=None, ge=0, le=1)
    must_visualize: bool | None = None
    mergeable: bool | None = None
    page_turn_hook: bool | None = None
    version: int = Field(ge=1)


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
    locked_fields: list
    version: int
    beats: list[BeatRead] = Field(default_factory=list)


class SceneUpdate(BaseModel):
    location: str | None = Field(default=None, max_length=200)
    time_label: str | None = Field(default=None, max_length=120)
    weather: str | None = Field(default=None, max_length=120)
    purpose: str | None = Field(default=None, max_length=8000)
    emotional_arc: str | None = Field(default=None, max_length=8000)
    version: int = Field(ge=1)


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


class OutfitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    locked_fields: list[str] | None = None
    reference_asset_ids: list[str] | None = None
    version: int = Field(ge=1)


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
    color_mode: str = Field(default="monochrome", pattern="^(monochrome|color)$")
    profile: dict = Field(default_factory=dict)
    reference_asset_ids: list[str] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=list)


class StyleProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    color_mode: str | None = Field(default=None, pattern="^(monochrome|color)$")
    profile: dict | None = None
    locked_fields: list[str] | None = None
    reference_asset_ids: list[str] | None = None
    version: int = Field(ge=1)


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
    model_alias: str = Field(pattern=MODEL_REFERENCE_PATTERN)
    resolution: Resolution = Resolution.DRAFT_1K
    generation_mode: str = Field(default="REFERENCE", pattern="^(REFERENCE|CONCEPT)$")
    appearance_description: str = Field(default="", max_length=4000)
    outfit_name: str = Field(default="", max_length=120)
    outfit_description: str = Field(default="", max_length=4000)


class AssetReferenceApproval(BaseModel):
    character_id: str
    bind_character_reference: bool = True
    set_canonical: bool = True
    outfit_name: str | None = Field(default=None, min_length=1, max_length=120)
    outfit_description: str = Field(default="", max_length=4000)
    outfit_locked_fields: list[str] = Field(default_factory=list)


class StylePaletteDraftRequest(BaseModel):
    atmosphere: str = Field(default="", max_length=2000)


class StylePaletteApproval(BaseModel):
    palette: dict
    version: int = Field(ge=1)


class StyleTestApproval(BaseModel):
    candidate_id: str
    approved: bool = True
    version: int = Field(ge=1)


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
    storyboard_version: int
    selected_candidate_ack_version: int | None
    continuity_status: str
    scene_ids: list
    beat_ids: list
    version: int


class DialogueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    panel_id: str
    speaker_character_id: str | None
    target_text: str
    reading_order: int
    text_direction: str
    region: dict
    rewrite_forbidden: bool


class PanelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    page_id: str
    reading_order: int
    bounds: dict
    shot_type: str
    camera_angle: str
    camera_height: str
    characters: list
    character_presence: dict[str, CharacterPresence] = Field(default_factory=dict)
    props: list[str] = Field(default_factory=list)
    outfits: dict
    actions: dict
    expressions: dict
    background: str
    bubble_regions: list
    sound_effects: list
    bleed: bool
    borderless: bool
    locked_fields: list
    version: int
    dialogues: list[DialogueRead] = Field(default_factory=list)


class StoryboardRead(BaseModel):
    page: PageRead
    panels: list[PanelRead]
    candidate_count: int = 0


class PanelUpdate(BaseModel):
    shot_type: str | None = Field(default=None, min_length=1, max_length=64)
    camera_angle: str | None = Field(default=None, min_length=1, max_length=64)
    camera_height: str | None = Field(default=None, min_length=1, max_length=64)
    characters: list[str] | None = Field(default=None, max_length=20)
    character_presence: dict[str, CharacterPresence] | None = None
    props: list[str] | None = Field(default=None, max_length=40)
    outfits: dict[str, str] | None = None
    actions: dict | None = None
    expressions: dict[str, str] | None = None
    background: str | None = Field(default=None, max_length=8000)
    sound_effects: list | None = None
    bleed: bool | None = None
    borderless: bool | None = None
    version: int = Field(ge=1)


class DialogueCreate(BaseModel):
    target_text: str = Field(min_length=1, max_length=4000)
    speaker_character_id: str | None = None
    text_direction: str = Field(default="vertical", pattern="^(vertical|horizontal)$")
    region: dict = Field(default_factory=lambda: {"preferred": "upper_inner"})
    rewrite_forbidden: bool = True
    panel_version: int = Field(ge=1)


class DialogueUpdate(BaseModel):
    target_text: str | None = Field(default=None, min_length=1, max_length=4000)
    speaker_character_id: str | None = None
    text_direction: str | None = Field(default=None, pattern="^(vertical|horizontal)$")
    region: dict | None = None
    rewrite_forbidden: bool | None = None
    panel_version: int = Field(ge=1)


class DialogueDelete(BaseModel):
    panel_version: int = Field(ge=1)


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
    model_alias: str = Field(pattern=MODEL_REFERENCE_PATTERN)
    resolution: Resolution
    storyboard_version: int = Field(ge=1)
    reference_selections: dict[str, dict[str, str | None]] = Field(default_factory=dict)


class PageLayoutUpdate(BaseModel):
    panel_count: int = Field(ge=3, le=5)
    layout_mode: str = Field(default="dynamic", pattern="^(dynamic|balanced)$")


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
    based_on_storyboard_version: int | None = None
    version_state: str = "LEGACY_UNKNOWN"
    staleness_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    variant: str | None = None
    prompt_snapshot: dict = Field(default_factory=dict)
    content_url: str | None = None
    thumbnail_url: str | None = None


class CandidateQueuedRead(BaseModel):
    job_id: str
    job_status: JobStatus
    candidate: PageCandidateRead


class AssetCandidateCreate(BaseModel):
    model_alias: str = Field(pattern=MODEL_REFERENCE_PATTERN)
    resolution: Resolution
    variant: str = Field(
        pattern="^(FRONT|SIDE|BACK|EXPRESSION|SHEET|OUTFIT|OUTFIT_SHEET|STYLE_TEST)$"
    )
    instruction: str = Field(default="", max_length=2000)


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class SelectCandidateRequest(BaseModel):
    candidate_id: str
    manual_text_confirmed: bool = False
    accept_stale: bool = False


class KeepSelectedCandidateRequest(BaseModel):
    candidate_id: str
    storyboard_version: int = Field(ge=1)
    manual_text_confirmed: bool = False


class PageReadinessBlocker(BaseModel):
    code: str
    message: str
    stage: str
    target_id: str | None = None
    severity: str = "BLOCKING"


class PageReadinessCharacter(BaseModel):
    character_id: str
    primary_name: str
    presence: CharacterPresence
    character_reference_ids: list[str] = Field(default_factory=list)
    outfit_id: str | None = None
    outfit_name: str | None = None
    outfit_reference_ids: list[str] = Field(default_factory=list)


class PageReadinessStyle(BaseModel):
    style_id: str | None = None
    name: str | None = None
    color_mode: str | None = None
    status: str | None = None
    palette_confirmed: bool = False
    test_image_approved: bool = False


class PageReadinessProvider(BaseModel):
    configured: bool
    health_state: str
    text_model_access: str
    image_model_access: str
    image_model_alias: str = "image.nano_banana_2"
    usable_image_model_count: int = 0
    auto_image_model_count: int = 0


class PageReadinessWorker(BaseModel):
    queue_mode: str
    executor: str
    can_execute: bool
    redis_state: str


class PageReadinessRead(BaseModel):
    page_id: str
    ready: bool
    source_complete: bool
    script_complete: bool
    visible_characters: list[PageReadinessCharacter] = Field(default_factory=list)
    mentioned_characters: list[PageReadinessCharacter] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    style: PageReadinessStyle
    provider: PageReadinessProvider
    worker: PageReadinessWorker
    blockers: list[PageReadinessBlocker] = Field(default_factory=list)
    estimated_image_calls: int = 1
    estimated_cost_note: str = "将调用 1 次 Nano Banana 2 1K 生图"


class ProductionBlocker(BaseModel):
    code: str
    message: str
    section: str
    candidate_id: str | None = None


class PageProductionReadinessRead(BaseModel):
    page_id: str
    state: str
    ready: bool
    selected_candidate_id: str | None = None
    blockers: list[ProductionBlocker] = Field(default_factory=list)


class ChapterProductionReadinessRead(BaseModel):
    chapter_id: str
    ready: bool
    total_pages: int
    ready_pages: int
    pages: list[PageProductionReadinessRead] = Field(default_factory=list)


class GenerationWorkbenchRead(BaseModel):
    page: PageRead
    storyboard: StoryboardRead
    readiness: PageReadinessRead
    production: PageProductionReadinessRead
    current_batch: GenerationBatchRead | None = None
    candidates: list[PageCandidateRead] = Field(default_factory=list)
    selected_candidate: PageCandidateRead | None = None
    selected_candidate_state: str = "NONE"


class JobResultRead(BaseModel):
    kind: str
    label: str
    candidate_id: str | None = None
    page_id: str | None = None
    content_url: str
    thumbnail_url: str | None = None


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
    archived_at: datetime | None
    request_parameters: dict = Field(default_factory=dict, exclude=True)
    usage_summary: dict = Field(default_factory=dict)
    estimated_cost: float | None = None
    estimated_cost_currency: str | None = None
    estimated_cost_status: str = "UNAVAILABLE"
    estimated_cost_pricing_versions: list[str] = Field(default_factory=list)
    estimated_cost_note: str = (
        "费用暂不可估算；估算值不等于供应商账单"
    )
    result: JobResultRead | None = None

    @computed_field
    @property
    def duration_ms(self) -> int | None:
        if not self.started_at:
            return None
        end = self.finished_at or self.updated_at
        return max(0, int((end - self.started_at).total_seconds() * 1000))

    @computed_field
    @property
    def workflow_run_id(self) -> str | None:
        value = self.request_parameters.get("workflow_run_id")
        return value if isinstance(value, str) else None

    @computed_field
    @property
    def workflow_node_id(self) -> str | None:
        value = self.request_parameters.get("node_id") or self.request_parameters.get(
            "workflow_node_id"
        )
        return value if isinstance(value, str) else None


class ModelCallAttemptRead(BaseModel):
    """Read-only view of one provider dispatch attempt.

    Redacted by construction: carries no credentials, headers, endpoints,
    credential paths or request payloads. ``selected_key_id`` is an opaque row
    reference for traceability only.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    job_attempt: int
    dispatch_no: int
    route_switched: bool
    outcome: str | None
    provider: str
    model_id: str
    catalog_model_id: str | None
    connection_id: str | None
    selected_key_id: str | None
    request_id: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    usage: dict | None
    route_reason: str | None
    route_score: float | None
    error_code: str | None
    error_message: str | None


class JobArchiveResult(BaseModel):
    archived_count: int


class JobBulkArchiveRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=100)


class LibraryBatchRead(BaseModel):
    batch: GenerationBatchRead
    candidates: list[PageCandidateRead]


class LibraryRead(BaseModel):
    groups: list[LibraryBatchRead]
    total_candidates: int
    favorite_count: int
    next_cursor: str | None = None
    limit: int = 30


class InspectionRequest(BaseModel):
    categories: list[str] = Field(
        default_factory=lambda: [
            "SPEAKER",
            "CHARACTER",
            "OUTFIT",
            "PROP",
            "CONTINUITY",
        ]
    )

    @model_validator(mode="after")
    def reject_text_checks(self):
        if {item.upper() for item in self.categories} & {"TEXT", "OCR"}:
            raise ValueError("文字由人工校对，不再创建 OCR 或文字检查任务")
        return self


class InspectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_id: str | None
    storyboard_version: int | None = None
    category: str
    outcome: str
    score: float | None
    details: dict
    regions: list
    severity: str
    created_at: datetime


class RepairRequest(BaseModel):
    inspection_result_id: str
    repair_type: str = Field(pattern="^(BUBBLE_REGION|PANEL|PAGE)$")
    target_regions: list[dict] = Field(default_factory=list)
    target_fields: list[str] = Field(default_factory=list)
    model_alias: str = Field(pattern=MODEL_REFERENCE_PATTERN)
    resolution: Resolution


class UpscaleRequest(BaseModel):
    model_alias: str = Field(pattern=MODEL_REFERENCE_PATTERN)
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

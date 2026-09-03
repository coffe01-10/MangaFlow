from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.domain.states import (
    CharacterPresence,
    JobStatus,
    PageStatus,
    Resolution,
    WorkflowMode,
)
from app.domain.storyboard_layout import (
    MAX_POLYGON_VERTICES,
    MAX_ROTATION,
    MIN_PANEL_SIZE,
    MIN_POLYGON_VERTICES,
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
    text_model_alias: str | None = Field(default=None, pattern=MODEL_REFERENCE_PATTERN)


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
    text_model_alias: str | None
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
        pattern="^(CHARACTER_REFERENCE|OUTFIT_REFERENCE|STYLE_REFERENCE|SCENE_REFERENCE)$",
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
    scene_asset_id: str | None = None
    scene_asset_variant_id: str | None = None
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


class SceneAssetStructured(BaseModel):
    """Normalized structured fields for a scene asset.

    Keys are fixed by the contract; unknown keys are rejected so typos cannot
    silently change prompt compilation.
    """

    model_config = ConfigDict(extra="forbid")

    place: str = Field(default="", max_length=200)
    subareas: list[str] = Field(default_factory=list, max_length=20)
    interior: bool | None = None
    time_of_day: str = Field(default="", pattern="^(dawn|day|dusk|night|)$")
    weather: str = Field(default="", max_length=120)
    season: str = Field(default="", max_length=120)
    lighting: str = Field(default="", max_length=120)
    palette: dict = Field(default_factory=dict)
    fixed_props: list[str] = Field(default_factory=list, max_length=40)
    spatial_relations: list[dict] = Field(default_factory=list, max_length=20)


class SceneAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=8000)
    location_hint: str = Field(default="", max_length=200)
    structured: SceneAssetStructured = Field(default_factory=SceneAssetStructured)


class SceneAssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=8000)
    structured: SceneAssetStructured | None = None
    status: str | None = Field(
        default=None,
        pattern="^(UPLOADED|ANALYZED|GENERATED|NEEDS_CONFIRMATION|CANONICAL|ARCHIVED)$",
    )
    version: int = Field(ge=1)


class SceneAssetReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scene_asset_id: str
    asset_id: str
    role: str
    is_canonical: bool
    created_at: datetime


class SceneAssetReferenceCreate(BaseModel):
    asset_id: str
    role: str = Field(default="main", max_length=32)
    is_canonical: bool = False


class SceneAssetVariantReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    variant_id: str
    asset_id: str
    role: str
    sort_order: int
    created_at: datetime


class SceneAssetVariantReferenceCreate(BaseModel):
    asset_id: str
    role: str = Field(default="main", max_length=32)
    sort_order: int = Field(default=0, ge=0, le=1000)


class SceneAssetVariantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    structured_overrides: dict = Field(default_factory=dict)
    is_canonical: bool = False


class SceneAssetVariantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    structured_overrides: dict | None = None
    is_canonical: bool | None = None
    version: int = Field(ge=1)


class SceneAssetVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scene_asset_id: str
    name: str
    structured_overrides: dict
    is_canonical: bool
    deleted_at: datetime | None = None
    version: int
    references: list[SceneAssetVariantReferenceRead] = Field(default_factory=list)


class SceneAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str
    location_hint: str
    structured: dict
    status: str
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int
    references: list[SceneAssetReferenceRead] = Field(default_factory=list)
    variants: list[SceneAssetVariantRead] = Field(default_factory=list)


class SceneBindAssetRequest(BaseModel):
    scene_asset_id: str | None = None
    scene_asset_variant_id: str | None = None


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
    # V02-30 storyboard layout contract; filled by the storyboard read path
    # from the stored canvas or the page_ratio default.
    canvas: dict | None = None


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
    # Normalized structured bubble; None falls back to the legacy region.
    bubble: dict | None = None


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
    # Normalized structural geometry derived from bounds when not stored.
    geometry: dict | None = None
    dialogues: list[DialogueRead] = Field(default_factory=list)


class StoryboardRead(BaseModel):
    page: PageRead
    panels: list[PanelRead]
    candidate_count: int = 0


class PanelBounds(BaseModel):
    """Flat normalized panel rect; the permanent compatibility shape."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=MIN_PANEL_SIZE, le=1.0)
    height: float = Field(ge=MIN_PANEL_SIZE, le=1.0)


class GeometryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class PanelGeometry(BaseModel):
    """Structural panel geometry extension (contract §4)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^(rect|polygon)$")
    rect: PanelBounds | None = None
    polygon: list[GeometryPoint] | None = Field(
        default=None, min_length=MIN_POLYGON_VERTICES, max_length=MAX_POLYGON_VERTICES
    )
    rotation: float = Field(default=0.0, ge=-MAX_ROTATION, le=MAX_ROTATION)
    z_order: int = Field(default=1, ge=1)


class BubbleRect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class BubbleGeometry(BaseModel):
    """Structured bubble geometry extension (contract §7)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="rect", pattern="^(rect|ellipse)$")
    rect: BubbleRect
    anchor: GeometryPoint | None = None
    tail_target: GeometryPoint | None = None
    rotation: float = Field(default=0.0, ge=-MAX_ROTATION, le=MAX_ROTATION)
    text_region: BubbleRect | None = None


class SoundEffect(BaseModel):
    """Structured sound-effect element (contract §12)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    x: float | None = Field(default=None, ge=0.0, le=1.0)
    y: float | None = Field(default=None, ge=0.0, le=1.0)
    rotation: float = Field(default=0.0, ge=-MAX_ROTATION, le=MAX_ROTATION)
    size: float | None = Field(default=None, ge=0.0, le=1.0)


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
    sound_effects: list[SoundEffect | str] | None = None
    bounds: PanelBounds | None = None
    geometry: PanelGeometry | None = None
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
    bubble: BubbleGeometry | None = None
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
    panel_count: int = Field(ge=3, le=8)
    layout_mode: str = Field(default="dynamic", pattern="^(dynamic|balanced)$")


class ReadingOrderUpdate(BaseModel):
    """Whole-page logical reading order re-numbering (contract §6)."""

    model_config = ConfigDict(extra="forbid")

    order: list[str] = Field(min_length=1)


class StoryboardGeometryPanelItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    panel_id: str
    bounds: PanelBounds
    geometry: PanelGeometry | None = None
    reading_order: int = Field(ge=1)


class StoryboardGeometryDialogueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dialogue_id: str
    bubble: BubbleGeometry | None = None
    reading_order: int = Field(ge=1)


class StoryboardGeometrySave(BaseModel):
    """Atomic whole-page geometry snapshot (contract §10.3)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    storyboard_version: int = Field(ge=1)
    panels: list[StoryboardGeometryPanelItem]
    dialogues: list[StoryboardGeometryDialogueItem] = Field(default_factory=list)


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
    image_model_alias: str | None = None
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
    estimated_cost_note: str = "将调用 1 次所选图片模型的 1K 生图"


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
    job_id: str | None
    project_id: str | None
    job_attempt: int
    dispatch_no: int
    dispatch_request_id: str | None
    route_switched: bool
    outcome: str | None
    channel: str
    provider: str
    model_id: str
    catalog_model_id: str | None
    connection_id: str | None
    selected_key_id: str | None
    request_id: str | None
    probe_id: str | None
    chapter_id: str | None
    page_id: str | None
    panel_id: str | None
    candidate_id: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    usage: dict | None
    usage_status: str | None
    usage_source: str | None
    unit_kind: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cache_hit: bool | None
    output_images: int | None
    output_image_dims: list | None
    output_asset_ids: list | None
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


class PackageIdentitySpec(BaseModel):
    """Fixed-key identity block; unknown keys are rejected so typos cannot
    silently change prompt compilation (contract §4.1)."""

    model_config = ConfigDict(extra="forbid")

    age_appearance: str | None = Field(default=None, max_length=120)
    gender: str | None = Field(default=None, max_length=32)
    personality: str | None = Field(default=None, max_length=800)
    identity_notes: str | None = Field(default=None, max_length=2000)


class PackageVisualSpec(BaseModel):
    """Fixed-key visual block; unknown keys are rejected (contract §4.1)."""

    model_config = ConfigDict(extra="forbid")

    hair: str | None = Field(default=None, max_length=400)
    hair_color: str | None = Field(default=None, max_length=400)
    face: str | None = Field(default=None, max_length=400)
    eyes: str | None = Field(default=None, max_length=400)
    body: str | None = Field(default=None, max_length=400)
    distinguishing_marks: str | None = Field(default=None, max_length=400)


PackageConstraintItem = Annotated[str, Field(min_length=1, max_length=120)]


class CharacterModelPackageCreate(BaseModel):
    identity_spec: PackageIdentitySpec = Field(default_factory=PackageIdentitySpec)
    visual_spec: PackageVisualSpec = Field(default_factory=PackageVisualSpec)
    negative_constraints: list[PackageConstraintItem] = Field(
        default_factory=list, max_length=20
    )


class CharacterModelPackageUpdate(BaseModel):
    identity_spec: PackageIdentitySpec | None = None
    visual_spec: PackageVisualSpec | None = None
    negative_constraints: list[PackageConstraintItem] | None = Field(
        default=None, max_length=20
    )
    version: int = Field(ge=1)


class PackageReferenceCreate(BaseModel):
    asset_id: str = Field(min_length=1, max_length=36)
    role: str = Field(min_length=1, max_length=32)
    label: str = Field(default="", max_length=48)
    sort_order: int = Field(default=0, ge=0, le=1000)
    version: int = Field(ge=1)


class PackageCoverCreate(BaseModel):
    asset_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)


class PackageReferenceDelete(BaseModel):
    version: int = Field(ge=1)


class PackageOutfitCreate(BaseModel):
    outfit_id: str = Field(min_length=1, max_length=36)
    is_default: bool = False
    sort_order: int = Field(default=0, ge=0, le=1000)
    version: int = Field(ge=1)


class PackageOutfitDefaultUpdate(BaseModel):
    is_default: bool
    version: int = Field(ge=1)


class PackageOutfitDelete(BaseModel):
    version: int = Field(ge=1)


class PackageVersionDerive(BaseModel):
    base_version_id: str | None = Field(default=None, max_length=36)


class PackageActivateRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=36)
    # Required CAS token: pass null when the package currently has no published
    # version. Omission is a payload-shape error (contract §5.3-8).
    expected_published_version_id: str | None


class PackageReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_id: str
    asset_id: str
    role: str
    label: str
    sort_order: int
    created_at: datetime


class PackageOutfitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_id: str
    outfit_id: str
    is_default: bool
    sort_order: int
    created_at: datetime


class PackageCompletenessMissing(BaseModel):
    code: str
    field: str
    message: str
    suggestion: str


class PackageCompletenessRead(BaseModel):
    score: int
    missing: list[PackageCompletenessMissing] = Field(default_factory=list)


class PackageVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    package_id: str
    version_number: int
    status: str
    spec_snapshot: dict
    derived_from_version_id: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int
    references: list[PackageReferenceRead] = Field(default_factory=list)
    outfits: list[PackageOutfitRead] = Field(default_factory=list)
    completeness: PackageCompletenessRead | None = None


class PackageCharacterSummary(BaseModel):
    id: str
    primary_name: str
    aliases: list[str]
    alias_conflict: bool


class PackageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    character_id: str
    project_id: str
    identity_spec: dict
    visual_spec: dict
    negative_constraints: list
    published_version_id: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    version: int
    versions: list[PackageVersionRead] = Field(default_factory=list)
    completeness: PackageCompletenessRead | None = None


class PackageSummaryRead(BaseModel):
    id: str
    character_id: str
    project_id: str
    status: str
    published_version_id: str | None = None
    created_at: datetime
    updated_at: datetime
    version: int
    character: PackageCharacterSummary
    published_version_number: int | None = None
    published_completeness: PackageCompletenessRead | None = None


class PackageSpecFieldChange(BaseModel):
    field: str
    base_value: str | None = None
    target_value: str | None = None


class PackageSpecBlockDiff(BaseModel):
    added: dict[str, str] = Field(default_factory=dict)
    removed: dict[str, str] = Field(default_factory=dict)
    changed: list[PackageSpecFieldChange] = Field(default_factory=list)


class PackageListDiff(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class PackageReferenceSlot(BaseModel):
    role: str
    label: str
    asset_id: str | None = None
    asset_deleted: bool = False


class PackageReferenceSlotChange(BaseModel):
    role: str
    label: str
    base_asset_id: str | None = None
    target_asset_id: str | None = None
    base_asset_deleted: bool = False
    target_asset_deleted: bool = False


class PackageReferenceDiff(BaseModel):
    added: list[PackageReferenceSlot] = Field(default_factory=list)
    removed: list[PackageReferenceSlot] = Field(default_factory=list)
    changed: list[PackageReferenceSlotChange] = Field(default_factory=list)


class PackageOutfitDiffItem(BaseModel):
    outfit_id: str
    is_default: bool
    sort_order: int


class PackageOutfitDiff(BaseModel):
    added: list[PackageOutfitDiffItem] = Field(default_factory=list)
    removed: list[PackageOutfitDiffItem] = Field(default_factory=list)
    changed: list[PackageOutfitDiffItem] = Field(default_factory=list)


class PackageDiffRead(BaseModel):
    base_version_id: str
    target_version_id: str
    identity_spec: PackageSpecBlockDiff
    visual_spec: PackageSpecBlockDiff
    negative_constraints: PackageListDiff
    references: PackageReferenceDiff
    outfits: PackageOutfitDiff

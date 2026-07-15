from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.states import JobStatus, PageStatus, Resolution, WorkflowMode


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class AssetStatus(StrEnum):
    UPLOADED = "UPLOADED"
    ANALYZED = "ANALYZED"
    GENERATED = "GENERATED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    CANONICAL = "CANONICAL"
    ARCHIVED = "ARCHIVED"


class StyleStatus(StrEnum):
    ANALYZING = "ANALYZING"
    DRAFT = "DRAFT"
    TEST_GENERATED = "TEST_GENERATED"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class Project(Timestamped, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    reading_direction: Mapped[str] = mapped_column(String(8), default="rtl")
    page_ratio: Mapped[str] = mapped_column(String(32), default="b5_portrait")
    default_resolution: Mapped[Resolution] = mapped_column(
        Enum(Resolution), default=Resolution.STANDARD_2K
    )
    draft_resolution: Mapped[Resolution] = mapped_column(
        Enum(Resolution), default=Resolution.DRAFT_1K
    )
    workflow_mode: Mapped[WorkflowMode] = mapped_column(
        Enum(WorkflowMode), default=WorkflowMode.SEMI_AUTO
    )
    default_concurrency: Mapped[int] = mapped_column(Integer, default=4)
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    consistency_check_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_style_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    text_model_alias: Mapped[str] = mapped_column(String(64), default="text.fast")
    # Kept for a one-migration compatibility window. New code uses the neutral
    # last-used value and requires every generation request to choose a model.
    image_model_alias: Mapped[str] = mapped_column(String(64), default="image.nano_banana_2")
    last_image_model_alias: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Chapter(Timestamped, Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("project_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="IMPORTED")
    current_source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="chapters")
    source_revisions: Mapped[list["SourceRevision"]] = relationship(cascade="all, delete-orphan")
    scenes: Mapped[list["Scene"]] = relationship(cascade="all, delete-orphan")
    pages: Mapped[list["MangaPage"]] = relationship(cascade="all, delete-orphan")


class SourceRevision(Base):
    __tablename__ = "source_revisions"
    __table_args__ = (UniqueConstraint("chapter_id", "revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(24))
    original_text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    character_count: Mapped[int] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Character(Timestamped, Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    primary_name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    aliases_normalized: Mapped[list] = mapped_column(JSON, default=list)
    alias_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    canonical_description: Mapped[str] = mapped_column(Text, default="")
    locked_features: Mapped[list] = mapped_column(JSON, default=list)
    forbidden_changes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.UPLOADED)


class Outfit(Timestamped, Base):
    __tablename__ = "outfits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    state_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)
    reference_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.UPLOADED)


class StyleProfile(Timestamped, Base):
    __tablename__ = "style_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    color_mode: Mapped[str] = mapped_column(String(24), default="monochrome")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[StyleStatus] = mapped_column(Enum(StyleStatus), default=StyleStatus.ANALYZING)


class Asset(Timestamped, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256"),
        Index("ix_assets_project_deleted_created", "project_id", "deleted_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500))
    thumbnail_320_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_640_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="USER_UPLOAD")
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.UPLOADED)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Scene(Timestamped, Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String(200), default="")
    time_label: Mapped[str] = mapped_column(String(120), default="")
    weather: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    emotional_arc: Mapped[str] = mapped_column(Text, default="")
    source_range: Mapped[dict] = mapped_column(JSON, default=dict)
    outfit_assignments: Mapped[dict] = mapped_column(JSON, default=dict)
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)

    beats: Mapped[list["Beat"]] = relationship(cascade="all, delete-orphan")


class Beat(Timestamped, Base):
    __tablename__ = "beats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text, default="")
    speaker_name: Mapped[str] = mapped_column(String(120), default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    narration: Mapped[str] = mapped_column(Text, default="")
    subtext: Mapped[str] = mapped_column(Text, default="")
    emotion: Mapped[str] = mapped_column(String(120), default="")
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    must_visualize: Mapped[bool] = mapped_column(Boolean, default=True)
    mergeable: Mapped[bool] = mapped_column(Boolean, default=False)
    page_turn_hook: Mapped[bool] = mapped_column(Boolean, default=False)
    source_range: Mapped[dict] = mapped_column(JSON, default=dict)


class MangaPage(Timestamped, Base):
    __tablename__ = "manga_pages"
    __table_args__ = (UniqueConstraint("chapter_id", "page_number", "revision_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    page_function: Mapped[str] = mapped_column(String(32), default="dialogue")
    panel_count: Mapped[int] = mapped_column(Integer, default=4)
    reading_direction: Mapped[str] = mapped_column(String(8), default="rtl")
    resolution: Mapped[Resolution] = mapped_column(Enum(Resolution), default=Resolution.DRAFT_1K)
    style_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[PageStatus] = mapped_column(Enum(PageStatus), default=PageStatus.PLANNED)
    scene_ids: Mapped[list] = mapped_column(JSON, default=list)
    beat_ids: Mapped[list] = mapped_column(JSON, default=list)
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)
    estimated_text_chars: Mapped[int] = mapped_column(Integer, default=0)
    estimated_bubbles: Mapped[int] = mapped_column(Integer, default=0)
    source_coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    continuity_status: Mapped[str] = mapped_column(String(32), default="NOT_CHECKED")

    panels: Mapped[list["Panel"]] = relationship(cascade="all, delete-orphan")


class Panel(Timestamped, Base):
    __tablename__ = "panels"
    __table_args__ = (UniqueConstraint("page_id", "reading_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(
        ForeignKey("manga_pages.id", ondelete="CASCADE"), index=True
    )
    reading_order: Mapped[int] = mapped_column(Integer)
    bounds: Mapped[dict] = mapped_column(JSON, default=dict)
    shot_type: Mapped[str] = mapped_column(String(64), default="medium_close_up")
    camera_angle: Mapped[str] = mapped_column(String(64), default="eye_level")
    camera_height: Mapped[str] = mapped_column(String(64), default="eye_level")
    characters: Mapped[list] = mapped_column(JSON, default=list)
    outfits: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[dict] = mapped_column(JSON, default=dict)
    expressions: Mapped[dict] = mapped_column(JSON, default=dict)
    background: Mapped[str] = mapped_column(Text, default="")
    bubble_regions: Mapped[list] = mapped_column(JSON, default=list)
    sound_effects: Mapped[list] = mapped_column(JSON, default=list)
    bleed: Mapped[bool] = mapped_column(Boolean, default=False)
    borderless: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)

    dialogues: Mapped[list["Dialogue"]] = relationship(cascade="all, delete-orphan")


class Dialogue(Base):
    __tablename__ = "dialogues"
    __table_args__ = (UniqueConstraint("panel_id", "reading_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    panel_id: Mapped[str] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"), index=True)
    speaker_character_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_text: Mapped[str] = mapped_column(Text)
    reading_order: Mapped[int] = mapped_column(Integer)
    text_direction: Mapped[str] = mapped_column(String(16), default="vertical")
    region: Mapped[dict] = mapped_column(JSON, default=dict)
    rewrite_forbidden: Mapped[bool] = mapped_column(Boolean, default=True)


class ContinuitySnapshot(Base):
    __tablename__ = "continuity_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    panel_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="USER_CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationJob(Timestamped, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        Index("ix_generation_jobs_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(36))
    job_type: Mapped[str] = mapped_column(String(48))
    priority: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.WAITING, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    model_alias: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobDependency(Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (UniqueConstraint("job_id", "depends_on_job_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True
    )
    depends_on_job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE")
    )


class GenerationRecord(Base):
    __tablename__ = "generation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="vertex-ai")
    model_id: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_template: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(32))
    prompt_checksum: Mapped[str] = mapped_column(String(64))
    input_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    reference_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    output_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="STARTED")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class InspectionResult(Base):
    __tablename__ = "inspection_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_records.id", ondelete="CASCADE"), index=True, nullable=True
    )
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("page_candidates.id", ondelete="CASCADE"), index=True, nullable=True
    )
    category: Mapped[str] = mapped_column(String(48))
    outcome: Mapped[str] = mapped_column(String(48))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    severity: Mapped[str] = mapped_column(String(24), default="INFO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RepairPlan(Base):
    __tablename__ = "repair_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inspection_result_id: Mapped[str] = mapped_column(
        ForeignKey("inspection_results.id", ondelete="CASCADE"), index=True
    )
    repair_type: Mapped[str] = mapped_column(String(48))
    target_regions: Mapped[list] = mapped_column(JSON, default=list)
    target_fields: Mapped[list] = mapped_column(JSON, default=list)
    lock_conflicts: Mapped[list] = mapped_column(JSON, default=list)
    automatic_attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_automatic_attempts: Mapped[int] = mapped_column(Integer, default=3)
    manual_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceSegment(Base):
    __tablename__ = "source_segments"
    __table_args__ = (UniqueConstraint("source_revision_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PageSourceSegment(Base):
    __tablename__ = "page_source_segments"
    __table_args__ = (UniqueConstraint("page_id", "source_segment_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(
        ForeignKey("manga_pages.id", ondelete="CASCADE"), index=True
    )
    source_segment_id: Mapped[str] = mapped_column(
        ForeignKey("source_segments.id", ondelete="CASCADE"), index=True
    )


class ScriptRevision(Timestamped, Base):
    __tablename__ = "script_revisions"
    __table_args__ = (UniqueConstraint("chapter_id", "revision_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="RESTRICT"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)


class CharacterReference(Base):
    __tablename__ = "character_references"
    __table_args__ = (UniqueConstraint("character_id", "asset_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    angle: Mapped[str] = mapped_column(String(32), default="unspecified")
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationBatch(Timestamped, Base):
    __tablename__ = "generation_batches"
    __table_args__ = (
        UniqueConstraint("project_id", "ordinal"),
        Index("ix_generation_batches_project_created_id", "project_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True, nullable=True
    )
    page_id: Mapped[str | None] = mapped_column(
        ForeignKey("manga_pages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    generation_kind: Mapped[str] = mapped_column(String(32), default="PAGE")
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PageCandidate(Timestamped, Base):
    __tablename__ = "page_candidates"
    __table_args__ = (
        UniqueConstraint("batch_id", "ordinal"),
        Index("ix_page_candidates_batch_deleted_ordinal", "batch_id", "deleted_at", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("generation_batches.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[str] = mapped_column(
        ForeignKey("manga_pages.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    model_alias: Mapped[str] = mapped_column(String(64))
    resolution: Mapped[Resolution] = mapped_column(Enum(Resolution))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    generation_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_records.id", ondelete="SET NULL"), nullable=True
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetCandidate(Timestamped, Base):
    __tablename__ = "asset_candidates"
    __table_args__ = (UniqueConstraint("batch_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("generation_batches.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    model_alias: Mapped[str] = mapped_column(String(64))
    resolution: Mapped[Resolution] = mapped_column(Enum(Resolution))
    variant: Mapped[str] = mapped_column(String(48))
    instruction: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    generation_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_records.id", ondelete="SET NULL"), nullable=True
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExportBundle(Timestamped, Base):
    __tablename__ = "export_bundles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True, nullable=True
    )
    export_type: Mapped[str] = mapped_column(String(24))
    storage_key: Mapped[str] = mapped_column(String(500))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowDefinition(Timestamped, Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        Index("ix_workflow_definitions_project_active", "project_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), default="默认漫画工作流")
    description: Mapped[str] = mapped_column(Text, default="")
    draft_graph: Mapped[dict] = mapped_column(JSON, default=dict)
    draft_version: Mapped[int] = mapped_column(Integer, default=1)
    published_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "revision"),
        Index("ix_workflow_versions_workflow_published", "workflow_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    graph_checksum: Mapped[str] = mapped_column(String(64))
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowRun(Timestamped, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), index=True
    )
    workflow_version_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scope_type: Mapped[str] = mapped_column(String(32), default="PROJECT")
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="WAITING", index=True)
    start_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    stop_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkflowNodeRun(Base):
    __tablename__ = "workflow_node_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "node_id", "attempt_count"),
        Index("ix_workflow_node_runs_run_status", "workflow_run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(120))
    node_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="WAITING", index=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    output_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProviderHealth(Timestamped, Base):
    __tablename__ = "provider_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_file_present: Mapped[bool] = mapped_column(Boolean, default=False)
    health_state: Mapped[str] = mapped_column(String(32), default="UNCONFIGURED")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    text_model_access: Mapped[str] = mapped_column(String(32), default="NOT_CHECKED")
    image_model_access: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

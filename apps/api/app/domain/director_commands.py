"""Director command envelope, payload whitelist and journal states (V02-40)."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PAYLOAD_MAX_BYTES = 16 * 1024
USER_PROMPT_MAX_CHARS = 4000
MASK_MAX_REGIONS = 8
MASK_MAX_POINTS = 64
UUID_LENGTH = 36

OPERATIONS = (
    "update_page_layout",
    "update_panel_layout",
    "update_panel_shot",
    "update_panel_cast",
    "update_scene_context",
    "update_dialogue",
    "move_dialogue",
    "regenerate_region",
)

TARGET_KEYS = (
    "project_id",
    "page_id",
    "panel_id",
    "dialogue_id",
    "scene_id",
    "asset_id",
)

OPERATION_TARGETS: dict[str, frozenset[str]] = {
    "update_page_layout": frozenset({"page_id"}),
    "update_panel_layout": frozenset({"page_id", "panel_id"}),
    "update_panel_shot": frozenset({"page_id", "panel_id"}),
    "update_panel_cast": frozenset({"page_id", "panel_id"}),
    "update_scene_context": frozenset({"page_id", "scene_id"}),
    "update_dialogue": frozenset({"page_id", "panel_id", "dialogue_id"}),
    "move_dialogue": frozenset({"page_id", "panel_id", "dialogue_id"}),
    "regenerate_region": frozenset({"page_id"}),
}

OPTIONAL_TARGETS: dict[str, frozenset[str]] = {
    "regenerate_region": frozenset({"panel_id", "asset_id"}),
    "update_scene_context": frozenset({"panel_id"}),
}

VERSION_SCOPES = ("panel", "page", "storyboard", "scene")


class CommandGroupStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PREVIEWED = "PREVIEWED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    COMMITTED = "COMMITTED"
    PARTIALLY_REJECTED = "PARTIALLY_REJECTED"
    REJECTED = "REJECTED"
    DISCARDED = "DISCARDED"


class CommandStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PREVIEWED = "PREVIEWED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    SUPERSEDED = "SUPERSEDED"
    DISCARDED = "DISCARDED"
    FAILED = "FAILED"


def require_uuid(value: str) -> str:
    try:
        UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError("必须是 uuid") from error
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandTarget(StrictModel):
    project_id: str = Field(min_length=1, max_length=UUID_LENGTH)
    page_id: str | None = Field(default=None, max_length=UUID_LENGTH)
    panel_id: str | None = Field(default=None, max_length=UUID_LENGTH)
    dialogue_id: str | None = Field(default=None, max_length=UUID_LENGTH)
    scene_id: str | None = Field(default=None, max_length=UUID_LENGTH)
    asset_id: str | None = Field(default=None, max_length=UUID_LENGTH)

    @field_validator(
        "project_id", "page_id", "panel_id", "dialogue_id", "scene_id", "asset_id"
    )
    @classmethod
    def _uuid(cls, value: str | None) -> str | None:
        return require_uuid(value) if value else value


class ExpectedVersion(StrictModel):
    scope: str
    value: int = Field(ge=1)

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        if value not in VERSION_SCOPES:
            raise ValueError("expected_version.scope 不在白名单内")
        return value


class CommandSourceModel(StrictModel):
    provider: str | None = Field(default=None, max_length=64)
    catalog_model_id: str | None = Field(default=None, max_length=36)
    model_id: str | None = Field(default=None, max_length=64)


class CommandSource(StrictModel):
    user_prompt: str = Field(max_length=USER_PROMPT_MAX_CHARS)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    model: CommandSourceModel | None = None
    raw_output_id: str | None = Field(default=None, max_length=64)


class MaskRegion(StrictModel):
    points: list[list[float]] = Field(min_length=3, max_length=MASK_MAX_POINTS)

    @field_validator("points")
    @classmethod
    def _points(cls, value: list[list[float]]) -> list[list[float]]:
        for point in value:
            if len(point) != 2:
                raise ValueError("mask 顶点必须是 [x, y]")
        return value


class PageLayoutPayload(StrictModel):
    panel_count: int = Field(ge=3, le=8)
    layout_mode: str = Field(pattern="^(dynamic|balanced)$")


class PanelLayoutPayload(StrictModel):
    bounds: dict | None = None
    reading_order: int | None = Field(default=None, ge=1, le=8)
    bleed: bool | None = None
    borderless: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "PanelLayoutPayload":
        if not self.model_dump(exclude_none=True):
            raise ValueError("payload 不能为空")
        return self


class PanelShotPayload(StrictModel):
    shot_type: str | None = Field(default=None, min_length=1, max_length=64)
    camera_angle: str | None = Field(default=None, min_length=1, max_length=64)
    camera_height: str | None = Field(default=None, min_length=1, max_length=64)
    background: str | None = Field(default=None, max_length=8000)
    sound_effects: list | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _not_empty(self) -> "PanelShotPayload":
        if not self.model_dump(exclude_none=True):
            raise ValueError("payload 不能为空")
        return self


class PanelCastPayload(StrictModel):
    characters: list[str] | None = Field(default=None, max_length=20)
    character_presence: dict[str, str] | None = None
    outfits: dict[str, str] | None = None
    expressions: dict[str, str] | None = None
    actions: dict | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "PanelCastPayload":
        if not self.model_dump(exclude_none=True):
            raise ValueError("payload 不能为空")
        return self


class SceneContextPayload(StrictModel):
    location: str | None = Field(default=None, max_length=200)
    time_label: str | None = Field(default=None, max_length=120)
    weather: str | None = Field(default=None, max_length=120)
    background: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def _not_empty(self) -> "SceneContextPayload":
        if not self.model_dump(exclude_none=True):
            raise ValueError("payload 不能为空")
        return self


class DialoguePayload(StrictModel):
    target_text: str | None = Field(default=None, min_length=1, max_length=4000)
    text_direction: str | None = Field(default=None, pattern="^(vertical|horizontal)$")
    region: dict | None = None
    speaker_character_id: str | None = Field(default=None, max_length=UUID_LENGTH)
    rewrite_forbidden: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "DialoguePayload":
        if not self.model_dump(exclude_none=True):
            raise ValueError("payload 不能为空")
        return self


class MoveDialoguePayload(StrictModel):
    reading_order: int | None = Field(default=None, ge=1, le=8)
    region: dict | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "MoveDialoguePayload":
        if not self.model_dump(exclude_none=True):
            raise ValueError("payload 不能为空")
        return self


class RegenerateRegionPayload(StrictModel):
    instruction: str = Field(min_length=1, max_length=4000)
    target_regions: list[MaskRegion] = Field(default_factory=list, max_length=MASK_MAX_REGIONS)
    mask: list[MaskRegion] | None = Field(default=None, max_length=MASK_MAX_REGIONS)
    model_alias: str | None = Field(default=None, max_length=64)
    resolution: str | None = Field(default=None, max_length=8)


PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    "update_page_layout": PageLayoutPayload,
    "update_panel_layout": PanelLayoutPayload,
    "update_panel_shot": PanelShotPayload,
    "update_panel_cast": PanelCastPayload,
    "update_scene_context": SceneContextPayload,
    "update_dialogue": DialoguePayload,
    "move_dialogue": MoveDialoguePayload,
    "regenerate_region": RegenerateRegionPayload,
}


class CommandEnvelope(StrictModel):
    schema_version: int
    command_id: str = Field(min_length=UUID_LENGTH, max_length=UUID_LENGTH)
    command_group_id: str = Field(min_length=UUID_LENGTH, max_length=UUID_LENGTH)
    created_at: str = Field(min_length=1, max_length=64)
    target: CommandTarget
    expected_version: ExpectedVersion
    retry_of_command_id: str | None = Field(default=None, max_length=UUID_LENGTH)
    operation: str
    payload: dict
    source: CommandSource

    @field_validator("command_id", "command_group_id", "retry_of_command_id")
    @classmethod
    def _ids(cls, value: str | None) -> str | None:
        return require_uuid(value) if value else value

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version 必须为 1")
        return value

    @field_validator("operation")
    @classmethod
    def _operation(cls, value: str) -> str:
        if value not in OPERATIONS:
            raise ValueError("operation 不在白名单内")
        return value

    @model_validator(mode="after")
    def _payload_and_target(self) -> "CommandEnvelope":
        required = OPERATION_TARGETS[self.operation]
        optional = OPTIONAL_TARGETS.get(self.operation, frozenset())
        present = {
            key
            for key in TARGET_KEYS
            if key != "project_id" and getattr(self.target, key)
        }
        missing = required - present
        if missing:
            raise ValueError(f"target 缺少 {sorted(missing)}")
        extra = present - required - optional
        if extra:
            raise ValueError(f"target 含有多余字段 {sorted(extra)}")
        parsed = PAYLOAD_MODELS[self.operation].model_validate(self.payload)
        self.payload = parsed.model_dump(exclude_none=True)
        if self.operation == "update_scene_context" and self.expected_version.scope != "scene":
            raise ValueError("update_scene_context 的 expected_version.scope 必须为 scene")
        return self

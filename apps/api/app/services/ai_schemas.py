from pydantic import BaseModel, Field

from app.domain.states import CharacterPresence

# Draft-field caps mirror the user-facing API contract (schemas.py
# CharacterCreate: 120-char names, 40 aliases, 8000-char descriptions) and the
# DB column widths (Character.primary_name String(120), Scene.location
# String(200), Beat.speaker_name/emotion String(120)) so an overlong model
# emission fails structured-output validation as INVALID_OUTPUT instead of
# reaching PostgreSQL as StringDataRightTruncation after every chunk was
# billed (#159). story_parse additionally truncates before insert as defense
# in depth for values that bypass validation (model_construct emissions,
# merge-time field mutation such as the cross-chunk alias union).
DRAFT_NAME_MAX_LENGTH = 120
DRAFT_LOCATION_MAX_LENGTH = 200
DRAFT_TEXT_MAX_LENGTH = 8000
DRAFT_ALIAS_MAX_ITEMS = 40


class CharacterDraft(BaseModel):
    primary_name: str = Field(max_length=DRAFT_NAME_MAX_LENGTH)
    aliases: list[str] = Field(default_factory=list, max_length=DRAFT_ALIAS_MAX_ITEMS)
    description: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    source_segment_ids: list[str] = Field(default_factory=list)


class BeatDraft(BaseModel):
    # Ordinals are re-sequenced per scene after the merge (#152); the bound
    # here only rejects clearly invalid emissions (0/negative) up front.
    ordinal: int = Field(ge=1)
    action: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    speaker_name: str = Field(default="", max_length=DRAFT_NAME_MAX_LENGTH)
    dialogue: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    narration: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    emotion: str = Field(default="", max_length=DRAFT_NAME_MAX_LENGTH)
    subtext: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    importance: float = Field(default=0.5, ge=0, le=1)
    must_visualize: bool = True
    mergeable: bool = False
    page_turn_hook: bool = False
    source_segment_ids: list[str] = Field(default_factory=list)
    character_presence: dict[str, CharacterPresence] = Field(default_factory=dict)
    props: list[str] = Field(default_factory=list)


class SceneDraft(BaseModel):
    ordinal: int
    location: str = Field(default="", max_length=DRAFT_LOCATION_MAX_LENGTH)
    time_label: str = Field(default="", max_length=DRAFT_NAME_MAX_LENGTH)
    weather: str = Field(default="", max_length=DRAFT_NAME_MAX_LENGTH)
    purpose: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    emotional_arc: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    source_segment_ids: list[str] = Field(default_factory=list)
    beats: list[BeatDraft]


class StoryParseOutput(BaseModel):
    characters: list[CharacterDraft]
    scenes: list[SceneDraft]


class BubbleTextDiff(BaseModel):
    balloon_index: int = Field(ge=1)
    target_text: str
    recognized_text: str
    similarity: float | None = Field(default=None, ge=0, le=1)


class InspectionDetails(BaseModel):
    expected: str
    observed: str
    differences: list[str] = Field(default_factory=list)
    bubble_diffs: list[BubbleTextDiff] = Field(default_factory=list)
    # PRESENCE compliance (#164): every character the model actually sees in
    # the generated image. The inspection handler cross-checks this list
    # deterministically against the snapshot's VISIBLE / OFFSCREEN /
    # MENTIONED sets, so the field must stay machine-readable.
    detected_characters: list[str] = Field(default_factory=list)


class InspectionItem(BaseModel):
    category: str
    outcome: str
    score: float | None = None
    severity: str = "INFO"
    details: InspectionDetails
    regions: list[dict] = Field(default_factory=list)


class PageInspectionOutput(BaseModel):
    items: list[InspectionItem]


class StyleAnalysisOutput(BaseModel):
    line_art: str = ""
    screentone: str = ""
    contrast: str = ""
    panel_language: str = ""
    character_rendering: str = ""
    background_rendering: str = ""
    lighting: str = ""
    composition_rules: list[str] = Field(default_factory=list)
    negative_rules: list[str] = Field(default_factory=list)
    prompt_summary: str = ""
    palette: dict = Field(default_factory=dict)
    color_rules: list[str] = Field(default_factory=list)

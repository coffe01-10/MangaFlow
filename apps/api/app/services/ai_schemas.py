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
# Container caps (#240/#244-1): the #159 string caps bounded every leaf field,
# but list/dict containers had no cap, so a hostile or degenerate emission
# could still bill unbounded validation/persistence work (segment-id lists,
# props, presence maps, inspection items/regions). The values stay far above
# anything a faithful emission produces; the story_parse sanitizer re-enforces
# them as defense in depth because merge-time mutation (the cross-chunk
# ``dict.fromkeys`` unions) can exceed caps every chunk validated against.
DRAFT_SEGMENT_MAX_ITEMS = 200
DRAFT_PROPS_MAX_ITEMS = 20
DRAFT_PRESENCE_MAX_ITEMS = 40
# Inspection-side caps (#244-1): the sanitizer-style bound for verdict text
# and the per-run caps on persisted item/region rows (the handler additionally
# dedupes per category before persisting).
INSPECTION_TEXT_MAX_LENGTH = 2000
INSPECTION_REGIONS_MAX_ITEMS = 16
INSPECTION_ITEMS_MAX = 32


class CharacterDraft(BaseModel):
    primary_name: str = Field(max_length=DRAFT_NAME_MAX_LENGTH)
    aliases: list[str] = Field(default_factory=list, max_length=DRAFT_ALIAS_MAX_ITEMS)
    description: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    source_segment_ids: list[str] = Field(
        default_factory=list, max_length=DRAFT_SEGMENT_MAX_ITEMS
    )
    # Internal merge-time marker (#240): never part of the model contract —
    # ``_merge_story_parse_outputs`` sets it when a cross-chunk fusion merges
    # two different primary names, so persistence can raise alias_conflict
    # instead of silently dropping the second name.
    alias_conflict: bool = False


class BeatDraft(BaseModel):
    # Ordinals are re-sequenced to 1..n per scene after the merge (#152), so
    # the schema tolerates 0-based or gapped emissions exactly like
    # SceneDraft.ordinal instead of failing the paid call; normalization is
    # the merge's job (_resequence_beats), and defense in depth continues at
    # the pre-insert sanitizer.
    ordinal: int
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
    source_segment_ids: list[str] = Field(
        default_factory=list, max_length=DRAFT_SEGMENT_MAX_ITEMS
    )
    character_presence: dict[str, CharacterPresence] = Field(
        default_factory=dict, max_length=DRAFT_PRESENCE_MAX_ITEMS
    )
    props: list[str] = Field(default_factory=list, max_length=DRAFT_PROPS_MAX_ITEMS)
    # Internal sanitizer-time marker (#240): the raw keys that collapsed onto
    # one normalized presence key with conflicting values. Persisted into
    # Beat.source_range so the silent last-write-wins becomes an auditable flag.
    presence_key_conflicts: list[str] = Field(default_factory=list)


class SceneDraft(BaseModel):
    ordinal: int
    location: str = Field(default="", max_length=DRAFT_LOCATION_MAX_LENGTH)
    time_label: str = Field(default="", max_length=DRAFT_NAME_MAX_LENGTH)
    weather: str = Field(default="", max_length=DRAFT_NAME_MAX_LENGTH)
    purpose: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    emotional_arc: str = Field(default="", max_length=DRAFT_TEXT_MAX_LENGTH)
    source_segment_ids: list[str] = Field(
        default_factory=list, max_length=DRAFT_SEGMENT_MAX_ITEMS
    )
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
    expected: str = Field(max_length=INSPECTION_TEXT_MAX_LENGTH)
    observed: str = Field(max_length=INSPECTION_TEXT_MAX_LENGTH)
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
    regions: list[dict] = Field(default_factory=list, max_length=INSPECTION_REGIONS_MAX_ITEMS)


class PageInspectionOutput(BaseModel):
    items: list[InspectionItem] = Field(max_length=INSPECTION_ITEMS_MAX)


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

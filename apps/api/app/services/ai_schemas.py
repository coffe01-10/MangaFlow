from pydantic import BaseModel, Field

from app.domain.states import CharacterPresence


class CharacterDraft(BaseModel):
    primary_name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    source_segment_ids: list[str] = Field(default_factory=list)


class BeatDraft(BaseModel):
    ordinal: int
    action: str = ""
    speaker_name: str = ""
    dialogue: str = ""
    narration: str = ""
    emotion: str = ""
    subtext: str = ""
    importance: float = Field(default=0.5, ge=0, le=1)
    must_visualize: bool = True
    mergeable: bool = False
    page_turn_hook: bool = False
    source_segment_ids: list[str] = Field(default_factory=list)
    character_presence: dict[str, CharacterPresence] = Field(default_factory=dict)
    props: list[str] = Field(default_factory=list)


class SceneDraft(BaseModel):
    ordinal: int
    location: str = ""
    time_label: str = ""
    weather: str = ""
    purpose: str = ""
    emotional_arc: str = ""
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

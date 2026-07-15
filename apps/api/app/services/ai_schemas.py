from pydantic import BaseModel, Field


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


class InspectionItem(BaseModel):
    category: str
    outcome: str
    score: float | None = None
    severity: str = "INFO"
    details: dict = Field(default_factory=dict)
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

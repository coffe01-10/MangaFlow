from pydantic import BaseModel, Field


class CharacterDraft(BaseModel):
    primary_name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    source_segment_ids: list[str] = Field(default_factory=list)


class BeatDraft(BaseModel):
    ordinal: int
    action: str = ""
    dialogue: str = ""
    narration: str = ""
    emotion: str = ""
    source_segment_ids: list[str] = Field(default_factory=list)


class SceneDraft(BaseModel):
    ordinal: int
    location: str = ""
    time_label: str = ""
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

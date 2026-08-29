"""Storyboard, layout, panel and dialogue routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.workflow.common import _page, _page_candidate_count, _panel_read
from app.database import get_db
from app.domain.states import CharacterPresence, ensure_unlocked
from app.models import Dialogue, MangaPage, Outfit, Panel
from app.schemas import (
    DialogueCreate,
    DialogueDelete,
    DialogueRead,
    DialogueUpdate,
    PageLayoutUpdate,
    PageRead,
    PanelRead,
    PanelUpdate,
    StoryboardRead,
)
from app.services.content_workflow import update_page_layout
from app.services.editor import (
    mark_pages_for_review,
    mark_storyboard_changed,
    project_id_for_page,
    refresh_page_text_metrics,
    validate_character_ids,
)

router = APIRouter()


def _panel_context(db: Session, panel_id: str) -> tuple[Panel, MangaPage, str]:
    panel = db.get(Panel, panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="分镜格不存在")
    page = _page(db, panel.page_id)
    return panel, page, project_id_for_page(db, page)


def _validate_dialogue_speaker(
    db: Session,
    project_id: str,
    speaker_character_id: str | None,
) -> str | None:
    if not speaker_character_id:
        return None
    return validate_character_ids(db, project_id, [speaker_character_id])[0]


@router.get("/pages/{page_id}/storyboard", response_model=StoryboardRead)
def get_storyboard(page_id: str, db: Session = Depends(get_db)) -> StoryboardRead:
    page = _page(db, page_id)
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    return StoryboardRead(
        page=PageRead.model_validate(page),
        panels=[_panel_read(db, panel) for panel in panels],
        candidate_count=_page_candidate_count(db, page.id),
    )


@router.patch("/pages/{page_id}/layout", response_model=StoryboardRead)
def patch_page_layout(
    page_id: str,
    payload: PageLayoutUpdate,
    db: Session = Depends(get_db),
) -> StoryboardRead:
    page = update_page_layout(
        db,
        _page(db, page_id),
        panel_count=payload.panel_count,
        layout_mode=payload.layout_mode,
    )
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    return StoryboardRead(
        page=PageRead.model_validate(page),
        panels=[_panel_read(db, panel) for panel in panels],
        candidate_count=_page_candidate_count(db, page.id),
    )


@router.patch("/panels/{panel_id}", response_model=PanelRead)
def update_panel(
    panel_id: str,
    payload: PanelUpdate,
    db: Session = Depends(get_db),
) -> PanelRead:
    panel, page, project_id = _panel_context(db, panel_id)
    if panel.version != payload.version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    try:
        ensure_unlocked(panel.locked_fields, list(values))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if "character_presence" in values:
        requested_presence = values.get("character_presence") or {}
        validate_character_ids(db, project_id, list(requested_presence))
        values["character_presence"] = {
            character_id: str(getattr(presence, "value", presence))
            for character_id, presence in requested_presence.items()
        }
        values["characters"] = [
            character_id
            for character_id, presence in values["character_presence"].items()
            if presence == CharacterPresence.VISIBLE.value
        ]
    elif "characters" in values:
        values["characters"] = validate_character_ids(db, project_id, values["characters"] or [])
        values["character_presence"] = {
            **{
                character_id: presence
                for character_id, presence in (panel.character_presence or {}).items()
                if presence != CharacterPresence.VISIBLE.value
            },
            **{
                character_id: CharacterPresence.VISIBLE.value
                for character_id in values["characters"]
            },
        }
    if "props" in values:
        values["props"] = list(
            dict.fromkeys(
                str(item).strip()
                for item in (values["props"] or [])
                if str(item).strip()
            )
        )
    character_ids = values.get("characters", panel.characters)
    if "outfits" in values:
        assignments = values["outfits"] or {}
        if any(character_id not in character_ids for character_id in assignments):
            raise HTTPException(status_code=409, detail="服装只能指定给本格出现的角色")
        for character_id, outfit_id in assignments.items():
            outfit = db.get(Outfit, outfit_id)
            if not outfit or outfit.project_id != project_id or outfit.character_id != character_id:
                raise HTTPException(status_code=409, detail="分镜服装与角色或项目不匹配")
    if "expressions" in values and any(
        character_id not in character_ids for character_id in (values["expressions"] or {})
    ):
        raise HTTPException(status_code=409, detail="表情只能指定给本格出现的角色")
    if "characters" in values:
        if "outfits" not in values:
            values["outfits"] = {
                character_id: outfit_id
                for character_id, outfit_id in (panel.outfits or {}).items()
                if character_id in character_ids
            }
        if "expressions" not in values:
            values["expressions"] = {
                character_id: expression
                for character_id, expression in (panel.expressions or {}).items()
                if character_id in character_ids
            }
    if "actions" in values:
        values["actions"] = {
            **panel.actions,
            **(values["actions"] or {}),
            "source_text": panel.actions.get("source_text", ""),
        }
    for key, value in values.items():
        setattr(panel, key, value.strip() if isinstance(value, str) else value)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()
    db.refresh(panel)
    return _panel_read(db, panel)


@router.post(
    "/panels/{panel_id}/dialogues",
    response_model=DialogueRead,
    status_code=status.HTTP_201_CREATED,
)
def create_dialogue(
    panel_id: str,
    payload: DialogueCreate,
    db: Session = Depends(get_db),
) -> Dialogue:
    panel, page, project_id = _panel_context(db, panel_id)
    if panel.version != payload.panel_version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    if not payload.target_text.strip():
        raise HTTPException(status_code=422, detail="气泡文字不能为空")
    reading_order = (
        db.scalar(select(func.max(Dialogue.reading_order)).where(Dialogue.panel_id == panel.id))
        or 0
    ) + 1
    dialogue = Dialogue(
        panel_id=panel.id,
        speaker_character_id=_validate_dialogue_speaker(
            db, project_id, payload.speaker_character_id
        ),
        target_text=payload.target_text.strip(),
        reading_order=reading_order,
        text_direction=payload.text_direction,
        region=payload.region,
        rewrite_forbidden=payload.rewrite_forbidden,
    )
    db.add(dialogue)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()
    db.refresh(dialogue)
    return dialogue


@router.patch("/dialogues/{dialogue_id}", response_model=DialogueRead)
def update_dialogue(
    dialogue_id: str,
    payload: DialogueUpdate,
    db: Session = Depends(get_db),
) -> Dialogue:
    dialogue = db.get(Dialogue, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="对白不存在")
    panel, page, project_id = _panel_context(db, dialogue.panel_id)
    if panel.version != payload.panel_version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"panel_version"})
    if "target_text" in values and not (values["target_text"] or "").strip():
        raise HTTPException(status_code=422, detail="气泡文字不能为空")
    if "speaker_character_id" in values:
        values["speaker_character_id"] = _validate_dialogue_speaker(
            db, project_id, values["speaker_character_id"]
        )
    for key, value in values.items():
        setattr(dialogue, key, value.strip() if isinstance(value, str) else value)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()
    db.refresh(dialogue)
    return dialogue


@router.delete("/dialogues/{dialogue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dialogue(
    dialogue_id: str,
    payload: DialogueDelete,
    db: Session = Depends(get_db),
) -> None:
    dialogue = db.get(Dialogue, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="对白不存在")
    panel, page, _ = _panel_context(db, dialogue.panel_id)
    if panel.version != payload.panel_version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    db.delete(dialogue)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()

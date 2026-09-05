"""Storyboard, layout, panel and dialogue routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.helpers import reject_required_nulls
from app.api.routes.workflow.common import _page, _panel_read, _storyboard_read
from app.database import get_db
from app.domain.storyboard_layout import canonical_bubble, read_bubble
from app.models import Dialogue, MangaPage, Panel
from app.schemas import (
    DialogueCreate,
    DialogueDelete,
    DialogueRead,
    DialogueUpdate,
    PageLayoutUpdate,
    PanelRead,
    PanelUpdate,
    ReadingOrderUpdate,
    StoryboardGeometrySave,
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
from app.services.storyboard_edits import apply_dialogue_fields, apply_panel_fields
from app.services.storyboard_geometry import (
    reorder_page_panels,
    save_storyboard_geometry,
)

router = APIRouter()


def _panel_context(db: Session, panel_id: str) -> tuple[Panel, MangaPage, str]:
    panel = db.get(Panel, panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="分镜格不存在")
    page = _page(db, panel.page_id)
    return panel, page, project_id_for_page(db, page)


def _claim_panel_version(db: Session, panel: Panel, expected: int) -> None:
    """Claim the panel row with an atomic conditional update so concurrent
    writers cannot both pass an in-memory version comparison and silently
    overwrite each other (same pattern as scene asset PATCH). The claim lives
    in the caller's transaction: a later validation failure rolls it back.
    """

    claimed = db.execute(
        update(Panel)
        .where(Panel.id == panel.id, Panel.version == expected)
        .values(version=Panel.version + 1)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")


def _validate_dialogue_speaker(
    db: Session,
    project_id: str,
    speaker_character_id: str | None,
) -> str | None:
    if not speaker_character_id:
        return None
    return validate_character_ids(db, project_id, [speaker_character_id])[0]


def _dialogue_read(dialogue: Dialogue) -> DialogueRead:
    read = DialogueRead.model_validate(dialogue)
    read.bubble = read_bubble(dialogue)
    return read


@router.get("/pages/{page_id}/storyboard", response_model=StoryboardRead)
def get_storyboard(page_id: str, db: Session = Depends(get_db)) -> StoryboardRead:
    page = _page(db, page_id)
    return _storyboard_read(db, page)


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
    return _storyboard_read(db, page)


@router.patch("/pages/{page_id}/reading-order", response_model=StoryboardRead)
def patch_page_reading_order(
    page_id: str,
    payload: ReadingOrderUpdate,
    db: Session = Depends(get_db),
) -> StoryboardRead:
    page = _page(db, page_id)
    reorder_page_panels(db, page, payload.order)
    return _storyboard_read(db, page)


@router.put("/pages/{page_id}/storyboard-geometry", response_model=StoryboardRead)
def put_page_storyboard_geometry(
    page_id: str,
    payload: StoryboardGeometrySave,
    db: Session = Depends(get_db),
) -> StoryboardRead:
    page = _page(db, page_id)
    save_storyboard_geometry(db, page, payload)
    return _storyboard_read(db, page)


@router.patch("/panels/{panel_id}", response_model=PanelRead)
def update_panel(
    panel_id: str,
    payload: PanelUpdate,
    db: Session = Depends(get_db),
) -> PanelRead:
    panel, page, project_id = _panel_context(db, panel_id)
    _claim_panel_version(db, panel, payload.version)
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    reject_required_nulls(Panel, values)
    apply_panel_fields(db, panel, page, project_id, values)
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
    _claim_panel_version(db, panel, payload.panel_version)
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
    return _dialogue_read(dialogue)


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
    _claim_panel_version(db, panel, payload.panel_version)
    values = payload.model_dump(exclude_unset=True, exclude={"panel_version"})
    reject_required_nulls(Dialogue, values)
    if "bubble" in values and values["bubble"] is not None:
        values["bubble"] = canonical_bubble(values["bubble"])
    apply_dialogue_fields(db, dialogue, panel, page, project_id, values)
    db.commit()
    db.refresh(dialogue)
    return _dialogue_read(dialogue)


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
    _claim_panel_version(db, panel, payload.panel_version)
    db.delete(dialogue)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()

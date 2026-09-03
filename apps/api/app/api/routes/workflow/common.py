"""Helpers shared by more than one workflow route group."""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.storyboard_layout import (
    read_bubble,
    read_canvas,
    read_panel_geometry,
    read_sound_effects,
)
from app.models import (
    Chapter,
    Dialogue,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Panel,
    Project,
)
from app.schemas import PageRead, PanelRead, StoryboardRead
from app.services.ordinal_allocator import (
    BatchOrdinalConflictError,
    commit_ordinal_transaction,
    create_generation_batch,
)


def _page(db: Session, page_id: str) -> MangaPage:
    page = db.get(MangaPage, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    return page


def _project_for_page(db: Session, page: MangaPage) -> Project:
    chapter = db.get(Chapter, page.chapter_id)
    return db.get(Project, chapter.project_id)


def _new_batch(
    db: Session,
    page: MangaPage,
    *,
    generation_kind: str = "PAGE",
) -> GenerationBatch:
    project = _project_for_page(db, page)
    try:
        batch = create_generation_batch(
            db,
            project_id=project.id,
            chapter_id=page.chapter_id,
            page_id=page.id,
            generation_kind=generation_kind,
            close_open_page_batches=True,
        )
        commit_ordinal_transaction(db, BatchOrdinalConflictError)
        db.refresh(batch)
        return batch
    except BatchOrdinalConflictError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


def _page_ratio(db: Session, page: MangaPage) -> str:
    chapter = db.get(Chapter, page.chapter_id)
    if not chapter:
        return "b5_portrait"
    project = db.get(Project, chapter.project_id)
    return project.page_ratio if project else "b5_portrait"


def _page_read(db: Session, page: MangaPage) -> PageRead:
    read = PageRead.model_validate(page)
    read.canvas = read_canvas(page, _page_ratio(db, page))
    return read


def _panel_read(db: Session, panel: Panel) -> PanelRead:
    panel.dialogues = list(
        db.scalars(
            select(Dialogue).where(Dialogue.panel_id == panel.id).order_by(Dialogue.reading_order)
        )
    )
    read = PanelRead.model_validate(panel)
    read.geometry = read_panel_geometry(panel)
    read.sound_effects = read_sound_effects(panel)
    for dialogue_read, dialogue in zip(read.dialogues, panel.dialogues, strict=True):
        dialogue_read.bubble = read_bubble(dialogue)
    return read


def _storyboard_read(db: Session, page: MangaPage) -> StoryboardRead:
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    return StoryboardRead(
        page=_page_read(db, page),
        panels=[_panel_read(db, panel) for panel in panels],
        candidate_count=_page_candidate_count(db, page.id),
    )


def _page_candidate_count(db: Session, page_id: str) -> int:
    return (
        db.scalar(
            select(func.count(PageCandidate.id)).where(
                PageCandidate.page_id == page_id,
                PageCandidate.deleted_at.is_(None),
            )
        )
        or 0
    )

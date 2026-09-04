"""Page listing, readiness and generation workbench routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import candidate_read
from app.api.routes.workflow.common import _page, _page_candidate_count, _panel_read
from app.config import get_settings
from app.database import get_db
from app.models import (
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Panel,
)
from app.schemas import (
    ChapterProductionReadinessRead,
    GenerationBatchRead,
    GenerationWorkbenchRead,
    PageProductionReadinessRead,
    PageRead,
    PageReadinessRead,
    StoryboardRead,
)
from app.services.page_completion import (
    build_chapter_production_readiness,
    build_page_production_readiness,
)
from app.services.page_readiness import build_page_readiness

router = APIRouter()


@router.get("/chapters/{chapter_id}/pages", response_model=list[PageRead])
def list_pages(chapter_id: str, db: Session = Depends(get_db)) -> list[MangaPage]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter_id)
            .order_by(MangaPage.page_number, MangaPage.revision_no)
        )
    )


@router.get("/pages/{page_id}", response_model=PageRead)
def get_page(page_id: str, db: Session = Depends(get_db)) -> MangaPage:
    return _page(db, page_id)


@router.get("/pages/{page_id}/readiness", response_model=PageReadinessRead)
def get_page_readiness(
    page_id: str,
    db: Session = Depends(get_db),
) -> PageReadinessRead:
    return build_page_readiness(db, _page(db, page_id), get_settings())


@router.get(
    "/chapters/{chapter_id}/production-readiness",
    response_model=ChapterProductionReadinessRead,
)
def get_chapter_production_readiness(
    chapter_id: str,
    db: Session = Depends(get_db),
) -> ChapterProductionReadinessRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return build_chapter_production_readiness(db, chapter)


@router.get(
    "/pages/{page_id}/production-readiness",
    response_model=PageProductionReadinessRead,
)
def get_page_production_readiness(
    page_id: str,
    db: Session = Depends(get_db),
) -> PageProductionReadinessRead:
    return build_page_production_readiness(db, _page(db, page_id))


@router.get("/pages/{page_id}/generation-workbench", response_model=GenerationWorkbenchRead)
def get_generation_workbench(
    page_id: str,
    db: Session = Depends(get_db),
) -> GenerationWorkbenchRead:
    page = _page(db, page_id)
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    batch = db.scalar(
        select(GenerationBatch)
        .where(GenerationBatch.page_id == page.id)
        .order_by(
            (GenerationBatch.status == "OPEN").desc(),
            GenerationBatch.ordinal.desc(),
        )
        .limit(1)
    )
    candidates = (
        list(
            db.scalars(
                select(PageCandidate)
                .where(
                    PageCandidate.batch_id == batch.id,
                    PageCandidate.deleted_at.is_(None),
                )
                .order_by(PageCandidate.ordinal.desc())
            )
        )
        if batch
        else []
    )
    selected = (
        db.get(PageCandidate, page.selected_candidate_id) if page.selected_candidate_id else None
    )
    selected_read = candidate_read(selected, page) if selected else None
    return GenerationWorkbenchRead(
        page=PageRead.model_validate(page),
        storyboard=StoryboardRead(
            page=PageRead.model_validate(page),
            panels=[_panel_read(db, panel) for panel in panels],
            candidate_count=_page_candidate_count(db, page.id),
        ),
        readiness=build_page_readiness(db, page, get_settings()),
        production=build_page_production_readiness(db, page),
        current_batch=GenerationBatchRead.model_validate(batch) if batch else None,
        candidates=[candidate_read(item, page) for item in candidates],
        selected_candidate=selected_read,
        selected_candidate_state=selected_read.version_state if selected_read else "NONE",
    )

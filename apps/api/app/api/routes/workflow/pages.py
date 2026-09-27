"""Page listing, readiness and generation workbench routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import candidate_read, ensure_project_scope
from app.api.routes.workflow.common import _page, _page_candidate_count, _page_read, _panel_read
from app.config import get_settings
from app.database import get_db
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Panel,
)
from app.schemas import (
    ChapterMobilePreviewRead,
    ChapterProductionReadinessRead,
    GenerationBatchRead,
    GenerationWorkbenchRead,
    MobilePreviewPageRead,
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
def list_pages(
    chapter_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> list[MangaPage]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    ensure_project_scope(db, chapter, project_id, label="章节")
    return list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter_id)
            .order_by(MangaPage.page_number, MangaPage.revision_no)
        )
    )


@router.get("/pages/{page_id}", response_model=PageRead)
def get_page(
    page_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> MangaPage:
    page = _page(db, page_id)
    ensure_project_scope(db, page, project_id, label="页面")
    return page


@router.get("/pages/{page_id}/readiness", response_model=PageReadinessRead)
def get_page_readiness(
    page_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> PageReadinessRead:
    page = _page(db, page_id)
    # #633 契约：已删除章节的 readiness 返回 200 + CHAPTER_DELETED 阻塞项
    # （UI 以此解释为什么不能生成），不隐藏为 404。
    ensure_project_scope(db, page, project_id, label="页面", require_live_chapter=False)
    return build_page_readiness(db, page, get_settings())


@router.get(
    "/chapters/{chapter_id}/production-readiness",
    response_model=ChapterProductionReadinessRead,
)
def get_chapter_production_readiness(
    chapter_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> ChapterProductionReadinessRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    ensure_project_scope(db, chapter, project_id, label="章节")
    return build_chapter_production_readiness(db, chapter)


@router.get(
    "/chapters/{chapter_id}/mobile-preview",
    response_model=ChapterMobilePreviewRead,
)
def get_chapter_mobile_preview(
    chapter_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> ChapterMobilePreviewRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    ensure_project_scope(db, chapter, project_id, label="章节")
    pages = list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter_id)
            .order_by(MangaPage.page_number, MangaPage.revision_no)
        )
    )
    states = {
        entry.page_id: entry
        for entry in build_chapter_production_readiness(db, chapter).pages
    }
    candidate_ids = {
        entry.selected_candidate_id
        for entry in states.values()
        if entry.ready and entry.selected_candidate_id
    }
    candidates = {
        candidate.id: candidate
        for candidate in db.scalars(
            select(PageCandidate).where(PageCandidate.id.in_(candidate_ids))
        )
    } if candidate_ids else {}
    asset_ids = {candidate.asset_id for candidate in candidates.values() if candidate.asset_id}
    assets = {
        asset.id: asset
        for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    } if asset_ids else {}
    items: list[MobilePreviewPageRead] = []
    for page in pages:
        entry = states[page.id]
        candidate = candidates.get(entry.selected_candidate_id) if entry.ready else None
        asset = assets.get(candidate.asset_id) if candidate and candidate.asset_id else None
        items.append(
            MobilePreviewPageRead(
                page_id=page.id,
                page_number=page.page_number,
                state=entry.state,
                ready=entry.ready,
                image_url=(
                    f"/api/v1/assets/{asset.id}/thumbnail/640" if asset else None
                ),
                full_image_url=(
                    f"/api/v1/pages/{page.id}/export.png" if entry.ready else None
                ),
                width=asset.width if asset else None,
                height=asset.height if asset else None,
                blockers=entry.blockers,
            )
        )
    return ChapterMobilePreviewRead(
        chapter_id=chapter.id,
        title=chapter.title,
        pages=items,
    )


@router.get(
    "/pages/{page_id}/production-readiness",
    response_model=PageProductionReadinessRead,
)
def get_page_production_readiness(
    page_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> PageProductionReadinessRead:
    page = _page(db, page_id)
    ensure_project_scope(db, page, project_id, label="页面")
    return build_page_production_readiness(db, page)


@router.get("/pages/{page_id}/generation-workbench", response_model=GenerationWorkbenchRead)
def get_generation_workbench(
    page_id: str,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> GenerationWorkbenchRead:
    page = _page(db, page_id)
    ensure_project_scope(db, page, project_id, label="页面")
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
    # canvas 与 storyboard 读路径同源（_page_read 派生画布/出血/安全区）：
    # 此前两处 PageRead.model_validate 都留 canvas=None，任何用 workbench 载荷
    # 驱动画布的消费方都会进入「画布信息缺失」降级分支。
    workbench_page = _page_read(db, page)
    return GenerationWorkbenchRead(
        page=workbench_page,
        storyboard=StoryboardRead(
            page=workbench_page,
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

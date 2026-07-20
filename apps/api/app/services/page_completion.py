from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, Chapter, MangaPage, PageCandidate
from app.schemas import (
    ChapterProductionReadinessRead,
    PageProductionReadinessRead,
    ProductionBlocker,
)


def _block(
    code: str,
    message: str,
    section: str,
    candidate_id: str | None = None,
) -> ProductionBlocker:
    return ProductionBlocker(
        code=code,
        message=message,
        section=section,
        candidate_id=candidate_id,
    )


def build_page_production_readiness(
    db: Session,
    page: MangaPage,
) -> PageProductionReadinessRead:
    candidate = (
        db.get(PageCandidate, page.selected_candidate_id)
        if page.selected_candidate_id
        else None
    )
    blockers: list[ProductionBlocker] = []

    if not candidate or candidate.deleted_at is not None or not candidate.is_selected:
        blockers.append(
            _block(
                "CANDIDATE_NOT_SELECTED",
                "请先人工校对文字并暂选一张当前页候选",
                "generate",
            )
        )
    else:
        asset = db.get(Asset, candidate.asset_id) if candidate.asset_id else None
        if not asset or asset.deleted_at is not None:
            blockers.append(
                _block(
                    "SELECTED_ASSET_MISSING",
                    "暂选候选的图片素材不存在，请重新生成或改选候选",
                    "generate",
                    candidate.id,
                )
            )
        if page.selected_candidate_ack_version != page.storyboard_version:
            blockers.append(
                _block(
                    "STORYBOARD_VERSION_UNCONFIRMED",
                    "分镜已经变化，请明确沿用旧候选或按当前分镜重新生成",
                    "generate",
                    candidate.id,
                )
            )
        if candidate.status == "NEEDS_REVIEW" or page.continuity_status == "NEEDS_REVIEW":
            blockers.append(
                _block(
                    "QUALITY_REVIEW_REQUIRED",
                    "视觉检查未通过，请修复或重新生成后再次检查",
                    "generate",
                    candidate.id,
                )
            )
        elif candidate.status != "INSPECTED" or page.continuity_status != "PASSED":
            blockers.append(
                _block(
                    "QUALITY_INSPECTION_REQUIRED",
                    "暂选候选尚未完成视觉质量检查",
                    "generate",
                    candidate.id,
                )
            )

    ready = not blockers
    if ready:
        state = "READY"
    elif any(item.code == "QUALITY_REVIEW_REQUIRED" for item in blockers):
        state = "NEEDS_REPAIR"
    elif any(item.code == "STORYBOARD_VERSION_UNCONFIRMED" for item in blockers):
        state = "STALE"
    elif candidate:
        state = "AWAITING_INSPECTION"
    else:
        state = "AWAITING_SELECTION"

    return PageProductionReadinessRead(
        page_id=page.id,
        state=state,
        ready=ready,
        selected_candidate_id=candidate.id if candidate else None,
        blockers=blockers,
    )


def production_error_detail(readiness: PageProductionReadinessRead) -> dict:
    return {
        "code": "PAGE_NOT_PRODUCTION_READY",
        "message": "页面尚未达到生产通过状态",
        "state": readiness.state,
        "blockers": [item.model_dump(mode="json") for item in readiness.blockers],
    }


def build_chapter_production_readiness(
    db: Session,
    chapter: Chapter,
) -> ChapterProductionReadinessRead:
    pages = list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter.id)
            .order_by(MangaPage.page_number)
        )
    )
    states = [build_page_production_readiness(db, page) for page in pages]
    ready_pages = sum(item.ready for item in states)
    return ChapterProductionReadinessRead(
        chapter_id=chapter.id,
        ready=bool(states) and ready_pages == len(states),
        total_pages=len(states),
        ready_pages=ready_pages,
        pages=states,
    )

import base64
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read
from app.config import get_settings
from app.database import get_db
from app.domain.states import PageStatus, Resolution, ensure_unlocked
from app.models import (
    AssetCandidate,
    Chapter,
    Character,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    RepairPlan,
    utcnow,
)
from app.schemas import (
    CandidateCreate,
    CandidateQueuedRead,
    FavoriteUpdate,
    GenerationBatchRead,
    InspectionRead,
    InspectionRequest,
    JobRead,
    LibraryBatchRead,
    LibraryRead,
    PageCandidateRead,
    PageRead,
    RepairRequest,
    SelectCandidateRequest,
    UpscaleRequest,
)
from app.services.job_service import cancel_job, create_job, enqueue_job, reset_for_retry
from app.services.model_registry import build_registry

router = APIRouter()


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
    db.execute(
        update(GenerationBatch)
        .where(
            GenerationBatch.page_id == page.id,
            GenerationBatch.status == "OPEN",
        )
        .values(status="CLOSED", closed_at=utcnow())
    )
    ordinal = (
        db.scalar(
            select(func.max(GenerationBatch.ordinal)).where(
                GenerationBatch.project_id == project.id
            )
        )
        or 0
    ) + 1
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=page.chapter_id,
        page_id=page.id,
        ordinal=ordinal,
        generation_kind=generation_kind,
        status="OPEN",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/chapters/{chapter_id}/pages", response_model=list[PageRead])
def list_pages(chapter_id: str, db: Session = Depends(get_db)) -> list[MangaPage]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
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


@router.post(
    "/pages/{page_id}/batches",
    response_model=GenerationBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def start_batch(page_id: str, db: Session = Depends(get_db)) -> GenerationBatch:
    page = _page(db, page_id)
    chapter = db.get(Chapter, page.chapter_id)
    if (
        chapter.status not in {"SCRIPT_READY", "PAGES_PLANNED"}
        or not page.scene_ids
        or not page.beat_ids
    ):
        raise HTTPException(
            status_code=409, detail="该页面不是从完整漫画剧本生成，请重新解析剧本并规划分镜"
        )
    if not page.source_coverage.get("complete"):
        raise HTTPException(status_code=409, detail="页面原文覆盖不完整，不能开始抽卡")
    return _new_batch(db, page)


@router.get("/pages/{page_id}/batches", response_model=list[GenerationBatchRead])
def list_batches(page_id: str, db: Session = Depends(get_db)) -> list[GenerationBatch]:
    _page(db, page_id)
    return list(
        db.scalars(
            select(GenerationBatch)
            .where(GenerationBatch.page_id == page_id)
            .order_by(GenerationBatch.ordinal.desc())
        )
    )


@router.post(
    "/batches/{batch_id}/candidates",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_candidate(
    batch_id: str,
    payload: CandidateCreate,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    batch = db.get(GenerationBatch, batch_id)
    if not batch or batch.status != "OPEN" or not batch.page_id:
        raise HTTPException(status_code=409, detail="抽卡批次不存在或已经关闭")
    if payload.model_alias not in build_registry(get_settings()):
        raise HTTPException(status_code=422, detail="未识别的图像模型")
    page = _page(db, batch.page_id)
    chapter = db.get(Chapter, page.chapter_id)
    if (
        chapter.status not in {"SCRIPT_READY", "PAGES_PLANNED"}
        or not page.scene_ids
        or not page.beat_ids
    ):
        raise HTTPException(status_code=409, detail="该页面缺少剧本与分镜来源，禁止生成")
    if not page.source_coverage.get("complete"):
        raise HTTPException(status_code=409, detail="页面原文覆盖不完整，禁止生成")
    ordinal = (
        db.scalar(select(func.max(PageCandidate.ordinal)).where(PageCandidate.batch_id == batch.id))
        or 0
    ) + 1
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=ordinal,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
    )
    db.add(candidate)
    project = _project_for_page(db, page)
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.version += 1
    db.flush()
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        model_alias=payload.model_alias,
        request_parameters={"resolution": payload.resolution.value},
        idempotency_key=f"candidate:{candidate.id}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate),
    )


@router.get("/batches/{batch_id}/candidates", response_model=list[PageCandidateRead])
def list_candidates(batch_id: str, db: Session = Depends(get_db)) -> list[PageCandidateRead]:
    if not db.get(GenerationBatch, batch_id):
        raise HTTPException(status_code=404, detail="抽卡批次不存在")
    candidates = list(
        db.scalars(
            select(PageCandidate)
            .where(
                PageCandidate.batch_id == batch_id,
                PageCandidate.deleted_at.is_(None),
            )
            .order_by(PageCandidate.ordinal.desc())
        )
    )
    return [candidate_read(item) for item in candidates]


@router.patch("/candidates/{candidate_id}/favorite", response_model=PageCandidateRead)
def favorite_candidate(
    candidate_id: str,
    payload: FavoriteUpdate,
    db: Session = Depends(get_db),
) -> PageCandidateRead:
    candidate = db.get(PageCandidate, candidate_id) or db.get(AssetCandidate, candidate_id)
    if not candidate or candidate.deleted_at is not None:
        raise HTTPException(status_code=404, detail="候选不存在")
    candidate.is_favorite = payload.is_favorite
    candidate.version += 1
    db.commit()
    db.refresh(candidate)
    return candidate_read(candidate)


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate(candidate_id: str, db: Session = Depends(get_db)) -> None:
    candidate = db.get(PageCandidate, candidate_id) or db.get(AssetCandidate, candidate_id)
    if not candidate or candidate.deleted_at is not None:
        raise HTTPException(status_code=404, detail="候选不存在")
    if isinstance(candidate, PageCandidate) and candidate.is_selected:
        raise HTTPException(status_code=409, detail="当前采用版本不能删除")
    candidate.deleted_at = utcnow()
    candidate.version += 1
    db.commit()


@router.post("/pages/{page_id}/select-candidate", response_model=PageRead)
def select_candidate(
    page_id: str,
    payload: SelectCandidateRequest,
    db: Session = Depends(get_db),
) -> MangaPage:
    page = _page(db, page_id)
    candidate = db.get(PageCandidate, payload.candidate_id)
    if (
        not candidate
        or candidate.page_id != page.id
        or candidate.deleted_at is not None
        or not candidate.asset_id
        or candidate.status not in {"READY", "INSPECTED", "NEEDS_REVIEW"}
    ):
        raise HTTPException(status_code=409, detail="该候选尚不能采用")
    db.execute(
        update(PageCandidate).where(PageCandidate.page_id == page.id).values(is_selected=False)
    )
    candidate.is_selected = True
    candidate.version += 1
    changed = page.selected_candidate_id and page.selected_candidate_id != candidate.id
    page.selected_candidate_id = candidate.id
    page.status = PageStatus.APPROVED
    page.version += 1
    if changed:
        db.execute(
            update(MangaPage)
            .where(
                MangaPage.chapter_id == page.chapter_id,
                MangaPage.page_number > page.page_number,
                MangaPage.selected_candidate_id.is_not(None),
            )
            .values(continuity_status="NEEDS_RECHECK")
        )
    db.commit()
    db.refresh(page)
    return page


@router.post("/pages/{page_id}/next", response_model=PageRead)
def next_page(page_id: str, db: Session = Depends(get_db)) -> MangaPage:
    page = _page(db, page_id)
    if not page.selected_candidate_id:
        raise HTTPException(status_code=409, detail="请先采用当前页的一个候选")
    db.execute(
        update(GenerationBatch)
        .where(
            GenerationBatch.page_id == page.id,
            GenerationBatch.status == "OPEN",
        )
        .values(status="CLOSED", closed_at=utcnow())
    )
    following = db.scalar(
        select(MangaPage).where(
            MangaPage.chapter_id == page.chapter_id,
            MangaPage.page_number == page.page_number + 1,
        )
    )
    db.commit()
    if not following:
        raise HTTPException(status_code=409, detail="当前页已经是本章最后一页")
    return following


@router.get("/projects/{project_id}/library", response_model=LibraryRead)
def library(
    project_id: str,
    group_by: str = Query(default="batch", pattern="^batch$"),
    model_alias: str | None = None,
    favorite: bool | None = None,
    generation_kind: str | None = None,
    character_id: str | None = None,
    resolution: Resolution | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LibraryRead:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    batch_query = select(GenerationBatch).where(GenerationBatch.project_id == project_id)
    if generation_kind:
        batch_query = batch_query.where(GenerationBatch.generation_kind == generation_kind.upper())
    if date_from:
        batch_query = batch_query.where(GenerationBatch.created_at >= date_from)
    if date_to:
        batch_query = batch_query.where(GenerationBatch.created_at <= date_to)
    if character_id:
        character = db.get(Character, character_id)
        if not character or character.project_id != project_id:
            raise HTTPException(status_code=404, detail="筛选角色不存在或不属于当前项目")
        outfit_ids = set(
            db.scalars(select(Outfit.id).where(Outfit.character_id == character_id))
        )
        page_ids = {
            panel.page_id
            for panel in db.scalars(
                select(Panel)
                .join(MangaPage, MangaPage.id == Panel.page_id)
                .join(Chapter, Chapter.id == MangaPage.chapter_id)
                .where(Chapter.project_id == project_id)
            )
            if character_id in panel.character_ids
        }
        character_filters = [
            and_(
                GenerationBatch.target_type == "CHARACTER",
                GenerationBatch.target_id == character_id,
            )
        ]
        if outfit_ids:
            character_filters.append(
                and_(
                    GenerationBatch.target_type == "OUTFIT",
                    GenerationBatch.target_id.in_(outfit_ids),
                )
            )
        if page_ids:
            character_filters.append(GenerationBatch.page_id.in_(page_ids))
        batch_query = batch_query.where(or_(*character_filters))

    page_filters = [
        PageCandidate.batch_id == GenerationBatch.id,
        PageCandidate.deleted_at.is_(None),
    ]
    asset_filters = [
        AssetCandidate.batch_id == GenerationBatch.id,
        AssetCandidate.deleted_at.is_(None),
    ]
    if model_alias:
        page_filters.append(PageCandidate.model_alias == model_alias)
        asset_filters.append(AssetCandidate.model_alias == model_alias)
    if favorite is not None:
        page_filters.append(PageCandidate.is_favorite == favorite)
        asset_filters.append(AssetCandidate.is_favorite == favorite)
    if resolution:
        page_filters.append(PageCandidate.resolution == resolution)
        asset_filters.append(AssetCandidate.resolution == resolution)
    batch_query = batch_query.where(
        or_(
            exists(select(1).where(*page_filters)),
            exists(select(1).where(*asset_filters)),
        )
    )

    if cursor:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            cursor_time = datetime.fromisoformat(payload["created_at"])
            cursor_id = payload["id"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=422, detail="素材库游标无效") from error
        batch_query = batch_query.where(
            or_(
                GenerationBatch.created_at < cursor_time,
                and_(
                    GenerationBatch.created_at == cursor_time,
                    GenerationBatch.id < cursor_id,
                ),
            )
        )

    batches = list(
        db.scalars(
            batch_query.order_by(
                GenerationBatch.created_at.desc(), GenerationBatch.id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(batches) > limit
    batches = batches[:limit]
    next_cursor = None
    if has_more and batches:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "created_at": batches[-1].created_at.isoformat(),
                    "id": batches[-1].id,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")

    batch_ids = [batch.id for batch in batches]
    candidates_by_batch: dict[str, list[PageCandidateRead]] = {
        batch_id: [] for batch_id in batch_ids
    }
    if batch_ids:
        page_query = select(PageCandidate).where(
            PageCandidate.batch_id.in_(batch_ids),
            PageCandidate.deleted_at.is_(None),
        )
        asset_query = select(AssetCandidate).where(
            AssetCandidate.batch_id.in_(batch_ids),
            AssetCandidate.deleted_at.is_(None),
        )
        if model_alias:
            page_query = page_query.where(PageCandidate.model_alias == model_alias)
            asset_query = asset_query.where(AssetCandidate.model_alias == model_alias)
        if favorite is not None:
            page_query = page_query.where(PageCandidate.is_favorite == favorite)
            asset_query = asset_query.where(AssetCandidate.is_favorite == favorite)
        if resolution:
            page_query = page_query.where(PageCandidate.resolution == resolution)
            asset_query = asset_query.where(AssetCandidate.resolution == resolution)
        for item in db.scalars(
            page_query.order_by(PageCandidate.batch_id, PageCandidate.ordinal.desc())
        ):
            candidates_by_batch[item.batch_id].append(candidate_read(item))
        for item in db.scalars(
            asset_query.order_by(AssetCandidate.batch_id, AssetCandidate.ordinal.desc())
        ):
            candidates_by_batch[item.batch_id].append(asset_candidate_read(item))

    groups = [
        LibraryBatchRead(
            batch=GenerationBatchRead.model_validate(batch),
            candidates=candidates_by_batch[batch.id],
        )
        for batch in batches
    ]
    all_candidates = [candidate for group in groups for candidate in group.candidates]
    return LibraryRead(
        groups=groups,
        total_candidates=len(all_candidates),
        favorite_count=sum(item.is_favorite for item in all_candidates),
        next_cursor=next_cursor,
        limit=limit,
    )


@router.get("/projects/{project_id}/jobs", response_model=list[JobRead])
def list_jobs(project_id: str, db: Session = Depends(get_db)) -> list[GenerationJob]:
    return list(
        db.scalars(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(100)
        )
    )


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return cancel_job(db, job)


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.attempt_count >= job.max_attempts:
        raise HTTPException(status_code=409, detail="任务已达到最大重试次数")
    return reset_for_retry(db, job)


@router.post(
    "/candidates/{candidate_id}/inspect",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def inspect_candidate(
    candidate_id: str,
    payload: InspectionRequest,
    db: Session = Depends(get_db),
) -> GenerationJob:
    candidate = db.get(PageCandidate, candidate_id)
    if not candidate or not candidate.asset_id:
        raise HTTPException(status_code=409, detail="候选图片尚未生成")
    page = _page(db, candidate.page_id)
    project = _project_for_page(db, page)
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        model_alias="text.fast",
        request_parameters={"categories": payload.categories},
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    return enqueue_job(db, job)


@router.get("/candidates/{candidate_id}/inspections", response_model=list[InspectionRead])
def list_inspections(candidate_id: str, db: Session = Depends(get_db)) -> list[InspectionResult]:
    return list(
        db.scalars(
            select(InspectionResult)
            .where(InspectionResult.candidate_id == candidate_id)
            .order_by(InspectionResult.created_at.desc())
        )
    )


@router.post(
    "/candidates/{candidate_id}/repairs",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def repair_candidate(
    candidate_id: str,
    payload: RepairRequest,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    original = db.get(PageCandidate, candidate_id)
    inspection = db.get(InspectionResult, payload.inspection_result_id)
    if not original or not original.asset_id:
        raise HTTPException(status_code=409, detail="原始候选图片不存在")
    if not inspection or inspection.candidate_id != original.id:
        raise HTTPException(status_code=409, detail="检查结果与候选不匹配")
    inspection_ids = select(InspectionResult.id).where(
        InspectionResult.candidate_id == original.id
    )
    previous_repairs = list(
        db.scalars(
            select(RepairPlan).where(RepairPlan.inspection_result_id.in_(inspection_ids))
        )
    )
    attempts = max((item.automatic_attempts for item in previous_repairs), default=0)
    if attempts >= get_settings().max_auto_repairs:
        raise HTTPException(status_code=409, detail="已达到最大自动修复次数，请人工处理")
    repair_rank = {"TEXT_REGION": 0, "BUBBLE_REGION": 1, "PANEL": 2, "PAGE": 3}
    previous_rank = max(
        (repair_rank[item.repair_type] for item in previous_repairs),
        default=-1,
    )
    if repair_rank[payload.repair_type] < previous_rank:
        raise HTTPException(status_code=409, detail="修复范围只能保持或逐步扩大，不能退回更小范围")
    page = _page(db, original.page_id)
    try:
        ensure_unlocked(page.locked_fields, payload.target_fields)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    batch = _new_batch(db, page, generation_kind="REPAIR")
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
    )
    db.add(candidate)
    repair = RepairPlan(
        inspection_result_id=inspection.id,
        repair_type=payload.repair_type,
        target_regions=payload.target_regions or inspection.regions,
        target_fields=payload.target_fields,
        lock_conflicts=[],
        automatic_attempts=attempts + 1,
        max_automatic_attempts=get_settings().max_auto_repairs,
    )
    db.add(repair)
    db.flush()
    project = _project_for_page(db, page)
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.version += 1
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_REPAIR",
        model_alias=payload.model_alias,
        request_parameters={
            "original_candidate_id": original.id,
            "repair_plan_id": repair.id,
            "repair_type": payload.repair_type,
            "target_regions": payload.target_regions,
        },
        idempotency_key=f"repair:{repair.id}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate),
    )


@router.post(
    "/candidates/{candidate_id}/upscale",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def upscale_candidate(
    candidate_id: str,
    payload: UpscaleRequest,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    original = db.get(PageCandidate, candidate_id)
    if not original or not original.asset_id:
        raise HTTPException(status_code=409, detail="原始候选图片不存在")
    resolution_rank = {"1K": 1, "2K": 2, "4K": 4}
    if resolution_rank[payload.resolution.value] <= resolution_rank[original.resolution.value]:
        raise HTTPException(status_code=409, detail="升清目标必须高于当前候选清晰度")
    page = _page(db, original.page_id)
    batch = _new_batch(db, page, generation_kind="UPSCALE")
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
    )
    db.add(candidate)
    db.flush()
    project = _project_for_page(db, page)
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.version += 1
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_UPSCALE",
        model_alias=payload.model_alias,
        request_parameters={
            "original_candidate_id": original.id,
            "preserve_structure": True,
            "source_resolution": original.resolution.value,
            "target_resolution": payload.resolution.value,
        },
        idempotency_key=f"upscale:{batch.id}:{payload.resolution.value}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate),
    )

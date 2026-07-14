from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read
from app.config import get_settings
from app.database import get_db
from app.domain.states import PageStatus, ensure_unlocked
from app.models import (
    AssetCandidate,
    Chapter,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
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
    if not page.source_coverage.get("complete"):
        raise HTTPException(status_code=409, detail="页面原文覆盖不完整，不能开始抽卡")
    return _new_batch(db, page)


@router.get("/pages/{page_id}/batches", response_model=list[GenerationBatchRead])
def list_batches(
    page_id: str, db: Session = Depends(get_db)
) -> list[GenerationBatch]:
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
    if not page.source_coverage.get("complete"):
        raise HTTPException(status_code=409, detail="页面原文覆盖不完整，禁止生成")
    ordinal = (
        db.scalar(
            select(func.max(PageCandidate.ordinal)).where(
                PageCandidate.batch_id == batch.id
            )
        )
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


@router.get(
    "/batches/{batch_id}/candidates", response_model=list[PageCandidateRead]
)
def list_candidates(
    batch_id: str, db: Session = Depends(get_db)
) -> list[PageCandidateRead]:
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
    candidate = db.get(PageCandidate, candidate_id)
    if not candidate or candidate.deleted_at is not None:
        raise HTTPException(status_code=404, detail="候选不存在")
    if candidate.is_selected:
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
        update(PageCandidate)
        .where(PageCandidate.page_id == page.id)
        .values(is_selected=False)
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
    db: Session = Depends(get_db),
) -> LibraryRead:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    batch_query = select(GenerationBatch).where(GenerationBatch.project_id == project_id)
    if generation_kind:
        batch_query = batch_query.where(
            GenerationBatch.generation_kind == generation_kind.upper()
        )
    batches = list(db.scalars(batch_query.order_by(GenerationBatch.created_at.desc())))
    groups: list[LibraryBatchRead] = []
    all_candidates: list[PageCandidateRead] = []
    for batch in batches:
        query = select(PageCandidate).where(
            PageCandidate.batch_id == batch.id,
            PageCandidate.deleted_at.is_(None),
        )
        if model_alias:
            query = query.where(PageCandidate.model_alias == model_alias)
        if favorite is not None:
            query = query.where(PageCandidate.is_favorite == favorite)
        candidates = [
            candidate_read(item)
            for item in db.scalars(query.order_by(PageCandidate.ordinal.desc()))
        ]
        asset_query = select(AssetCandidate).where(
            AssetCandidate.batch_id == batch.id,
            AssetCandidate.deleted_at.is_(None),
        )
        if model_alias:
            asset_query = asset_query.where(AssetCandidate.model_alias == model_alias)
        if favorite is not None:
            asset_query = asset_query.where(AssetCandidate.is_favorite == favorite)
        candidates.extend(
            asset_candidate_read(item)
            for item in db.scalars(asset_query.order_by(AssetCandidate.ordinal.desc()))
        )
        if candidates:
            groups.append(
                LibraryBatchRead(
                    batch=GenerationBatchRead.model_validate(batch),
                    candidates=candidates,
                )
            )
            all_candidates.extend(candidates)
    return LibraryRead(
        groups=groups,
        total_candidates=len(all_candidates),
        favorite_count=sum(item.is_favorite for item in all_candidates),
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


@router.get(
    "/candidates/{candidate_id}/inspections", response_model=list[InspectionRead]
)
def list_inspections(
    candidate_id: str, db: Session = Depends(get_db)
) -> list[InspectionResult]:
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
    attempts = db.scalar(
        select(func.count(RepairPlan.id)).where(
            RepairPlan.inspection_result_id == inspection.id
        )
    ) or 0
    if attempts >= get_settings().max_auto_repairs:
        raise HTTPException(status_code=409, detail="已达到最大自动修复次数，请人工处理")
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
        target_regions=payload.target_regions,
        target_fields=payload.target_fields,
        lock_conflicts=[],
        automatic_attempts=attempts + 1,
        max_automatic_attempts=get_settings().max_auto_repairs,
    )
    db.add(repair)
    db.flush()
    project = _project_for_page(db, page)
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

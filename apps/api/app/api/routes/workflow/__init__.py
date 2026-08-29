from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read
from app.api.routes.workflow.common import (
    _new_batch,
    _page,
    _project_for_page,
)
from app.api.routes.workflow.generation import router as generation_router
from app.api.routes.workflow.library import router as library_router
from app.api.routes.workflow.pages import router as pages_router
from app.api.routes.workflow.storyboard import router as storyboard_router
from app.config import get_settings
from app.database import get_db
from app.domain.states import ensure_unlocked
from app.models import (
    AssetCandidate,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    JobDependency,
    PageCandidate,
    RepairPlan,
    WorkflowNodeRun,
    utcnow,
)
from app.schemas import (
    CandidateQueuedRead,
    InspectionRead,
    InspectionRequest,
    JobArchiveResult,
    JobBulkArchiveRequest,
    JobRead,
    JobResultRead,
    RepairRequest,
    UpscaleRequest,
)
from app.services.job_service import cancel_job, create_job, enqueue_job, reset_for_retry
from app.services.model_router import model_supports_resolution, resolve_model

router = APIRouter()

router.include_router(pages_router)
router.include_router(storyboard_router)
router.include_router(generation_router)
router.include_router(library_router)

TERMINAL_JOB_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}
DELETABLE_JOB_STATUSES = {"FAILED", "CANCELLED"}


def _job_reads(db: Session, jobs: list[GenerationJob]) -> list[JobRead]:
    job_ids = [job.id for job in jobs]
    target_ids = [job.target_id for job in jobs]
    records = (
        list(
            db.scalars(
                select(GenerationRecord).where(GenerationRecord.job_id.in_(job_ids))
            )
        )
        if job_ids
        else []
    )
    usage_by_job = {record.job_id: record.usage for record in records}
    page_candidates = (
        list(
            db.scalars(
                select(PageCandidate).where(
                    or_(
                        PageCandidate.job_id.in_(job_ids),
                        PageCandidate.id.in_(target_ids),
                    )
                )
            )
        )
        if job_ids
        else []
    )
    asset_candidates = (
        list(
            db.scalars(
                select(AssetCandidate).where(
                    or_(
                        AssetCandidate.job_id.in_(job_ids),
                        AssetCandidate.id.in_(target_ids),
                    )
                )
            )
        )
        if job_ids
        else []
    )
    page_by_job = {item.job_id: item for item in page_candidates if item.job_id}
    page_by_id = {item.id: item for item in page_candidates}
    asset_by_job = {item.job_id: item for item in asset_candidates if item.job_id}
    asset_by_id = {item.id: item for item in asset_candidates}

    def job_result(job: GenerationJob) -> JobResultRead | None:
        page_candidate = page_by_job.get(job.id) or page_by_id.get(job.target_id)
        if page_candidate and page_candidate.asset_id:
            value = candidate_read(page_candidate)
            return JobResultRead(
                kind="IMAGE",
                label=f"页面候选 {page_candidate.ordinal} · {page_candidate.resolution.value}",
                candidate_id=page_candidate.id,
                page_id=page_candidate.page_id,
                content_url=value.content_url,
                thumbnail_url=value.thumbnail_url,
            )
        asset_candidate = asset_by_job.get(job.id) or asset_by_id.get(job.target_id)
        if asset_candidate and asset_candidate.asset_id:
            value = asset_candidate_read(asset_candidate)
            return JobResultRead(
                kind="IMAGE",
                label=f"素材候选 {asset_candidate.variant} · {asset_candidate.resolution.value}",
                candidate_id=asset_candidate.id,
                content_url=value.content_url,
                thumbnail_url=value.thumbnail_url,
            )
        return None

    return [
        JobRead.model_validate(job).model_copy(
            update={
                "usage_summary": usage_by_job.get(job.id, {}),
                "estimated_cost": None,
                "result": job_result(job),
            }
        )
        for job in jobs
    ]


@router.get("/projects/{project_id}/jobs", response_model=list[JobRead])
def list_jobs(
    project_id: str,
    archived: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[JobRead]:
    jobs = list(
        db.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.archived_at.is_not(None)
                if archived
                else GenerationJob.archived_at.is_(None),
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(100)
        )
    )
    return _job_reads(db, jobs)


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _job_reads(db, [job])[0]


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


@router.post("/jobs/{job_id}/archive", response_model=JobRead)
def archive_job(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status.value not in TERMINAL_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="运行中的任务不能归档，请先取消")
    if job.archived_at is None:
        job.archived_at = utcnow()
        job.version += 1
        db.commit()
        db.refresh(job)
    return job


@router.post("/jobs/{job_id}/restore", response_model=JobRead)
def restore_job(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.archived_at is not None:
        job.archived_at = None
        job.version += 1
        db.commit()
        db.refresh(job)
    return job


@router.post(
    "/projects/{project_id}/jobs/archive-completed",
    response_model=JobArchiveResult,
)
def archive_completed_jobs(project_id: str, db: Session = Depends(get_db)) -> JobArchiveResult:
    jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.archived_at.is_(None),
                GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
            )
        )
    )
    archived_at = utcnow()
    for job in jobs:
        job.archived_at = archived_at
        job.version += 1
    db.commit()
    return JobArchiveResult(archived_count=len(jobs))


@router.post(
    "/projects/{project_id}/jobs/bulk-archive",
    response_model=JobArchiveResult,
)
def bulk_archive_jobs(
    project_id: str,
    payload: JobBulkArchiveRequest,
    db: Session = Depends(get_db),
) -> JobArchiveResult:
    jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.id.in_(payload.job_ids),
                GenerationJob.project_id == project_id,
            )
        )
    )
    if len(jobs) != len(set(payload.job_ids)):
        raise HTTPException(status_code=404, detail="部分任务不存在或不属于当前项目")
    non_terminal = [job.id for job in jobs if job.status.value not in TERMINAL_JOB_STATUSES]
    if non_terminal:
        raise HTTPException(status_code=409, detail="运行中的任务不能批量归档")
    archived_at = utcnow()
    archived_count = 0
    for job in jobs:
        if job.archived_at is not None:
            continue
        job.archived_at = archived_at
        job.version += 1
        archived_count += 1
    db.commit()
    return JobArchiveResult(archived_count=archived_count)


def _job_has_references(db: Session, job_id: str) -> bool:
    checks = (
        select(GenerationRecord.id).where(GenerationRecord.job_id == job_id),
        select(PageCandidate.id).where(PageCandidate.job_id == job_id),
        select(AssetCandidate.id).where(AssetCandidate.job_id == job_id),
        select(WorkflowNodeRun.id).where(WorkflowNodeRun.job_id == job_id),
        select(JobDependency.id).where(
            or_(
                JobDependency.job_id == job_id,
                JobDependency.depends_on_job_id == job_id,
            )
        ),
    )
    return any(db.scalar(query.limit(1)) is not None for query in checks)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status.value not in DELETABLE_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="只有失败或已取消任务可以彻底删除")
    if _job_has_references(db, job.id):
        raise HTTPException(status_code=409, detail="任务仍被候选、生成记录或工作流引用，只能归档")
    db.delete(job)
    db.commit()


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
        reference_asset_ids=[candidate.asset_id],
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
    inspection_ids = select(InspectionResult.id).where(InspectionResult.candidate_id == original.id)
    previous_repairs = list(
        db.scalars(select(RepairPlan).where(RepairPlan.inspection_result_id.in_(inspection_ids)))
    )
    attempts = max((item.automatic_attempts for item in previous_repairs), default=0)
    if attempts >= get_settings().max_auto_repairs:
        raise HTTPException(status_code=409, detail="已达到最大自动修复次数，请人工处理")
    if inspection.category.upper() in {"TEXT", "OCR"}:
        raise HTTPException(
            status_code=409, detail="历史文字检查仅供查看，文字问题不再创建修复任务"
        )
    repair_rank = {"BUBBLE_REGION": 0, "PANEL": 1, "PAGE": 2}
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
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"storyboard_version": page.storyboard_version},
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
    resolved_model = resolve_model(
        db,
        get_settings(),
        operation="image_edit",
        explicit_reference=payload.model_alias,
        project_id=project.id,
        task_kind="PAGE_REPAIR",
    )
    if not model_supports_resolution(resolved_model.model, payload.resolution.value):
        raise HTTPException(status_code=422, detail="所选模型不支持该输出清晰度")
    candidate.catalog_model_id = resolved_model.model.id
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.last_image_model_id = resolved_model.model.id
    project.version += 1
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_REPAIR",
        model_alias=payload.model_alias,
        catalog_model_id=resolved_model.model.id,
        request_parameters={
            "original_candidate_id": original.id,
            "repair_plan_id": repair.id,
            "repair_type": payload.repair_type,
            "target_regions": payload.target_regions,
            "storyboard_version": page.storyboard_version,
        },
        reference_asset_ids=[original.asset_id],
        idempotency_key=f"repair:{repair.id}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate, page),
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
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"storyboard_version": page.storyboard_version},
    )
    db.add(candidate)
    db.flush()
    project = _project_for_page(db, page)
    resolved_model = resolve_model(
        db,
        get_settings(),
        operation="image_edit",
        explicit_reference=payload.model_alias,
        project_id=project.id,
        task_kind="PAGE_UPSCALE",
    )
    if not model_supports_resolution(resolved_model.model, payload.resolution.value):
        raise HTTPException(status_code=422, detail="所选模型不支持目标升清规格")
    candidate.catalog_model_id = resolved_model.model.id
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.last_image_model_id = resolved_model.model.id
    project.version += 1
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_UPSCALE",
        model_alias=payload.model_alias,
        catalog_model_id=resolved_model.model.id,
        request_parameters={
            "original_candidate_id": original.id,
            "preserve_structure": True,
            "source_resolution": original.resolution.value,
            "target_resolution": payload.resolution.value,
            "storyboard_version": page.storyboard_version,
        },
        reference_asset_ids=[original.asset_id],
        idempotency_key=f"upscale:{batch.id}:{payload.resolution.value}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate, page),
    )

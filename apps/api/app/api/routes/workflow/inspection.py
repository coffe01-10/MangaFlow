"""Candidate inspection, repair and upscale routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import candidate_read
from app.api.routes.workflow.common import _page, _project_for_page
from app.config import get_settings
from app.database import get_db
from app.domain.states import ensure_unlocked
from app.models import (
    GenerationJob,
    InspectionResult,
    LineageKind,
    PageCandidate,
    RepairPlan,
)
from app.schemas import (
    CandidateQueuedRead,
    InspectionRead,
    InspectionRequest,
    JobRead,
    RepairRequest,
    UpscaleRequest,
)
from app.services.candidate_lineage import attach_derived_lineage, inherited_reference_ids
from app.services.job_service import create_job, enqueue_job
from app.services.model_router import model_supports_resolution, resolve_model
from app.services.ordinal_allocator import create_generation_batch

router = APIRouter()


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
    if not candidate or not candidate.asset_id or candidate.deleted_at is not None:
        raise HTTPException(status_code=409, detail="候选图片尚未生成")
    page = _page(db, candidate.page_id)
    project = _project_for_page(db, page)
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        model_alias="auto",
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
    if not original or not original.asset_id or original.deleted_at is not None:
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
    project = _project_for_page(db, page)
    batch = create_generation_batch(
        db,
        project_id=project.id,
        chapter_id=page.chapter_id,
        page_id=page.id,
        generation_kind="REPAIR",
        close_open_page_batches=True,
    )
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        # Contract §8.6-3: repairs inherit the original candidate's complete
        # queue-time snapshot, including character_packages, without re-resolving.
        prompt_snapshot=dict(original.prompt_snapshot or {}),
    )
    db.add(candidate)
    db.flush()
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
    # Contract §7: every derived candidate records its parent lineage at
    # creation time, in the same transaction as its job (helper also mirrors
    # the link into prompt_snapshot.lineage for the local-edit tracker).
    attach_derived_lineage(
        db,
        child=candidate,
        parent=original,
        lineage_kind=LineageKind.REPAIRED,
    )
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
        reference_asset_ids=[
            original.asset_id,
            *inherited_reference_ids(original.prompt_snapshot or {}),
        ],
        idempotency_key=f"repair:{repair.id}",
        auto_commit=False,
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
    if not original or not original.asset_id or original.deleted_at is not None:
        raise HTTPException(status_code=409, detail="原始候选图片不存在")
    resolution_rank = {"1K": 1, "2K": 2, "4K": 4}
    if resolution_rank[payload.resolution.value] <= resolution_rank[original.resolution.value]:
        raise HTTPException(status_code=409, detail="升清目标必须高于当前候选清晰度")
    page = _page(db, original.page_id)
    project = _project_for_page(db, page)
    batch = create_generation_batch(
        db,
        project_id=project.id,
        chapter_id=page.chapter_id,
        page_id=page.id,
        generation_kind="UPSCALE",
        close_open_page_batches=True,
    )
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        # Contract §8.6-3: upscales inherit the original candidate's complete
        # queue-time snapshot, including character_packages, without re-resolving.
        prompt_snapshot=dict(original.prompt_snapshot or {}),
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
    attach_derived_lineage(
        db,
        child=candidate,
        parent=original,
        lineage_kind=LineageKind.UPSCALED,
    )
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
        reference_asset_ids=[
            original.asset_id,
            *inherited_reference_ids(original.prompt_snapshot or {}),
        ],
        idempotency_key=f"upscale:{batch.id}:{payload.resolution.value}",
        auto_commit=False,
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

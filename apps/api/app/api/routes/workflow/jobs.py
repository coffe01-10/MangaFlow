"""Project job listing, lifecycle actions and bulk archive routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read, ensure_project_scope
from app.database import get_db
from app.domain.states import JobStatus
from app.models import (
    AssetCandidate,
    GenerationJob,
    GenerationRecord,
    JobDependency,
    ModelCallAttempt,
    PageCandidate,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.schemas import (
    JobArchiveResult,
    JobBulkArchiveRequest,
    JobRead,
    JobResultRead,
    ModelCallAttemptRead,
)
from app.services.job_service import cancel_job, dependencies_complete, reset_for_retry
from app.services.model_costs import estimate_jobs

router = APIRouter()

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
    estimates_by_job = estimate_jobs(db, job_ids)
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

    result: list[JobRead] = []
    for job in jobs:
        estimate = estimates_by_job[job.id]
        result.append(
            JobRead.model_validate(job).model_copy(
                update={
                    "usage_summary": usage_by_job.get(job.id, {}),
                    "estimated_cost": (
                        float(estimate.value) if estimate.value is not None else None
                    ),
                    "estimated_cost_currency": estimate.currency,
                    "estimated_cost_status": estimate.status,
                    "estimated_cost_pricing_versions": list(
                        estimate.pricing_versions
                    ),
                    "estimated_cost_note": estimate.note,
                    "result": job_result(job),
                }
            )
        )
    return result


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
def get_job(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> JobRead:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
    return _job_reads(db, [job])[0]


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
    return cancel_job(db, job)


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
    # An archived row is invisible in the default list; retrying it would
    # re-run paid work the user filed away. Restore first, then retry.
    if job.archived_at is not None:
        raise HTTPException(status_code=409, detail="已归档的任务不能重试，请先恢复后再试")
    # reset_for_retry silently ignores every other status and would return the
    # unchanged row as a 200 "success"; reject those no-ops up front.
    if job.status not in {JobStatus.FAILED, JobStatus.NEEDS_REVIEW, JobStatus.WAITING}:
        raise HTTPException(status_code=409, detail="当前状态的任务不能重试")
    if job.attempt_count >= job.max_attempts:
        raise HTTPException(status_code=409, detail="任务已达到最大重试次数")
    # A dependency-blocked WAITING child (parent job not COMPLETED) must not
    # reach reset_for_retry: it would revive a FAILED run to phantom RUNNING
    # before enqueue_job's dependency gate refuses the enqueue, leaving the
    # studio polling a run with nothing executing. FAILED/NEEDS_REVIEW jobs
    # always have COMPLETED dependencies, so legitimate retries never hit this.
    if not dependencies_complete(db, job):
        raise HTTPException(status_code=409, detail="依赖任务未完成，不能重试")
    # A job whose run already ended must not retry: reset_for_retry's node_run
    # revival has no run-status predicate (only its FAILED-run revival gates),
    # so the job would execute paid work for a dead run and strand its
    # node_run RUNNING forever inside a terminal run (reconcile early-returns
    # on terminal runs). Reachable through the non-atomic worker-failure/
    # cancel window: cancel_run's sweep deliberately keeps terminal FAILED
    # jobs. A COMPLETED run with a late lease-expiry-FAILED job has the
    # identical orphan shape, so both terminal statuses are gated.
    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    if node_run:
        run = db.get(WorkflowRun, node_run.workflow_run_id)
        if run and run.status in {"CANCELLED", "COMPLETED"}:
            raise HTTPException(status_code=409, detail="所属运行已取消或已结束，不能重试该任务")
    return reset_for_retry(db, job)


@router.post("/jobs/{job_id}/archive", response_model=JobRead)
def archive_job(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
    if job.status.value not in TERMINAL_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="运行中的任务不能归档，请先取消")
    if job.archived_at is None:
        job.archived_at = utcnow()
        job.version += 1
        db.commit()
        db.refresh(job)
    return job


@router.post("/jobs/{job_id}/restore", response_model=JobRead)
def restore_job(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
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
    archived_at = utcnow()
    # Single conditional update: a job concurrently retried back to an active
    # status between the SELECT and the write must not be archived out of the
    # job list while it still runs.
    archived = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.archived_at.is_(None),
            GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
        )
        .values(archived_at=archived_at, version=GenerationJob.version + 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return JobArchiveResult(archived_count=archived.rowcount)


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
    # Conditional for the same reason as archive-completed: only rows that
    # are still terminal at write time are archived.
    archived = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id.in_(payload.job_ids),
            GenerationJob.project_id == project_id,
            GenerationJob.archived_at.is_(None),
            GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
        )
        .values(archived_at=archived_at, version=GenerationJob.version + 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return JobArchiveResult(archived_count=archived.rowcount)


def _job_has_references(db: Session, job_id: str) -> bool:
    checks = (
        select(GenerationRecord.id).where(GenerationRecord.job_id == job_id),
        select(PageCandidate.id).where(PageCandidate.job_id == job_id),
        select(AssetCandidate.id).where(AssetCandidate.job_id == job_id),
        select(WorkflowNodeRun.id).where(WorkflowNodeRun.job_id == job_id),
        select(ModelCallAttempt.id).where(ModelCallAttempt.job_id == job_id),
        select(JobDependency.id).where(
            or_(
                JobDependency.job_id == job_id,
                JobDependency.depends_on_job_id == job_id,
            )
        ),
    )
    return any(db.scalar(query.limit(1)) is not None for query in checks)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> None:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
    if job.status.value not in DELETABLE_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="只有失败或已取消任务可以彻底删除")
    if _job_has_references(db, job.id):
        raise HTTPException(status_code=409, detail="任务仍被候选、生成记录或工作流引用，只能归档")
    db.delete(job)
    db.commit()


@router.get("/jobs/{job_id}/model-call-attempts", response_model=list[ModelCallAttemptRead])
def list_model_call_attempts(
    job_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> list[ModelCallAttempt]:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_project_scope(db, job, project_id, label="任务")
    return list(
        db.scalars(
            select(ModelCallAttempt)
            .where(ModelCallAttempt.job_id == job_id)
            .order_by(ModelCallAttempt.started_at, ModelCallAttempt.dispatch_no)
        )
    )

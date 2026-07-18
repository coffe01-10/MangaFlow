from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import (
    AssetCandidate,
    GenerationJob,
    JobAssetReference,
    JobDependency,
    MangaPage,
    PageCandidate,
    StyleProfile,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.services.runtime_settings import apply_runtime_overrides, read_queue_mode

LOCAL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mangaflow-local")
LOCAL_SUBMISSION_LOCK = Lock()
LOCAL_SUBMITTED_JOB_IDS: set[str] = set()


def create_job(
    db: Session,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    job_type: str,
    model_alias: str | None = None,
    catalog_model_id: str | None = None,
    request_parameters: dict | None = None,
    reference_asset_ids: list[str] | None = None,
    priority: int = 50,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    dependency_ids: list[str] | None = None,
) -> GenerationJob:
    if idempotency_key:
        existing = db.scalar(
            select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
        )
        if existing:
            return existing
    job = GenerationJob(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        job_type=job_type,
        model_alias=model_alias,
        catalog_model_id=catalog_model_id,
        request_parameters=request_parameters or {},
        priority=priority,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        status=JobStatus.WAITING,
    )
    db.add(job)
    db.flush()
    for dependency_id in dependency_ids or []:
        db.add(JobDependency(job_id=job.id, depends_on_job_id=dependency_id))
    for asset_id in dict.fromkeys(reference_asset_ids or []):
        db.add(JobAssetReference(job_id=job.id, asset_id=asset_id))
    db.commit()
    db.refresh(job)
    return job


def dependencies_complete(db: Session, job: GenerationJob) -> bool:
    dependencies = list(
        db.scalars(
            select(GenerationJob)
            .join(
                JobDependency,
                JobDependency.depends_on_job_id == GenerationJob.id,
            )
            .where(JobDependency.job_id == job.id)
        )
    )
    return all(item.status == JobStatus.COMPLETED for item in dependencies)


def enqueue_job(db: Session, job: GenerationJob) -> GenerationJob:
    settings = get_settings()
    apply_runtime_overrides(db, settings)
    if not dependencies_complete(db, job):
        job.status = JobStatus.WAITING
        db.commit()
        db.refresh(job)
        return job
    # Legacy environment-level maintenance switch. Runtime LOCAL no longer
    # toggles this flag, so selecting LOCAL still executes immediately.
    if not settings.queue_enabled:
        job.status = JobStatus.WAITING
        job.error_code = "QUEUE_DISABLED"
        job.error_message = "任务已保存，后台执行器当前未启用"
        db.commit()
        db.refresh(job)
        return job
    queue_mode = read_queue_mode(db)
    if queue_mode == "LOCAL":
        return _enqueue_locally(db, job, "本地后台执行器正在处理任务")

    connection = None
    try:
        from redis import Redis
        from rq import Queue, Retry

        connection = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
        connection.ping()
        queue = Queue(settings.queue_name, connection=connection)
        queue.enqueue(
            "app.worker_tasks.execute_job",
            job.id,
            job_id=job.id,
            job_timeout=settings.job_timeout_seconds,
            retry=Retry(max=max(job.max_attempts - 1, 0), interval=[10, 30, 90]),
        )
        job.status = JobStatus.QUEUED
        job.error_code = None
        job.error_message = None
    except Exception:
        if queue_mode == "AUTO" and settings.environment == "development":
            return _enqueue_locally(db, job, "Redis 不可用，已切换到本地后台执行")
        job.status = JobStatus.WAITING
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = (
            "任务已保存；REDIS 模式要求 Redis 可用"
            if queue_mode == "REDIS"
            else "任务已保存；Redis 队列暂时不可用"
        )
    finally:
        if connection is not None:
            connection.close()
    db.commit()
    db.refresh(job)
    return job


def _enqueue_locally(db: Session, job: GenerationJob, message: str) -> GenerationJob:
    job.status = JobStatus.QUEUED
    job.error_code = "LOCAL_WORKER"
    job.error_message = message
    db.commit()
    db.refresh(job)
    _submit_local(job.id)
    return job


def _submit_local(job_id: str) -> bool:
    """Submit once per API process, including during startup recovery."""

    with LOCAL_SUBMISSION_LOCK:
        if job_id in LOCAL_SUBMITTED_JOB_IDS:
            return False
        LOCAL_SUBMITTED_JOB_IDS.add(job_id)
    try:
        LOCAL_EXECUTOR.submit(_execute_locally, job_id)
    except Exception:
        with LOCAL_SUBMISSION_LOCK:
            LOCAL_SUBMITTED_JOB_IDS.discard(job_id)
        raise
    return True


def _execute_locally(job_id: str) -> None:
    from app.worker_tasks import execute_job

    try:
        while True:
            execute_job(job_id)
            from app.database import SessionLocal

            with SessionLocal() as db:
                job = db.get(GenerationJob, job_id)
                if (
                    not job
                    or job.status != JobStatus.WAITING
                    or job.error_code != "CONCURRENCY_LIMIT"
                ):
                    return
            Event().wait(0.25)
    finally:
        with LOCAL_SUBMISSION_LOCK:
            LOCAL_SUBMITTED_JOB_IDS.discard(job_id)


def recover_pending_jobs(db: Session) -> int:
    """Re-enqueue jobs orphaned by an API restart in local-capable modes.

    Only WAITING jobs and jobs previously handed to this process-local executor are
    eligible. Active jobs are intentionally left alone because their lease handling
    belongs to the worker layer.
    """

    settings = get_settings()
    apply_runtime_overrides(db, settings)
    if not settings.queue_enabled:
        return 0
    queue_mode = read_queue_mode(db)
    if queue_mode == "REDIS" or (queue_mode == "AUTO" and settings.environment != "development"):
        return 0

    jobs = list(
        db.scalars(
            select(GenerationJob)
            .where(
                or_(
                    GenerationJob.status == JobStatus.WAITING,
                    and_(
                        GenerationJob.status == JobStatus.QUEUED,
                        GenerationJob.error_code == "LOCAL_WORKER",
                    ),
                )
            )
            .order_by(GenerationJob.priority.desc(), GenerationJob.created_at)
        )
    )
    recovered = 0
    for job in jobs:
        with LOCAL_SUBMISSION_LOCK:
            already_submitted = job.id in LOCAL_SUBMITTED_JOB_IDS
        if already_submitted or not dependencies_complete(db, job):
            continue
        job.status = JobStatus.WAITING
        db.commit()
        enqueue_job(db, job)
        recovered += 1
    return recovered


def mark_job_cancelled(db: Session, job: GenerationJob) -> GenerationJob:
    """Mark a job and its visible target as cancelled without committing."""

    if job.status == JobStatus.COMPLETED:
        return job
    if job.status != JobStatus.CANCELLED:
        job.status = JobStatus.CANCELLED
        job.cancelled_at = utcnow()
        job.finished_at = utcnow()
    page_candidate = db.scalar(select(PageCandidate).where(PageCandidate.job_id == job.id))
    if page_candidate and page_candidate.status not in {"READY", "FAILED", "CANCELLED"}:
        page_candidate.status = "CANCELLED"
        page = db.get(MangaPage, page_candidate.page_id)
        if page and page.status.value == "DRAFT_GENERATING":
            other_ready = db.scalar(
                select(PageCandidate.id).where(
                    PageCandidate.page_id == page.id,
                    PageCandidate.id != page_candidate.id,
                    PageCandidate.status.in_({"READY", "INSPECTED", "NEEDS_REVIEW"}),
                    PageCandidate.deleted_at.is_(None),
                )
            )
            page.status = (
                "DRAFT_READY" if page.selected_candidate_id or other_ready else "STORYBOARDED"
            )
    asset_candidate = db.scalar(select(AssetCandidate).where(AssetCandidate.job_id == job.id))
    if asset_candidate and asset_candidate.status not in {"READY", "FAILED", "CANCELLED"}:
        asset_candidate.status = "CANCELLED"

    if job.job_type == "STYLE_ANALYZE":
        style = db.get(StyleProfile, job.target_id)
        if style and style.status.value == "ANALYZING":
            style.status = "DRAFT"
            style.version += 1

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    if node_run and node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
        node_run.status = "CANCELLED"
        node_run.finished_at = utcnow()
    return job


def cancel_job(db: Session, job: GenerationJob) -> GenerationJob:
    if job.status == JobStatus.COMPLETED:
        return job
    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    if node_run:
        run = db.get(WorkflowRun, node_run.workflow_run_id)
        if run and run.status not in {"COMPLETED", "CANCELLED", "FAILED"}:
            from app.services.workflow_engine import cancel_run

            cancel_run(db, run)
            db.refresh(job)
            return job
    mark_job_cancelled(db, job)
    db.commit()
    db.refresh(job)
    return job


def reset_for_retry(db: Session, job: GenerationJob) -> GenerationJob:
    if job.status not in {JobStatus.FAILED, JobStatus.NEEDS_REVIEW, JobStatus.WAITING}:
        return job
    job.status = JobStatus.WAITING
    job.error_code = None
    job.error_message = None
    job.progress = 0
    job.scheduled_at = utcnow() + timedelta(seconds=1)
    workflow_run_id = job.request_parameters.get("workflow_run_id")
    if workflow_run_id:
        run = db.get(WorkflowRun, workflow_run_id)
        node_run = db.scalar(
            select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id)
        )
        if run and run.status == "FAILED":
            run.status = "RUNNING"
            run.finished_at = None
            run.version += 1
        if node_run and node_run.status == "FAILED":
            node_run.status = "RUNNING"
            node_run.error_code = None
            node_run.error_message = None
            node_run.finished_at = None
    db.commit()
    return enqueue_job(db, job)

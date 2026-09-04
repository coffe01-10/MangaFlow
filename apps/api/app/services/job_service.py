import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock, Thread

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
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

LOGGER = logging.getLogger("mangaflow.jobs")

LOCAL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mangaflow-local")
LOCAL_SUBMISSION_LOCK = Lock()
LOCAL_SUBMITTED_JOB_IDS: set[str] = set()
LEASED_JOB_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}
ACTIVE_JOB_STATUSES = {JobStatus.WAITING, JobStatus.QUEUED, *LEASED_JOB_STATUSES}


def has_active_job(
    db: Session,
    *,
    job_type: str,
    target_id: str,
    target_type: str | None = None,
) -> bool:
    filters = [
        GenerationJob.job_type == job_type,
        GenerationJob.target_id == target_id,
        GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
    ]
    if target_type is not None:
        filters.append(GenerationJob.target_type == target_type)
    return db.scalar(select(GenerationJob.id).where(*filters).limit(1)) is not None


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
    auto_commit: bool = True,
) -> GenerationJob:
    if idempotency_key:
        existing = db.scalar(
            select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
        )
        if existing:
            # Collapse in-flight and successful duplicates only. A FAILED or
            # CANCELLED row must not poison retries (PAGE_INSPECT target_id is
            # the inspected READY candidate; returning that terminal job makes
            # re-inspect a silent no-op).
            if existing.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                existing.idempotency_key = f"closed:{existing.id}"
                db.flush()
            else:
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
    try:
        # Keep the caller's pending work intact if another request won the
        # idempotency race between the initial lookup and this insert.
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        if idempotency_key:
            existing = db.scalar(
                select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
            )
            if existing:
                return existing
        raise
    for dependency_id in dependency_ids or []:
        db.add(JobDependency(job_id=job.id, depends_on_job_id=dependency_id))
    for asset_id in dict.fromkeys(reference_asset_ids or []):
        db.add(JobAssetReference(job_id=job.id, asset_id=asset_id))
    db.flush()
    if auto_commit:
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


def rq_retry_policy(job: GenerationJob):
    from rq import Retry

    remaining_retries = job.max_attempts - job.attempt_count - 1
    return Retry(max=remaining_retries, interval=[10, 30, 90]) if remaining_retries > 0 else None


def _job_already_advanced(job: GenerationJob) -> bool:
    """True when a worker or terminal path already owns the job row."""
    return (
        job.status in LEASED_JOB_STATUSES
        or job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}
        or job.lease_owner is not None
        or job.cancelled_at is not None
    )


def _transition_waiting_to_queued(
    db: Session,
    job: GenerationJob,
    *,
    error_code: str | None,
    error_message: str | None,
) -> bool:
    """Atomically mark WAITING as QUEUED. Returns False if another party advanced."""
    updated = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status == JobStatus.WAITING,
            GenerationJob.lease_owner.is_(None),
            GenerationJob.cancelled_at.is_(None),
        )
        .values(
            status=JobStatus.QUEUED,
            error_code=error_code,
            error_message=error_message,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    return updated.rowcount == 1


def enqueue_job(db: Session, job: GenerationJob) -> GenerationJob:
    settings = get_settings()
    apply_runtime_overrides(db, settings)
    db.refresh(job)
    if _job_already_advanced(job):
        return job
    if not dependencies_complete(db, job):
        return job
    # Legacy environment-level maintenance switch. Runtime LOCAL no longer
    # toggles this flag, so selecting LOCAL still executes immediately.
    if not settings.queue_enabled:
        if _job_already_advanced(job):
            return job
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job.id,
                GenerationJob.status == JobStatus.WAITING,
                GenerationJob.lease_owner.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="QUEUE_DISABLED",
                error_message="任务已保存，后台执行器当前未启用",
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(job)
        return job
    queue_mode = read_queue_mode(db)
    if queue_mode == "LOCAL":
        return _enqueue_locally(db, job, "本地后台执行器正在处理任务")

    if not _transition_waiting_to_queued(db, job, error_code=None, error_message=None):
        return job

    connection = None
    try:
        from redis import Redis
        from rq import Queue

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
            retry=rq_retry_policy(job),
        )
    except Exception:
        db.refresh(job)
        if _job_already_advanced(job):
            return job
        if queue_mode == "AUTO" and settings.environment == "development":
            return _adopt_queued_job_locally(db, job, "Redis 不可用，已切换到本地后台执行")
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job.id,
                GenerationJob.status == JobStatus.QUEUED,
                GenerationJob.lease_owner.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="QUEUE_UNAVAILABLE",
                error_message=(
                    "任务已保存；REDIS 模式要求 Redis 可用"
                    if queue_mode == "REDIS"
                    else "任务已保存；Redis 队列暂时不可用"
                ),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
    finally:
        if connection is not None:
            connection.close()
    db.refresh(job)
    return job


def _enqueue_locally(db: Session, job: GenerationJob, message: str) -> GenerationJob:
    if not _transition_waiting_to_queued(
        db, job, error_code="LOCAL_WORKER", error_message=message
    ):
        return job
    _submit_local(job.id)
    return job


def _adopt_queued_job_locally(db: Session, job: GenerationJob, message: str) -> GenerationJob:
    """Keep a QUEUED row for local execution without clobbering a worker advance."""
    db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status == JobStatus.QUEUED,
            GenerationJob.lease_owner.is_(None),
            GenerationJob.cancelled_at.is_(None),
        )
        .values(error_code="LOCAL_WORKER", error_message=message)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    if job.status == JobStatus.QUEUED and job.lease_owner is None:
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
    from app.model_adapters.base import ProviderAdapterError
    from app.worker_tasks import JobCancelledError, JobLeaseLostError, execute_job

    delay = 0.1
    try:
        while True:
            try:
                execute_job(job_id)
            except (JobCancelledError, JobLeaseLostError):
                return
            except ProviderAdapterError as error:
                if not getattr(error, "retryable", True):
                    return
            except Exception:
                # The backoff loop still self-heals, but a silent pass would
                # hide real failures (DB lock, programming error) with zero
                # observability while the job appears stuck.
                LOGGER.exception(
                    "local executor retry loop hit an unexpected error for job %s",
                    job_id,
                )
            from app.database import SessionLocal

            with SessionLocal() as db:
                job = db.get(GenerationJob, job_id)
                if not job or job.status in {
                    JobStatus.COMPLETED,
                    JobStatus.CANCELLED,
                    JobStatus.FAILED,
                }:
                    return
            Event().wait(delay)
            delay = min(delay * 2, 5.0)
    finally:
        with LOCAL_SUBMISSION_LOCK:
            LOCAL_SUBMITTED_JOB_IDS.discard(job_id)


def restore_page_after_generation_exit(db: Session, page_candidate: PageCandidate) -> None:
    """Return a page from DRAFT_GENERATING to a stable status once its
    generating candidate reached a terminal state (failed/cancelled).

    Keeps the page operable instead of showing "generating" forever after a
    paid call failure; mirrors the reset previously only done on cancellation.
    """

    page = db.get(MangaPage, page_candidate.page_id)
    if page is None or str(getattr(page.status, "value", page.status)) != "DRAFT_GENERATING":
        return
    other_ready = db.scalar(
        select(PageCandidate.id).where(
            PageCandidate.page_id == page.id,
            PageCandidate.id != page_candidate.id,
            PageCandidate.status.in_({"READY", "INSPECTED", "NEEDS_REVIEW"}),
            PageCandidate.deleted_at.is_(None),
        )
    )
    page.status = "DRAFT_READY" if page.selected_candidate_id or other_ready else "STORYBOARDED"


def start_periodic_recovery() -> tuple[Thread, Event]:
    """Reclaim expired leases and re-enqueue waiting jobs while the API runs.

    RQ retries fire within the lease window and then stop, so a killed REDIS
    worker used to leave its job ACTIVE until the next API restart. A periodic
    pass in the long-lived API process closes that gap (and re-enqueues jobs
    parked as WAITING/QUEUE_UNAVAILABLE after a Redis outage). Returns the
    daemon thread with its stop event so embedders (tests) can park it.
    """

    stop = Event()

    def _loop() -> None:
        from app.database import SessionLocal
        from app.services.cli_executor import recover_abandoned_cli_runs

        settings = get_settings()
        interval = max(30.0, settings.job_lease_seconds / 2)
        while True:
            time.sleep(interval)
            if stop.is_set():
                return
            try:
                with SessionLocal() as db:
                    recover_pending_jobs(db)
            except Exception:
                LOGGER.exception("periodic job recovery pass failed")
            try:
                for run_id in recover_abandoned_cli_runs():
                    LOGGER.warning("released abandoned CLI run %s", run_id)
            except Exception:
                LOGGER.exception("periodic CLI run recovery failed")

    thread = Thread(target=_loop, name="mangaflow-job-recovery", daemon=True)
    thread.start()
    return thread, stop


def recover_pending_jobs(db: Session) -> int:
    """Reclaim expired worker leases and re-enqueue recoverable jobs."""

    settings = get_settings()
    apply_runtime_overrides(db, settings)
    if not settings.queue_enabled:
        return 0
    now = utcnow()
    expired_jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.status.in_(LEASED_JOB_STATUSES),
                or_(
                    GenerationJob.lease_expires_at.is_(None),
                    GenerationJob.lease_expires_at <= now,
                ),
            )
        )
    )
    workflow_run_ids: set[str] = set()
    for job in expired_jobs:
        observed_status = job.status
        observed_owner = job.lease_owner

        base_filter = [
            GenerationJob.id == job.id,
            GenerationJob.status == observed_status,
        ]
        if observed_owner is not None:
            base_filter.append(GenerationJob.lease_owner == observed_owner)
        else:
            base_filter.append(GenerationJob.lease_owner.is_(None))

        base_filter.append(
            or_(
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at <= now,
            )
        )

        if job.attempt_count >= job.max_attempts:
            updated = db.execute(
                update(GenerationJob)
                .where(*base_filter)
                .values(
                    status=JobStatus.FAILED,
                    error_code="LEASE_EXPIRED",
                    error_message="执行器租约已过期，且已达到最大尝试次数",
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if updated.rowcount == 1:
                page_candidate = db.scalar(
                    select(PageCandidate).where(PageCandidate.job_id == job.id)
                )
                if page_candidate:
                    page_candidate.status = "FAILED"
                    restore_page_after_generation_exit(db, page_candidate)
                asset_candidate = db.scalar(
                    select(AssetCandidate).where(AssetCandidate.job_id == job.id)
                )
                if asset_candidate:
                    asset_candidate.status = "FAILED"
                style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
                if style:
                    style.status = "DRAFT"
                node_run = db.scalar(
                    select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id)
                )
                workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
                if node_run:
                    workflow_run_id = workflow_run_id or node_run.workflow_run_id
                    if node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        node_run.status = "FAILED"
                        node_run.error_code = "LEASE_EXPIRED"
                        node_run.error_message = "执行器租约已过期，且已达到最大尝试次数"
                        node_run.finished_at = now
                if workflow_run_id:
                    workflow_run_ids.add(workflow_run_id)
        else:
            db.execute(
                update(GenerationJob)
                .where(*base_filter)
                .values(
                    status=JobStatus.WAITING,
                    error_code="LEASE_EXPIRED",
                    error_message="执行器已退出，任务等待重新执行",
                    started_at=None,
                    scheduled_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
    if expired_jobs:
        db.commit()
        db.expire_all()
    for workflow_run_id in workflow_run_ids:
        from app.services.workflow_engine import reconcile_run

        reconcile_run(db, workflow_run_id)

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
        if job.status == JobStatus.QUEUED:
            # Re-adopt a local-queue row without clobbering concurrent
            # progress: if a worker already claimed the row, the conditional
            # update misses and this round skips the job instead of writing
            # WAITING over PREPARING.
            readopted = db.execute(
                update(GenerationJob)
                .where(
                    GenerationJob.id == job.id,
                    GenerationJob.status == JobStatus.QUEUED,
                    GenerationJob.error_code == "LOCAL_WORKER",
                    GenerationJob.lease_owner.is_(None),
                    GenerationJob.cancelled_at.is_(None),
                )
                .values(status=JobStatus.WAITING)
                .execution_options(synchronize_session=False)
            )
            if readopted.rowcount != 1:
                continue
            db.commit()
            db.expire(job)
        enqueue_job(db, job)
        recovered += 1
    return recovered


def mark_job_failed(
    db: Session,
    job: GenerationJob,
    error_code: str,
    error_message: str,
    *,
    candidate_status: str = "FAILED",
) -> str | None:
    """Mark a job and its visible targets as failed without committing."""

    if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
        return None
    now = utcnow()
    job.status = JobStatus.FAILED
    job.error_code = error_code
    job.error_message = error_message[:500]
    job.finished_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    page_candidate = db.scalar(select(PageCandidate).where(PageCandidate.job_id == job.id))
    if page_candidate:
        page_candidate.status = candidate_status
        restore_page_after_generation_exit(db, page_candidate)
    asset_candidate = db.scalar(
        select(AssetCandidate).where(AssetCandidate.job_id == job.id)
    )
    if asset_candidate:
        asset_candidate.status = "FAILED"
    style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
    if style:
        style.status = "DRAFT"

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id
        if node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            node_run.status = "FAILED"
            node_run.error_code = error_code
            node_run.error_message = error_message[:500]
            node_run.finished_at = now
    return workflow_run_id


def mark_job_cancelled(db: Session, job: GenerationJob) -> GenerationJob:
    """Mark a job and its visible target as cancelled without committing."""

    now = utcnow()
    claimed = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status.not_in(
                {JobStatus.COMPLETED, JobStatus.CANCELLED}
            ),
        )
        .values(
            status=JobStatus.CANCELLED,
            cancelled_at=now,
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.refresh(job)
    if claimed.rowcount != 1:
        return job
    page_candidate = db.scalar(select(PageCandidate).where(PageCandidate.job_id == job.id))
    if page_candidate and page_candidate.status not in {
        "READY",
        "FAILED",
        "CANCELLED",
        "INSPECTED",
        "NEEDS_REVIEW",
    }:
        page_candidate.status = "CANCELLED"
        restore_page_after_generation_exit(db, page_candidate)
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
    # Single conditional claim instead of read-modify-write: a worker lease or
    # a cancellation landing between the caller's read and this write must win
    # over the retry (no clobbered lease, no resurrection), mirroring
    # _transition_waiting_to_queued and mark_job_cancelled.
    claimed = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status.in_(
                [JobStatus.FAILED, JobStatus.NEEDS_REVIEW, JobStatus.WAITING]
            ),
            GenerationJob.lease_owner.is_(None),
            GenerationJob.cancelled_at.is_(None),
        )
        .values(
            status=JobStatus.WAITING,
            error_code=None,
            error_message=None,
            progress=0,
            started_at=None,
            finished_at=None,
            cancelled_at=None,
            lease_owner=None,
            lease_expires_at=None,
            scheduled_at=utcnow() + timedelta(seconds=1),
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        # Another party advanced the row; surface its current state as-is.
        db.refresh(job)
        return job
    # The claim above owns the job row, which serializes this revival against
    # concurrent cancel_run/worker claims on the same job, so the cross-table
    # writes below cannot clobber a concurrent terminal transition.
    # Resolve the run via the node link too: an adopted route-created inspect
    # job carries no workflow_run_id in request_parameters, and without the
    # fallback its retry would skip the WorkflowRun/WorkflowNodeRun revival.
    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id
    if workflow_run_id:
        run = db.get(WorkflowRun, workflow_run_id)
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
    db.refresh(job)
    return enqueue_job(db, job)

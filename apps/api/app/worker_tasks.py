import logging
import os
import socket
from datetime import timedelta
from threading import Event, Lock, Thread
from uuid import uuid4

from sqlalchemy import func, or_, select, update

from app.config import get_settings
from app.database import SessionLocal
from app.domain.states import JobStatus
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    AssetCandidate,
    GenerationJob,
    PageCandidate,
    Project,
    StyleProfile,
    WorkflowNodeRun,
    utcnow,
)
from app.services.worker_handlers import provider
from app.services.worker_handlers.asset_generate import _run_asset_generate
from app.services.worker_handlers.execution import (
    JobCancelledError,
    JobLeaseLostError,
    StaleStoryboardVersionError,
    _ensure_job_not_cancelled,
    _lease_is_expired,
)
from app.services.worker_handlers.inspection import _run_inspection
from app.services.worker_handlers.page_generate import _run_page_generate
from app.services.worker_handlers.story_parse import _run_story_parse
from app.services.worker_handlers.style_analyze import _run_style_analyze

LOGGER = logging.getLogger("mangaflow.worker")

ACTIVE_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}
CLAIMABLE_STATUSES = {JobStatus.WAITING, JobStatus.QUEUED}
EXECUTION_RESERVATION_LOCK = Lock()


def _adapter(_alias: str):
    """Legacy test seam retained while production calls use catalog bindings."""

    return None


# Handlers bind models through ``provider._binding``; bridge this module's
# ``_adapter`` seam at call time so existing monkeypatches keep steering it.
provider.install_legacy_adapter_lookup(lambda alias: _adapter(alias))


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex}"


def _lease_duration() -> timedelta:
    return timedelta(seconds=get_settings().job_lease_seconds)


class _LeaseHeartbeat:
    """Refresh a job lease without sharing the worker's SQLAlchemy session."""

    def __init__(self, job_id: str, owner: str):
        self.job_id = job_id
        self.owner = owner
        self.duration = _lease_duration()
        self.interval = max(5.0, min(30.0, self.duration.total_seconds() / 3))
        self.stop = Event()
        self.lost = False
        self.thread: Thread | None = None

    def __enter__(self):
        self.thread = Thread(
            target=self._run,
            name=f"mangaflow-lease-{self.job_id[:8]}",
            daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=max(1.0, self.interval))

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                now = utcnow()
                with SessionLocal() as db:
                    updated = db.execute(
                        update(GenerationJob)
                        .where(
                            GenerationJob.id == self.job_id,
                            GenerationJob.lease_owner == self.owner,
                            GenerationJob.lease_expires_at.is_not(None),
                            GenerationJob.lease_expires_at > now,
                            GenerationJob.status.in_(ACTIVE_STATUSES),
                            GenerationJob.status.not_in(
                                {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
                            ),
                        )
                        .values(lease_expires_at=now + self.duration)
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
                    if updated.rowcount != 1:
                        self.lost = True
                        return
            except Exception:
                # A transient heartbeat failure should not turn a healthy
                # provider call into a second paid request.  The lease itself
                # remains the source of truth and will be reclaimed if it
                # eventually expires.
                LOGGER.warning(
                    "lease heartbeat failed for job %s", self.job_id, exc_info=True
                )
                if self.stop.wait(1.0):
                    return


def _claim_job(db, job_id: str, owner: str) -> GenerationJob | None:
    """Atomically claim one queued/retryable job for this worker."""

    job = db.get(GenerationJob, job_id)
    if not job or job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
        return None
    now = utcnow()
    expired = job.status in ACTIVE_STATUSES and _lease_is_expired(job.lease_expires_at)
    if job.status not in CLAIMABLE_STATUSES and not expired:
        return None
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    is_postgres = dialect_name == "postgresql"

    if is_postgres:
        project = db.scalar(
            select(Project)
            .where(Project.id == job.project_id)
            .with_for_update()
        )
    else:
        project = db.get(Project, job.project_id)

    if not project or project.deleted_at is not None:
        return None

    expected_status = job.status
    active_subquery = (
        select(func.count(GenerationJob.id))
        .where(
            GenerationJob.project_id == project.id,
            GenerationJob.id != job.id,
            GenerationJob.status.in_(ACTIVE_STATUSES),
            or_(
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at > now,
            ),
        )
        .scalar_subquery()
    )

    claim_filter = [
        GenerationJob.id == job.id,
        GenerationJob.attempt_count < GenerationJob.max_attempts,
        active_subquery < project.default_concurrency,
    ]
    if expired:
        claim_filter.append(GenerationJob.status == expected_status)
        if job.lease_owner is not None:
            claim_filter.append(GenerationJob.lease_owner == job.lease_owner)
        else:
            claim_filter.append(GenerationJob.lease_owner.is_(None))
        if job.lease_expires_at is not None:
            claim_filter.append(GenerationJob.lease_expires_at <= now)
        else:
            claim_filter.append(GenerationJob.lease_expires_at.is_(None))
    else:
        claim_filter.append(GenerationJob.status == expected_status)
    updated = db.execute(
        update(GenerationJob)
        .where(*claim_filter)
        .values(
            status=JobStatus.PREPARING,
            progress=5,
            attempt_count=GenerationJob.attempt_count + 1,
            started_at=func.coalesce(GenerationJob.started_at, now),
            error_code=None,
            error_message=None,
            lease_owner=owner,
            lease_expires_at=now + _lease_duration(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        # 在仍持有锁的事务中通过严格条件更新标记等待状态，绝不释放锁后无条件覆盖新租约
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.status == expected_status,
                GenerationJob.lease_owner.is_(None),
                GenerationJob.lease_expires_at.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="CONCURRENCY_LIMIT",
                error_message="等待项目并发名额",
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return None
    db.commit()
    db.expire_all()
    return db.get(GenerationJob, job_id)


def _mark_worker_failure(
    db,
    job_id: str,
    owner: str,
    error_code: str,
    error_message: str,
    *,
    candidate_status: str = "FAILED",
    retryable: bool = False,
) -> tuple[bool, str | None, bool]:
    """Persist failure output or reset for retry while worker holds valid lease.

    Returns (updated, workflow_run_id, is_final_failure).
    """

    now = utcnow()
    job = db.get(GenerationJob, job_id)
    if not job:
        return False, None, False

    is_retryable = bool(retryable and (job.attempt_count < job.max_attempts))
    target_status = JobStatus.WAITING if is_retryable else JobStatus.FAILED

    updated = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.lease_owner == owner,
            GenerationJob.lease_expires_at.is_not(None),
            GenerationJob.lease_expires_at > now,
            GenerationJob.status.in_(ACTIVE_STATUSES),
            GenerationJob.status != JobStatus.CANCELLED,
        )
        .values(
            status=target_status,
            error_code=error_code,
            error_message=error_message[:500],
            finished_at=None if is_retryable else now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        return False, None, False

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id

    if not is_retryable:
        # Own the candidate this job produced (job_id), not the row named in
        # target_id. PAGE_INSPECT points target_id at an existing READY
        # candidate; stamping that row FAILED/STALE destroys adopted work.
        page_candidate = db.scalar(
            select(PageCandidate).where(PageCandidate.job_id == job.id)
        )
        if page_candidate:
            page_candidate.status = candidate_status
            # A finally-failed generation must not leave the page stuck in
            # DRAFT_GENERATING: restore a stable status so the UI and the next
            # generation attempt stay operable (mirrors the cancel path).
            from app.services.job_service import restore_page_after_generation_exit

            restore_page_after_generation_exit(db, page_candidate)
        asset_candidate = db.scalar(
            select(AssetCandidate).where(AssetCandidate.job_id == job.id)
        )
        if asset_candidate:
            asset_candidate.status = "FAILED"
        style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
        if style:
            style.status = "DRAFT"

        if node_run and node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            node_run.status = "FAILED"
            node_run.error_code = error_code
            node_run.error_message = error_message[:500]
            node_run.finished_at = now

    db.commit()
    db.expire_all()
    return True, workflow_run_id, not is_retryable


def _defer_concurrency_wait(job_id: str) -> None:
    """Keep a slot-wait job schedulable instead of silently succeeding out of RQ."""

    from rq import Queue, get_current_job

    from app.services.job_service import rq_retry_policy

    current = get_current_job()
    if current is None:
        # LOCAL/AUTO's local executor already owns a bounded backoff loop.
        return
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if (
            job is None
            or job.status != JobStatus.WAITING
            or job.error_code != "CONCURRENCY_LIMIT"
        ):
            return
        retry = rq_retry_policy(job)
    # Use the running worker's queue/connection. A child-local thread cannot survive RQ exit.
    # Let a scheduling failure reach RQ's retry/error handling instead of hiding it.
    Queue(current.origin, connection=current.connection).enqueue_in(
        timedelta(seconds=3),
        "app.worker_tasks.execute_job",
        job_id,
        job_id=f"{job_id}-slot-{uuid4().hex}",
        job_timeout=settings.job_timeout_seconds,
        retry=retry,
    )


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    owner = _worker_id()
    db.info["job_lease_owner"] = owner
    try:
        job = db.get(GenerationJob, job_id)
        if not job or job.status == JobStatus.CANCELLED:
            return
        project = db.get(Project, job.project_id)
        if not project or project.deleted_at is not None:
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
            return
        db.info["job_id"] = job_id
        with EXECUTION_RESERVATION_LOCK:
            job = _claim_job(db, job_id, owner)
        if not job:
            _defer_concurrency_wait(job_id)
            return
        with _LeaseHeartbeat(job.id, owner) as heartbeat:
            if job.job_type in {
                "PAGE_GENERATE",
                "PAGE_REPAIR",
                "PAGE_UPSCALE",
                "PAGE_REGION_REGENERATE",
            }:
                _run_page_generate(db, job)
            elif job.job_type == "ASSET_GENERATE":
                _run_asset_generate(db, job)
            elif job.job_type == "SOURCE_PARSE":
                _run_story_parse(db, job)
            elif job.job_type == "STYLE_ANALYZE":
                _run_style_analyze(db, job)
            elif job.job_type == "PAGE_INSPECT":
                _run_inspection(db, job)
            elif job.job_type == "WORKFLOW_NODE":
                from app.services.workflow_engine import execute_workflow_node

                execute_workflow_node(db, job)
            else:
                raise RuntimeError(f"未知任务类型：{job.job_type}")
            _ensure_job_not_cancelled(db, job)
            if heartbeat.lost:
                raise JobLeaseLostError("任务租约已被其他执行器接管")
            workflow_run_id = job.request_parameters.get("workflow_run_id")
            db.expire(
                job,
                attribute_names=[
                    "status",
                    "progress",
                    "finished_at",
                    "error_code",
                    "error_message",
                    "lease_owner",
                    "lease_expires_at",
                ],
            )
            with db.no_autoflush:
                completed = db.execute(
                    update(GenerationJob)
                    .where(
                        GenerationJob.id == job.id,
                        GenerationJob.lease_owner == owner,
                        GenerationJob.status != JobStatus.CANCELLED,
                    )
                    .values(
                        status=JobStatus.COMPLETED,
                        progress=100,
                        finished_at=utcnow(),
                        error_code=None,
                        error_message=None,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            if completed.rowcount != 1:
                current = db.get(GenerationJob, job.id)
                if current and current.status == JobStatus.CANCELLED:
                    raise JobCancelledError("任务已取消，完成状态不再写入")
                raise JobLeaseLostError("任务租约已被其他执行器接管")
            db.commit()
            provider.flush_staged_attempt_outputs(db)
        if workflow_run_id:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
    except JobLeaseLostError:
        db.rollback()
        return
    except JobCancelledError:
        db.rollback()
        db.expire_all()
        job = db.get(GenerationJob, job_id)
        if job and job.status != JobStatus.COMPLETED and (
            job.status == JobStatus.CANCELLED or job.lease_owner == owner
        ):
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
        return
    except StaleStoryboardVersionError as error:
        db.rollback()
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            "STALE_STORYBOARD_VERSION",
            str(error),
            candidate_status="STALE",
            retryable=False,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    except ProviderAdapterError as error:
        db.rollback()
        is_retryable = getattr(error, "retryable", True)
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            error.code,
            error.user_message,
            retryable=is_retryable,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    except Exception as error:
        db.rollback()
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            "WORKER_ERROR",
            str(error),
            retryable=True,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    finally:
        db.close()

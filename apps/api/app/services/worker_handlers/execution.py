"""Execution-shell primitives shared by the worker shell and task handlers.

Single definitions for the cancellation/lease checks and owned-progress
commits.  ``app.worker_tasks`` owns when they run; handlers call them as-is
and must not copy or weaken the checks.
"""

from datetime import UTC, datetime

from sqlalchemy import update

from app.domain.states import JobStatus
from app.models import GenerationJob, utcnow


class StaleStoryboardVersionError(RuntimeError):
    """Stop a queued image call when its storyboard input has already changed."""


class JobCancelledError(RuntimeError):
    """Stop persisting provider output after a concurrent cancellation."""


class JobLeaseLostError(RuntimeError):
    """Stop persisting provider output when another worker reclaimed the lease."""


def _lease_is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= utcnow()


def _ensure_job_not_cancelled(db, job: GenerationJob) -> None:
    db.refresh(job, attribute_names=["status", "cancelled_at", "lease_owner", "lease_expires_at"])
    if job.status == JobStatus.CANCELLED or job.cancelled_at is not None:
        raise JobCancelledError("任务已取消，模型返回结果不再写入")
    owner = db.info.get("job_lease_owner")
    if owner and (
        job.lease_owner != owner
        or job.lease_expires_at is None
        or _lease_is_expired(job.lease_expires_at)
    ):
        raise JobLeaseLostError("任务租约已被其他执行器接管")


def _commit_owned_progress(
    db, job: GenerationJob, *, status: JobStatus, progress: int
) -> None:
    """Persist an intermediate status only while this worker still owns the job."""

    _ensure_job_not_cancelled(db, job)
    owner = db.info.get("job_lease_owner")
    now = datetime.now(UTC)
    filters = [
        GenerationJob.id == job.id,
        GenerationJob.cancelled_at.is_(None),
        GenerationJob.status.not_in(
            {JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.FAILED}
        ),
    ]
    if owner:
        filters.extend(
            [
                GenerationJob.lease_owner == owner,
                GenerationJob.lease_expires_at.is_not(None),
                GenerationJob.lease_expires_at > now,
            ]
        )
    updated = db.execute(
        update(GenerationJob)
        .where(*filters)
        .values(status=status, progress=progress)
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        db.rollback()
        current = db.get(GenerationJob, job.id)
        if current is not None:
            _ensure_job_not_cancelled(db, current)
        raise JobLeaseLostError("任务租约已被其他执行器接管")
    db.commit()
    db.refresh(job)

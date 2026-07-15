from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import GenerationJob, JobDependency, utcnow
from app.services.runtime_settings import apply_runtime_overrides

LOCAL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mangaflow-local")


def create_job(
    db: Session,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    job_type: str,
    model_alias: str | None = None,
    request_parameters: dict | None = None,
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
    if not settings.queue_enabled:
        job.error_code = "QUEUE_DISABLED"
        job.error_message = "任务已保存，队列当前未启用"
        db.commit()
        db.refresh(job)
        return job
    try:
        from redis import Redis
        from rq import Queue, Retry

        connection = Redis.from_url(settings.redis_url)
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
        if settings.environment == "development":
            job.status = JobStatus.QUEUED
            job.error_code = "LOCAL_WORKER"
            job.error_message = "Redis 不可用，已切换到本地后台执行"
            db.commit()
            db.refresh(job)
            LOCAL_EXECUTOR.submit(_execute_locally, job.id)
            return job
        job.status = JobStatus.WAITING
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = "任务已保存；Redis 队列暂时不可用"
    db.commit()
    db.refresh(job)
    return job


def _execute_locally(job_id: str) -> None:
    from app.worker_tasks import execute_job

    while True:
        execute_job(job_id)
        from app.database import SessionLocal

        with SessionLocal() as db:
            job = db.get(GenerationJob, job_id)
            if not job or job.status != JobStatus.WAITING or job.error_code != "CONCURRENCY_LIMIT":
                return
        Event().wait(0.25)


def cancel_job(db: Session, job: GenerationJob) -> GenerationJob:
    if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
        return job
    job.status = JobStatus.CANCELLED
    job.cancelled_at = utcnow()
    job.finished_at = utcnow()
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
    db.commit()
    return enqueue_job(db, job)

"""Regression: a stranded Redis handoff must be recoverable.

REDIS-mode enqueue committed QUEUED with a NULL error code and then handed
the job to Redis; a process death between the two left the row QUEUED with
no payload and no recovery path — visible forever as a pending job no
worker would ever claim. The handoff now writes an RQ_PENDING marker in the
same commit, a successful enqueue clears it, and recover_pending_jobs
re-adopts stale markers back into the queue.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update as sa_update

from app.config import get_settings
from app.models import GenerationJob, Project
from app.services import job_service


def _queued_rq_pending(db, *, age_seconds: int) -> GenerationJob:
    project = Project(name="rq-pending")
    db.add(project)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="chapter-1",
        job_type="SOURCE_PARSE",
        status="QUEUED",
        error_code="RQ_PENDING",
    )
    db.add(job)
    db.commit()
    db.execute(
        sa_update(GenerationJob)
        .where(GenerationJob.id == job.id)
        .values(
            updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        )
    )
    db.commit()
    db.expire(job)
    return job


def test_stale_rq_pending_row_is_readopted(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "queue_enabled", True)
    monkeypatch.setattr(job_service, "read_queue_mode", lambda db: "LOCAL")
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: None)
    job = _queued_rq_pending(db_session, age_seconds=600)

    recovered = job_service.recover_pending_jobs(db_session)
    assert recovered >= 1

    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.error_code != "RQ_PENDING"
    assert row.status in {"WAITING", "QUEUED"}


def test_fresh_rq_pending_row_is_not_swept(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "queue_enabled", True)
    job = _queued_rq_pending(db_session, age_seconds=0)

    job_service.recover_pending_jobs(db_session)

    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    # The in-flight enqueue owns this row; the sweep must not race it.
    assert row.status == "QUEUED"
    assert row.error_code == "RQ_PENDING"

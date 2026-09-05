"""Guard: POST /jobs/{id}/retry must reject non-retryable statuses.

reset_for_retry ignores every status outside FAILED/NEEDS_REVIEW/WAITING and
returns the row unchanged; the route used to answer 200 for those no-ops,
which read to clients as "retry accepted". Only genuinely retryable statuses
may pass; the service-level guard stays as defense in depth.
"""

from sqlalchemy import select

from app.domain.states import JobStatus
from app.models import GenerationJob, Project, utcnow


def _seed_job(db, status: JobStatus, **kwargs) -> GenerationJob:
    project = Project(name="重试状态守卫")
    db.add(project)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id="unused",
        job_type="STYLE_ANALYZE",
        status=status,
        **kwargs,
    )
    db.add(job)
    db.commit()
    return job


def _job_count(db, project_id: str) -> int:
    return len(
        list(
            db.scalars(
                select(GenerationJob.id).where(GenerationJob.project_id == project_id)
            )
        )
    )


def test_retry_rejects_cancelled_job_and_keeps_row_unchanged(client, db_session):
    cancelled = _seed_job(
        db_session,
        JobStatus.CANCELLED,
        cancelled_at=utcnow(),
        finished_at=utcnow(),
    )

    response = client.post(f"/api/v1/jobs/{cancelled.id}/retry")

    assert response.status_code == 409, response.json()
    db_session.expire_all()
    row = db_session.get(GenerationJob, cancelled.id)
    assert row.status == JobStatus.CANCELLED
    assert row.cancelled_at is not None
    assert row.finished_at is not None
    assert _job_count(db_session, cancelled.project_id) == 1


def test_retry_rejects_completed_job(client, db_session):
    completed = _seed_job(db_session, JobStatus.COMPLETED, finished_at=utcnow())

    response = client.post(f"/api/v1/jobs/{completed.id}/retry")

    assert response.status_code == 409, response.json()
    db_session.expire_all()
    assert db_session.get(GenerationJob, completed.id).status == JobStatus.COMPLETED


def test_retry_rejects_queued_job(client, db_session):
    queued = _seed_job(db_session, JobStatus.QUEUED)

    response = client.post(f"/api/v1/jobs/{queued.id}/retry")

    assert response.status_code == 409, response.json()
    db_session.expire_all()
    assert db_session.get(GenerationJob, queued.id).status == JobStatus.QUEUED


def test_retry_still_resets_failed_job(client, db_session, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    failed = _seed_job(
        db_session,
        JobStatus.FAILED,
        error_code="WORKER_ERROR",
        finished_at=utcnow(),
    )

    response = client.post(f"/api/v1/jobs/{failed.id}/retry")

    assert response.status_code == 200, response.json()
    db_session.expire_all()
    row = db_session.get(GenerationJob, failed.id)
    assert row.status == JobStatus.WAITING
    assert row.started_at is None
    assert row.finished_at is None


def test_retry_rejects_archived_failed_job(client, db_session):
    """An archived FAILED job must be restored before it can retry.

    Pre-fix the retry endpoint happily reset an archived row to WAITING and
    re-enqueued paid work that is invisible in the default jobs list.
    """

    archived = _seed_job(
        db_session,
        JobStatus.FAILED,
        finished_at=utcnow(),
        archived_at=utcnow(),
    )

    response = client.post(f"/api/v1/jobs/{archived.id}/retry")

    assert response.status_code == 409, response.json()
    assert "归档" in response.json()["detail"]
    db_session.expire_all()
    row = db_session.get(GenerationJob, archived.id)
    assert row.status == JobStatus.FAILED
    assert row.archived_at is not None

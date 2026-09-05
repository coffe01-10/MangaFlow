"""Regression: retrying a claimed job must not clear the live worker's lease.

``reset_for_retry`` used to read the status and then unconditionally rewrite
WAITING + a null lease: a worker that claimed the job between the route's
read and the commit lost its lease mid-paid-call, its output was discarded
after success, and a second dispatch re-paid the provider. The reset is now
a conditional update against the observed state and an unowned lease.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.models import GenerationJob, Project
from app.services.job_service import reset_for_retry


def test_reset_for_retry_refuses_concurrently_claimed_job(db_session):
    project = Project(name="retry-claim-race")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="chapter-1",
        job_type="SOURCE_PARSE",
        status="FAILED",
        error_code="UPSTREAM",
    )
    db_session.add(job)
    db_session.commit()
    stale = GenerationJob(status=job.status, id=job.id)

    from datetime import UTC, datetime, timedelta

    db_session.execute(
        update(GenerationJob)
        .where(GenerationJob.id == job.id)
        .values(
            status="GENERATING",
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        reset_for_retry(db_session, stale)
    assert exc_info.value.status_code == 409

    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == "GENERATING"
    assert row.lease_owner == "worker-1"


def test_reset_for_retry_still_resets_terminal_job(db_session):
    project = Project(name="retry-terminal")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="chapter-1",
        job_type="SOURCE_PARSE",
        status="FAILED",
        error_code="UPSTREAM",
        attempt_count=1,
    )
    db_session.add(job)
    db_session.commit()

    reset = reset_for_retry(db_session, job)
    db_session.refresh(job)
    # reset requeues as WAITING; enqueue_job immediately advances it to QUEUED.
    assert job.status in {"WAITING", "QUEUED"}
    assert job.lease_owner is None
    assert reset.id == job.id

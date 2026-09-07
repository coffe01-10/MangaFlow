"""Regression: retry-arbitration compensation actually stops the loser.

Two gaps in the retry-revival arbitration: (1) a loser whose pre-retry state
was itself dispatchable (WAITING) was "compensated" by writing WAITING over
WAITING — reported as compensated while the duplicate stayed alive; the
arbitration now cancels such a loser. (2) A loser whose row was advanced to
QUEUED by the recovery pass between commit and verify short-circuited the
arbitration as "advanced normally" — an unleased QUEUED row is still
compensable, so arbitration now runs for it too.
"""

import pytest

from app.domain.states import JobStatus
from app.models import GenerationJob, Project
from app.services.job_service import (
    _compensate_retry_revival,
    _verify_retry_revival_post_commit,
)


def _job(db, name: str, **overrides) -> GenerationJob:
    project = db.query(Project).filter_by(name=name).one_or_none()
    if project is None:
        project = Project(name=name)
        db.add(project)
        db.flush()
    fields = {
        "project_id": project.id,
        "target_type": "PAGE_CANDIDATE",
        "target_id": "cand-1",
        "job_type": "PAGE_INSPECT",
        "status": JobStatus.WAITING,
    }
    fields.update(overrides)
    job = GenerationJob(**fields)
    db.add(job)
    db.commit()
    return job


def _snapshot_waiting() -> dict:
    return {
        "job": {
            "status": JobStatus.WAITING,
            "error_code": None,
            "error_message": None,
            "progress": 0,
            "started_at": None,
            "finished_at": None,
            "cancelled_at": None,
            "scheduled_at": None,
            "lease_owner": None,
            "lease_expires_at": None,
        },
        "run": None,
    }


def _snapshot_failed() -> dict:
    snapshot = _snapshot_waiting()
    snapshot["job"]["status"] = JobStatus.FAILED
    snapshot["job"]["error_code"] = "UPSTREAM"
    return snapshot


def test_compensation_cancels_dispatchable_preimage_loser(db_session):
    job = _job(db_session, "仲裁等待前像")

    compensated = _compensate_retry_revival(
        db_session, job, _snapshot_waiting(), observed_status=JobStatus.WAITING
    )

    assert compensated is True
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.CANCELLED
    assert row.cancelled_at is not None


def test_compensation_restores_terminal_preimage(db_session):
    job = _job(db_session, "仲裁失败前像")

    compensated = _compensate_retry_revival(
        db_session, job, _snapshot_failed(), observed_status=JobStatus.WAITING
    )

    assert compensated is True
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.FAILED
    assert row.error_code == "UPSTREAM"


def test_verify_arbitrates_queued_loser(db_session):
    from datetime import timedelta

    from fastapi import HTTPException

    from app.models import utcnow

    older = _job(
        db_session,
        "仲裁排队兄",
        status=JobStatus.QUEUED,
    )
    # Pin the ordering: both created_at defaults can land on the same clock
    # tick, and the id tie-break on random UUIDs would flip the winner.
    older.created_at = utcnow() - timedelta(seconds=5)
    db_session.commit()
    younger = _job(db_session, "仲裁排队弟", status=JobStatus.QUEUED)
    snapshot = _snapshot_waiting()

    with pytest.raises(HTTPException) as exc_info:
        _verify_retry_revival_post_commit(db_session, younger, snapshot)

    assert exc_info.value.status_code == 409
    db_session.expire_all()
    row = db_session.get(GenerationJob, younger.id)
    assert row.status == JobStatus.CANCELLED
    # The older sibling stays dispatchable.
    kept = db_session.get(GenerationJob, older.id)
    assert kept.status == JobStatus.QUEUED


def test_verify_skips_leased_queued_row(db_session):
    job = _job(
        db_session,
        "仲裁租约弟",
        status=JobStatus.QUEUED,
        lease_owner="worker-1",
    )
    snapshot = _snapshot_waiting()

    survived = _verify_retry_revival_post_commit(db_session, job, snapshot)

    assert survived is True
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.QUEUED
    assert row.lease_owner == "worker-1"

"""Regression (issue #125): retry must keep PAGE_INSPECT one-active-per-target.

The manual route (/candidates/{id}/inspect) guards duplicates with
``has_active_job``, but the retry entry point did not: a FAILED inspect job's
idempotency key was collapsed to ``closed:{id}`` by ``create_job`` when a newer
inspect took the key, so nothing deduped the revival — ``reset_for_retry`` could
resurrect the dead job next to a live manual/workflow inspect on the same
candidate, producing two paid ``analyze_multimodal`` calls, duplicate
InspectionResult rows and racing candidate/page status writes.
"""

import pytest
from fastapi import HTTPException

from app.domain.states import JobStatus
from app.models import AppSetting, GenerationJob, Project
from app.services import job_service


def _set_queue_mode(db, mode: str) -> None:
    db.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db.commit()


def _project(db, name: str) -> Project:
    project = Project(name=name)
    db.add(project)
    db.flush()
    return project


def _inspect_job(project_id: str, target_id: str, status: JobStatus, **overrides) -> GenerationJob:
    fields = dict(
        project_id=project_id,
        target_type="PAGE_CANDIDATE",
        target_id=target_id,
        job_type="PAGE_INSPECT",
        status=status,
        error_code="UPSTREAM" if status == JobStatus.FAILED else None,
    )
    fields.update(overrides)
    return GenerationJob(**fields)


@pytest.mark.parametrize(
    "sibling_status",
    [JobStatus.WAITING, JobStatus.QUEUED, JobStatus.GENERATING],
)
def test_reset_for_retry_rejects_inspect_when_sibling_active(
    db_session, monkeypatch, sibling_status
):
    project = _project(db_session, "重试互斥-" + sibling_status.value)
    failed = _inspect_job(project.id, "candidate-1", JobStatus.FAILED)
    sibling = _inspect_job(
        project.id,
        "candidate-1",
        sibling_status,
        lease_owner="live-worker" if sibling_status == JobStatus.GENERATING else None,
    )
    db_session.add_all([failed, sibling])
    db_session.commit()
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, failed)

    assert exc_info.value.status_code == 409
    db_session.expire_all()
    row = db_session.get(GenerationJob, failed.id)
    # The revival claim is rolled back: the row stays FAILED, untouched.
    assert row.status == JobStatus.FAILED
    assert row.error_code == "UPSTREAM"
    assert db_session.get(GenerationJob, sibling.id).status == sibling_status
    assert submitted == []  # nothing was enqueued


def test_reset_for_retry_inspect_without_sibling_proceeds(db_session, monkeypatch):
    """Self-exclusion: the claim moves the retried row itself back to WAITING,
    so the guard must not trip on the job's own revived row."""

    _set_queue_mode(db_session, "LOCAL")
    project = _project(db_session, "重试自排除")
    waiting = _inspect_job(project.id, "candidate-2", JobStatus.WAITING)
    waiting.error_code = "QUEUE_UNAVAILABLE"
    db_session.add(waiting)
    db_session.commit()
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    reset = job_service.reset_for_retry(db_session, waiting)

    db_session.expire_all()
    row = db_session.get(GenerationJob, waiting.id)
    assert reset.id == waiting.id
    assert row.status in {JobStatus.WAITING, JobStatus.QUEUED}
    assert row.lease_owner is None


@pytest.mark.parametrize("sibling_status", [JobStatus.COMPLETED, JobStatus.FAILED])
def test_reset_for_retry_ignores_terminal_inspect_sibling(db_session, monkeypatch, sibling_status):
    project = _project(db_session, "重试终态兄弟-" + sibling_status.value)
    failed = _inspect_job(project.id, "candidate-3", JobStatus.FAILED)
    terminal = _inspect_job(project.id, "candidate-3", sibling_status)
    db_session.add_all([failed, terminal])
    db_session.commit()
    _set_queue_mode(db_session, "LOCAL")
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    job_service.reset_for_retry(db_session, failed)

    db_session.expire_all()
    row = db_session.get(GenerationJob, failed.id)
    assert row.status in {JobStatus.WAITING, JobStatus.QUEUED}


def test_reset_for_retry_mutex_is_inspect_only(db_session, monkeypatch):
    """Non-inspect types keep their existing retry semantics: the same-target
    guard is deliberately scoped to INSPECT_JOB_TYPES (cross-entry mutex for
    e.g. SOURCE_PARSE is a separate issue, #124)."""

    _set_queue_mode(db_session, "LOCAL")
    project = _project(db_session, "重试非质检")
    failed = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="chapter-9",
        job_type="SOURCE_PARSE",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
    )
    sibling = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="chapter-9",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add_all([failed, sibling])
    db_session.commit()
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    job_service.reset_for_retry(db_session, failed)

    db_session.expire_all()
    assert db_session.get(GenerationJob, failed.id).status in {
        JobStatus.WAITING,
        JobStatus.QUEUED,
    }

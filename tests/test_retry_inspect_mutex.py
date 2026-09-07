"""Regression (issues #125/#124): retry must keep one-per-target job types
one-active-per-target.

The manual route (/candidates/{id}/inspect) guards duplicates with
``has_active_job``, but the retry entry point did not: a FAILED inspect job's
idempotency key was collapsed to ``closed:{id}`` by ``create_job`` when a newer
inspect took the key, so nothing deduped the revival — ``reset_for_retry`` could
resurrect the dead job next to a live manual/workflow inspect on the same
candidate, producing two paid ``analyze_multimodal`` calls, duplicate
InspectionResult rows and racing candidate/page status writes.

SOURCE_PARSE joins the retry mutex (#124): its route and workflow entries use
disjoint idempotency-key namespaces, so a retried FAILED parse was equally
invisible to every guard and could run a second paid structuring call next to
a live parse on the same chapter.
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.domain.states import JobStatus
from app.models import (
    AppSetting,
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    utcnow,
)
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


def test_reset_for_retry_rejects_source_parse_when_sibling_active(
    db_session, monkeypatch
):
    """#124: a retried FAILED SOURCE_PARSE must 409 while another parse for the
    same chapter is still ACTIVE — the route/workflow guards cannot see the
    revival (disjoint idempotency-key namespaces), so this is the only fence
    between the retry and a second paid structuring call."""

    project = _project(db_session, "解析重试互斥")
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
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, failed)

    assert exc_info.value.status_code == 409
    db_session.expire_all()
    assert db_session.get(GenerationJob, failed.id).status == JobStatus.FAILED
    assert db_session.get(GenerationJob, sibling.id).status == JobStatus.QUEUED
    assert submitted == []  # nothing was enqueued


def test_reset_for_retry_mutex_still_scoped_to_mutex_types(db_session, monkeypatch):
    """Job types outside RETRY_MUTEX_JOB_TYPES keep their existing retry
    semantics: the same-target guard is deliberately scoped to the mutex set
    (SOURCE_PARSE joined it, closing #124), so an ordinary generation retry
    still proceeds next to a queued sibling of the same type."""

    _set_queue_mode(db_session, "LOCAL")
    project = _project(db_session, "重试非互斥")
    failed = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="candidate-9",
        job_type="PAGE_GENERATE",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
    )
    sibling = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="candidate-9",
        job_type="PAGE_GENERATE",
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


def test_reset_for_retry_rejects_style_analyze_when_sibling_active(
    db_session, monkeypatch
):
    """STYLE_ANALYZE is one-paid-analysis-per-style across the analyze and
    palette-draft routes (shared guard), so a retried FAILED analyze must 409
    while a sibling palette/analyze job is still ACTIVE — pre-fix the retry
    mutex was scoped to PAGE_INSPECT/SOURCE_PARSE and the revival dispatched a
    second paid multimodal call on the same style row."""

    project = _project(db_session, "风格重试互斥")
    failed = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id="style-7",
        job_type="STYLE_ANALYZE",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
    )
    sibling = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id="style-7",
        job_type="STYLE_ANALYZE",
        status=JobStatus.QUEUED,
    )
    db_session.add_all([failed, sibling])
    db_session.commit()
    _set_queue_mode(db_session, "LOCAL")
    submitted: list[str] = []
    monkeypatch.setattr(job_service, "_submit_local", lambda job_id: submitted.append(job_id))

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, failed)

    assert exc_info.value.status_code == 409
    db_session.expire_all()
    assert db_session.get(GenerationJob, failed.id).status == JobStatus.FAILED
    assert db_session.get(GenerationJob, sibling.id).status == JobStatus.QUEUED
    assert submitted == []


def test_reset_for_retry_style_analyze_allows_terminal_sibling(db_session, monkeypatch):
    """The mutex only guards against ACTIVE siblings: retrying after a
    terminal palette run stays possible."""

    project = _project(db_session, "风格重试终态兄弟")
    failed = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id="style-8",
        job_type="STYLE_ANALYZE",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
    )
    terminal = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id="style-8",
        job_type="STYLE_ANALYZE",
        status=JobStatus.COMPLETED,
    )
    db_session.add_all([failed, terminal])
    db_session.commit()
    _set_queue_mode(db_session, "LOCAL")
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    reset = job_service.reset_for_retry(db_session, failed)

    assert reset.id == failed.id
    db_session.expire_all()
    row = db_session.get(GenerationJob, failed.id)
    assert row.status in {JobStatus.WAITING, JobStatus.QUEUED}


def test_route_arbitrates_fence_split_duplicates(db_session):
    """A fence bump between the route's and the reconciler's version reads
    mints different-keyed PAGE_INSPECT jobs for one candidate; key equality
    cannot collapse those, and each side's check-then-act window can miss
    the other's uncommitted row. The shared post-insert arbitration is the
    backstop: the older ACTIVE job wins, the younger duplicate is cancelled
    and the caller adopts the older one."""

    from app.services.job_service import arbitrate_inspection_creation

    project_row = Project(name="栅栏分裂仲裁")
    db_session.add(project_row)
    db_session.flush()
    chapter = Chapter(project_id=project_row.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=3)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project_row.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project_row.id,
        kind="page_candidate",
        original_name="ready.png",
        storage_key="generated/ready.png",
        mime_type="image/png",
        byte_size=10,
        sha256="9" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution="DRAFT_1K",
        status="READY",
        asset_id=asset.id,
    )
    db_session.add(candidate)
    db_session.flush()

    # The reconciler's creation landed first, keyed on page version 3; a
    # fence then bumped the page, so the route computes a version-4 key.
    # created_at is pinned apart: both defaults can land on the same clock
    # tick, and the id tie-break on random UUIDs would flip the winner.
    reconciler_job = GenerationJob(
        project_id=project_row.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.QUEUED,
        request_parameters={"categories": ["SPEAKER"], "workflow_run_id": "run-1"},
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}:3",
        created_at=utcnow() - timedelta(seconds=5),
    )
    route_job = GenerationJob(
        project_id=project_row.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.WAITING,
        request_parameters={"categories": ["SPEAKER"]},
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}:4",
    )
    db_session.add_all([reconciler_job, route_job])
    db_session.commit()

    winner = arbitrate_inspection_creation(db_session, route_job)

    assert winner.id == reconciler_job.id
    db_session.expire_all()
    assert (
        db_session.get(GenerationJob, reconciler_job.id).status
        == JobStatus.QUEUED
    )
    assert db_session.get(GenerationJob, route_job.id).status == JobStatus.CANCELLED

    # The older job itself stays untouched when arbitrated (first-committer
    # symmetry): it is the oldest ACTIVE and returns itself.
    winner_again = arbitrate_inspection_creation(db_session, reconciler_job)
    assert winner_again.id == reconciler_job.id
    assert (
        db_session.get(GenerationJob, reconciler_job.id).status
        == JobStatus.QUEUED
    )

"""Recovery's terminal lease-expiry branch must release FINAL_CHECKING pages.

The worker-failure and cancel paths release a page parked in FINAL_CHECKING
when its PAGE_INSPECT job terminally fails (commit 48993a5); recovery's
terminal branch (expired lease with attempts exhausted) terminalizes the job
but used to skip the inspection-exit restore entirely, leaving the adopted
page stuck FINAL_CHECKING forever.
"""

from datetime import timedelta

from sqlalchemy import select

from app.config import Settings
from app.domain.states import JobStatus, PageStatus, Resolution
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
from app.worker_tasks import _mark_worker_failure


def _set_queue_mode(db_session, mode: str) -> None:
    db_session.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db_session.commit()


def _ready_candidate(db, *, candidate_status="READY"):
    project = Project(name="租约过期质检恢复")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=1)
    db.add(page)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="ready.png",
        storage_key="generated/ready.png",
        mime_type="image/png",
        byte_size=10,
        sha256="c" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add_all([batch, asset])
    db.flush()
    generate_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="pending",
        job_type="PAGE_GENERATE",
        status=JobStatus.COMPLETED,
    )
    db.add(generate_job)
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status=candidate_status,
        asset_id=asset.id,
        job_id=generate_job.id,
        is_selected=True,
    )
    db.add(candidate)
    db.flush()
    generate_job.target_id = candidate.id
    page.selected_candidate_id = candidate.id
    db.commit()
    return project, page, candidate, generate_job


def _adopt_candidate(page, candidate) -> None:
    """Mirror select_candidate's post-adoption write: page parked in
    FINAL_CHECKING until a fresh PAGE_INSPECT resolves it."""

    page.selected_candidate_ack_version = page.storyboard_version
    page.status = PageStatus.FINAL_CHECKING
    page.continuity_status = "NOT_CHECKED"
    page.version += 1


def _extra_candidate(db, page, *, ordinal=2, is_selected=False, status="READY"):
    batch = db.scalar(select(GenerationBatch).where(GenerationBatch.page_id == page.id))
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=ordinal,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status=status,
        is_selected=is_selected,
    )
    db.add(candidate)
    db.commit()
    return candidate


def _expired_inspect_job(db, project, candidate) -> GenerationJob:
    """A leased PAGE_INSPECT job whose lease expired with attempts exhausted:
    recovery's terminal FAILED branch owns this row."""
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.GENERATING,
        attempt_count=3,
        max_attempts=3,
        lease_owner="spawn-worker",
        lease_expires_at=utcnow() - timedelta(seconds=120),
    )
    db.add(job)
    db.commit()
    return job


def _run_recovery(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)
    return job_service.recover_pending_jobs(db_session)


def test_recovery_terminal_failure_restores_final_checking_page(db_session, monkeypatch):
    """T1: an expired PAGE_INSPECT lease with attempts exhausted must terminalize
    the job AND release the adopted page from FINAL_CHECKING (pre-fix the page
    stayed FINAL_CHECKING forever because recovery only resolved candidates via
    ``candidate.job_id``, which inspect jobs never set)."""
    project, page, candidate, generate_job = _ready_candidate(db_session)
    _adopt_candidate(page, candidate)
    db_session.commit()
    inspect_job = _expired_inspect_job(db_session, project, candidate)
    version_before = page.version

    recovered = _run_recovery(db_session, monkeypatch)

    assert recovered == 0
    db_session.expire_all()
    final = db_session.get(GenerationJob, inspect_job.id)
    assert final.status == JobStatus.FAILED
    assert final.error_code == "LEASE_EXPIRED"
    assert final.finished_at is not None
    assert final.lease_owner is None
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.NEEDS_REPAIR
    assert restored.continuity_status == "NEEDS_REVIEW"
    assert restored.version == version_before + 1
    # The inspected candidate holds adopted work and must stay untouched: the
    # ``candidate.job_id`` lookup matches nothing for inspect jobs, and the
    # target_id-named row must never be stamped FAILED by recovery.
    untouched = db_session.get(PageCandidate, candidate.id)
    assert untouched.status == "READY"
    assert untouched.is_selected is True
    assert untouched.job_id == generate_job.id


def test_recovery_terminal_failure_ignores_non_selected_candidate(db_session, monkeypatch):
    """T2: an expired inspect lease on a NON-selected candidate must not touch
    the page — the FINAL_CHECKING gate belongs to the selected candidate's run."""
    project, page, selected, _generate_job = _ready_candidate(db_session)
    other = _extra_candidate(db_session, page)
    _adopt_candidate(page, selected)
    db_session.commit()
    inspect_job = _expired_inspect_job(db_session, project, other)
    version_before = page.version

    recovered = _run_recovery(db_session, monkeypatch)

    assert recovered == 0
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.FAILED
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.FINAL_CHECKING
    assert restored.continuity_status == "NOT_CHECKED"
    assert restored.version == version_before
    assert db_session.get(PageCandidate, other.id).status == "READY"
    assert db_session.get(PageCandidate, selected.id).status == "READY"


def test_recovery_terminal_failure_does_not_downgrade_final_ready_page(
    db_session, monkeypatch
):
    """T2b: the page-status gate — recovery must not downgrade a page whose
    inspection gate already completed (FINAL_READY from a prior passing run)."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    page.selected_candidate_ack_version = page.storyboard_version
    page.status = PageStatus.FINAL_READY
    page.continuity_status = "PASSED"
    page.version += 1
    db_session.commit()
    inspect_job = _expired_inspect_job(db_session, project, candidate)
    version_before = page.version

    recovered = _run_recovery(db_session, monkeypatch)

    assert recovered == 0
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.FAILED
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.FINAL_READY
    assert restored.continuity_status == "PASSED"
    assert restored.version == version_before


def test_terminal_worker_failure_leaves_inspected_candidate_ready_by_default(db_session):
    """T3 (converted from the deleted mark_job_failed helper): the dead helper's
    pinned guarantee moves to the live worker-failure path — a terminal
    PAGE_INSPECT failure with the default candidate_status must mark the job
    FAILED while leaving the adopted candidate (and its generate-job link)
    untouched."""
    project, _page, candidate, generate_job = _ready_candidate(db_session)
    inspect_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
    )
    db_session.add(inspect_job)
    db_session.commit()
    owner = "owner-inspect-recovery"
    inspect_job.lease_owner = owner
    inspect_job.lease_expires_at = utcnow() + timedelta(minutes=5)
    inspect_job.attempt_count = max(inspect_job.attempt_count or 0, 1)
    db_session.commit()

    marked, _, is_final = _mark_worker_failure(
        db_session,
        inspect_job.id,
        owner,
        "WORKER_ERROR",
        "质检失败",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, inspect_job.id).status == JobStatus.FAILED
    untouched = db_session.get(PageCandidate, candidate.id)
    assert untouched.status == "READY"
    assert untouched.is_selected is True
    assert untouched.job_id == generate_job.id

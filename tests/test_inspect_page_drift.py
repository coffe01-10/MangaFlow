"""PAGE_INSPECT must not erase page drift that commits during the paid call.

W3-2: the handler snapshotted only ``storyboard_version`` before the provider
call and re-checked only that sbv after it.  Scene writes deliberately never
bump sbv, so a review flag committed by ``mark_pages_for_review`` (every
scene-asset mutation) while the call ran was silently overwritten by the
success write (``PASSED`` / ``FINAL_READY``) — the page became exportable
against changed scene inputs.  The page ``version`` baseline closes that gap;
the select-candidate end got the same overwrite-class fix in 0fb94f4.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.domain.states import JobStatus, PageStatus
from app.models import GenerationJob, MangaPage, PageCandidate, utcnow
from app.services import job_service
from app.services.ai_schemas import PageInspectionOutput
from app.services.worker_handlers import provider
from app.services.worker_handlers.execution import StaleStoryboardVersionError
from app.services.worker_handlers.inspection import _run_inspection
from app.worker_tasks import _mark_worker_failure
from test_inspect_and_parse_guards import _adopt_candidate, _ready_candidate

INSPECT_CATEGORIES = ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"]


def _leased_inspect_job(db, project, candidate) -> GenerationJob:
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.PREPARING,
        attempt_count=1,
        request_parameters={"categories": INSPECT_CATEGORIES},
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
        lease_owner="offline-owner",
        lease_expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(job)
    db.commit()
    db.info.update(job_id=job.id, job_lease_owner=job.lease_owner)
    return job


def _run_inspect(db, monkeypatch, job, *, during_call=None):
    """Offline PAGE_INSPECT dispatch: the provider seam runs ``during_call``
    inside the interference window (between handler start and the post-call
    stale guard), then returns a fully passing result."""

    output = PageInspectionOutput.model_validate(
        {
            "items": [
                {
                    "category": category,
                    "outcome": "PASS",
                    "details": {"expected": "offline", "observed": "offline"},
                }
                for category in INSPECT_CATEGORIES
            ]
        }
    )

    def fake_invoke(_db, _binding, _callback):
        if during_call:
            during_call()
        return output

    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    monkeypatch.setattr(provider, "_invoke_provider", fake_invoke)
    _run_inspection(db, job)


def test_inspect_success_does_not_erase_concurrent_page_review(db_session, monkeypatch):
    """A scene-asset review landing during the paid call (the mark_pages_for_review
    shape: continuity NEEDS_REVIEW + version bump, sbv untouched) must cancel the
    inspection via the stale guard instead of being overwritten by the success
    write; the worker rollback preserves the committed review."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    _adopt_candidate(page, candidate)
    db_session.commit()
    baseline_version = page.version
    job = _leased_inspect_job(db_session, project, candidate)

    def scene_review_lands_during_call():
        # Second session: exactly what mark_pages_for_review commits for a
        # scene-asset change — continuity NEEDS_REVIEW + version bump, with
        # storyboard_version deliberately left alone.
        reviewer = sessionmaker(
            bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
        )()
        try:
            row = reviewer.get(MangaPage, page.id)
            row.continuity_status = "NEEDS_REVIEW"
            row.version += 1
            reviewer.commit()
        finally:
            reviewer.close()

    with pytest.raises(StaleStoryboardVersionError) as excinfo:
        _run_inspect(
            db_session,
            monkeypatch,
            job,
            during_call=scene_review_lands_during_call,
        )
    assert "页面内容在检查期间已变化" in str(excinfo.value)
    # execute_job's StaleStoryboardVersionError path rolls back before failing
    # the job; the concurrent review commit must survive that rollback.
    db_session.rollback()
    db_session.expire_all()
    page_row = db_session.get(MangaPage, page.id)
    assert page_row.continuity_status == "NEEDS_REVIEW"
    assert page_row.version == baseline_version + 1
    assert page_row.storyboard_version == 1
    # The adopted candidate is not stamped (retryable=False semantics stay
    # identical to the sbv-stale case).
    kept = db_session.get(PageCandidate, candidate.id)
    assert kept.status == "READY"
    assert kept.is_selected is True

    # The job then fails honestly through the standard worker failure path and
    # the page is restored out of FINAL_CHECKING without losing the review.
    marked, _, is_final = _mark_worker_failure(
        db_session,
        job.id,
        "offline-owner",
        "STALE_STORYBOARD_VERSION",
        str(excinfo.value),
        candidate_status="STALE",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    failed = db_session.get(GenerationJob, job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_code == "STALE_STORYBOARD_VERSION"
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.NEEDS_REPAIR
    assert restored.continuity_status == "NEEDS_REVIEW"
    assert restored.version == baseline_version + 2

    # Re-inspect stays possible: creating a job with the failed key collapses
    # the FAILED row to closed:{id} and yields a fresh WAITING job.
    retried = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    assert retried.id != job.id
    assert retried.status == JobStatus.WAITING
    db_session.refresh(failed)
    assert failed.idempotency_key == f"closed:{failed.id}"


def test_inspect_success_without_drift_still_marks_page_passed(db_session, monkeypatch):
    """Preservation: with no interference the success path is unchanged —
    FINAL_READY / PASSED, candidate INSPECTED, exactly one version bump."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    _adopt_candidate(page, candidate)
    db_session.commit()
    baseline_version = page.version
    job = _leased_inspect_job(db_session, project, candidate)

    _run_inspect(db_session, monkeypatch, job)

    # execute_job commits the worker session after the handler returns; only a
    # committed read proves the success writes landed.
    db_session.commit()
    db_session.expire_all()
    page_row = db_session.get(MangaPage, page.id)
    assert page_row.status == PageStatus.FINAL_READY
    assert page_row.continuity_status == "PASSED"
    assert page_row.version == baseline_version + 1
    assert db_session.get(PageCandidate, candidate.id).status == "INSPECTED"

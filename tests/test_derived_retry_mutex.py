"""Retry mutex for derived (repair/upscale/region) jobs.

Derived jobs target a freshly created CHILD candidate, so the generic
RETRY_MUTEX check (has_active_job on target_id) is vacuous for them, and the
kept FAILED job of an original can be revived while an ACTIVE sibling with the
same original + intent is still queued — two paid repair calls for one intent.
The revival must consult the lineage-scoped derived guard instead.
"""

import pytest
from fastapi import HTTPException

from app.domain.states import JobStatus, Resolution
from app.models import (
    CandidateLineage,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
)
from app.services import job_service


def _seed_parent_with_children(db):
    project = Project(name="派生重试互斥")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db.add(page)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db.add(batch)
    db.flush()
    parent = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
    )
    child_a = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=2,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
    )
    child_b = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=3,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
    )
    db.add_all([parent, child_a, child_b])
    db.flush()
    db.add(
        CandidateLineage(
            child_candidate_id=child_a.id,
            parent_candidate_id=parent.id,
            lineage_kind="REPAIRED",
        )
    )
    db.add(
        CandidateLineage(
            child_candidate_id=child_b.id,
            parent_candidate_id=parent.id,
            lineage_kind="REPAIRED",
        )
    )
    db.commit()
    return project, parent, child_a, child_b


def test_reset_for_retry_refuses_repair_when_same_intent_sibling_active(db_session):
    project, parent, child_a, child_b = _seed_parent_with_children(db_session)

    failed = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_a.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    failed.status = JobStatus.FAILED
    db_session.commit()
    active = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_b.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    active.status = JobStatus.QUEUED
    db_session.commit()
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, failed)

    assert exc_info.value.status_code == 409
    db_session.expire_all()
    assert db_session.get(GenerationJob, failed.id).status == JobStatus.FAILED
    assert db_session.get(GenerationJob, active.id).status == JobStatus.QUEUED


def test_reset_for_retry_allows_repair_when_sibling_terminal(db_session):
    project, parent, child_a, child_b = _seed_parent_with_children(db_session)

    failed = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_a.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    failed.status = JobStatus.FAILED
    db_session.commit()
    terminal = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_b.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    terminal.status = JobStatus.COMPLETED
    db_session.commit()
    db_session.commit()

    reset = job_service.reset_for_retry(db_session, failed)
    assert reset.id == failed.id
    db_session.expire_all()
    row = db_session.get(GenerationJob, failed.id)
    assert row.status in {JobStatus.WAITING, JobStatus.QUEUED}


def test_derived_retry_arbitration_oldest_wins(db_session):
    """Two concurrent revivals of same-intent sibling repairs: the strictly
    older stays committed and dispatches; the younger is compensated back to
    FAILED with a 409 (mirrors the RETRY_MUTEX post-commit arbitration, which
    is vacuous for derived jobs because each targets its own child)."""
    from app.services.job_service import _verify_retry_revival_post_commit

    project, parent, child_a, child_b = _seed_parent_with_children(db_session)
    older = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_a.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    younger = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_b.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    # Simulate both concurrent revivals having committed (both WAITING).
    older.status = JobStatus.WAITING
    younger.status = JobStatus.WAITING
    db_session.commit()

    younger_snapshot = {
        "job": {
            "status": JobStatus.FAILED,
            "error_code": "UPSTREAM",
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
    with pytest.raises(HTTPException) as exc_info:
        _verify_retry_revival_post_commit(db_session, younger, younger_snapshot)
    assert exc_info.value.status_code == 409
    db_session.expire_all()
    assert db_session.get(GenerationJob, younger.id).status == JobStatus.FAILED

    # The older claimant survives arbitration and proceeds to dispatch.
    assert (
        _verify_retry_revival_post_commit(db_session, older, {}) is True
    )
    db_session.expire_all()
    assert db_session.get(GenerationJob, older.id).status == JobStatus.WAITING


def test_retry_guard_respects_intent_filters(db_session):
    """The intent filters (repair_type / target_resolution) at the retry call
    sites are load-bearing: a retried LOCAL_REPAIR must NOT be blocked by an
    ACTIVE sibling of a DIFFERENT intent (the creation route deliberately
    allows that coexistence), while a same-intent ACTIVE sibling blocks."""
    project, parent, child_a, child_b = _seed_parent_with_children(db_session)
    failed_local = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_a.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    failed_local.status = JobStatus.FAILED
    db_session.commit()

    # A same-parent sibling with a DIFFERENT intent must not block the retry.
    different_intent = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_b.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "PAGE",
        },
    )
    different_intent.status = JobStatus.QUEUED
    db_session.commit()

    reset = job_service.reset_for_retry(db_session, failed_local)
    assert reset.id == failed_local.id
    db_session.expire_all()
    row = db_session.get(GenerationJob, failed_local.id)
    assert row.status in {JobStatus.WAITING, JobStatus.QUEUED}

    # A same-intent ACTIVE sibling still blocks (the pin for the filter).
    younger = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_b.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    younger.status = JobStatus.QUEUED
    db_session.commit()

    second = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child_a.id,
        job_type="PAGE_REPAIR",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_type": "LOCAL_REPAIR",
        },
    )
    second.status = JobStatus.FAILED
    db_session.commit()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, second)
    assert exc_info.value.status_code == 409

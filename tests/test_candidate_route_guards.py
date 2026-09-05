"""Candidate route guards around deletion, selection and favorite reads."""

from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    utcnow,
)


def _seed_page_candidate(db_session, *, job_status=JobStatus.QUEUED, with_job=True):
    project = Project(name="候选路由守卫")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=1)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    job = None
    if with_job:
        job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id="pending",
            job_type="PAGE_GENERATE",
            status=job_status,
        )
        db_session.add(job)
        db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        job_id=job.id if job else None,
    )
    db_session.add(candidate)
    db_session.flush()
    if job is not None:
        job.target_id = candidate.id
    db_session.commit()
    return project, page, candidate, job


def test_delete_candidate_cancels_active_generation_job(client, db_session):
    """Deleting a candidate must cancel its still-active generation job.

    The worker resolves its generation target without a deleted_at filter and
    attaches the asset to the soft-deleted row after the paid provider call;
    without a route-side cancel the job resurrects the deleted candidate.
    """

    _project, _page, candidate, job = _seed_page_candidate(db_session)

    response = client.delete(f"/api/v1/candidates/{candidate.id}")

    assert response.status_code == 204, response.text
    db_session.refresh(candidate)
    db_session.refresh(job)
    assert candidate.deleted_at is not None
    assert job.status == JobStatus.CANCELLED


def test_delete_candidate_leaves_terminal_job_untouched(client, db_session):
    """A terminal job needs no cancel and the delete still succeeds."""

    _project, _page, candidate, job = _seed_page_candidate(
        db_session, job_status=JobStatus.COMPLETED
    )

    response = client.delete(f"/api/v1/candidates/{candidate.id}")

    assert response.status_code == 204, response.text
    db_session.refresh(candidate)
    db_session.refresh(job)
    assert candidate.deleted_at is not None
    assert job.status == JobStatus.COMPLETED


def test_select_candidate_rejects_soft_deleted_asset(client, db_session):
    """Selection must fail closed when the candidate's backing asset is gone."""

    project, page, candidate, _job = _seed_page_candidate(db_session, with_job=False)
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="deleted.png",
        storage_key="generated/deleted.png",
        mime_type="image/png",
        byte_size=10,
        sha256="b" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
        deleted_at=utcnow(),
    )
    db_session.add(asset)
    db_session.flush()
    candidate.asset_id = asset.id
    candidate.status = "READY"
    db_session.commit()

    response = client.post(
        f"/api/v1/pages/{page.id}/select-candidate",
        json={"candidate_id": candidate.id, "manual_text_confirmed": True},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "该候选的图片素材已删除，请重新生成"
    db_session.refresh(page)
    assert page.selected_candidate_id is None

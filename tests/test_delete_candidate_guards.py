"""Regression: deleting a shared or in-flight candidate cannot strand output.

``delete_candidate`` tombstoned the candidate's asset without checking the
active-job lease or sibling candidates that deduped onto the same row: a
running job's paid output was tombstoned under it, and sibling candidates
kept pointing at the deleted row (READY card, 404 content). Both are now
409s, and the worker finalize paths refuse to write onto a candidate that
was deleted while the paid call was in flight.
"""

from app.models import (
    Asset,
    AssetCandidate,
    GenerationBatch,
    GenerationJob,
    JobAssetReference,
    Project,
)


def _asset_candidate(db, project: Project, asset: Asset, ordinal: int, batch: GenerationBatch):
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=ordinal,
        model_alias="image.test",
        resolution="DRAFT_1K",
        variant="STYLE_TEST",
        status="READY",
        asset_id=asset.id,
    )
    db.add(candidate)
    db.commit()
    return candidate


def test_delete_candidate_refuses_while_job_uses_asset(client, db_session):
    project = Project(name="del-candidate-lease")
    db_session.add(project)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, target_type="STYLE", ordinal=1, generation_kind="STYLE_TEST"
    )
    asset = Asset(
        project_id=project.id,
        kind="style_test",
        original_name="a.png",
        storage_key="a.png",
        mime_type="image/png",
        byte_size=10,
        sha256="1" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = _asset_candidate(db_session, project, asset, 1, batch)
    job = GenerationJob(
        project_id=project.id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        status="GENERATING",
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(JobAssetReference(job_id=job.id, asset_id=asset.id))
    db_session.commit()

    response = client.delete(f"/api/v1/candidates/{candidate.id}")
    assert response.status_code == 409
    db_session.refresh(asset)
    assert asset.deleted_at is None


def test_delete_candidate_refuses_shared_live_sibling(client, db_session):
    project = Project(name="del-candidate-shared")
    db_session.add(project)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, target_type="STYLE", ordinal=1, generation_kind="STYLE_TEST"
    )
    asset = Asset(
        project_id=project.id,
        kind="style_test",
        original_name="shared.png",
        storage_key="shared.png",
        mime_type="image/png",
        byte_size=10,
        sha256="2" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    first = _asset_candidate(db_session, project, asset, 1, batch)
    _asset_candidate(db_session, project, asset, 2, batch)

    response = client.delete(f"/api/v1/candidates/{first.id}")
    assert response.status_code == 409
    db_session.refresh(asset)
    assert asset.deleted_at is None


def test_delete_candidate_alone_still_works(client, db_session):
    project = Project(name="del-candidate-solo")
    db_session.add(project)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, target_type="STYLE", ordinal=1, generation_kind="STYLE_TEST"
    )
    asset = Asset(
        project_id=project.id,
        kind="style_test",
        original_name="solo.png",
        storage_key="solo.png",
        mime_type="image/png",
        byte_size=10,
        sha256="3" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([batch, asset])
    db_session.flush()
    candidate = _asset_candidate(db_session, project, asset, 1, batch)

    response = client.delete(f"/api/v1/candidates/{candidate.id}")
    assert response.status_code == 204
    db_session.refresh(asset)
    assert asset.deleted_at is not None


def test_delete_loses_to_concurrent_select(client, db_session):
    """A select landing between the guards and the write turns the delete
    into a 409 instead of tombstoning the newly adopted candidate."""

    from app.models import Chapter, MangaPage, PageCandidate

    project = Project(name="del-vs-select")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
    )
    db_session.add_all([page, batch])
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.test",
        resolution="DRAFT_1K",
        status="READY",
    )
    db_session.add(candidate)
    db_session.commit()

    # Simulate the concurrent adoption winning after the route's guards read
    # the row: flip is_selected directly, leaving the in-memory copy stale.
    from sqlalchemy import update as sa_update

    db_session.execute(
        sa_update(PageCandidate)
        .where(PageCandidate.id == candidate.id)
        .values(is_selected=True)
    )
    db_session.commit()

    response = client.delete(f"/api/v1/candidates/{candidate.id}")
    assert response.status_code == 409

    db_session.expire_all()
    row = db_session.get(PageCandidate, candidate.id)
    assert row.deleted_at is None
    assert row.is_selected is True

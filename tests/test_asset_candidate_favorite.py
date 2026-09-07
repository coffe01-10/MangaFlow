"""Regression: PATCH /candidates/{id}/favorite must work for asset candidates.

``favorite_candidate`` used to serialize every candidate through
``candidate_read`` (``PageCandidateRead.model_validate``), which requires
``page_id``/``is_selected`` attributes that only page candidates have; the
route therefore 500'd on every asset candidate after already committing the
favorite flag.
"""

from sqlalchemy import select

from app.models import Asset, AssetCandidate, GenerationBatch, Project


def _asset_candidate(db, project_id: str) -> AssetCandidate:
    batch = GenerationBatch(
        project_id=project_id,
        target_type="STYLE",
        target_id=None,
        ordinal=1,
        generation_kind="STYLE_TEST",
    )
    asset = Asset(
        project_id=project_id,
        kind="style_test",
        original_name="style-test.png",
        storage_key="style-test.png",
        mime_type="image/png",
        byte_size=10,
        sha256="f" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db.add_all([batch, asset])
    db.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.test",
        resolution="DRAFT_1K",
        variant="STYLE_TEST",
        status="READY",
        asset_id=asset.id,
    )
    db.add(candidate)
    db.commit()
    return candidate


def test_favorite_asset_candidate_returns_serialized_candidate(client, db_session):
    project = Project(name="favorite-asset-candidate")
    db_session.add(project)
    db_session.commit()
    candidate = _asset_candidate(db_session, project.id)

    response = client.patch(
        f"/api/v1/candidates/{candidate.id}/favorite",
        json={"is_favorite": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == candidate.id
    assert body["is_favorite"] is True
    assert body["asset_id"] == candidate.asset_id
    assert body["is_selected"] is False

    persisted = db_session.scalar(
        select(AssetCandidate).where(AssetCandidate.id == candidate.id)
    )
    assert persisted is not None and persisted.is_favorite is True


def test_favorite_page_candidate_still_uses_page_shape(client, db_session):
    """The page-candidate path keeps its page-aware serialization."""

    from app.models import Chapter, MangaPage, PageCandidate

    project = Project(name="favorite-page-candidate")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=2,
    )
    db_session.add(batch)
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

    response = client.patch(
        f"/api/v1/candidates/{candidate.id}/favorite",
        json={"is_favorite": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == candidate.id
    assert body["page_id"] == page.id
    assert body["is_favorite"] is True

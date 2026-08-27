from datetime import timedelta

from sqlalchemy import select

from app.domain.states import PageStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
    utcnow,
)
from app.services.page_completion import build_page_production_readiness


def _ready_page(db_session, *, candidate_status="INSPECTED", continuity="PASSED"):
    project = Project(name="质检门禁")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        status=PageStatus.FINAL_READY,
        storyboard_version=1,
        selected_candidate_ack_version=1,
        continuity_status=continuity,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="page.png",
        storage_key="generated/page.png",
        mime_type="image/png",
        byte_size=12,
        sha256="b" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status=candidate_status,
        is_selected=True,
        asset_id=asset.id,
    )
    db_session.add(candidate)
    db_session.flush()
    page.selected_candidate_id = candidate.id
    db_session.commit()
    return page, candidate


def _pass_all(db_session, candidate_id: str) -> None:
    candidate = db_session.get(PageCandidate, candidate_id)
    page = db_session.get(MangaPage, candidate.page_id)
    for category in ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"):
        db_session.add(
            InspectionResult(
                candidate_id=candidate_id,
                storyboard_version=page.storyboard_version,
                category=category,
                outcome="PASS",
                severity="INFO",
            )
        )
    db_session.commit()


def test_empty_inspections_are_not_production_ready(db_session):
    page, candidate = _ready_page(db_session)
    readiness = build_page_production_readiness(db_session, page)
    assert readiness.ready is False
    assert any(
        item.code == "QUALITY_INSPECTION_REQUIRED" for item in readiness.blockers
    )


def test_single_category_pass_is_not_production_ready(db_session):
    page, candidate = _ready_page(db_session)
    db_session.add(
        InspectionResult(
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            category="CONTINUITY",
            outcome="PASS",
            severity="INFO",
        )
    )
    db_session.commit()
    db_session.expire_all()
    readiness = build_page_production_readiness(db_session, page)
    assert readiness.ready is False
    assert any(
        item.code == "QUALITY_INSPECTION_REQUIRED" for item in readiness.blockers
    )


def test_latest_failure_blocks_even_after_older_pass(db_session):
    page, candidate = _ready_page(db_session)
    _pass_all(db_session, candidate.id)
    db_session.add(
        InspectionResult(
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            category="CHARACTER",
            outcome="MISMATCH",
            severity="ERROR",
        )
    )
    db_session.commit()
    readiness = build_page_production_readiness(db_session, page)
    assert readiness.ready is False
    assert any(item.code == "QUALITY_REVIEW_REQUIRED" for item in readiness.blockers)


def test_five_passing_categories_are_production_ready(db_session):
    page, candidate = _ready_page(db_session)
    _pass_all(db_session, candidate.id)
    readiness = build_page_production_readiness(db_session, page)
    assert readiness.ready is True
    assert readiness.blockers == []


def test_equal_timestamp_failure_cannot_be_hidden_by_uuid_order(db_session):
    page, candidate = _ready_page(db_session)
    _pass_all(db_session, candidate.id)
    passing = db_session.scalar(
        select(InspectionResult).where(
            InspectionResult.candidate_id == candidate.id,
            InspectionResult.category == "CHARACTER",
        )
    )
    passing.id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    db_session.add(
        InspectionResult(
            id="00000000-0000-0000-0000-000000000000",
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            category="CHARACTER",
            outcome="MISMATCH",
            created_at=passing.created_at,
        )
    )
    db_session.commit()
    assert not build_page_production_readiness(db_session, page).ready


def test_newer_pass_can_replace_an_older_failure(db_session):
    page, candidate = _ready_page(db_session)
    db_session.add(
        InspectionResult(
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            category="CHARACTER",
            outcome="MISMATCH",
            created_at=utcnow() - timedelta(days=1),
        )
    )
    db_session.commit()
    _pass_all(db_session, candidate.id)
    assert build_page_production_readiness(db_session, page).ready

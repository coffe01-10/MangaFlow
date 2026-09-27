"""PUB-01A 章节手机预览只读端点的回归测试。"""

from app.domain.states import PageStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
)


def _make_chapter(db_session) -> tuple[Project, Chapter]:
    project = Project(name="手机预览")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    return project, chapter


def _make_page(db_session, chapter: Chapter, page_number: int) -> MangaPage:
    page = MangaPage(chapter_id=chapter.id, page_number=page_number)
    db_session.add(page)
    db_session.flush()
    return page


def _adopted_page(
    db_session,
    project: Project,
    chapter: Chapter,
    page_number: int,
    *,
    width: int = 1440,
    height: int = 2160,
) -> MangaPage:
    """造一页满足生产门禁的页：选中候选 + 版本确认 + 五类检查全过。"""
    page = _make_page(db_session, chapter, page_number)
    page.status = PageStatus.FINAL_READY
    page.storyboard_version = 1
    page.selected_candidate_ack_version = 1
    page.continuity_status = "PASSED"
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=page_number,
    )
    db_session.add(batch)
    db_session.flush()
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name=f"page-{page_number}.png",
        storage_key=f"generated/page-{page_number}.png",
        mime_type="image/png",
        byte_size=12,
        sha256=f"{page_number:x}" * 64,
        source="AI_GENERATED",
        status="GENERATED",
        width=width,
        height=height,
    )
    db_session.add(asset)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="INSPECTED",
        is_selected=True,
        asset_id=asset.id,
        based_on_storyboard_version=1,
    )
    db_session.add(candidate)
    db_session.flush()
    page.selected_candidate_id = candidate.id
    for category in ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"):
        db_session.add(
            InspectionResult(
                candidate_id=candidate.id,
                storyboard_version=1,
                category=category,
                outcome="PASS",
                severity="INFO",
            )
        )
    db_session.commit()
    return page


def test_mobile_preview_mixed_ready_and_blocked_pages(client, db_session):
    project, chapter = _make_chapter(db_session)
    ready = _adopted_page(db_session, project, chapter, 2)
    blocked = _make_page(db_session, chapter, 1)
    db_session.commit()

    response = client.get(f"/api/v1/chapters/{chapter.id}/mobile-preview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter_id"] == chapter.id
    assert payload["title"] == "第一章"
    # 返回按页码升序，而非插入顺序。
    assert [item["page_number"] for item in payload["pages"]] == [1, 2]

    blocked_entry = payload["pages"][0]
    assert blocked_entry["page_id"] == blocked.id
    assert blocked_entry["ready"] is False
    assert blocked_entry["image_url"] is None
    assert blocked_entry["full_image_url"] is None
    assert blocked_entry["blockers"]
    codes = {blocker["code"] for blocker in blocked_entry["blockers"]}
    assert "CANDIDATE_NOT_SELECTED" in codes

    ready_entry = payload["pages"][1]
    assert ready_entry["page_id"] == ready.id
    assert ready_entry["ready"] is True
    assert ready_entry["image_url"].endswith("/thumbnail/640")
    assert ready_entry["full_image_url"].endswith("/export.png")
    assert ready_entry["width"] == 1440
    assert ready_entry["height"] == 2160
    assert ready_entry["blockers"] == []


def test_mobile_preview_empty_chapter_returns_no_pages(client, db_session):
    _, chapter = _make_chapter(db_session)
    db_session.commit()
    response = client.get(f"/api/v1/chapters/{chapter.id}/mobile-preview")
    assert response.status_code == 200
    assert response.json()["pages"] == []


def test_mobile_preview_unknown_chapter_is_404(client):
    response = client.get("/api/v1/chapters/missing/mobile-preview")
    assert response.status_code == 404


def test_mobile_preview_rejects_cross_project_scope(client, db_session):
    project, chapter = _make_chapter(db_session)
    other = Project(name="其他项目")
    db_session.add(other)
    db_session.commit()
    response = client.get(
        f"/api/v1/chapters/{chapter.id}/mobile-preview?project_id={other.id}"
    )
    assert response.status_code == 404
    response = client.get(
        f"/api/v1/chapters/{chapter.id}/mobile-preview?project_id={project.id}"
    )
    assert response.status_code == 200

"""#136 route-side defense: downstream NEEDS_RECHECK writes bump page.version.

``select_candidate`` and ``retract_selected_candidate`` flag downstream pages
``continuity_status="NEEDS_RECHECK"`` when the adoption on an earlier page
changes. The worker_handlers/inspection.py page-version baseline must be able
to observe that drift, so the flag write now rides with a single-statement
``version = version + 1`` on the same UPDATE.
"""

import pytest

from app.domain.states import Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
)


def _orm(db, obj):
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@pytest.fixture
def adoption_world(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "重选联动项目"}).json()
    chapter = _orm(db_session, Chapter(project_id=project["id"], title="联动章节", ordinal=1))
    pages = {}
    for number in (1, 2, 3):
        pages[number] = _orm(db_session, MangaPage(chapter_id=chapter.id, page_number=number))
    asset = _orm(
        db_session,
        Asset(
            project_id=project["id"],
            kind="PAGE",
            original_name="page.png",
            storage_key="generated/recheck.png",
            mime_type="image/png",
            byte_size=8,
            sha256="d" * 64,
            source="AI_GENERATED",
        ),
    )
    batch = _orm(
        db_session,
        GenerationBatch(
            project_id=project["id"],
            ordinal=1,
            generation_kind="PAGE",
            page_id=pages[1].id,
            status="OPEN",
        ),
    )

    def make_candidate(page: MangaPage, ordinal: int) -> PageCandidate:
        return _orm(
            db_session,
            PageCandidate(
                batch_id=batch.id,
                page_id=page.id,
                ordinal=ordinal,
                model_alias="test-model",
                resolution=Resolution.DRAFT_1K,
                status="READY",
                asset_id=asset.id,
                based_on_storyboard_version=1,
            ),
        )

    previously_selected = make_candidate(pages[1], 1)
    previously_selected.is_selected = True
    pages[1].selected_candidate_id = previously_selected.id
    for number in (2, 3):
        downstream = make_candidate(pages[number], number + 1)
        downstream.is_selected = True
        pages[number].selected_candidate_id = downstream.id
    db_session.commit()
    return {
        "project": project,
        "pages": pages,
        "replacement": make_candidate(pages[1], 9),
    }


def _versions(db_session, pages: dict[int, MangaPage]) -> dict[int, int]:
    db_session.expire_all()
    return {
        number: db_session.get(MangaPage, page.id).version
        for number, page in pages.items()
    }


def _assert_downstream_flagged_and_bumped(
    db_session, pages: dict[int, MangaPage], before: dict[int, int]
) -> None:
    db_session.expire_all()
    for number in (2, 3):
        page = db_session.get(MangaPage, pages[number].id)
        assert page.continuity_status == "NEEDS_RECHECK"
        assert page.version == before[number] + 1


def test_select_candidate_bumps_downstream_page_versions(
    client, db_session, adoption_world
):
    pages = adoption_world["pages"]
    before = _versions(db_session, pages)
    response = client.post(
        f"/api/v1/pages/{pages[1].id}/select-candidate",
        json={
            "candidate_id": adoption_world["replacement"].id,
            "manual_text_confirmed": True,
            "accept_stale": True,
        },
    )
    assert response.status_code == 200, response.text
    _assert_downstream_flagged_and_bumped(db_session, pages, before)
    # The re-selected page itself gets its own explicit bump in the route.
    db_session.expire_all()
    assert db_session.get(MangaPage, pages[1].id).version == before[1] + 1


def test_retract_selected_candidate_bumps_downstream_page_versions(
    client, db_session, adoption_world
):
    pages = adoption_world["pages"]
    before = _versions(db_session, pages)
    response = client.delete(f"/api/v1/pages/{pages[1].id}/selected-candidate")
    assert response.status_code == 200, response.text
    _assert_downstream_flagged_and_bumped(db_session, pages, before)
    retracted = db_session.get(MangaPage, pages[1].id)
    assert retracted.selected_candidate_id is None

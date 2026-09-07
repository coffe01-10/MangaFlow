"""keep/retract selected-candidate routes must take the page lock.

W3-3: ``select_candidate`` and ``delete_asset`` take ``lock_entity(MangaPage)``
before their guard reads, but ``keep_selected_candidate`` and
``retract_selected_candidate`` wrote page state unlocked.  A selection landing
between the route's ``_page`` read (an identity-map ``db.get``) and its commit
was invisible to the guards: keep rewrote the adoption pointer onto its stale
snapshot, and retract stranded the freshly adopted candidate with
``is_selected=True`` while clearing ``selected_candidate_id``.

Race seam: the established stale-identity-map pattern — the test seeds and
reads through ``db_session``, a second session commits select_candidate's
adoption shape, then the route runs on the stale session; only a post-lock
re-read can observe the concurrent adoption.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.api.routes.workflow import generation
from app.domain.states import PageStatus, Resolution
from app.models import (
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    Project,
)
from app.schemas import KeepSelectedCandidateRequest


def _seed_adopted_page(db_session):
    """One page with two candidates; candidate 1 is the adopted selection."""
    project = Project(name="采用路由页锁")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=2)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    db_session.add(batch)
    db_session.flush()
    first = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="INSPECTED",
        based_on_storyboard_version=page.storyboard_version,
    )
    second = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=2,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        based_on_storyboard_version=page.storyboard_version,
    )
    db_session.add_all([first, second])
    db_session.flush()
    first.is_selected = True
    page.selected_candidate_id = first.id
    page.selected_candidate_ack_version = page.storyboard_version
    page.version += 1
    db_session.commit()
    return project, page, first, second


def _concurrent_select_lands(db_session, page, adopted) -> int:
    """Commit select_candidate's adoption shape from a second session: the
    pointer moves to ``adopted``, it becomes the only selected candidate, and
    the page is parked FINAL_CHECKING with a version bump.  Returns the page
    version after the adoption."""
    selector = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )()
    try:
        selector.execute(
            update(PageCandidate)
            .where(PageCandidate.page_id == page.id)
            .values(is_selected=False)
        )
        winner = selector.get(PageCandidate, adopted.id)
        winner.is_selected = True
        winner.version += 1
        page_row = selector.get(MangaPage, page.id)
        page_row.selected_candidate_id = adopted.id
        page_row.selected_candidate_ack_version = page_row.storyboard_version
        page_row.status = PageStatus.FINAL_CHECKING
        page_row.continuity_status = "NOT_CHECKED"
        page_row.version += 1
        selector.commit()
        return page_row.version
    finally:
        selector.close()


def test_keep_selected_candidate_does_not_clobber_concurrent_reselection(db_session):
    """Keep called for the previously adopted candidate while a concurrent
    select adopts candidate 2 must refuse on the post-lock state instead of
    rewriting the adoption pointer back onto its stale snapshot."""
    _project, page, first, second = _seed_adopted_page(db_session)
    drifted_version = _concurrent_select_lands(db_session, page, second)

    with pytest.raises(HTTPException) as excinfo:
        generation.keep_selected_candidate(
            page.id,
            KeepSelectedCandidateRequest(
                candidate_id=first.id,
                storyboard_version=page.storyboard_version,
                manual_text_confirmed=True,
            ),
            db_session,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "只能继续使用当前已采用的候选"

    db_session.expire_all()
    final_page = db_session.get(MangaPage, page.id)
    # The fresh adoption survives untouched: the pointer stays on the second
    # candidate and keep added no version bump of its own.
    assert final_page.selected_candidate_id == second.id
    assert final_page.version == drifted_version
    assert db_session.get(PageCandidate, second.id).is_selected is True
    assert db_session.get(PageCandidate, first.id).is_selected is False


def test_retract_selected_candidate_retracts_fresh_adoption(db_session):
    """Retract must operate on the post-lock adoption: the freshly adopted
    candidate is deselected instead of being stranded is_selected=True while
    the page pointer is cleared."""
    _project, page, first, second = _seed_adopted_page(db_session)
    _concurrent_select_lands(db_session, page, second)

    retracted = generation.retract_selected_candidate(page.id, db_session)

    assert retracted.selected_candidate_id is None
    db_session.expire_all()
    final_page = db_session.get(MangaPage, page.id)
    assert final_page.selected_candidate_id is None
    assert final_page.selected_candidate_ack_version is None
    assert final_page.status == PageStatus.REVIEW_REQUIRED
    # No stranded adoption: the concurrently selected candidate converges to
    # is_selected=False together with the cleared pointer.
    assert db_session.get(PageCandidate, second.id).is_selected is False
    assert db_session.get(PageCandidate, first.id).is_selected is False


def test_keep_and_retract_normal_flows_unchanged(db_session):
    """Preservation: without a race, keep and retract produce exactly the
    pre-lock behavior."""
    _project, page, first, _second = _seed_adopted_page(db_session)
    version_before = page.version
    candidate_version_before = first.version

    kept = generation.keep_selected_candidate(
        page.id,
        KeepSelectedCandidateRequest(
            candidate_id=first.id,
            storyboard_version=page.storyboard_version,
            manual_text_confirmed=True,
        ),
        db_session,
    )
    assert kept.selected_candidate_id == first.id
    assert kept.status == PageStatus.FINAL_CHECKING
    assert kept.continuity_status == "NOT_CHECKED"
    assert kept.selected_candidate_ack_version == page.storyboard_version
    assert kept.version == version_before + 1
    db_session.refresh(first)
    assert first.version == candidate_version_before + 1
    assert first.is_selected is True

    retracted = generation.retract_selected_candidate(page.id, db_session)
    assert retracted.selected_candidate_id is None
    assert retracted.selected_candidate_ack_version is None
    assert retracted.status == PageStatus.REVIEW_REQUIRED
    db_session.refresh(first)
    assert first.is_selected is False

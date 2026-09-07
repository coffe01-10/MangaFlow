"""plan_chapter_pages red-team regression tests (#144 / #163).

#144: re-planning must not hard-delete in-flight generation batches via the
FK cascade (the designed 409 has to hold under concurrency), a concurrent
double-plan must never surface an unhandled 500, and the chapter version
bump must happen inside the lock window.
#163: the paginator caps anchored beats per page at what panel_count can
hold, so a planned page never carries more beats than panels and the
beats→panels mapping drops nothing silently.

All tests use a file-backed WAL SQLite database with real cross-session
concurrency (the conftest in-memory StaticPool shares one connection and
cannot exercise two transactions). PostgreSQL interleavings remain NOT RUN
(known gap #12); the SQLite paths exercise the same retry/re-read logic.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.domain.states import Resolution
from app.models import (
    Beat,
    Chapter,
    GenerationBatch,
    MangaPage,
    PageCandidate,
    PageSourceSegment,
    Panel,
    Project,
    Scene,
    SourceRevision,
    SourceSegment,
)
from app.services.content_workflow import plan_chapter_pages

BEAT_TEXTS = [f"「{index}」他说。" for index in range(1, 8)]
SEVEN_BEAT_SEGMENT_TEXT = "".join(BEAT_TEXTS) + "窗外雨声渐大，灯影摇晃。"


@pytest.fixture
def file_db(tmp_path):
    """File-backed WAL SQLite factory shared by concurrent sessions."""
    from sqlalchemy import event

    db_path = tmp_path / "plan_concurrency.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()

    @event.listens_for(engine, "begin")
    def do_begin(conn):
        raw_conn = getattr(conn.connection, "dbapi_connection", None)
        if raw_conn and not getattr(raw_conn, "in_transaction", False):
            conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_script(factory, *, text: str = SEVEN_BEAT_SEGMENT_TEXT) -> str:
    """Seed a SCRIPT_READY chapter whose single segment carries 7 anchored beats."""
    with factory() as db:
        project = Project(name="分页并发测试")
        db.add(project)
        db.flush()
        chapter = Chapter(
            project_id=project.id,
            title="第一章",
            ordinal=1,
            status="SCRIPT_READY",
        )
        db.add(chapter)
        db.flush()
        revision = SourceRevision(
            chapter_id=chapter.id,
            revision=1,
            source_type="IMPORTED",
            original_text=text,
            sha256="seed-revision",
            character_count=len(text),
        )
        db.add(revision)
        db.flush()
        segment = SourceSegment(
            source_revision_id=revision.id,
            ordinal=1,
            text=text,
            start_offset=0,
            end_offset=len(text),
            sha256="seed-segment",
        )
        db.add(segment)
        db.flush()
        scene = Scene(
            chapter_id=chapter.id,
            ordinal=1,
            location="灵堂",
            source_range={"segment_ids": [segment.id]},
        )
        db.add(scene)
        db.flush()
        for index, beat_text in enumerate(BEAT_TEXTS, 1):
            db.add(
                Beat(
                    scene_id=scene.id,
                    ordinal=index,
                    action=f"动作{index}",
                    speaker_name="我",
                    dialogue=beat_text,
                    source_range={"segment_ids": [segment.id]},
                )
            )
        chapter.current_source_revision_id = revision.id
        db.commit()
        return chapter.id


def _open_batch(db, page: MangaPage, *, ordinal: int = 1) -> PageCandidate:
    """Insert an OPEN generation batch + candidate on the page (committed)."""
    batch = GenerationBatch(
        project_id=_project_id(db, page),
        chapter_id=page.chapter_id,
        page_id=page.id,
        ordinal=ordinal,
        generation_kind="PAGE",
        status="OPEN",
    )
    db.add(batch)
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
    )
    db.add(candidate)
    db.commit()
    return candidate


def _project_id(db, page: MangaPage) -> str:
    return db.scalar(
        select(Chapter.project_id).where(Chapter.id == page.chapter_id)
    )


# --- #163: the plan itself never overflows a page with beats -----------------


def test_plan_splits_pages_so_beats_never_exceed_panel_count(file_db):
    chapter_id = _seed_script(file_db)

    with file_db() as db:
        chapter = db.get(Chapter, chapter_id)
        scene_ids = list(
            db.scalars(select(Scene.id).where(Scene.chapter_id == chapter_id))
        )
        seeded_beat_ids = list(
            db.scalars(
                select(Beat.id)
                .where(Beat.scene_id.in_(scene_ids))
                .order_by(Beat.scene_id, Beat.ordinal)
            )
        )
        assert len(seeded_beat_ids) == 7
        pages = plan_chapter_pages(db, chapter)
        db.commit()

        # Every seeded beat is still mapped onto exactly one page…
        covered = [beat_id for page in pages for beat_id in page.beat_ids]
        assert covered == seeded_beat_ids
        for page in pages:
            # …and no page plans more beats than it has panels: the old
            # paginator put all 7 beats on one 5-panel page and silently
            # dropped beats 6-7 (dialogue + presence vanished).
            assert len(page.beat_ids) <= page.panel_count
            assert page.panel_count <= 5
            assert "orphan_beat_ids" not in page.source_coverage
            panel_count = db.scalar(
                select(func.count(Panel.id)).where(Panel.page_id == page.id)
            )
            assert panel_count == page.panel_count


def test_populate_page_storyboard_records_beat_overflow(db_session):
    """Defense in depth (#163): when beats still exceed panels (e.g. the
    nearest-beat fallback, or legacy pages), _populate_page_storyboard records
    the overflow on page.source_coverage instead of dropping it silently."""
    from app.services.content_workflow import _populate_page_storyboard

    project = Project(name="拍溢出记录")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1, source_range={"segment_ids": ["s1"]})
    db_session.add(scene)
    db_session.flush()
    beats = [
        Beat(
            scene_id=scene.id,
            ordinal=index,
            action=f"动作{index}",
            dialogue=f"「{index}」",
            source_range={"segment_ids": ["s1"]},
        )
        for index in range(1, 6)
    ]
    db_session.add_all(beats)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        panel_count=3,
        scene_ids=[scene.id],
        beat_ids=[beat.id for beat in beats],
        source_coverage={"ranges": [], "complete": True},
    )
    db_session.add(page)
    db_session.flush()

    _populate_page_storyboard(db_session, page, [], [], beats, [])
    db_session.flush()

    assert page.source_coverage["orphan_beat_ids"] == [beats[3].id, beats[4].id]

    # A rebuild that can hold every beat clears the marker again.
    from sqlalchemy import delete

    db_session.execute(delete(Panel).where(Panel.page_id == page.id))
    page.panel_count = 5
    _populate_page_storyboard(db_session, page, [], [], beats, [])
    db_session.flush()
    assert "orphan_beat_ids" not in page.source_coverage


# --- #144 instance a: in-flight batches survive a re-plan --------------------


def test_plan_rejects_and_keeps_committed_open_batch(file_db):
    chapter_id = _seed_script(file_db)

    with file_db() as db:
        chapter = db.get(Chapter, chapter_id)
        pages = plan_chapter_pages(db, chapter)
        candidate = _open_batch(db, pages[0])
        batch_id = candidate.batch_id

        with pytest.raises(HTTPException) as excinfo:
            plan_chapter_pages(db, chapter)

        assert excinfo.value.status_code == 409
        assert "抽卡批次" in excinfo.value.detail
        db.rollback()
        assert db.get(GenerationBatch, batch_id) is not None
        assert db.get(PageCandidate, candidate.id) is not None
        page_count = db.scalar(
            select(func.count(MangaPage.id)).where(MangaPage.chapter_id == chapter_id)
        )
        assert page_count == len(pages)


def test_plan_409_when_batch_commits_inside_the_lock_window(file_db):
    """Barrier race (#144 instance a): the batch creator holds the SQLite
    writer before commit while plan runs; plan must block, re-read the batch
    count after the creator commits and answer 409 instead of cascade-deleting
    the paid work."""
    chapter_id = _seed_script(file_db)
    with file_db() as db:
        chapter = db.get(Chapter, chapter_id)
        first_pages = plan_chapter_pages(db, chapter)
        page_id = first_pages[0].id
        project_id = _project_id(db, first_pages[0])

    created = Event()
    release = Event()

    def creator():
        with file_db() as db:
            db.get(MangaPage, page_id)  # open the session lazily
            batch = GenerationBatch(
                project_id=project_id,
                chapter_id=chapter_id,
                page_id=page_id,
                ordinal=1,
                generation_kind="PAGE",
                status="OPEN",
            )
            candidate = PageCandidate(
                batch_id=None,
                page_id=page_id,
                ordinal=1,
                model_alias="image.nano_banana_2",
                resolution=Resolution.DRAFT_1K,
                status="QUEUED",
            )
            db.add(batch)
            db.flush()
            candidate.batch_id = batch.id
            db.add(candidate)
            db.flush()  # writer lock held from here until commit
            created.set()
            assert release.wait(10)
            db.commit()

    def planner():
        with file_db() as db:
            chapter = db.get(Chapter, chapter_id)
            try:
                plan_chapter_pages(db, chapter)
            except HTTPException as error:
                outcome.update(status_code=error.status_code, detail=str(error.detail))
            else:
                outcome.update(status_code=200)
            finally:
                db.rollback()

    outcome: dict = {}
    creator_thread = threading.Thread(target=creator)
    creator_thread.start()
    assert created.wait(10)
    planner_thread = threading.Thread(target=planner)
    planner_thread.start()
    time.sleep(0.3)  # planner parks on the write lock inside ordinal_savepoint
    release.set()
    creator_thread.join(15)
    planner_thread.join(20)

    assert not creator_thread.is_alive()
    assert not planner_thread.is_alive()
    assert outcome.get("status_code") == 409
    assert "抽卡批次" in outcome.get("detail", "")

    with file_db() as db:
        batch_count = db.scalar(
            select(func.count(GenerationBatch.id)).where(
                GenerationBatch.page_id == page_id
            )
        )
        candidate_count = db.scalar(
            select(func.count(PageCandidate.id)).where(PageCandidate.page_id == page_id)
        )
        assert batch_count == 1
        assert candidate_count == 1
        page_count = db.scalar(
            select(func.count(MangaPage.id)).where(MangaPage.chapter_id == chapter_id)
        )
        assert page_count == len(first_pages)


# --- #144 instance b: concurrent double-plan no longer 500s ------------------


def test_concurrent_double_plan_never_500s_and_leaves_consistent_pages(file_db):
    chapter_id = _seed_script(file_db)
    barrier = Barrier(2)
    outcomes: list = []

    def planner():
        with file_db() as db:
            chapter = db.get(Chapter, chapter_id)
            barrier.wait(10)
            try:
                plan_chapter_pages(db, chapter)
                outcomes.append("ok")
            except HTTPException as error:
                # A clean conflict is acceptable; an unhandled 500 is the bug.
                outcomes.append(("conflict", error.status_code))
            except Exception as error:  # pragma: no cover - failure mode
                outcomes.append(("unhandled", repr(error)))
            finally:
                db.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(planner) for _ in range(2)]
        for future in futures:
            future.result(timeout=60)

    assert outcomes
    for outcome in outcomes:
        assert outcome == "ok" or (
            isinstance(outcome, tuple) and outcome[0] == "conflict" and outcome[1] == 409
        ), outcomes

    with file_db() as db:
        pages = list(
            db.scalars(
                select(MangaPage)
                .where(MangaPage.chapter_id == chapter_id)
                .order_by(MangaPage.page_number)
            )
        )
        page_numbers = [page.page_number for page in pages]
        assert page_numbers == sorted(set(page_numbers))
        assert len(pages) >= 1
        chapter = db.get(Chapter, chapter_id)
        assert chapter.status == "PAGES_PLANNED"
        successful_plans = sum(1 for outcome in outcomes if outcome == "ok")
        # The version bump lives inside the lock window: exactly one bump per
        # serialized plan, no lost increments (#144 instance c).
        assert chapter.version == 1 + successful_plans
        for page in pages:
            assert db.scalar(
                select(func.count(PageSourceSegment.id)).where(
                    PageSourceSegment.page_id == page.id
                )
            ) >= 1
            assert len(page.beat_ids) <= page.panel_count

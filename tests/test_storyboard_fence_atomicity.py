"""Regression: page storyboard/version fences must be atomic increments.

The ``storyboard_version`` and ``version`` fences gate candidate staleness
(``STALE_CANDIDATE_CONFIRMATION_REQUIRED``), inspection freshness (inspections
are keyed to the page's storyboard version) and director accepts. An ORM
``page.x += 1`` read-modify-write loses increments when two writers
interleave (both read N, both write N+1): one edit silently vanishes from
every staleness check. The SQL-expression form lands +1 per caller even
without the page lock; the storyboard routes additionally take the page row
lock to serialize check-then-act rechecks (PostgreSQL FOR UPDATE).

These tests interleave two sessions on a WAL SQLite file: session B holds a
stale identity-map copy when it writes, which is exactly the racing shape
that used to drop an increment.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.domain.states import PageStatus
from app.models import Chapter, MangaPage, Project
from app.services.editor import mark_pages_for_review, mark_storyboard_changed


@pytest.fixture
def file_sessions(tmp_path):
    """File-backed SQLite with WAL for multi-session interleaving."""

    db_path = tmp_path / "storyboard_fence.db"
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


def _seed(factory):
    with factory() as db:
        project = Project(name="围栏原子性测试项目")
        db.add(project)
        db.flush()
        chapter = Chapter(
            project_id=project.id,
            ordinal=1,
            title="第一章",
            status="PAGES_PLANNED",
        )
        db.add(chapter)
        db.flush()
        pages = []
        for number in (1, 2):
            page = MangaPage(
                chapter_id=chapter.id,
                page_number=number,
                storyboard_version=3,
                status=PageStatus.PLANNED,
                source_coverage={"complete": True},
                scene_ids=[],
                beat_ids=[],
            )
            db.add(page)
            pages.append(page)
        db.commit()
        return chapter.id, [page.id for page in pages]


def test_storyboard_fence_survives_interleaved_writers(file_sessions):
    chapter_id, page_ids = _seed(file_sessions)
    target = page_ids[0]

    db_a = file_sessions()
    db_b = file_sessions()
    page_a = db_a.get(MangaPage, target)
    page_b = db_b.get(MangaPage, target)
    baseline = page_a.storyboard_version
    # Close B's read transaction but keep its stale identity-map copy
    # (expire_on_commit=False): B now writes through a fresh transaction
    # while still holding the pre-A attribute values — the exact racing
    # shape where an ORM ``+= 1`` would recompute from the stale value.
    db_b.commit()

    mark_storyboard_changed(db_a, page_a)
    db_a.commit()
    # Session B still holds the pre-A snapshot: an ORM ``+= 1`` on this copy
    # would write baseline+1 again and erase A's increment.
    mark_storyboard_changed(db_b, page_b)
    db_b.commit()
    db_a.close()
    db_b.close()

    with file_sessions() as check:
        final = check.get(MangaPage, target)
        assert final.storyboard_version == baseline + 2
        assert final.selected_candidate_ack_version is None


def test_review_fence_survives_interleaved_writers(file_sessions):
    chapter_id, page_ids = _seed(file_sessions)
    later = page_ids[1]
    earlier_id = page_ids[0]

    db_a = file_sessions()
    db_b = file_sessions()
    page_a = db_a.get(MangaPage, later)
    earlier_a = db_a.get(MangaPage, earlier_id)
    # Pre-load B's copy and close its read transaction while keeping the
    # stale identity-map values (expire_on_commit=False): with the old ORM
    # loop, B's ``version += 1`` would recompute from the pre-A value and
    # erase A's increment.
    page_b = db_b.get(MangaPage, later)
    baseline = page_a.version
    earlier_baseline = earlier_a.version
    assert page_b.version == baseline, "B must hold the pre-A snapshot"
    db_a.commit()
    db_b.commit()

    mark_pages_for_review(db_a, chapter_id, from_page_number=2)
    db_a.commit()
    mark_pages_for_review(db_b, chapter_id, from_page_number=2)
    db_b.commit()
    db_a.close()
    db_b.close()

    with file_sessions() as check:
        final = check.get(MangaPage, later)
        assert final.version == baseline + 2
        assert str(getattr(final.continuity_status, "value", final.continuity_status)) == (
            "NEEDS_REVIEW"
        )
        # Earlier pages are outside the review window and stay untouched.
        earlier = check.get(MangaPage, earlier_id)
        assert earlier.version == earlier_baseline
        assert str(getattr(earlier.continuity_status, "value", earlier.continuity_status)) != (
            "NEEDS_REVIEW"
        )

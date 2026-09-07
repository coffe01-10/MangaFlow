import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.domain.states import PageStatus, Resolution
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    SourceRevision,
    SourceSegment,
    StyleProfile,
)
from app.schemas import AssetCandidateCreate, CandidateCreate
from app.services.content_workflow import import_source, revise_chapter_source
from app.services.job_service import create_job
from app.services.ordinal_allocator import (
    BatchOrdinalConflictError,
    CandidateOrdinalConflictError,
    ChapterOrdinalConflictError,
    SourceRevisionConflictError,
    create_asset_candidate,
    create_generation_batch,
    create_page_candidate,
    is_sqlite_lock_error,
)


@pytest.fixture
def file_sessions(tmp_path):
    """Provide a file-backed SQLite database for multi-session concurrency testing."""
    from sqlalchemy import event

    db_path = tmp_path / "ordinal_concurrency.db"
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


def _seed_test_hierarchy(factory):
    """Seed project, chapter, page, character, and outfit for candidate tests."""
    from app.config import get_settings
    from app.services.provider_presets import ensure_provider_presets

    with factory() as db:
        ensure_provider_presets(db, get_settings(), auto_commit=True)
        project = Project(name="序号并发测试项目")
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

        page = MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            storyboard_version=1,
            status=PageStatus.PLANNED,
            source_coverage={"complete": True},
            scene_ids=["scene-1"],
            beat_ids=["beat-1"],
        )
        db.add(page)
        db.flush()

        asset = Asset(
            project_id=project.id,
            kind="CHARACTER_REFERENCE",
            original_name="char.png",
            storage_key="test/char.png",
            mime_type="image/png",
            byte_size=1024,
            width=512,
            height=512,
            sha256="dummy-char-hash",
        )
        db.add(asset)
        db.flush()

        character = Character(
            project_id=project.id,
            primary_name="测试主角",
        )
        db.add(character)
        db.flush()

        reference = CharacterReference(
            character_id=character.id,
            asset_id=asset.id,
            is_canonical=True,
        )
        db.add(reference)

        outfit_asset = Asset(
            project_id=project.id,
            kind="OUTFIT_REFERENCE",
            original_name="outfit.png",
            storage_key="test/outfit.png",
            mime_type="image/png",
            byte_size=1024,
            width=512,
            height=512,
            sha256="dummy-outfit-hash",
        )
        db.add(outfit_asset)
        db.flush()

        outfit = Outfit(
            project_id=project.id,
            character_id=character.id,
            name="校服",
            reference_asset_ids=[outfit_asset.id],
        )
        db.add(outfit)
        db.flush()

        style = StyleProfile(
            project_id=project.id,
            name="默认日漫风格",
            color_mode="color",
            status="ACTIVE",
            profile={
                "palette_confirmed": True,
                "test_image_approved": True,
                "reference_asset_ids": [asset.id],
            },
        )
        db.add(style)
        db.flush()
        project.default_style_id = style.id

        panel = Panel(
            page_id=page.id,
            reading_order=1,
            characters=[character.id],
            outfits={character.id: outfit.id},
        )
        db.add(panel)
        db.commit()

        return {
            "project_id": project.id,
            "chapter_id": chapter.id,
            "page_id": page.id,
            "character_id": character.id,
            "character_asset_id": asset.id,
            "outfit_id": outfit.id,
            "outfit_asset_id": outfit_asset.id,
        }


def test_concurrent_generation_batch_allocation(file_sessions):
    """Verify that multiple threads concurrently creating batches under the same project get unique sequential ordinals."""
    factory = file_sessions
    with factory() as db:
        project = Project(name="批次并发项目")
        db.add(project)
        db.commit()
        project_id = project.id

    num_threads = 5
    barrier = Barrier(num_threads)

    def create_batch_worker(thread_idx):
        barrier.wait()
        with factory() as db:
            batch = create_generation_batch(
                db,
                project_id=project_id,
                generation_kind="PAGE",
            )
            db.commit()
            return batch.ordinal

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        ordinals = list(executor.map(create_batch_worker, range(num_threads)))

    assert len(ordinals) == num_threads
    assert sorted(ordinals) == list(range(1, num_threads + 1))

    with factory() as db:
        db_batches = list(
            db.scalars(
                select(GenerationBatch)
                .where(GenerationBatch.project_id == project_id)
                .order_by(GenerationBatch.ordinal)
            )
        )
        assert [b.ordinal for b in db_batches] == list(range(1, num_threads + 1))
        assert len(set(b.ordinal for b in db_batches)) == num_threads


def test_concurrent_page_candidate_allocation(file_sessions):
    """Verify that multiple threads concurrently creating candidates in the same batch get unique sequential ordinals."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    with factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        db.commit()
        batch_id = batch.id

    num_threads = 4
    barrier = Barrier(num_threads)

    def create_candidate_worker(thread_idx):
        barrier.wait()
        with factory() as db:
            payload = CandidateCreate(
                model_alias="image.nano_banana_2",
                resolution=Resolution.DRAFT_1K,
                storyboard_version=1,
                reference_selections={
                    seeded["character_id"]: {
                        "character_asset_id": seeded["character_asset_id"],
                        "outfit_id": seeded["outfit_id"],
                        "outfit_asset_id": seeded["outfit_asset_id"],
                    }
                },
            )
            candidate, job = create_page_candidate(
                db,
                batch_id=batch_id,
                payload=payload,
            )
            return candidate.ordinal, job.id

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        results = list(executor.map(create_candidate_worker, range(num_threads)))

    ordinals = sorted(r[0] for r in results)
    assert ordinals == list(range(1, num_threads + 1))

    with factory() as db:
        candidates = list(
            db.scalars(
                select(PageCandidate)
                .where(PageCandidate.batch_id == batch_id)
                .order_by(PageCandidate.ordinal)
            )
        )
        assert [c.ordinal for c in candidates] == list(range(1, num_threads + 1))
        job_ids = [c.job_id for c in candidates]
        assert len(set(job_ids)) == num_threads
        jobs = list(db.scalars(select(GenerationJob).where(GenerationJob.id.in_(job_ids))))
        assert len(jobs) == num_threads


def test_concurrent_asset_candidate_allocation(file_sessions):
    """Verify that multiple threads concurrently creating asset candidates in the same batch get unique sequential ordinals."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    with factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            generation_kind="CHARACTER",
            target_type="CHARACTER",
            target_id=seeded["character_id"],
        )
        db.commit()
        batch_id = batch.id

    num_threads = 4
    barrier = Barrier(num_threads)

    def create_asset_candidate_worker(thread_idx):
        barrier.wait()
        with factory() as db:
            payload = AssetCandidateCreate(
                model_alias="image.nano_banana_2",
                resolution=Resolution.DRAFT_1K,
                variant="FRONT",
                instruction="正面立绘",
            )
            candidate, job = create_asset_candidate(
                db,
                batch_id=batch_id,
                payload=payload,
            )
            return candidate.ordinal, job.id

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        results = list(executor.map(create_asset_candidate_worker, range(num_threads)))

    ordinals = sorted(r[0] for r in results)
    assert ordinals == list(range(1, num_threads + 1))

    with factory() as db:
        candidates = list(
            db.scalars(
                select(AssetCandidate)
                .where(AssetCandidate.batch_id == batch_id)
                .order_by(AssetCandidate.ordinal)
            )
        )
        assert [c.ordinal for c in candidates] == list(range(1, num_threads + 1))


def test_concurrent_source_revision_allocation(file_sessions):
    """Verify that multiple threads revising the same chapter concurrently allocate unique sequential revisions."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    num_threads = 4
    barrier = Barrier(num_threads)

    def revise_worker(thread_idx):
        barrier.wait()
        with factory() as db:
            rev = revise_chapter_source(
                db,
                chapter_id=seeded["chapter_id"],
                title=f"修订标题-{thread_idx}",
                text=f"这是修订内容第{thread_idx}份，用于测试并发修订分配。\n\n新的段落展开叙述。",
                source_type="TXT",
            )
            return rev.revision

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        revisions = list(executor.map(revise_worker, range(num_threads)))

    assert sorted(revisions) == list(range(1, num_threads + 1))

    with factory() as db:
        all_revs = list(
            db.scalars(
                select(SourceRevision)
                .where(SourceRevision.chapter_id == seeded["chapter_id"])
                .order_by(SourceRevision.revision)
            )
        )
        assert [r.revision for r in all_revs] == list(range(1, num_threads + 1))
        chapter = db.get(Chapter, seeded["chapter_id"])
        assert chapter.current_source_revision_id == all_revs[-1].id
        segments = list(
            db.scalars(
                select(SourceSegment).where(
                    SourceSegment.source_revision_id == chapter.current_source_revision_id
                )
            )
        )
        assert len(segments) > 0


def test_concurrent_chapter_import_allocation(file_sessions):
    """Verify that multiple threads importing sources concurrently into the same project allocate unique sequential chapter ordinals."""
    factory = file_sessions
    with factory() as db:
        project = Project(name="章节并发导入项目")
        db.add(project)
        db.commit()
        project_id = project.id

    num_threads = 4
    barrier = Barrier(num_threads)

    def import_worker(thread_idx):
        barrier.wait()
        with factory() as db:
            chapters = import_source(
                db,
                project_id=project_id,
                title=f"第{thread_idx}卷",
                text=f"正文内容第{thread_idx}段，讲述主角的故事。",
                source_type="TXT",
            )
            return [c.ordinal for c in chapters]

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        results = list(executor.map(import_worker, range(num_threads)))

    created_ordinals = [ord_val for sublist in results for ord_val in sublist]
    assert sorted(created_ordinals) == list(range(1, len(created_ordinals) + 1))

    with factory() as db:
        db_chapters = list(
            db.scalars(
                select(Chapter)
                .where(Chapter.project_id == project_id)
                .order_by(Chapter.ordinal)
            )
        )
        assert [c.ordinal for c in db_chapters] == list(range(1, len(created_ordinals) + 1))


def test_batch_ordinal_exhaustion_raises_conflict_error(file_sessions, monkeypatch):
    """Verify that if batch ordinal allocation retries are exhausted, BatchOrdinalConflictError is raised."""
    factory = file_sessions
    with factory() as db:
        project = Project(name="重试耗尽测试")
        db.add(project)
        db.commit()
        project_id = project.id

    def force_conflict(*_args, **_kwargs):
        raise OperationalError("statement", {}, sqlite3.OperationalError("database is locked"))

    with factory() as db:
        monkeypatch.setattr(
            "app.services.ordinal_allocator.lock_entity",
            force_conflict,
        )
        with pytest.raises(BatchOrdinalConflictError):
            create_generation_batch(db, project_id=project_id, max_attempts=2)


def test_candidate_ordinal_exhaustion_raises_conflict_error(file_sessions, monkeypatch):
    """Verify that if candidate ordinal allocation retries are exhausted, CandidateOrdinalConflictError is raised."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    with factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        db.commit()
        payload = CandidateCreate(
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            storyboard_version=1,
            reference_selections={},
        )

        def force_conflict(*_args, **_kwargs):
            raise OperationalError("statement", {}, sqlite3.OperationalError("database is locked"))

        monkeypatch.setattr("app.services.ordinal_allocator.lock_entity", force_conflict)

        with pytest.raises(CandidateOrdinalConflictError):
            create_page_candidate(
                db,
                batch_id=batch.id,
                payload=payload,
                max_attempts=2,
            )


def test_source_revision_exhaustion_raises_conflict_error(file_sessions, monkeypatch):
    """Verify that if source revision retries are exhausted, SourceRevisionConflictError is raised."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    def force_conflict(*_args, **_kwargs):
        raise OperationalError("statement", {}, sqlite3.OperationalError("database is locked"))

    with factory() as db:
        monkeypatch.setattr("app.services.content_workflow.lock_entity", force_conflict)

        with pytest.raises(SourceRevisionConflictError):
            revise_chapter_source(
                db,
                chapter_id=seeded["chapter_id"],
                title="重试耗尽",
                text="正文内容",
                source_type="TXT",
                max_attempts=2,
            )


def test_chapter_ordinal_exhaustion_raises_conflict_error(file_sessions, monkeypatch):
    """Verify that if chapter ordinal allocation retries are exhausted, ChapterOrdinalConflictError is raised."""
    factory = file_sessions
    with factory() as db:
        project = Project(name="章节重试耗尽")
        db.add(project)
        db.commit()
        project_id = project.id

    def force_conflict(*_args, **_kwargs):
        raise OperationalError("statement", {}, sqlite3.OperationalError("database is locked"))

    with factory() as db:
        monkeypatch.setattr("app.services.content_workflow.lock_entity", force_conflict)

        with pytest.raises(ChapterOrdinalConflictError):
            import_source(
                db,
                project_id=project_id,
                title="标题",
                text="正文",
                source_type="TXT",
                max_attempts=2,
            )


@pytest.fixture
def app_default_sqlite_sessions(tmp_path):
    """Provide a standard SQLite database using the project's exact app.database configuration."""
    from sqlalchemy import event

    db_path = tmp_path / "app_default.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_integrity(dbapi_connection, _connection_record) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_default_sqlite_batch_rollback_leaves_zero_orphan_batches(app_default_sqlite_sessions):
    """Verify that on default SQLite configuration, outer db.rollback() leaves zero orphan batches."""
    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)

    db = factory()
    try:
        _batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        raise RuntimeError("Simulated workflow downstream error")
    except RuntimeError:
        db.rollback()
    finally:
        db.close()

    with factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(
                    GenerationBatch.project_id == seeded["project_id"]
                )
            )
        )
        assert len(batches) == 0


def test_default_sqlite_create_job_failure_rolls_back_candidate_and_job(app_default_sqlite_sessions):
    """Verify that when create_job uses auto_commit=False and downstream fails, candidate and job are rolled back cleanly."""
    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)

    db = factory()
    try:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        candidate = PageCandidate(
            batch_id=batch.id,
            page_id=seeded["page_id"],
            ordinal=1,
            model_alias="image.fast",
            resolution=Resolution.STANDARD_2K,
            status="QUEUED",
            based_on_storyboard_version=1,
            prompt_snapshot={},
        )
        db.add(candidate)
        db.flush()
        job = create_job(
            db,
            project_id=seeded["project_id"],
            target_type="PAGE_CANDIDATE",
            target_id=candidate.id,
            job_type="PAGE_GENERATE",
            model_alias="image.fast",
            auto_commit=False,
        )
        candidate.job_id = job.id
        db.flush()
        raise RuntimeError("Simulated failure after create_job before final commit")
    except RuntimeError:
        db.rollback()
    finally:
        db.close()

    with factory() as verify_db:
        candidates = list(verify_db.scalars(select(PageCandidate)))
        jobs = list(verify_db.scalars(select(GenerationJob)))
        batches = list(verify_db.scalars(select(GenerationBatch)))
        assert len(candidates) == 0
        assert len(jobs) == 0
        assert len(batches) == 0


def test_create_page_candidate_retry_rejects_stale_storyboard_version(file_sessions, monkeypatch):
    """Verify that during retry backoff if another session bumps storyboard_version, candidate creation is rejected."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    with factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        db.commit()
        batch_id = batch.id

    from contextlib import contextmanager
    from app.services import ordinal_allocator as allocator

    original_savepoint = allocator.ordinal_savepoint
    attempts = 0

    @contextmanager
    def race_before_reserving_writer(db):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            with factory() as other:
                page = other.get(MangaPage, seeded["page_id"])
                page.storyboard_version = 2
                other.commit()
            raise OperationalError("reserve writer", {}, sqlite3.OperationalError("database is locked"))
        with original_savepoint(db):
            yield

    monkeypatch.setattr(allocator, "ordinal_savepoint", race_before_reserving_writer)

    with factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            create_page_candidate(
                db,
                batch_id=batch_id,
                payload=CandidateCreate(
                    model_alias="image.nano_banana_2",
                    resolution=Resolution.DRAFT_1K,
                    storyboard_version=1,
                    reference_selections={
                        seeded["character_id"]: {
                            "character_asset_id": seeded["character_asset_id"],
                            "outfit_id": seeded["outfit_id"],
                            "outfit_asset_id": seeded["outfit_asset_id"],
                        }
                    },
                ),
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail.get("code") == "STALE_STORYBOARD_VERSION"

    with factory() as verify_db:
        candidates = list(
            verify_db.scalars(select(PageCandidate).where(PageCandidate.batch_id == batch_id))
        )
        assert len(candidates) == 0


def test_workflow_approval_endpoint_maps_ordinal_conflict_to_409(monkeypatch):
    """Verify that BatchOrdinalConflictError from approve_node is mapped to HTTP 409 in the route."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.workflow_definitions import router as workflow_router
    from app.database import get_db

    app = FastAPI()
    app.include_router(workflow_router)

    def fake_db():
        yield None

    app.dependency_overrides[get_db] = fake_db

    def mock_approve_node(*_args, **_kwargs):
        raise BatchOrdinalConflictError("抽卡批次分配冲突，请稍后重试")

    monkeypatch.setattr(
        "app.api.routes.workflow_definitions.approve_node",
        mock_approve_node,
    )

    # The route's #143 project-scope pre-check loads the run before delegating;
    # this test isolates the exception mapping only, so stub the loader too.
    monkeypatch.setattr(
        "app.api.routes.workflow_definitions._run",
        lambda *_args, **_kwargs: None,
    )

    client = TestClient(app)
    response = client.post(
        "/workflow-runs/run-123/nodes/node-generate/approve",
        json={"image_model_alias": "image.fast", "resolution": "2K"},
    )
    assert response.status_code == 409
    assert "抽卡批次分配冲突" in response.json()["detail"]


def test_workflow_approval_failure_rolls_back_batch_atomically(file_sessions):
    """Verify that if subsequent work in an atomic unit of work fails, rolling back the session leaves zero orphan batches."""
    factory = file_sessions
    seeded = _seed_test_hierarchy(factory)

    db = factory()
    try:
        # Step 1: create batch (uncommitted, flushed in savepoint)
        _batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        # Step 2: simulated downstream failure (e.g., job creation or validation error)
        raise RuntimeError("Downstream workflow step failure")
    except RuntimeError:
        db.rollback()
    finally:
        db.close()

    with factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(
                    GenerationBatch.project_id == seeded["project_id"]
                )
            )
        )
        # Ensure no orphan batch was committed
        assert len(batches) == 0


def test_workflow_approve_node_downstream_failure_rolls_back_entire_unit(app_default_sqlite_sessions, monkeypatch):
    """Verify that if approve_node fails during or before final commit, the entire unit (batch, candidate, job, run status) rolls back cleanly on default SQLite."""
    from app.models import WorkflowDefinition, WorkflowNodeRun
    from app.services.workflow_engine import approve_node, create_workflow_run, default_graph

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)

    with factory() as db:
        wf = WorkflowDefinition(
            project_id=seeded["project_id"],
            name="单页审批工作流",
            draft_graph=default_graph(),
            is_active=True,
        )
        db.add(wf)
        db.commit()
        from app.services.workflow_engine import publish_workflow
        publish_workflow(db, wf)

        run = create_workflow_run(
            db,
            wf,
            scope_type="PAGE",
            scope_id=seeded["page_id"],
            start_node_ids=["generate"],
            stop_node_ids=["generate"],
        )
        run_id = run.id

        node_run = db.scalar(
            select(WorkflowNodeRun).where(
                WorkflowNodeRun.workflow_run_id == run.id,
                WorkflowNodeRun.status == "WAITING_APPROVAL",
            )
        )
        assert node_run is not None
        node_id = node_run.node_id

    with factory() as db:
        def fail_create_job(*_args, **_kwargs):
            raise RuntimeError("Simulated job creation failure before final commit")

        monkeypatch.setattr("app.services.workflow_engine.create_job", fail_create_job)

        try:
            approve_node(
                db,
                run_id,
                node_id,
                image_model_alias="image.nano_banana_2",
                resolution="1K",
            )
        except RuntimeError:
            db.rollback()

    with factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(GenerationBatch.project_id == seeded["project_id"])
            )
        )
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.page_id == seeded["page_id"])
            )
        )
        jobs = list(
            verify_db.scalars(
                select(GenerationJob).where(GenerationJob.project_id == seeded["project_id"])
            )
        )
        reloaded_node_run = verify_db.scalar(
            select(WorkflowNodeRun).where(
                WorkflowNodeRun.workflow_run_id == run_id,
                WorkflowNodeRun.node_id == node_id,
            )
        )
        assert len(batches) == 0
        assert len(candidates) == 0
        assert len(jobs) == 0
        assert reloaded_node_run.status == "WAITING_APPROVAL"


def test_concurrent_batch_allocation_serializes_before_read(
    app_default_sqlite_sessions, monkeypatch
):
    """Two simultaneous requests reserve SQLite's writer before reading max."""
    from contextlib import contextmanager
    from app.services import ordinal_allocator as allocator

    factory = app_default_sqlite_sessions
    with factory() as db:
        project = Project(name="Serialized batch allocation")
        db.add(project)
        db.commit()
        project_id = project.id
    barrier = Barrier(2)
    original_savepoint = allocator.ordinal_savepoint
    original_next = allocator._next_batch_ordinal
    reads = []

    @contextmanager
    def start_together(db):
        barrier.wait(timeout=5)
        with original_savepoint(db):
            yield

    def record_next(db, pid):
        ordinal = original_next(db, pid)
        reads.append(ordinal)
        return ordinal

    monkeypatch.setattr(allocator, "ordinal_savepoint", start_together)
    monkeypatch.setattr(allocator, "_next_batch_ordinal", record_next)

    def worker(_):
        with factory() as db:
            batch = create_generation_batch(db, project_id=project_id)
            db.commit()
            return batch.ordinal

    with ThreadPoolExecutor(max_workers=2) as executor:
        ordinals = list(executor.map(worker, range(2)))
    assert sorted(ordinals) == [1, 2]
    assert reads == [1, 2]


def test_candidate_and_concurrent_close_commit_in_serial_order(
    app_default_sqlite_sessions, monkeypatch
):
    """A close during validation cannot commit until candidate creation commits."""
    from app.services import ordinal_allocator as allocator

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)
    with factory() as db:
        batch = create_generation_batch(
            db, project_id=seeded["project_id"], chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
        )
        db.commit()
        batch_id = batch.id
    started = Event()
    finished = Event()
    original_resolve = allocator.resolve_model
    close_future = None

    def close_batch():
        with factory() as other:
            started.set()
            other.execute(
                update(GenerationBatch).where(GenerationBatch.id == batch_id)
                .values(status="CLOSED")
            )
            other.commit()
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        def resolve_while_closing(*args, **kwargs):
            nonlocal close_future
            resolved = original_resolve(*args, **kwargs)
            close_future = executor.submit(close_batch)
            assert started.wait(timeout=2)
            assert not finished.wait(timeout=0.05)
            return resolved

        monkeypatch.setattr(allocator, "resolve_model", resolve_while_closing)
        with factory() as db:
            candidate, job = create_page_candidate(
                db, batch_id=batch_id,
                payload=CandidateCreate(
                    model_alias="image.nano_banana_2", resolution=Resolution.DRAFT_1K,
                    storyboard_version=1,
                    reference_selections={seeded["character_id"]: {
                        "character_asset_id": seeded["character_asset_id"],
                        "outfit_id": seeded["outfit_id"],
                        "outfit_asset_id": seeded["outfit_asset_id"],
                    }},
                ),
            )
            candidate_id, job_id = candidate.id, job.id
        assert close_future is not None
        close_future.result(timeout=5)
    with factory() as db:
        assert db.get(GenerationBatch, batch_id).status == "CLOSED"
        assert db.get(PageCandidate, candidate_id).job_id == job_id


def test_character_sheet_job_failure_rolls_back_batch_and_candidate_completely(app_default_sqlite_sessions, monkeypatch):
    """Verify that in generate_complete_character_sheet, if create_job fails before commit, rollback leaves zero extra batches or candidates."""
    from app.api.routes.asset_generation import generate_complete_character_sheet
    from app.schemas import CharacterSheetCreate

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)

    def fail_create_job(*_args, **_kwargs):
        raise RuntimeError("Simulated create_job failure in sheet generation")

    monkeypatch.setattr("app.services.ordinal_allocator.create_job", fail_create_job)

    with factory() as db:
        with pytest.raises(RuntimeError):
            try:
                generate_complete_character_sheet(
                    character_id=seeded["character_id"],
                    payload=CharacterSheetCreate(
                        model_alias="image.nano_banana_2",
                        resolution=Resolution.DRAFT_1K,
                        generation_mode="CONCEPT",
                        appearance_description="测试外观",
                        outfit_name="日常制服",
                        outfit_description="日常制服描述",
                    ),
                    db=db,
                )
            except Exception:
                db.rollback()
                raise

    with factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(
                    GenerationBatch.project_id == seeded["project_id"],
                    GenerationBatch.target_type == "CHARACTER",
                )
            )
        )
        candidates = list(verify_db.scalars(select(AssetCandidate)))
        jobs = list(verify_db.scalars(select(GenerationJob)))
        assert len(batches) == 0
        assert len(candidates) == 0
        assert len(jobs) == 0


def test_approve_node_final_commit_failure_rolls_back_and_allows_subsequent_retry(app_default_sqlite_sessions):
    """Verify that if approve_node's final db.commit() fails after job creation, rollback leaves zero orphan records, run status is preserved, and subsequent retry succeeds."""
    from app.models import WorkflowDefinition, WorkflowNodeRun
    from app.services.workflow_engine import approve_node, create_workflow_run, default_graph, publish_workflow

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)

    with factory() as db:
        wf = WorkflowDefinition(
            project_id=seeded["project_id"],
            name="单页审批工作流",
            draft_graph=default_graph(),
            is_active=True,
        )
        db.add(wf)
        db.commit()
        publish_workflow(db, wf)

        run = create_workflow_run(
            db,
            wf,
            scope_type="PAGE",
            scope_id=seeded["page_id"],
            start_node_ids=["generate"],
            stop_node_ids=["generate"],
        )
        run_id = run.id

        node_run = db.scalar(
            select(WorkflowNodeRun).where(
                WorkflowNodeRun.workflow_run_id == run.id,
                WorkflowNodeRun.status == "WAITING_APPROVAL",
            )
        )
        assert node_run is not None
        node_id = node_run.node_id

    # Attempt 1: Injected final commit failure
    with factory() as db:
        def failing_commit():
            raise RuntimeError("Simulated final database commit crash")

        db.commit = failing_commit

        try:
            approve_node(
                db,
                run_id,
                node_id,
                image_model_alias="image.nano_banana_2",
                resolution="1K",
            )
        except RuntimeError:
            db.rollback()

    with factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(GenerationBatch.project_id == seeded["project_id"])
            )
        )
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.page_id == seeded["page_id"])
            )
        )
        jobs = list(
            verify_db.scalars(
                select(GenerationJob).where(GenerationJob.project_id == seeded["project_id"])
            )
        )
        reloaded_node_run = verify_db.scalar(
            select(WorkflowNodeRun).where(
                WorkflowNodeRun.workflow_run_id == run_id,
                WorkflowNodeRun.node_id == node_id,
            )
        )
        assert len(batches) == 0
        assert len(candidates) == 0
        assert len(jobs) == 0
        assert reloaded_node_run.status == "WAITING_APPROVAL"

    # Attempt 2: Successful retry
    with factory() as db:
        run_result = approve_node(
            db,
            run_id,
            node_id,
            image_model_alias="image.nano_banana_2",
            resolution="1K",
        )
        assert run_result.status == "RUNNING"

    with factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(GenerationBatch.project_id == seeded["project_id"])
            )
        )
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.page_id == seeded["page_id"])
            )
        )
        jobs = list(
            verify_db.scalars(
                select(GenerationJob).where(GenerationJob.project_id == seeded["project_id"])
            )
        )
        reloaded_node_run = verify_db.scalar(
            select(WorkflowNodeRun).where(
                WorkflowNodeRun.workflow_run_id == run_id,
                WorkflowNodeRun.node_id == node_id,
            )
        )
        assert len(batches) == 1
        assert len(candidates) == 1
        assert len(jobs) == 1
        assert reloaded_node_run.status == "RUNNING"


def test_is_sqlite_lock_error_identification():
    """Verify is_sqlite_lock_error correctly identifies busy and locked sqlite errors and excludes others."""
    busy_op = OperationalError(
        "statement", {}, sqlite3.OperationalError("database is locked")
    )
    assert is_sqlite_lock_error(busy_op) is True

    busy_msg = OperationalError("statement", {}, Exception("sqlite busy"))
    assert is_sqlite_lock_error(busy_msg) is False

    syntax_op = OperationalError(
        "statement", {}, sqlite3.OperationalError("no such table: fake_table")
    )
    assert is_sqlite_lock_error(syntax_op) is False

    assert is_sqlite_lock_error(ValueError("unrelated error")) is False


def test_batch_unique_retry_preserves_pending_caller_data(
    app_default_sqlite_sessions, monkeypatch
):
    from app.services import ordinal_allocator as allocator

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)
    with factory() as db:
        create_generation_batch(db, project_id=seeded["project_id"])
        db.commit()
    original_next = allocator._next_batch_ordinal
    attempts = 0

    def collide_once(db, project_id):
        nonlocal attempts
        attempts += 1
        return 1 if attempts == 1 else original_next(db, project_id)

    monkeypatch.setattr(allocator, "_next_batch_ordinal", collide_once)
    with factory() as db:
        pending = Character(project_id=seeded["project_id"], primary_name="Preserved caller data")
        db.add(pending)
        batch = create_generation_batch(db, project_id=seeded["project_id"])
        pending_id = pending.id
        assert batch.ordinal == 2
        db.commit()
    with factory() as db:
        assert db.get(Character, pending_id).primary_name == "Preserved caller data"
    assert attempts == 2


def test_batch_exhaustion_does_not_rollback_callers_unit(
    app_default_sqlite_sessions, monkeypatch
):
    from app.services import ordinal_allocator as allocator

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)
    with factory() as db:
        create_generation_batch(db, project_id=seeded["project_id"])
        db.commit()
    monkeypatch.setattr(allocator, "_next_batch_ordinal", lambda *_: 1)
    with factory() as db:
        pending = Character(project_id=seeded["project_id"], primary_name="Caller owns rollback")
        db.add(pending)
        with pytest.raises(BatchOrdinalConflictError):
            create_generation_batch(db, project_id=seeded["project_id"], max_attempts=2)
        pending_id = pending.id
        assert db.is_active
        assert db.get(Character, pending_id) is pending
        db.rollback()
    with factory() as db:
        assert db.get(Character, pending_id) is None


def test_character_sheet_retry_preserves_new_batch(app_default_sqlite_sessions, monkeypatch):
    from app.api.routes import asset_generation
    from app.schemas import CharacterSheetCreate
    from app.services import ordinal_allocator as allocator

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)
    original_create_job = allocator.create_job
    attempts = 0

    def fail_after_first_job(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        job = original_create_job(*args, **kwargs)
        if attempts == 1:
            raise OperationalError("injected job write", {}, sqlite3.OperationalError("database is locked"))
        return job

    monkeypatch.setattr(allocator, "create_job", fail_after_first_job)
    monkeypatch.setattr(asset_generation, "enqueue_job", lambda db, job: job)
    with factory() as db:
        result = asset_generation.generate_complete_character_sheet(
            seeded["character_id"],
            CharacterSheetCreate(model_alias="image.nano_banana_2", resolution=Resolution.DRAFT_1K),
            db,
        )
        job_id = result.job_id
    with factory() as db:
        assert len(list(db.scalars(select(GenerationBatch)))) == 1
        candidates = list(db.scalars(select(AssetCandidate)))
        assert len(candidates) == 1
        assert candidates[0].job_id == job_id
        assert len(list(db.scalars(select(GenerationJob)))) == 1
    assert attempts == 2


@pytest.mark.parametrize("operation", ["import", "revision"])
def test_source_final_commit_failure_rolls_back_content(
    app_default_sqlite_sessions, monkeypatch, operation
):
    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)
    with factory() as db:
        def fail_commit():
            raise RuntimeError("Injected final source commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="final source commit"):
            if operation == "import":
                import_source(
                    db, project_id=seeded["project_id"], title="New chapter",
                    text="New source content", source_type="TXT",
                )
            else:
                revise_chapter_source(
                    db, chapter_id=seeded["chapter_id"], title="Replacement title",
                    text="Replacement source", source_type="TXT",
                )
        db.rollback()
    with factory() as db:
        assert len(list(db.scalars(select(Chapter)))) == 1
        assert db.get(MangaPage, seeded["page_id"]) is not None
        assert list(db.scalars(select(SourceRevision))) == []
        assert list(db.scalars(select(SourceSegment))) == []


def test_batch_final_commit_lock_failure_is_controlled_and_rolled_back(
    app_default_sqlite_sessions, monkeypatch
):
    from app.services.ordinal_allocator import commit_ordinal_transaction

    factory = app_default_sqlite_sessions
    seeded = _seed_test_hierarchy(factory)
    with factory() as db:
        create_generation_batch(db, project_id=seeded["project_id"])
        def fail_commit():
            raise OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(BatchOrdinalConflictError):
            commit_ordinal_transaction(db, BatchOrdinalConflictError)
        assert db.is_active
    with factory() as db:
        assert list(db.scalars(select(GenerationBatch))) == []

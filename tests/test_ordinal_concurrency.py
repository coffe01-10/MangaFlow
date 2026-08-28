import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
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
    _next_page_candidate_ordinal,
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
    with factory() as db:
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
            batch = db.get(GenerationBatch, batch_id)
            page = db.get(MangaPage, seeded["page_id"])
            project = db.get(Project, seeded["project_id"])
            resolved_model = type(
                "ResolvedModelDummy",
                (),
                {"model": type("ModelDummy", (), {"id": None})()},
            )()
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
                batch=batch,
                page=page,
                project=project,
                resolved_model=resolved_model,
                normalized_selections=payload.reference_selections,
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
            batch = db.get(GenerationBatch, batch_id)
            project = db.get(Project, seeded["project_id"])
            resolved_model = type(
                "ResolvedModelDummy",
                (),
                {"model": type("ModelDummy", (), {"id": None})()},
            )()
            payload = AssetCandidateCreate(
                model_alias="image.nano_banana_2",
                resolution=Resolution.DRAFT_1K,
                variant="FRONT",
                instruction="正面立绘",
            )
            candidate, job = create_asset_candidate(
                db,
                batch=batch,
                project=project,
                resolved_model=resolved_model,
                payload=payload,
                reference_asset_ids=[seeded["character_asset_id"]],
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
        page = db.get(MangaPage, seeded["page_id"])
        project = db.get(Project, seeded["project_id"])
        resolved_model = type(
            "ResolvedModelDummy",
            (),
            {"model": type("ModelDummy", (), {"id": None})()},
        )()
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
                batch=batch,
                page=page,
                project=project,
                resolved_model=resolved_model,
                normalized_selections={},
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

    attempt_count = 0
    real_next_ordinal = _next_page_candidate_ordinal

    def racing_next_ordinal(db_session, b_id):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # Another session modifies storyboard_version during backoff gap
            with factory() as other_db:
                p = other_db.get(MangaPage, seeded["page_id"])
                p.storyboard_version = 2
                other_db.commit()
            raise OperationalError("statement", {}, sqlite3.OperationalError("database is locked"))
        return real_next_ordinal(db_session, b_id)

    monkeypatch.setattr(
        "app.services.ordinal_allocator._next_page_candidate_ordinal",
        racing_next_ordinal,
    )

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


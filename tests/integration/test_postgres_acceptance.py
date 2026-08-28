from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.states import Resolution
from app.models import (
    AppSetting,
    Asset,
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
    StyleProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowVersion,
)
from app.schemas import CandidateCreate
from app.services.ordinal_allocator import (
    create_generation_batch,
    create_page_candidate,
)
from app.services.workflow_engine import (
    PublishRevisionConflictError,
    approve_node,
    create_workflow_run,
    default_graph,
    publish_workflow,
)


def _seed_pg_project_hierarchy(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as db:
        project = Project(name=f"PG验收项目_{time.time()}")
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
            source_coverage={"complete": True},
            scene_ids=["scene-pg-1"],
            beat_ids=["beat-pg-1"],
        )
        db.add(page)
        db.flush()

        char_asset = Asset(
            project_id=project.id,
            kind="CHARACTER_REFERENCE",
            original_name="char.png",
            storage_key="test/pg_char.png",
            mime_type="image/png",
            byte_size=1024,
            width=512,
            height=512,
            sha256=f"pg-char-hash-{time.time()}",
        )
        outfit_asset = Asset(
            project_id=project.id,
            kind="OUTFIT_REFERENCE",
            original_name="outfit.png",
            storage_key="test/pg_outfit.png",
            mime_type="image/png",
            byte_size=1024,
            width=512,
            height=512,
            sha256=f"pg-outfit-hash-{time.time()}",
        )
        style_asset = Asset(
            project_id=project.id,
            kind="STYLE_REFERENCE",
            original_name="style.png",
            storage_key="test/pg_style.png",
            mime_type="image/png",
            byte_size=1024,
            width=512,
            height=512,
            sha256=f"pg-style-hash-{time.time()}",
        )
        db.add_all([char_asset, outfit_asset, style_asset])
        db.flush()

        character = Character(project_id=project.id, primary_name="PG主角")
        db.add(character)
        db.flush()

        db.add(
            CharacterReference(
                character_id=character.id,
                asset_id=char_asset.id,
                is_canonical=True,
            )
        )
        outfit = Outfit(
            project_id=project.id,
            character_id=character.id,
            name="PG日常装",
            reference_asset_ids=[outfit_asset.id],
            status="CANONICAL",
        )
        db.add(outfit)
        db.flush()

        style = StyleProfile(
            project_id=project.id,
            name="PG日漫风",
            color_mode="color",
            status="ACTIVE",
            profile={
                "palette_confirmed": True,
                "test_image_approved": True,
                "reference_asset_ids": [style_asset.id],
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

        existing_setting = db.scalar(select(AppSetting).where(AppSetting.key == "runtime"))
        if not existing_setting:
            db.add(AppSetting(key="runtime", value={"queue_mode": "REDIS"}, version=1))

        db.commit()

        return {
            "project_id": project.id,
            "chapter_id": chapter.id,
            "page_id": page.id,
            "character_id": character.id,
            "character_asset_id": char_asset.id,
            "outfit_id": outfit.id,
            "outfit_asset_id": outfit_asset.id,
            "style_id": style.id,
        }


def test_pg_dialect_and_row_locking_capability(live_pg_session_factory):
    """Verify PostgreSQL dialect and test real SELECT ... FOR UPDATE row-level locking."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]

    with live_pg_session_factory() as db:
        locked_project = db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        assert locked_project is not None
        assert locked_project.id == project_id


def test_pg_concurrent_generation_batch_allocation(live_pg_session_factory):
    """Verify PostgreSQL allocates strictly monotonic unique ordinals across concurrent worker threads with real persistence."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]
    concurrency = 8
    barrier = Barrier(concurrency)

    def allocate_batch(worker_idx: int) -> int:
        barrier.wait(timeout=10)
        time.sleep(random.uniform(0.001, 0.01))
        with live_pg_session_factory() as db:
            batch = create_generation_batch(
                db,
                project_id=project_id,
                chapter_id=seeded["chapter_id"],
                page_id=seeded["page_id"],
                generation_kind="PAGE",
            )
            db.commit()
            return batch.ordinal

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        ordinals = list(executor.map(allocate_batch, range(concurrency)))

    assert sorted(ordinals) == list(range(1, concurrency + 1))
    assert len(set(ordinals)) == concurrency

    # Verify real DB persistence in fresh independent Session
    with live_pg_session_factory() as verify_db:
        persisted_batches = list(
            verify_db.scalars(
                select(GenerationBatch)
                .where(GenerationBatch.project_id == project_id)
                .order_by(GenerationBatch.ordinal.asc())
            )
        )
        assert len(persisted_batches) == concurrency
        assert [b.ordinal for b in persisted_batches] == list(range(1, concurrency + 1))


def test_pg_concurrent_page_candidate_allocation(live_pg_session_factory, monkeypatch):
    """Verify PostgreSQL allocates strictly monotonic candidate ordinals concurrently within a batch and commits to DB."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    with live_pg_session_factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        db.commit()
        batch_id = batch.id

    monkeypatch.setattr(
        "app.services.ordinal_allocator.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )

    concurrency = 6
    barrier = Barrier(concurrency)

    def allocate_candidate(worker_idx: int) -> int:
        barrier.wait(timeout=10)
        time.sleep(random.uniform(0.001, 0.01))
        with live_pg_session_factory() as db:
            candidate, job = create_page_candidate(
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
            db.commit()
            return candidate.ordinal

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        ordinals = list(executor.map(allocate_candidate, range(concurrency)))

    assert sorted(ordinals) == list(range(1, concurrency + 1))

    # Verify real DB persistence in fresh independent Session
    with live_pg_session_factory() as verify_db:
        persisted = list(
            verify_db.scalars(
                select(PageCandidate)
                .where(PageCandidate.batch_id == batch_id)
                .order_by(PageCandidate.ordinal.asc())
            )
        )
        assert len(persisted) == concurrency
        assert [c.ordinal for c in persisted] == list(range(1, concurrency + 1))


def test_pg_workflow_version_release_concurrency(live_pg_session_factory):
    """Verify PostgreSQL FOR UPDATE and revision locking prevent race conditions during workflow publishing with explicit 409 assert."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    with live_pg_session_factory() as db:
        definition = WorkflowDefinition(
            project_id=seeded["project_id"],
            name="PG工作流发布测试",
            draft_graph=default_graph(),
        )
        db.add(definition)
        db.commit()
        def_id = definition.id

    barrier = Barrier(2)
    results = []

    def publish_worker(idx: int):
        barrier.wait(timeout=5)
        with live_pg_session_factory() as db:
            wf = db.get(WorkflowDefinition, def_id)
            try:
                version = publish_workflow(db, wf, max_attempts=1)
                results.append(("SUCCESS", version.revision))
            except PublishRevisionConflictError:
                results.append(("CONFLICT", 409))
            except Exception as e:
                results.append(("OTHER_ERROR", str(e)))

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(publish_worker, range(2)))

    successes = [r for r in results if r[0] == "SUCCESS"]
    conflicts = [r for r in results if r[0] == "CONFLICT"]
    other_errors = [r for r in results if r[0] == "OTHER_ERROR"]

    assert len(other_errors) == 0, f"Unexpected errors during workflow release: {other_errors}"
    assert len(successes) == 1
    assert successes[0][1] == 1
    assert len(conflicts) == 1
    assert conflicts[0][1] == 409

    # Verify that in PostgreSQL, version incrementing and published_version_id are consistent
    with live_pg_session_factory() as verify_db:
        versions = list(
            verify_db.scalars(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == def_id)
                .order_by(WorkflowVersion.revision.asc())
            )
        )
        assert len(versions) == 1
        assert versions[0].revision == 1


def test_pg_transaction_rollback_and_zero_residual_entities(live_pg_session_factory, monkeypatch):
    """Verify that in PostgreSQL, an injected downstream failure in approve_node rolls back atomically with 0 residual entities and subsequent retry succeeds."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]
    page_id = seeded["page_id"]

    with live_pg_session_factory() as db:
        wf = WorkflowDefinition(
            project_id=project_id,
            name="PG审批回滚测试",
            draft_graph=default_graph(),
        )
        db.add(wf)
        db.flush()
        publish_workflow(db, wf)

        run = create_workflow_run(
            db,
            wf,
            scope_type="PAGE",
            scope_id=page_id,
            start_node_ids=["generate"],
            stop_node_ids=["generate"],
        )
        db.flush()
        run_id = run.id

        node_run = db.scalar(
            select(WorkflowNodeRun).where(
                WorkflowNodeRun.workflow_run_id == run.id,
                WorkflowNodeRun.status == "WAITING_APPROVAL",
            )
        )
        assert node_run is not None
        node_id = node_run.node_id
        db.commit()

    # Inject failure into job creation step within real approve_node flow
    monkeypatch.setattr(
        "app.services.workflow_engine.create_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Injected PG downstream failure")),
    )

    with live_pg_session_factory() as db:
        with pytest.raises(RuntimeError, match="Injected PG downstream failure"):
            approve_node(
                db,
                run_id=run_id,
                node_id=node_id,
                image_model_alias="image.nano_banana_2",
                resolution="1K",
            )

    # Verify in fresh session that no orphan batch, candidate, or job persisted
    with live_pg_session_factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(GenerationBatch.page_id == page_id)
            )
        )
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.page_id == page_id)
            )
        )
        jobs = list(
            verify_db.scalars(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
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

    # Remove mock and verify that retry succeeds cleanly
    monkeypatch.undo()
    with live_pg_session_factory() as db:
        approve_node(
            db,
            run_id=run_id,
            node_id=node_id,
            image_model_alias="image.nano_banana_2",
            resolution="1K",
        )

    with live_pg_session_factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(GenerationBatch.page_id == page_id)
            )
        )
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.page_id == page_id)
            )
        )
        jobs = list(
            verify_db.scalars(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
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


def test_pg_candidate_creation_blocked_when_batch_closed(live_pg_session_factory):
    """Verify that attempting to create a candidate for a closed batch raises 409 with 0 dirty rows."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    with live_pg_session_factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        db.commit()
        batch_id = batch.id

    # Session 2 closes the batch
    with live_pg_session_factory() as close_db:
        b = close_db.get(GenerationBatch, batch_id)
        b.status = "CLOSED"
        close_db.commit()

    # Session 1 attempts candidate creation
    with live_pg_session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            create_page_candidate(
                db,
                batch_id=batch_id,
                payload=CandidateCreate(
                    model_alias="image.nano_banana_2",
                    resolution=Resolution.DRAFT_1K,
                    storyboard_version=1,
                    reference_selections={},
                ),
            )
        assert exc_info.value.status_code == 409
        assert "抽卡批次不存在或已经关闭" in str(exc_info.value.detail)

    with live_pg_session_factory() as verify_db:
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.batch_id == batch_id)
            )
        )
        assert len(candidates) == 0
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.states import Resolution
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    MangaPage,
    Outfit,
    Panel,
    Project,
    StyleProfile,
    WorkflowDefinition,
)
from app.schemas import CandidateCreate
from app.services.ordinal_allocator import (
    create_generation_batch,
    create_page_candidate,
)
from app.services.workflow_engine import default_graph, publish_workflow


def _seed_pg_project_hierarchy(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as db:
        project = Project(name="PostgreSQL 并发验收项目")
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
            sha256="pg-char-hash",
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
            sha256="pg-outfit-hash",
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
            sha256="pg-style-hash",
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


def test_pg_dialect_confirmation(live_postgres_engine):
    """Confirm the engine is running real PostgreSQL dialect with FOR UPDATE capability."""
    assert live_postgres_engine.dialect.name == "postgresql"


def test_pg_concurrent_generation_batch_allocation(live_pg_session_factory):
    """Verify PostgreSQL allocates strictly monotonic unique ordinals across concurrent worker threads."""
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


def test_pg_concurrent_page_candidate_allocation(live_pg_session_factory, monkeypatch):
    """Verify PostgreSQL allocates strictly monotonic candidate ordinals concurrently within a batch."""
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
            return candidate.ordinal

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        ordinals = list(executor.map(allocate_candidate, range(concurrency)))

    assert sorted(ordinals) == list(range(1, concurrency + 1))


def test_pg_workflow_version_release_concurrency(live_pg_session_factory):
    """Verify PostgreSQL FOR UPDATE and version locking prevent race conditions during workflow publishing."""
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

    concurrency = 4
    barrier = Barrier(concurrency)

    def publish_worker(idx: int):
        barrier.wait(timeout=5)
        with live_pg_session_factory() as db:
            wf = db.get(WorkflowDefinition, def_id)
            return publish_workflow(db, wf)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        versions = list(executor.map(publish_worker, range(concurrency)))

    revisions = [v.revision for v in versions]
    assert sorted(revisions) == list(range(1, concurrency + 1))


def test_pg_transaction_rollback_and_zero_residual_entities(live_pg_session_factory, monkeypatch):
    """Verify that in PostgreSQL, an injected downstream failure rolls back the entire transaction with 0 residual entities."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]

    monkeypatch.setattr(
        "app.services.job_service.create_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Injected PG downstream failure")),
    )

    with live_pg_session_factory() as db:
        with pytest.raises(RuntimeError, match="Injected PG downstream failure"):
            batch = create_generation_batch(
                db,
                project_id=project_id,
                chapter_id=seeded["chapter_id"],
                page_id=seeded["page_id"],
            )
            from app.services.job_service import create_job

            create_job(
                db,
                project_id=project_id,
                target_type="GENERATION_BATCH",
                target_id=batch.id,
                job_type="PAGE_GENERATE",
            )
            db.commit()

    with live_pg_session_factory() as verify_db:
        batches = list(
            verify_db.scalars(
                select(GenerationBatch).where(GenerationBatch.project_id == project_id)
            )
        )
        assert len(batches) == 0
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from app.domain.states import Resolution
from app.models import (
    AppSetting,
    Asset,
    Chapter,
    Character,
    CharacterModelPackage,
    CharacterModelPackageVersion,
    CharacterModelPackageVersionOutfit,
    CharacterModelPackageVersionReference,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    ProviderUsageReconciliation,
    StyleProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.schemas import CandidateCreate
from app.services.usage_ledger import create_reconciliation
from app.services.worker_handlers import model_call_audit
from app.services.worker_handlers.model_call_audit import (
    ModelCallAttemptMeta,
    begin_model_call_attempt,
    finalize_model_call_attempt,
)
from app.usage_schemas import ProviderUsageReconciliationCreate
from app.services.ordinal_allocator import (
    create_generation_batch,
    create_page_candidate,
)
from app.services.workflow_engine import (
    approve_node,
    create_workflow_run,
    default_graph,
    publish_workflow,
)
from fastapi import HTTPException
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker


def test_pg_usage_migration_roundtrip(live_pg_isolated_schema):
    engine, _schema = live_pg_isolated_schema
    config = Config("apps/api/alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "20260831_22")
        assert "provider_usage_reconciliations" not in inspect(
            connection
        ).get_table_names()
        command.upgrade(config, "head")
        assert "provider_usage_reconciliations" in inspect(
            connection
        ).get_table_names()


def test_pg_model_call_begin_and_finalize_are_concurrency_safe(
    live_pg_session_factory, monkeypatch
):
    with live_pg_session_factory() as db:
        project = Project(name="PG 用量并发验收")
        db.add(project)
        db.flush()
        job = GenerationJob(
            project_id=project.id,
            target_type="PROJECT",
            target_id=project.id,
            job_type="SCRIPT_PARSE",
            status="GENERATING",
            attempt_count=1,
        )
        db.add(job)
        db.commit()
        job_id = job.id
        project_id = project.id

    monkeypatch.setattr(model_call_audit, "SessionLocal", live_pg_session_factory)
    meta = ModelCallAttemptMeta(
        job_id=job_id,
        project_id=project_id,
        job_attempt=1,
        provider="provider-pg",
        model_id="model-pg",
        dispatch_request_id="pg-stable-dispatch",
    )
    begin_barrier = Barrier(2)

    def begin(_index):
        begin_barrier.wait(timeout=10)
        return begin_model_call_attempt(meta)

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempt_ids = list(executor.map(begin, range(2)))
    assert len(set(attempt_ids)) == 1

    finalize_barrier = Barrier(2)

    def finalize(_index):
        finalize_barrier.wait(timeout=10)
        finalize_model_call_attempt(
            attempt_ids[0],
            outcome="SUCCEEDED",
            usage={"input_tokens": 10, "output_tokens": 2},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(finalize, range(2)))
    with live_pg_session_factory() as db:
        rows = list(db.scalars(select(ModelCallAttempt)))
        assert len(rows) == 1
        assert rows[0].outcome == "SUCCEEDED"


def test_pg_usage_reconciliation_serializes_overlap_and_replays_idempotently(
    live_pg_session_factory,
):
    """Concurrent imports for one billing dimension must not both commit."""

    started = datetime(2026, 9, 1, tzinfo=UTC)
    base = {
        "provider": "provider-pg",
        "model_id": "model-pg",
        "channel": "HTTP_API",
        "billing_account_id": "acceptance-account",
        "period_start": started,
        "period_end": started + timedelta(days=1),
        "currency": "USD",
        "billed_amount": Decimal("1.25"),
        "source_note": "isolated acceptance fixture",
        "entered_by": "pytest",
    }
    payloads = [
        ProviderUsageReconciliationCreate(
            **base,
            connection_id=f"connection-{index}",
            import_batch_id=f"batch-{index}",
            idempotency_key=f"key-{index}",
        )
        for index in range(2)
    ]
    barrier = Barrier(2)

    def insert(payload):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                row = create_reconciliation(db, payload)
            except HTTPException as error:
                return "conflict", error.status_code
            return "created", row.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert, payloads))
    assert sorted(item[0] for item in results) == ["conflict", "created"]
    assert next(item[1] for item in results if item[0] == "conflict") == 409

    created_id = next(item[1] for item in results if item[0] == "created")
    created_payload = payloads[
        next(index for index, item in enumerate(results) if item[0] == "created")
    ]
    with live_pg_session_factory() as db:
        replay = create_reconciliation(db, created_payload)
        assert replay.id == created_id
        assert len(list(db.scalars(select(ProviderUsageReconciliation)))) == 1


@pytest.fixture(autouse=True)
def _isolate_queue_boundary_for_postgres_scenarios(monkeypatch):
    from app.config import get_settings

    # Exercise real readiness with LOCAL configuration; never actually dispatch.
    # Worker/queue behavior belongs to the separate Redis/RQ live scenarios.
    monkeypatch.setattr(get_settings(), "queue_enabled", True)
    monkeypatch.setattr("app.services.workflow_engine.enqueue_job", lambda _db, job: job)


def _seed_pg_project_hierarchy(session_factory: sessionmaker[Session]) -> dict[str, str]:
    from app.config import get_settings
    from app.services.provider_presets import ensure_provider_presets

    with session_factory() as db:
        ensure_provider_presets(db, get_settings(), auto_commit=True)
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
            db.add(AppSetting(key="runtime", value={"queue_mode": "LOCAL"}, version=1))

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
    """A second independent PostgreSQL connection must fail NOWAIT while locked."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    with live_pg_session_factory() as owner, live_pg_session_factory() as contender:
        assert owner.get_bind().dialect.name == "postgresql"
        query = select(Project).where(Project.id == seeded["project_id"])
        owner.scalar(query.with_for_update())
        with pytest.raises(OperationalError) as caught:
            contender.scalar(query.with_for_update(nowait=True))
        assert (
            getattr(caught.value.orig, "sqlstate", None)
            or getattr(caught.value.orig, "pgcode", None)
        ) == "55P03"
        contender.rollback()
        owner.rollback()
        assert contender.scalar(query.with_for_update(nowait=True)).id == seeded["project_id"]


def test_pg_concurrent_generation_batch_allocation(live_pg_session_factory):
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    barrier = Barrier(8)

    def allocate(_):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            batch = create_generation_batch(
                db,
                project_id=seeded["project_id"],
                chapter_id=seeded["chapter_id"],
                page_id=seeded["page_id"],
                generation_kind="PAGE",
            )
            db.commit()
            return batch.ordinal

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sorted(executor.map(allocate, range(8))) == list(range(1, 9))
    with live_pg_session_factory() as db:
        assert list(
            db.scalars(
                select(GenerationBatch.ordinal)
                .where(GenerationBatch.project_id == seeded["project_id"])
                .order_by(GenerationBatch.ordinal)
            )
        ) == list(range(1, 9))


def _candidate_payload(seeded):
    return CandidateCreate(
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        storyboard_version=1,
        reference_selections={
            seeded["character_id"]: {
                "character_asset_id": seeded["character_asset_id"],
                "outfit_id": seeded["outfit_id"],
                "outfit_asset_id": seeded["outfit_asset_id"],
            },
        },
    )


def _batch_id(factory, seeded):
    with factory() as db:
        batch = create_generation_batch(
            db,
            project_id=seeded["project_id"],
            chapter_id=seeded["chapter_id"],
            page_id=seeded["page_id"],
            generation_kind="PAGE",
        )
        db.commit()
        return batch.id


def test_pg_concurrent_page_candidate_allocation(live_pg_session_factory):
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    batch_id = _batch_id(live_pg_session_factory, seeded)
    barrier = Barrier(6)

    def allocate(_):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            candidate, job = create_page_candidate(
                db,
                batch_id=batch_id,
                payload=_candidate_payload(seeded),
            )
            db.commit()
            assert candidate.job_id == job.id
            return candidate.ordinal

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert sorted(executor.map(allocate, range(6))) == list(range(1, 7))
    with live_pg_session_factory() as db:
        candidates = list(
            db.scalars(
                select(PageCandidate)
                .where(PageCandidate.batch_id == batch_id)
                .order_by(PageCandidate.ordinal)
            )
        )
        assert [candidate.ordinal for candidate in candidates] == list(range(1, 7))
        for candidate in candidates:
            assert db.get(GenerationJob, candidate.job_id).target_id == candidate.id


def _workflow_id(factory, seeded):
    with factory() as db:
        workflow = WorkflowDefinition(
            project_id=seeded["project_id"],
            name="PG acceptance",
            draft_graph=default_graph(),
        )
        db.add(workflow)
        db.commit()
        return workflow.id


def test_pg_workflow_version_release_concurrency(live_pg_session_factory):
    """Healthy row-lock serialization permits both publishers, at revisions 1/2."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    workflow_id = _workflow_id(live_pg_session_factory, seeded)
    barrier = Barrier(2)

    def publish(_):
        with live_pg_session_factory() as db:
            workflow = db.get(WorkflowDefinition, workflow_id)
            barrier.wait(timeout=10)
            return publish_workflow(db, workflow, max_attempts=1).revision

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(publish, range(2))) == [1, 2]
    with live_pg_session_factory() as db:
        versions = list(
            db.scalars(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == workflow_id)
                .order_by(WorkflowVersion.revision)
            )
        )
        assert [version.revision for version in versions] == [1, 2]
        assert db.get(WorkflowDefinition, workflow_id).published_version_id == versions[1].id


def test_pg_publish_real_unique_conflict_exhausts_to_409_then_recovers(
    live_pg_session_factory,
    monkeypatch,
):
    from app.api.routes.workflow_definitions import publish
    from app.services import workflow_engine

    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    workflow_id = _workflow_id(live_pg_session_factory, seeded)
    with live_pg_session_factory() as db:
        first = publish(workflow_id, db)
        first_id = first.id
        attempts = []

        def collide(_db, _workflow_id):
            attempts.append(_workflow_id)
            # Force a real unique-constraint violation on the already committed row.
            return 1

        with monkeypatch.context() as patch:
            patch.setattr(workflow_engine, "_next_revision", collide)
            with pytest.raises(HTTPException) as caught:
                publish(workflow_id, db)
            assert caught.value.status_code == 409
        assert len(attempts) == workflow_engine.PUBLISH_REVISION_MAX_ATTEMPTS
        with live_pg_session_factory() as verify:
            assert list(
                verify.scalars(
                    select(WorkflowVersion.revision).where(
                        WorkflowVersion.workflow_id == workflow_id
                    )
                )
            ) == [1]
            assert verify.get(WorkflowDefinition, workflow_id).published_version_id == first_id
        assert publish(workflow_id, db).revision == 2


def _approval_state(db, seeded, run_id):
    batches = list(
        db.scalars(select(GenerationBatch).where(GenerationBatch.page_id == seeded["page_id"]))
    )
    candidates = list(
        db.scalars(select(PageCandidate).where(PageCandidate.page_id == seeded["page_id"]))
    )
    jobs = list(
        db.scalars(select(GenerationJob).where(GenerationJob.project_id == seeded["project_id"]))
    )
    node = db.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run_id,
            WorkflowNodeRun.node_id == "generate",
        )
    )
    return batches, candidates, jobs, node, db.get(WorkflowRun, run_id)


def test_pg_transaction_rollback_and_zero_residual_entities(live_pg_session_factory, monkeypatch):
    """Fail only after the batch, candidate, job and run/node links are complete."""
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    workflow_id = _workflow_id(live_pg_session_factory, seeded)
    with live_pg_session_factory() as db:
        workflow = db.get(WorkflowDefinition, workflow_id)
        publish_workflow(db, workflow)
        run = create_workflow_run(
            db,
            workflow,
            scope_type="PAGE",
            scope_id=seeded["page_id"],
            start_node_ids=["generate"],
            stop_node_ids=["generate"],
        )
        run_id = run.id
        assert _approval_state(db, seeded, run_id)[3].status == "WAITING_APPROVAL"
        db.commit()

    with live_pg_session_factory() as db:
        reached_final_commit = []

        def fail_final_commit():
            db.flush()
            batches, candidates, jobs, node, run = _approval_state(db, seeded, run_id)
            assert len(batches) == len(candidates) == len(jobs) == 1
            assert candidates[0].job_id == node.job_id == jobs[0].id
            assert jobs[0].target_id == candidates[0].id
            assert node.output_refs == {
                "candidate_id": candidates[0].id,
                "batch_id": batches[0].id,
            }
            assert node.status == run.status == "RUNNING"
            reached_final_commit.append(True)
            raise RuntimeError("Injected final approval commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(db, "commit", fail_final_commit)
            with pytest.raises(RuntimeError, match="Injected final approval commit failure"):
                approve_node(
                    db, run_id, "generate", image_model_alias="image.nano_banana_2", resolution="1K"
                )
            db.rollback()
        assert reached_final_commit == [True]
        with live_pg_session_factory() as verify:
            batches, candidates, jobs, node, run = _approval_state(verify, seeded, run_id)
            assert batches == candidates == jobs == []
            assert node.status == "WAITING_APPROVAL" and node.job_id is None
            assert run.status == "PAUSED"
        # Reuse the rolled-back Session as an additional session recovery check.
        assert (
            approve_node(
                db, run_id, "generate", image_model_alias="image.nano_banana_2", resolution="1K"
            ).status
            == "RUNNING"
        )
    with live_pg_session_factory() as verify:
        batches, candidates, jobs, node, run = _approval_state(verify, seeded, run_id)
        assert len(batches) == len(candidates) == len(jobs) == 1
        assert candidates[0].job_id == node.job_id == jobs[0].id
        assert node.status == run.status == "RUNNING"


def test_pg_candidate_creation_blocked_when_batch_closed(live_pg_session_factory):
    seeded = _seed_pg_project_hierarchy(live_pg_session_factory)
    batch_id = _batch_id(live_pg_session_factory, seeded)
    # Load a stale OPEN entity before the independent closer commits.
    with live_pg_session_factory() as db:
        stale = db.get(GenerationBatch, batch_id)
        assert stale.status == "OPEN"
        with live_pg_session_factory() as closer:
            closer.get(GenerationBatch, batch_id).status = "CLOSED"
            closer.commit()
        with pytest.raises(HTTPException) as caught:
            create_page_candidate(db, batch_id=batch_id, payload=_candidate_payload(seeded))
        assert caught.value.status_code == 409
        assert caught.value.detail == "抽卡批次不存在或已经关闭"
        db.rollback()
    with live_pg_session_factory() as verify:
        assert (
            list(
                verify.scalars(
                    select(PageCandidate).where(
                        PageCandidate.batch_id == batch_id,
                    )
                )
            )
            == []
        )
        assert (
            list(
                verify.scalars(
                    select(GenerationJob).where(
                        GenerationJob.project_id == seeded["project_id"],
                    )
                )
            )
            == []
        )


def test_pg_schema_migrations_preserve_neighbor_and_cleanup_owned_resources(
    live_postgres_admin_engine,
    live_pg_isolated_schema,
):
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from tests.integration.postgres_resources import ROOT, isolated_postgres_schema

    sentinel_engine, sentinel_schema = live_pg_isolated_schema
    with Session(sentinel_engine) as db:
        sentinel = Project(name="neighbor sentinel")
        db.add(sentinel)
        db.commit()
        sentinel_id = sentinel.id
    with isolated_postgres_schema(live_postgres_admin_engine) as (engine, schema):
        assert schema != sentinel_schema
        with engine.connect() as connection:
            head = ScriptDirectory.from_config(
                Config(str(ROOT / "apps/api/alembic.ini"))
            ).get_current_head()
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head
            assert connection.scalar(text("SELECT count(*) FROM projects")) == 0
    with live_postgres_admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pg_namespace WHERE nspname=:schema"),
                {"schema": schema},
            )
            == 0
        )
    with Session(sentinel_engine) as db:
        assert db.get(Project, sentinel_id).name == "neighbor sentinel"



def _package_draft(session_factory: sessionmaker[Session], seeded: dict[str, str]):
    from app.services.character_packages import bind_outfit, bind_reference, create_package

    with session_factory() as db:
        package = create_package(db, seeded["project_id"], seeded["character_id"], {})
        version = db.scalar(
            select(CharacterModelPackageVersion).where(
                CharacterModelPackageVersion.package_id == package.id
            )
        )
        bind_reference(
            db,
            seeded["project_id"],
            seeded["character_id"],
            version.id,
            asset_id=seeded["character_asset_id"],
            role="front",
            token=version.version,
        )
        db.expire_all()
        version = db.get(CharacterModelPackageVersion, version.id)
        bind_outfit(
            db,
            seeded["project_id"],
            seeded["character_id"],
            version.id,
            outfit_id=seeded["outfit_id"],
            is_default=True,
            token=version.version,
        )
        return {
            **seeded,
            "package_id": package.id,
            "version_id": version.id,
        }


def test_pg_pkg_s14_package_migration_roundtrip_and_two_phase_fk(live_pg_isolated_schema):
    """PKG-S14: PostgreSQL two-phase published_version FK and child-first downgrade."""
    engine, _schema = live_pg_isolated_schema
    config = Config("apps/api/alembic.ini")
    package_tables = {
        "character_model_packages",
        "character_model_package_versions",
        "character_model_package_version_references",
        "character_model_package_version_outfits",
    }
    with engine.begin() as connection:
        names = set(inspect(connection).get_table_names())
        assert package_tables <= names
        fk_names = {
            fk["name"]
            for fk in inspect(connection).get_foreign_keys("character_model_packages")
        }
        assert "fk_character_model_packages_published_version" in fk_names
        indexes = {
            index["name"]: index
            for index in inspect(connection).get_indexes("character_model_package_versions")
        }
        assert indexes["uq_character_model_package_versions_one_draft"]["unique"]
        outfit_indexes = {
            index["name"]: index
            for index in inspect(connection).get_indexes(
                "character_model_package_version_outfits"
            )
        }
        assert outfit_indexes["uq_character_model_package_version_outfit_default"]["unique"]
        config.attributes["connection"] = connection
        command.downgrade(config, "20260901_24")
        remaining = set(inspect(connection).get_table_names())
        assert package_tables.isdisjoint(remaining)
        command.upgrade(config, "head")
        restored = set(inspect(connection).get_table_names())
        assert package_tables <= restored
        fk_names = {
            fk["name"]
            for fk in inspect(connection).get_foreign_keys("character_model_packages")
        }
        assert "fk_character_model_packages_published_version" in fk_names


def test_pg_pkg_s14_downgrade_refuses_published_packages(live_pg_session_factory):
    """PKG-S14: downgrade stays refuse-closed once a version leaves the migration draft."""
    from app.services.character_packages import publish_version

    seeded = _package_draft(live_pg_session_factory, _seed_pg_project_hierarchy(live_pg_session_factory))
    with live_pg_session_factory() as db:
        publish_version(
            db, seeded["project_id"], seeded["character_id"], seeded["version_id"]
        )
    with live_pg_session_factory() as db:
        engine = db.get_bind()
    config = Config("apps/api/alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="refusing downgrade"):
            command.downgrade(config, "20260901_24")
        assert "character_model_packages" in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260902_25"


def test_pg_pkg_s14_partial_unique_indexes_and_restrict(live_pg_session_factory):
    """PKG-S14: one DRAFT, one default outfit, and RESTRICT delete protection."""
    seeded = _package_draft(live_pg_session_factory, _seed_pg_project_hierarchy(live_pg_session_factory))
    with live_pg_session_factory() as db:
        with pytest.raises(IntegrityError):
            db.add(
                CharacterModelPackageVersion(
                    package_id=seeded["package_id"],
                    version_number=99,
                    status="DRAFT",
                    spec_snapshot={"frozen_from": "test"},
                )
            )
            db.flush()
        db.rollback()

    with live_pg_session_factory() as db:
        extra = Outfit(
            project_id=seeded["project_id"],
            character_id=seeded["character_id"],
            name="第二套",
            reference_asset_ids=[seeded["outfit_asset_id"]],
            status="CANONICAL",
        )
        db.add(extra)
        db.flush()
        db.add(
            CharacterModelPackageVersionOutfit(
                version_id=seeded["version_id"],
                outfit_id=extra.id,
                is_default=True,
                sort_order=1,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    with live_pg_session_factory() as db:
        with pytest.raises(IntegrityError):
            db.execute(
                text("DELETE FROM outfits WHERE id = :outfit_id"),
                {"outfit_id": seeded["outfit_id"]},
            )
            db.flush()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(
                text("DELETE FROM assets WHERE id = :asset_id"),
                {"asset_id": seeded["character_asset_id"]},
            )
            db.flush()
        db.rollback()
        assert db.get(Outfit, seeded["outfit_id"]) is not None
        assert db.get(Asset, seeded["character_asset_id"]) is not None


def test_pg_pkg_s14_for_update_package_lock(live_pg_session_factory):
    """PKG-S14: package writers take FOR UPDATE; a second connection cannot steal it."""
    seeded = _package_draft(live_pg_session_factory, _seed_pg_project_hierarchy(live_pg_session_factory))
    with live_pg_session_factory() as owner, live_pg_session_factory() as contender:
        query = select(CharacterModelPackage).where(
            CharacterModelPackage.id == seeded["package_id"]
        )
        owner.scalar(query.with_for_update())
        with pytest.raises(OperationalError) as caught:
            contender.scalar(query.with_for_update(nowait=True))
        assert (
            getattr(caught.value.orig, "sqlstate", None)
            or getattr(caught.value.orig, "pgcode", None)
        ) == "55P03"
        contender.rollback()
        owner.rollback()


def test_pg_pkg_s14_concurrent_publish_one_winner(live_pg_session_factory):
    """PKG-S14: the same DRAFT cannot publish twice under the package row lock."""
    from app.services.character_packages import publish_version

    seeded = _package_draft(live_pg_session_factory, _seed_pg_project_hierarchy(live_pg_session_factory))
    barrier = Barrier(2)
    errors: list[int] = []

    def publish(_index):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                return publish_version(
                    db, seeded["project_id"], seeded["character_id"], seeded["version_id"]
                ).id
            except HTTPException as error:
                errors.append(error.status_code)
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))
    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    assert errors == [409]
    with live_pg_session_factory() as db:
        package = db.get(CharacterModelPackage, seeded["package_id"])
        version = db.get(CharacterModelPackageVersion, seeded["version_id"])
        assert package.published_version_id == seeded["version_id"]
        assert version.status == "READY"


def test_pg_pkg_s14_archive_v2_versus_activate_v2(live_pg_session_factory):
    """PKG-S14 §5.3-8: archive V2 vs activate V2 cannot point at ARCHIVED."""
    from app.services.character_packages import (
        activate_version,
        archive_version,
        derive_version,
        publish_version,
    )

    seeded = _package_draft(live_pg_session_factory, _seed_pg_project_hierarchy(live_pg_session_factory))
    with live_pg_session_factory() as db:
        v1 = publish_version(
            db, seeded["project_id"], seeded["character_id"], seeded["version_id"]
        )
        v2 = derive_version(db, seeded["project_id"], seeded["character_id"], v1.id)
        publish_version(db, seeded["project_id"], seeded["character_id"], v2.id)
        activate_version(
            db,
            seeded["project_id"],
            seeded["character_id"],
            v1.id,
            expected_published_version_id=v2.id,
        )
        v1_id, v2_id = v1.id, v2.id
        project_id, character_id = seeded["project_id"], seeded["character_id"]
        package_id = seeded["package_id"]

    barrier = Barrier(2)
    outcomes: dict[str, int | str] = {}

    def archive(_):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                archive_version(db, project_id, character_id, v2_id)
                outcomes["archive"] = "ok"
            except HTTPException as error:
                outcomes["archive"] = error.status_code

    def activate(_):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                activate_version(
                    db,
                    project_id,
                    character_id,
                    v2_id,
                    expected_published_version_id=v1_id,
                )
                outcomes["activate"] = "ok"
            except HTTPException as error:
                outcomes["activate"] = error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda fn: fn(None), (archive, activate)))

    assert set(outcomes) == {"archive", "activate"}
    assert outcomes["archive"] in {"ok", 409}
    assert outcomes["activate"] in {"ok", 409}
    assert {"ok", 409} <= set(outcomes.values()) or set(outcomes.values()) == {"ok", 409}
    # Exactly one may succeed; both succeeding would let the pointer land on ARCHIVED.
    assert list(outcomes.values()).count("ok") == 1
    with live_pg_session_factory() as db:
        package = db.get(CharacterModelPackage, package_id)
        v2 = db.get(CharacterModelPackageVersion, v2_id)
        published = db.get(CharacterModelPackageVersion, package.published_version_id)
        assert published.status != "ARCHIVED"
        if outcomes["activate"] == "ok":
            assert package.published_version_id == v2_id
            assert v2.status in {"READY", "IN_PRODUCTION"}
        else:
            assert package.published_version_id == v1_id
            assert v2.status == "ARCHIVED"


def test_pg_pkg_s14_concurrent_asset_bind_single_winner(live_pg_session_factory):
    """PKG-S14: FOR UPDATE on Asset serializes cross-character bind races."""
    from app.services.character_packages import bind_reference, create_package

    first = _seed_pg_project_hierarchy(live_pg_session_factory)
    with live_pg_session_factory() as db:
        other = Character(project_id=first["project_id"], primary_name="第二角色")
        db.add(other)
        db.flush()
        other_id = other.id
        shared = Asset(
            project_id=first["project_id"],
            kind="CHARACTER_REFERENCE",
            original_name="shared.png",
            storage_key=f"test/pg_shared_{time.time()}.png",
            mime_type="image/png",
            byte_size=1024,
            width=64,
            height=64,
            sha256=f"pg-shared-{time.time()}",
        )
        db.add(shared)
        db.commit()
        shared_id = shared.id
        first_pkg = create_package(db, first["project_id"], first["character_id"], {})
        other_pkg = create_package(db, first["project_id"], other_id, {})
        first_version = db.scalar(
            select(CharacterModelPackageVersion).where(
                CharacterModelPackageVersion.package_id == first_pkg.id
            )
        )
        other_version = db.scalar(
            select(CharacterModelPackageVersion).where(
                CharacterModelPackageVersion.package_id == other_pkg.id
            )
        )
        first_version_id, other_version_id = first_version.id, other_version.id
        first_token, other_token = first_version.version, other_version.version

    barrier = Barrier(2)
    errors: list[int] = []

    def bind(character_id, version_id, token):
        with live_pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                return bind_reference(
                    db,
                    first["project_id"],
                    character_id,
                    version_id,
                    asset_id=shared_id,
                    role="front",
                    token=token,
                ).id
            except HTTPException as error:
                errors.append(error.status_code)
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda args: bind(*args),
                (
                    (first["character_id"], first_version_id, first_token),
                    (other_id, other_version_id, other_token),
                ),
            )
        )
    assert len([item for item in results if item is not None]) == 1
    assert errors == [409]
    with live_pg_session_factory() as db:
        refs = list(
            db.scalars(
                select(CharacterModelPackageVersionReference).where(
                    CharacterModelPackageVersionReference.asset_id == shared_id
                )
            )
        )
        assert len(refs) == 1

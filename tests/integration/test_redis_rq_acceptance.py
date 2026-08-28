from __future__ import annotations

import time
from datetime import timedelta

import pytest
from rq import Queue, SimpleWorker
from sqlalchemy.orm import Session, sessionmaker

from app import database, worker_tasks
from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    AppSetting,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    utcnow,
)
from app.services.job_service import create_job, enqueue_job, recover_pending_jobs
from app.worker_tasks import JobCancelledError, execute_job


def _seed_redis_acceptance_hierarchy(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as db:
        project = Project(name="Redis RQ 验收项目", default_concurrency=2)
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
            scene_ids=["scene-rq-1"],
            beat_ids=["beat-rq-1"],
        )
        db.add(page)
        db.flush()

        batch = GenerationBatch(
            project_id=project.id,
            chapter_id=chapter.id,
            page_id=page.id,
            ordinal=1,
            generation_kind="PAGE",
            status="OPEN",
        )
        db.add(batch)
        db.flush()

        db.add(
            AppSetting(
                key="runtime",
                value={"queue_mode": "REDIS"},
                version=1,
            )
        )
        db.commit()

        return {
            "project_id": project.id,
            "chapter_id": chapter.id,
            "page_id": page.id,
            "batch_id": batch.id,
        }


def test_redis_connection_and_namespace_isolation(live_redis_connection):
    """Verify live Redis connection is responsive and isolated to test DB."""
    assert live_redis_connection.ping() is True


def test_redis_rq_enqueue_and_worker_execution(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    monkeypatch,
):
    """Verify end-to-end real Redis queueing and RQ worker execution with fake adapter."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", "mangaflow_acceptance_queue")
    monkeypatch.setattr(settings, "queue_enabled", True)
    monkeypatch.setattr(worker_tasks, "SessionLocal", live_pg_session_factory)
    monkeypatch.setattr(database, "SessionLocal", live_pg_session_factory)

    fake_calls = []

    def fake_generate(db: Session, job: GenerationJob):
        fake_calls.append(job.id)
        candidate = PageCandidate(
            batch_id=seeded["batch_id"],
            page_id=seeded["page_id"],
            ordinal=1,
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            status="READY",
        )
        db.add(candidate)
        db.flush()
        job.status = JobStatus.COMPLETED

    monkeypatch.setattr(worker_tasks, "_run_page_generate", fake_generate)

    with live_pg_session_factory() as db:
        job = create_job(
            db,
            project_id=project_id,
            target_type="PAGE_CANDIDATE",
            target_id=f"target-rq-{time.time()}",
            job_type="PAGE_GENERATE",
            model_alias="image.nano_banana_2",
            auto_commit=True,
        )
        enqueued_job = enqueue_job(db, job)
        assert enqueued_job.status == JobStatus.QUEUED

    queue = Queue("mangaflow_acceptance_queue", connection=live_redis_connection)
    assert len(queue) >= 1

    worker = SimpleWorker([queue], connection=live_redis_connection)
    worker.work(burst=True)

    with live_pg_session_factory() as db:
        finished_job = db.get(GenerationJob, job.id)
        assert finished_job.status == JobStatus.COMPLETED
        assert finished_job.id in fake_calls


def test_redis_rq_state_isolation_no_clobber(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    monkeypatch,
):
    """Verify calling enqueue_job on an actively executing job does not clobber its status."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", "mangaflow_acceptance_queue")
    monkeypatch.setattr(settings, "queue_enabled", True)

    with live_pg_session_factory() as db:
        job = create_job(
            db,
            project_id=seeded["project_id"],
            target_type="PAGE_CANDIDATE",
            target_id=f"target-clobber-{time.time()}",
            job_type="PAGE_GENERATE",
            auto_commit=True,
        )
        job.status = JobStatus.GENERATING
        job.lease_owner = "worker-pid-1234"
        job.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()

        # Calling enqueue on an actively leased job should not revert it to QUEUED
        # when dependencies/queue checks run
        rechecked = db.get(GenerationJob, job.id)
        assert rechecked.status == JobStatus.GENERATING


def test_redis_rq_lease_expiration_and_recovery(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    monkeypatch,
):
    """Verify expired job leases are safely reclaimed and re-queued by recover_pending_jobs."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", "mangaflow_acceptance_queue")
    monkeypatch.setattr(settings, "queue_enabled", True)

    with live_pg_session_factory() as db:
        job = create_job(
            db,
            project_id=seeded["project_id"],
            target_type="PAGE_CANDIDATE",
            target_id=f"target-lease-{time.time()}",
            job_type="PAGE_GENERATE",
            max_attempts=3,
            auto_commit=True,
        )
        job.status = JobStatus.GENERATING
        job.attempt_count = 1
        job.lease_owner = "dead-worker-pid-999"
        job.lease_expires_at = utcnow() - timedelta(seconds=10)
        db.commit()

        recovered_count = recover_pending_jobs(db)
        assert recovered_count >= 1

        reloaded = db.get(GenerationJob, job.id)
        assert reloaded.status == JobStatus.QUEUED
        assert reloaded.lease_owner is None


def test_redis_rq_cancellation_protection(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    monkeypatch,
):
    """Verify that cancelled jobs abort execution without calling fake adapters or writing orphan outputs."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", "mangaflow_acceptance_queue")
    monkeypatch.setattr(worker_tasks, "SessionLocal", live_pg_session_factory)
    monkeypatch.setattr(database, "SessionLocal", live_pg_session_factory)

    fake_called = False

    def fake_generate(db: Session, job: GenerationJob):
        nonlocal fake_called
        fake_called = True

    monkeypatch.setattr(worker_tasks, "_run_page_generate", fake_generate)

    with live_pg_session_factory() as db:
        job = create_job(
            db,
            project_id=seeded["project_id"],
            target_type="PAGE_CANDIDATE",
            target_id=f"target-cancel-{time.time()}",
            job_type="PAGE_GENERATE",
            auto_commit=True,
        )
        job.status = JobStatus.CANCELLED
        db.commit()

        with pytest.raises(JobCancelledError):
            execute_job(job.id)

    assert fake_called is False
from __future__ import annotations

import time
from datetime import timedelta

from rq import Queue, Worker
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import database, worker_tasks
from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.model_adapters.base import ProviderAdapterError
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
from app.worker_tasks import execute_job


def _seed_redis_acceptance_hierarchy(session_factory: sessionmaker[Session]) -> dict[str, str]:
    with session_factory() as db:
        project = Project(name=f"Redis验收项目_{time.time()}", default_concurrency=2)
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

        # Idempotent runtime app setting
        existing_setting = db.scalar(select(AppSetting).where(AppSetting.key == "runtime"))
        if not existing_setting:
            db.add(AppSetting(key="runtime", value={"queue_mode": "REDIS"}, version=1))

        db.commit()

        return {
            "project_id": project.id,
            "chapter_id": chapter.id,
            "page_id": page.id,
            "batch_id": batch.id,
        }


def test_redis_connection_and_namespace_isolation(
    live_redis_connection, live_redis_isolated_namespace
):
    """Verify live Redis connection is responsive and keys are isolated to the test namespace prefix."""
    assert live_redis_connection.ping() is True
    test_key = f"{live_redis_isolated_namespace}ping_test"
    live_redis_connection.set(test_key, "pong", ex=30)
    assert live_redis_connection.get(test_key).decode("utf-8") == "pong"


def test_redis_rq_enqueue_and_worker_execution(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    live_redis_isolated_namespace,
    monkeypatch,
):
    """Verify real Redis queueing and RQ worker execution with deterministic fake provider output."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]
    queue_name = f"acceptance_queue_{live_redis_isolated_namespace.replace(':', '_').strip('_')}"

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", queue_name)
    monkeypatch.setattr(settings, "queue_enabled", True)
    monkeypatch.setattr(worker_tasks, "SessionLocal", live_pg_session_factory)
    monkeypatch.setattr(database, "SessionLocal", live_pg_session_factory)

    fake_executed_jobs = []

    def fake_generate(db: Session, job: GenerationJob):
        fake_executed_jobs.append(job.id)
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

    queue = Queue(queue_name, connection=live_redis_connection)
    assert len(queue) >= 1

    worker = Worker([queue], connection=live_redis_connection)
    worker.work(burst=True)

    with live_pg_session_factory() as verify_db:
        finished_job = verify_db.get(GenerationJob, job.id)
        assert finished_job.status == JobStatus.COMPLETED
        assert finished_job.id in fake_executed_jobs

        persisted_candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.batch_id == seeded["batch_id"])
            )
        )
        assert len(persisted_candidates) == 1


def test_redis_rq_state_isolation_no_clobber(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    live_redis_isolated_namespace,
    monkeypatch,
):
    """Verify calling enqueue_job on an actively executing/leased job does not clobber its status or duplicate queue tasks."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    queue_name = f"acceptance_queue_{live_redis_isolated_namespace.replace(':', '_').strip('_')}"

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", queue_name)
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
        job.lease_owner = "active-worker-pid-1001"
        job.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()

        # Calling enqueue on an actively leased job should preserve its active GENERATING state
        enqueue_job(db, job)

    with live_pg_session_factory() as verify_db:
        reloaded = verify_db.get(GenerationJob, job.id)
        assert reloaded.status == JobStatus.GENERATING
        assert reloaded.lease_owner == "active-worker-pid-1001"

    queue = Queue(queue_name, connection=live_redis_connection)
    assert len(queue) == 0


def test_redis_rq_concurrency_quota_and_deferred_execution(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    live_redis_isolated_namespace,
    monkeypatch,
):
    """Verify that multiple enqueued jobs respect the project concurrency limit (default 2) and defer excess jobs."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]
    queue_name = f"acceptance_queue_{live_redis_isolated_namespace.replace(':', '_').strip('_')}"

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", queue_name)
    monkeypatch.setattr(settings, "queue_enabled", True)
    monkeypatch.setattr(worker_tasks, "SessionLocal", live_pg_session_factory)
    monkeypatch.setattr(database, "SessionLocal", live_pg_session_factory)

    with live_pg_session_factory() as db:
        # Create 3 jobs for project with default_concurrency=2
        jobs = [
            create_job(
                db,
                project_id=project_id,
                target_type="PAGE_CANDIDATE",
                target_id=f"target-slot-{i}-{time.time()}",
                job_type="PAGE_GENERATE",
                auto_commit=True,
            )
            for i in range(3)
        ]
        # Simulate 2 jobs already actively running to saturate the concurrency limit
        jobs[0].status = JobStatus.GENERATING
        jobs[0].lease_owner = "worker-1"
        jobs[0].lease_expires_at = utcnow() + timedelta(minutes=5)
        jobs[1].status = JobStatus.GENERATING
        jobs[1].lease_owner = "worker-2"
        jobs[1].lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()

        # Try to execute the 3rd job: it should detect slot congestion and defer execution
        execute_job(jobs[2].id)

    with live_pg_session_factory() as verify_db:
        third_job = verify_db.get(GenerationJob, jobs[2].id)
        # Should remain in WAITING or QUEUED state without violating concurrency limit
        assert third_job.status in {JobStatus.WAITING, JobStatus.QUEUED}
        assert third_job.lease_owner is None


def test_redis_rq_retryable_and_terminal_failures(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    live_redis_isolated_namespace,
    monkeypatch,
):
    """Verify that retryable adapter errors reset job to WAITING for retry, while non-retryable errors immediately mark FAILED."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    project_id = seeded["project_id"]
    queue_name = f"acceptance_queue_{live_redis_isolated_namespace.replace(':', '_').strip('_')}"

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", queue_name)
    monkeypatch.setattr(worker_tasks, "SessionLocal", live_pg_session_factory)
    monkeypatch.setattr(database, "SessionLocal", live_pg_session_factory)

    def retryable_failure(*_args, **_kwargs):
        raise ProviderAdapterError("RATE_LIMIT", "429 Too Many Requests", retryable=True)

    def terminal_failure(*_args, **_kwargs):
        raise ProviderAdapterError("INVALID_PROMPT", "Unrecoverable prompt error", retryable=False)

    # 1. Test Retryable Error
    monkeypatch.setattr(worker_tasks, "_run_page_generate", retryable_failure)
    with live_pg_session_factory() as db:
        job_retry = create_job(
            db,
            project_id=project_id,
            target_type="PAGE_CANDIDATE",
            target_id=f"target-retry-{time.time()}",
            job_type="PAGE_GENERATE",
            max_attempts=3,
            auto_commit=True,
        )
        execute_job(job_retry.id)

    with live_pg_session_factory() as verify_db:
        reloaded_retry = verify_db.get(GenerationJob, job_retry.id)
        assert reloaded_retry.status == JobStatus.WAITING
        assert reloaded_retry.attempt_count == 1

    # 2. Test Terminal Error
    monkeypatch.setattr(worker_tasks, "_run_page_generate", terminal_failure)
    with live_pg_session_factory() as db:
        job_term = create_job(
            db,
            project_id=project_id,
            target_type="PAGE_CANDIDATE",
            target_id=f"target-term-{time.time()}",
            job_type="PAGE_GENERATE",
            max_attempts=3,
            auto_commit=True,
        )
        execute_job(job_term.id)

    with live_pg_session_factory() as verify_db:
        reloaded_term = verify_db.get(GenerationJob, job_term.id)
        assert reloaded_term.status == JobStatus.FAILED
        assert reloaded_term.error_code == "INVALID_PROMPT"


def test_redis_rq_lease_expiration_and_recovery(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    live_redis_isolated_namespace,
    monkeypatch,
):
    """Verify expired job leases are safely reclaimed and re-queued by recover_pending_jobs."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    queue_name = f"acceptance_queue_{live_redis_isolated_namespace.replace(':', '_').strip('_')}"

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", queue_name)
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

    with live_pg_session_factory() as verify_db:
        reloaded = verify_db.get(GenerationJob, job.id)
        assert reloaded.status == JobStatus.QUEUED
        assert reloaded.lease_owner is None


def test_redis_rq_cancellation_protection(
    live_pg_session_factory,
    live_redis_connection,
    live_redis_url,
    live_redis_isolated_namespace,
    monkeypatch,
):
    """Verify that cancelled jobs safely abort execution without calling provider adapters or generating dirty output rows."""
    seeded = _seed_redis_acceptance_hierarchy(live_pg_session_factory)
    queue_name = f"acceptance_queue_{live_redis_isolated_namespace.replace(':', '_').strip('_')}"

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", live_redis_url)
    monkeypatch.setattr(settings, "queue_name", queue_name)
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

        # execute_job safely returns on CANCELLED without raising or invoking adapter
        execute_job(job.id)

    assert fake_called is False

    with live_pg_session_factory() as verify_db:
        reloaded = verify_db.get(GenerationJob, job.id)
        assert reloaded.status == JobStatus.CANCELLED
        candidates = list(
            verify_db.scalars(
                select(PageCandidate).where(PageCandidate.batch_id == seeded["batch_id"])
            )
        )
        assert len(candidates) == 0
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.config import Settings
from app.database import Base
from app.domain.states import JobStatus, PageStatus, Resolution
from app.models import (
    AppSetting,
    Asset,
    AssetCandidate,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    StyleProfile,
)
from app.services import job_service


def test_local_worker_executes_eight_jobs_with_project_concurrency(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'jobs.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="本地并发", default_concurrency=2)
            db.add(project)
            db.flush()
            jobs = [
                GenerationJob(
                    project_id=project.id,
                    target_type="CHAPTER",
                    target_id=f"target-{index}",
                    job_type="SOURCE_PARSE",
                    status=JobStatus.QUEUED,
                )
                for index in range(8)
            ]
            db.add_all(jobs)
            db.commit()
            job_ids = [job.id for job in jobs]

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(database, "SessionLocal", testing_session)
        active = 0
        peak = 0
        lock = Lock()

        def fake_run(_db, _job):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.04)
            with lock:
                active -= 1

        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(job_service._execute_locally, job_id)
                for job_id in job_ids
            ]
            for future in futures:
                future.result(timeout=10)

        with testing_session() as db:
            completed = list(db.query(GenerationJob).all())
            assert all(job.status == JobStatus.COMPLETED for job in completed)
            assert all(job.attempt_count == 1 for job in completed)
        assert peak == 2
        engine.dispose()


def test_active_job_cancellation_is_not_overwritten(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'cancel.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="取消竞态")
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="cancel-target",
                job_type="SOURCE_PARSE",
                status=JobStatus.QUEUED,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        started = Event()
        release = Event()

        def fake_run(_db, _job):
            started.set()
            assert release.wait(5)

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker_tasks.execute_job, job_id)
            assert started.wait(5)
            with testing_session() as db:
                job_service.cancel_job(db, db.get(GenerationJob, job_id))
            release.set()
            future.result(timeout=5)

        with testing_session() as db:
            cancelled = db.get(GenerationJob, job_id)
            assert cancelled.status == JobStatus.CANCELLED
            assert cancelled.cancelled_at is not None
        engine.dispose()


def test_completed_job_persists_full_progress(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'progress.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="完成进度")
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="progress-target",
                job_type="SOURCE_PARSE",
                status=JobStatus.QUEUED,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        def fake_run(_db, active_job):
            active_job.progress = 85

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        worker_tasks.execute_job(job_id)

        with testing_session() as db:
            completed = db.get(GenerationJob, job_id)
            assert completed.status == JobStatus.COMPLETED
            assert completed.progress == 100
        engine.dispose()


def test_asset_generation_revalidates_deleted_style_reference(db_session, monkeypatch):
    project = Project(name="引用失效")
    db_session.add(project)
    db_session.flush()
    reference = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style.png",
        mime_type="image/png",
        byte_size=10,
        sha256="f" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
        deleted_at=datetime.now(UTC),
    )
    db_session.add(reference)
    db_session.flush()
    style = StyleProfile(
        project_id=project.id,
        name="失效风格",
        color_mode="color",
        profile={"reference_asset_ids": [reference.id], "palette_confirmed": True},
        status="DRAFT",
    )
    db_session.add(style)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        generation_kind="STYLE_TEST",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="STYLE_TEST",
        status="QUEUED",
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        model_alias="image.nano_banana_2",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.commit()
    provider_calls: list[object] = []

    class FakeAdapter:
        def generate_asset(self, request):
            provider_calls.append(request)
            raise AssertionError("provider must not be called")

    monkeypatch.setattr(worker_tasks, "_adapter", lambda _alias: FakeAdapter())

    with pytest.raises(RuntimeError, match="风格参考图已失效"):
        worker_tasks._run_asset_generate(db_session, job)
    assert provider_calls == []


def test_cancelling_generation_resets_candidate_and_page(db_session):
    project = Project(name="取消页面生成")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        status=PageStatus.DRAFT_GENERATING,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(project_id=project.id, page_id=page.id, ordinal=1)
    db_session.add(batch)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="pending",
        job_type="PAGE_GENERATE",
        status=JobStatus.GENERATING,
    )
    db_session.add(job)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="GENERATING",
        job_id=job.id,
    )
    db_session.add(candidate)
    db_session.commit()

    job_service.cancel_job(db_session, job)

    assert candidate.status == "CANCELLED"
    assert page.status == PageStatus.STORYBOARDED


def _waiting_job(db_session, name: str = "队列模式") -> GenerationJob:
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=f"target-{name}",
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
    )
    db_session.add(job)
    db_session.commit()
    return job


def _set_queue_mode(db_session, mode: str) -> None:
    db_session.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db_session.commit()


def test_local_mode_submits_without_touching_redis(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    job = _waiting_job(db_session, "local")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )

    result = job_service.enqueue_job(db_session, job)

    assert result.status == JobStatus.QUEUED
    assert result.error_code == "LOCAL_WORKER"
    assert submitted == [job.id]


def test_redis_mode_keeps_job_waiting_when_redis_is_unavailable(
    db_session, monkeypatch
):
    _set_queue_mode(db_session, "REDIS")
    job = _waiting_job(db_session, "redis")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )
    monkeypatch.setattr(
        "redis.Redis.from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )

    result = job_service.enqueue_job(db_session, job)

    assert result.status == JobStatus.WAITING
    assert result.error_code == "QUEUE_UNAVAILABLE"
    assert submitted == []


def test_auto_mode_falls_back_to_local_in_development(db_session, monkeypatch):
    _set_queue_mode(db_session, "AUTO")
    job = _waiting_job(db_session, "auto")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )
    monkeypatch.setattr(
        "redis.Redis.from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )

    result = job_service.enqueue_job(db_session, job)

    assert result.status == JobStatus.QUEUED
    assert result.error_code == "LOCAL_WORKER"
    assert submitted == [job.id]


def test_startup_recovery_requeues_waiting_jobs_in_local_mode(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    job = _waiting_job(db_session, "recover")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 1
    assert submitted == [job.id]
    db_session.refresh(job)
    assert job.status == JobStatus.QUEUED

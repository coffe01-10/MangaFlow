import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.config import Settings
from app.database import Base
from app.domain.states import JobStatus
from app.models import AppSetting, GenerationJob, Project
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

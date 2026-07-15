import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.database import Base
from app.domain.states import JobStatus
from app.models import GenerationJob, Project
from app.services import job_service


def test_local_worker_executes_eight_jobs_with_project_concurrency(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'jobs.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
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
            futures = [executor.submit(job_service._execute_locally, job_id) for job_id in job_ids]
            for future in futures:
                future.result(timeout=10)

        with testing_session() as db:
            completed = list(db.query(GenerationJob).all())
            assert all(job.status == JobStatus.COMPLETED for job in completed)
            assert all(job.attempt_count == 1 for job in completed)
        assert peak == 2
        engine.dispose()

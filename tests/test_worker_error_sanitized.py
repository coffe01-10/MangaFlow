"""Regression: unclassified worker exceptions persist sanitized messages.

The generic ``except Exception`` branch of ``execute_job`` used to store
``str(error)`` as the user-visible ``error_message`` — exactly the surface
the codebase's own sanitization rule (``_begin_or_fail``) reserves for raw
driver text like SQL, local paths and URLs. The raw text now stays in the
re-raised exception chain for logs only.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import worker_tasks
from app.database import Base
from app.models import GenerationJob, Project


SENTINEL = "SECRET postgresql://user:pass@db.internal:5432/prod"


def test_generic_exception_message_is_sanitized(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)

    with testing_session() as db:
        project = Project(name="sanitize-worker-error")
        db.add(project)
        db.flush()
        job = GenerationJob(
            project_id=project.id,
            target_type="CHAPTER",
            target_id="chapter-1",
            job_type="SOURCE_PARSE",
            status="QUEUED",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    def exploding_handler(_db, _job):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(worker_tasks, "_run_story_parse", exploding_handler)

    with pytest.raises(RuntimeError, match="SECRET postgresql://"):
        worker_tasks.execute_job(job_id)

    with testing_session() as db:
        loaded = db.get(GenerationJob, job_id)
        # retryable=True requeues the job for another attempt.
        assert loaded.status == "WAITING"
        assert loaded.error_code == "WORKER_ERROR"
        assert SENTINEL not in (loaded.error_message or "")
        assert loaded.error_message == "模型任务出现未分类异常，已记录诊断日志"

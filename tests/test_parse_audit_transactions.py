"""Parse workers must release their JOB lock before autonomous audit writes.

The offline case checks durable pre-call progress on separate connections.
The same scenario runs against PostgreSQL in the opt-in integration suite,
where the audit INSERT's FK lock conflicts with an unreleased FOR UPDATE.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app import worker_tasks
from app.database import Base
from app.domain.states import JobStatus
from app.models import (
    Chapter,
    DirectorCommandGroup,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Project,
    SourceRevision,
    SourceSegment,
)
from app.services.ai_schemas import BeatDraft, DirectorParseOutput, SceneDraft, StoryParseOutput
from app.services.worker_handlers import model_call_audit, provider
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


def run_parse_with_independent_audit(factory, monkeypatch, job_type):
    with factory() as db:
        project = Project(name="独立解析审计")
        db.add(project)
        db.flush()
        chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
        db.add(chapter)
        db.flush()
        params = {}
        target_id = chapter.id
        target_type = "CHAPTER"
        if job_type == "SOURCE_PARSE":
            revision = SourceRevision(
                chapter_id=chapter.id, revision=1, source_type="TEXT",
                original_text="他走进房间。", sha256="a" * 64, character_count=7,
            )
            db.add(revision)
            db.flush()
            chapter.current_source_revision_id = revision.id
            db.add(SourceSegment(
                source_revision_id=revision.id, ordinal=1, text=revision.original_text,
                start_offset=0, end_offset=7, sha256="a" * 64,
            ))
        else:
            page = MangaPage(chapter_id=chapter.id, page_number=1)
            db.add(page)
            db.flush()
            group = DirectorCommandGroup(
                project_id=project.id, page_id=page.id,
                command_group_id=str(uuid4()), status="PARSING",
            )
            db.add(group)
            db.flush()
            target_id = group.id
            target_type = "DIRECTOR_COMMAND_GROUP"
            params = {
                "command_group_id": group.command_group_id, "page_id": page.id,
                "storyboard_version": page.storyboard_version, "utterance": "修改镜头",
            }
        job = GenerationJob(
            project_id=project.id, target_type=target_type, target_id=target_id,
            job_type=job_type, status=JobStatus.QUEUED, request_parameters=params,
        )
        db.add(job)
        db.commit()
        job_id = job.id

    calls = []

    class FakeAdapter:
        def generate_structured(self, request, schema):
            # These reads use another connection, not the handler's identity
            # map or uncommitted state. The real audit begin has already run.
            with factory() as observer:
                assert observer.get(GenerationJob, job_id).status == JobStatus.GENERATING
                attempt = observer.scalar(select(ModelCallAttempt).where(
                    ModelCallAttempt.job_id == job_id,
                ))
                assert attempt is not None and attempt.outcome is None
            calls.append(request)
            if schema is DirectorParseOutput:
                return DirectorParseOutput(commands=[], clarifications=[], unsupported=[])
            segments = json.loads(request.prompt.rsplit("输入：", 1)[1])
            ids = [segment["id"] for segment in segments]
            return StoryParseOutput(characters=[], scenes=[SceneDraft(
                ordinal=1, location="房间", purpose="推进", source_segment_ids=ids,
                beats=[BeatDraft(ordinal=1, action="走进房间", source_segment_ids=ids)],
            )])

    binding = SimpleNamespace(
        adapter=FakeAdapter(), selected_key=None,
        resolved=SimpleNamespace(
            model=SimpleNamespace(id=None, provider_model_id="offline-text"),
            provider=SimpleNamespace(preset_key="offline", name="offline"),
            connection=SimpleNamespace(id=None, protocol="HTTP_API"),
            route_reason=None, route_score=None,
        ),
    )
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    monkeypatch.setattr(model_call_audit, "SessionLocal", factory)
    monkeypatch.setattr(provider, "_binding", lambda *args, **kwargs: binding)
    worker_tasks.execute_job(job_id)
    with factory() as db:
        completed = db.get(GenerationJob, job_id)
        assert completed.status == JobStatus.COMPLETED, completed.error_message
        attempt = db.scalar(select(ModelCallAttempt).where(ModelCallAttempt.job_id == job_id))
        assert attempt.outcome == "SUCCEEDED"
    assert len(calls) == 1


@pytest.mark.parametrize("job_type", ["SOURCE_PARSE", "DIRECTOR_PARSE"])
def test_parse_audit_uses_committed_job_state(tmp_path, monkeypatch, job_type):
    path = tmp_path / "parse-audit.sqlite"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        run_parse_with_independent_audit(factory, monkeypatch, job_type)
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)

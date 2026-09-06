"""Worker-side deleted-chapter guards for paid jobs.

delete_chapter is a soft delete with no active-job 409 (unlike delete_script)
and cancels nothing, so a queued PAGE_GENERATE / PAGE_INSPECT / SOURCE_PARSE
must fence itself against the deleted chapter before dispatching the provider
call — the same authoritative-backstop pattern the deleted-candidate guards
use. Chapter deletion bumps only chapter.version, so neither the page
storyboard fence nor the page-version baseline notices.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.states import JobStatus
from app.model_adapters.base import ProviderAdapterError
from app.models import Chapter, GenerationJob, PageCandidate
from app.services.ai_schemas import PageInspectionOutput
from app.services.worker_handlers import provider
from app.services.worker_handlers.execution import JobCancelledError
from app.services.worker_handlers.inspection import _run_inspection
from test_deleted_candidate_worker import _seed_page_job, _worker_database
from test_inspect_and_parse_guards import _ready_candidate

INSPECT_CATEGORIES = [
    "SPEAKER",
    "CHARACTER",
    "OUTFIT",
    "PROP",
    "CONTINUITY",
    "PRESENCE",
]


def _delete_chapter(factory, chapter_id: str) -> None:
    with factory() as db:
        chapter = db.get(Chapter, chapter_id)
        chapter.deleted_at = datetime.now(UTC)
        db.commit()


def _chapter_id_of(factory, candidate_id: str) -> str:
    with factory() as db:
        candidate = db.get(PageCandidate, candidate_id)
        from app.models import MangaPage

        page = db.get(MangaPage, candidate.page_id)
        return page.chapter_id


def test_page_generate_on_deleted_chapter_cancels_without_paid_call(monkeypatch):
    """A chapter soft-deleted after its page job was enqueued must end the job
    CANCELLED before the provider is invoked; the candidate never goes READY."""

    from app import worker_tasks

    with _worker_database(monkeypatch) as factory:
        job_id, candidate_id = _seed_page_job(factory)
        _delete_chapter(factory, _chapter_id_of(factory, candidate_id))

        provider_calls: list[object] = []

        class ForbiddenAdapter:
            def generate_page(self, request):
                provider_calls.append(request)
                raise AssertionError("已删除章节不得调用模型")

        monkeypatch.setattr(worker_tasks, "_adapter", lambda alias: ForbiddenAdapter())
        worker_tasks.execute_job(job_id)

        with factory() as db:
            job = db.get(GenerationJob, job_id)
            assert job.status == JobStatus.CANCELLED
            assert job.cancelled_at is not None
            assert provider_calls == []
            candidate = db.get(PageCandidate, candidate_id)
            assert candidate.status != "READY"
            assert candidate.asset_id is None


def _leased_inspect_job(db, project, candidate):
    from app.models import GenerationJob, utcnow

    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.PREPARING,
        attempt_count=1,
        request_parameters={"categories": INSPECT_CATEGORIES},
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
        lease_owner="offline-owner",
        lease_expires_at=utcnow() + __import__("datetime", fromlist=["timedelta"]).timedelta(minutes=5),
    )
    db.add(job)
    db.commit()
    return job


def test_inspection_on_deleted_chapter_cancels_without_paid_call(
    db_session, monkeypatch
):
    """Same backstop for PAGE_INSPECT: the chapter is resolved through the
    page, and a deleted chapter must cancel the job before the multimodal
    dispatch."""

    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    job = _leased_inspect_job(db_session, project, candidate)
    chapter = db_session.get(Chapter, _page.chapter_id)
    chapter.deleted_at = datetime.now(UTC)
    db_session.commit()

    calls: list = []

    def fake_invoke(_db, _binding, _callback):
        calls.append(1)
        return PageInspectionOutput.model_validate(
            {
                "items": [
                    {
                        "category": category,
                        "outcome": "PASS",
                        "details": {"expected": "offline", "observed": "offline"},
                    }
                    for category in INSPECT_CATEGORIES
                ]
            }
        )

    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    monkeypatch.setattr(provider, "_invoke_provider", fake_invoke)

    with pytest.raises(JobCancelledError):
        _run_inspection(db_session, job)
    assert calls == []


def test_story_parse_on_deleted_chapter_fails_before_any_chunk_call(
    client, db_session, monkeypatch
):
    """A queued SOURCE_PARSE whose chapter was deleted must fail CHAPTER_DELETED
    before the first paid chunk — the pre-fix check only ran after the entire
    chunked loop had already paid for every chunk."""

    from app.models import GenerationJob
    from app.services.worker_handlers.story_parse import _run_story_parse

    project = client.post("/api/v1/projects", json={"name": "章节删除解析守卫"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    assert client.delete(f"/api/v1/chapters/{chapter_id}").status_code == 204

    def forbid_binding(*_args, **_kwargs):
        raise AssertionError("已删除章节不得发起分块模型调用")

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding", forbid_binding
    )
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, job)
    assert excinfo.value.code == "CHAPTER_DELETED"
    assert excinfo.value.retryable is False

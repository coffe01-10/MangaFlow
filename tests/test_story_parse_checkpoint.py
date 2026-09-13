"""Regression: SOURCE_PARSE retries must resume from the paid prefix (#646).

``chunk_outputs`` lived only in the attempt's memory, so a transient chunk
failure (TIMEOUT) put the job back in WAITING and every remaining attempt
re-ran from chunk 1 — an 8-chunk chapter failing at chunk 5 re-billed chunks
1-4 on both retries. The handler now checkpoints completed chunk outputs into
``job.request_parameters["story_parse_checkpoint"]`` through the
owned-commit path (lease/cancel-guarded, same discipline as
``_commit_owned_progress``), keys them by the segments' content hash, and
resumes from the breakpoint when the key still matches.
"""

import json

import pytest
from sqlalchemy import select

from app.domain.states import JobStatus
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    Chapter,
    GenerationJob,
    ScriptRevision,
    SourceSegment,
)
from app.services.ai_schemas import (
    BeatDraft,
    SceneDraft,
    StoryParseOutput,
)
from app.services.worker_handlers.story_parse import _run_story_parse

# Three ~460-char paragraphs: 460+460 > STORY_PARSE_CHUNK_MAX_CHARS (800), so
# each paragraph is its own segment and its own paid chunk (3 chunks total).
_PARAGRAPH = "夜色压在旧城之上。" + "顾川沿着湿漉漉的石板路往前走，路灯把影子拉得很长。" * 18
THREE_CHUNK_TEXT = "\n\n".join([_PARAGRAPH, _PARAGRAPH, _PARAGRAPH])


class ChunkedFakeProvider:
    """Covers exactly the segments named in each chunk prompt; can fail one call."""

    def __init__(self, fail_on_call: int | None = None):
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.prompts: list[str] = []

    def generate_structured(self, request, schema):
        self.calls += 1
        self.prompts.append(request.prompt)
        if self.calls == self.fail_on_call:
            raise ProviderAdapterError("TIMEOUT", "模拟瞬时超时", retryable=True)
        payload = json.loads(request.prompt.rsplit("输入：", 1)[1])
        segment_ids = [item["id"] for item in payload]
        return StoryParseOutput(
            characters=[],
            scenes=[
                SceneDraft(
                    ordinal=1,
                    location="旧城",
                    purpose="推进",
                    source_segment_ids=segment_ids,
                    beats=[
                        BeatDraft(
                            ordinal=index,
                            action="推进剧情",
                            speaker_name="",
                            dialogue="",
                            source_segment_ids=[segment_id],
                        )
                        for index, segment_id in enumerate(segment_ids, 1)
                    ],
                )
            ],
        )


def _seed_chapter(client, db_session) -> tuple[str, Chapter]:
    project = client.post("/api/v1/projects", json={"name": "断点续跑"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": THREE_CHUNK_TEXT},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    return project["id"], db_session.get(Chapter, chapter_id)


def _parse_job(db_session, project_id: str, chapter_id: str) -> GenerationJob:
    job = GenerationJob(
        project_id=project_id,
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.commit()
    return job


def _segments(db_session, chapter: Chapter) -> list[SourceSegment]:
    return list(
        db_session.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == chapter.current_source_revision_id)
            .order_by(SourceSegment.ordinal)
        )
    )


def test_retry_resumes_from_checkpoint_without_rebilling_prefix(
    client, db_session, monkeypatch
):
    project_id, chapter = _seed_chapter(client, db_session)
    assert len(_segments(db_session, chapter)) == 3
    job = _parse_job(db_session, project_id, chapter.id)
    provider = ChunkedFakeProvider(fail_on_call=3)
    monkeypatch.setattr(
        "app.worker_tasks._adapter", lambda alias: provider
    )

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, job)
    assert excinfo.value.code == "TIMEOUT"
    # First attempt paid for chunks 1 and 2, then chunk 3 failed.
    assert provider.calls == 3

    # The paid prefix is durably checkpointed on the job row (committed, not
    # just in-memory) via the owned-commit path.
    db_session.expire_all()
    stored = db_session.get(GenerationJob, job.id).request_parameters[
        "story_parse_checkpoint"
    ]
    assert stored["completed_chunks"] == 2
    assert len(stored["outputs"]) == 2

    # Worker retry: reclaim into an active status and rerun the handler.
    db_session.expire_all()
    job = db_session.get(GenerationJob, job.id)
    job.status = JobStatus.WAITING
    db_session.commit()
    job.status = JobStatus.PREPARING
    db_session.commit()

    _run_story_parse(db_session, job)
    db_session.commit()

    # Only the failed chunk 3 was billed again: 3 + 1, not 3 + 3.
    assert provider.calls == 4
    assert "这是连续片段 3/3" in provider.prompts[-1]
    assert "这是连续片段 1/3" not in provider.prompts[3]

    script = db_session.scalar(
        select(ScriptRevision).where(ScriptRevision.chapter_id == chapter.id)
    )
    assert script is not None and script.status == "READY"
    assert script.coverage["covered"] == script.coverage["expected"] == 3
    assert db_session.get(Chapter, chapter.id).status == "SCRIPT_READY"


def test_checkpoint_is_ignored_when_segments_changed(
    client, db_session, monkeypatch
):
    project_id, chapter = _seed_chapter(client, db_session)
    job = _parse_job(db_session, project_id, chapter.id)
    provider = ChunkedFakeProvider(fail_on_call=2)
    monkeypatch.setattr(
        "app.worker_tasks._adapter", lambda alias: provider
    )

    with pytest.raises(ProviderAdapterError):
        _run_story_parse(db_session, job)
    assert provider.calls == 2
    db_session.expire_all()
    assert (
        db_session.get(GenerationJob, job.id).request_parameters[
            "story_parse_checkpoint"
        ]["completed_chunks"]
        == 1
    )

    # The source text is edited between attempts: the checkpoint key no longer
    # matches, so the retry must start from chunk 1 and pay for all chunks.
    segment = _segments(db_session, chapter)[2]
    segment.text = segment.text + "他忽然停下了脚步。"
    db_session.commit()

    db_session.expire_all()
    job = db_session.get(GenerationJob, job.id)
    job.status = JobStatus.WAITING
    db_session.commit()
    job.status = JobStatus.PREPARING
    db_session.commit()

    _run_story_parse(db_session, job)
    db_session.commit()

    assert provider.calls == 2 + 3
    script = db_session.scalar(
        select(ScriptRevision).where(ScriptRevision.chapter_id == chapter.id)
    )
    assert script is not None and script.status == "READY"

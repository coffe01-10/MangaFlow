"""Run-time SOURCE_PARSE claim arbitration (#124, liveness half).

The route and workflow entries guard duplicates before enqueue, but jobs made
through disjoint idempotency-key namespaces can both be claimed in the same
window — and the handler-level guard used to be symmetric: each claimant saw
the other ACTIVE and failed terminally, so the chapter got ZERO parses (safe,
but a liveness hole). The loser is now deterministic: only the OLDEST active
parse (created_at, tie-break id) proceeds; every younger claimant still fails
SOURCE_PARSE_CONFLICT before any paid call.
"""

from datetime import timedelta

import pytest

from app.domain.states import JobStatus
from app.model_adapters.base import ProviderAdapterError
from app.models import GenerationJob, utcnow
from app.services.job_service import oldest_active_job_id
from app.services.worker_handlers.story_parse import _run_story_parse


def _imported_chapter(client, project_name: str) -> tuple[dict, str]:
    project = client.post("/api/v1/projects", json={"name": project_name}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    )
    assert imported.status_code == 201, imported.text
    return project, imported.json()["chapters"][0]["id"]


def _forbid_paid_calls(monkeypatch) -> None:
    """Any claimant that passes the guard must stop at the binding stage —
    proving it proceeded without a real provider call in this suite."""

    def forbid_paid_call(*_args, **_kwargs):
        raise AssertionError("本测试禁止发起付费模型调用")

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding",
        forbid_paid_call,
    )


def test_same_window_parse_claims_let_exactly_the_oldest_proceed(
    client, db_session, monkeypatch
):
    project, chapter_id = _imported_chapter(client, "同窗解析仅最老放行")
    older = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
        created_at=utcnow() - timedelta(seconds=30),
    )
    younger = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
    )
    db_session.add_all([older, younger])
    db_session.commit()
    assert (
        oldest_active_job_id(
            db_session,
            job_type="SOURCE_PARSE",
            target_id=chapter_id,
            target_type="CHAPTER",
        )
        == older.id
    )
    _forbid_paid_calls(monkeypatch)

    # The older claim passes the conflict guard and only stops at the (here
    # forbidden) provider binding: exactly one claimant proceeds.
    with pytest.raises(AssertionError, match="付费"):
        _run_story_parse(db_session, older)

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, younger)
    assert excinfo.value.code == "SOURCE_PARSE_CONFLICT"
    assert excinfo.value.retryable is False


def test_parse_claim_tie_break_prefers_smaller_job_id(client, db_session, monkeypatch):
    """Identical created_at (bulk-enqueued in one transaction): the id ordering
    still names exactly one winner instead of letting both claimants lose."""

    project, chapter_id = _imported_chapter(client, "同刻解析按id决胜")
    same_instant = utcnow()
    first = GenerationJob(
        id="parse-claim-a",
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
        created_at=same_instant,
    )
    second = GenerationJob(
        id="parse-claim-b",
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
        created_at=same_instant,
    )
    db_session.add_all([first, second])
    db_session.commit()
    assert (
        oldest_active_job_id(
            db_session,
            job_type="SOURCE_PARSE",
            target_id=chapter_id,
            target_type="CHAPTER",
        )
        == first.id
    )
    _forbid_paid_calls(monkeypatch)

    with pytest.raises(AssertionError, match="付费"):
        _run_story_parse(db_session, first)
    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, second)
    assert excinfo.value.code == "SOURCE_PARSE_CONFLICT"
    assert excinfo.value.retryable is False

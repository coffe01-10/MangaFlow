"""Inspection worker integrity around soft-deleted candidates and partial
model responses.

I-1: a PAGE_INSPECT job whose target candidate was soft-deleted after enqueue
used to run the paid multimodal call and commit verdicts onto the tombstoned
row (the sibling generate handlers guard; the inspection handler did not).
I-2: an incomplete re-inspect (model omits a requested category) used to
downgrade an INSPECTED candidate to READY and flip the adopted page's
PASSED to NOT_CHECKED while leaving page.status FINAL_READY — a status pair
no other writer produces — even though the merged verdict set at the same
storyboard version was still complete.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.states import JobStatus, PageStatus
from app.models import GenerationJob, utcnow
from app.services.ai_schemas import PageInspectionOutput
from app.services.worker_handlers import provider
from app.services.worker_handlers.execution import JobCancelledError
from app.services.worker_handlers.inspection import _run_inspection
from test_inspect_and_parse_guards import _adopt_candidate, _ready_candidate

INSPECT_CATEGORIES = [
    "SPEAKER",
    "CHARACTER",
    "OUTFIT",
    "PROP",
    "CONTINUITY",
    "PRESENCE",
]


def _leased_inspect_job(db, project, candidate, *, seq=1) -> GenerationJob:
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.PREPARING,
        attempt_count=1,
        request_parameters={"categories": INSPECT_CATEGORIES},
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}:{seq}",
        lease_owner="offline-owner",
        lease_expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(job)
    db.commit()
    return job


def _inspection_output(categories: list[str]) -> PageInspectionOutput:
    return PageInspectionOutput.model_validate(
        {
            "items": [
                {
                    "category": category,
                    "outcome": "PASS",
                    "details": {"expected": "offline", "observed": "offline"},
                }
                for category in categories
            ]
        }
    )


def _seam_provider(db, monkeypatch, output: PageInspectionOutput, calls: list):
    def fake_invoke(_db, _binding, _callback):
        calls.append(1)
        return output

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


def test_inspect_of_deleted_candidate_never_takes_the_paid_call(
    db_session, monkeypatch
):
    """I-1: the soft-delete landed after the inspect job was enqueued; the
    handler must raise the shell's cancellation error before dispatching the
    multimodal call, not run it against the tombstoned row."""
    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    candidate.deleted_at = utcnow()
    db_session.commit()
    job = _leased_inspect_job(db_session, project, candidate)

    calls: list = []
    _seam_provider(db_session, monkeypatch, _inspection_output(INSPECT_CATEGORIES), calls)

    with pytest.raises(JobCancelledError):
        _run_inspection(db_session, job)
    assert calls == []


def test_incomplete_reinspect_keeps_prior_complete_verdicts(db_session, monkeypatch):
    """I-2: first run is a full pass (candidate INSPECTED, adopted page
    FINAL_READY/PASSED). A re-inspect whose model response omits one requested
    category must not downgrade the candidate or flip the page to
    FINAL_READY+NOT_CHECKED: the merged verdict set at this storyboard version
    is still complete."""
    project, page, candidate, _generate_job = _ready_candidate(db_session)
    _adopt_candidate(page, candidate)
    page.selected_candidate_id = candidate.id
    candidate.is_selected = True
    db_session.commit()

    first_job = _leased_inspect_job(db_session, project, candidate)
    calls: list = []
    _seam_provider(db_session, monkeypatch, _inspection_output(INSPECT_CATEGORIES), calls)
    _run_inspection(db_session, first_job)
    # The handler deliberately leaves the commit to the execute_job shell;
    # commit here so the expire below reads the terminal state.
    db_session.commit()
    db_session.expire(candidate)
    db_session.expire(page)
    assert candidate.status == "INSPECTED"
    assert page.continuity_status == "PASSED"
    assert page.status == PageStatus.FINAL_READY

    # Re-inspect; this run's model response omits PROP.
    second_job = _leased_inspect_job(db_session, project, candidate, seq=2)
    partial = [category for category in INSPECT_CATEGORIES if category != "PROP"]
    second_calls: list = []
    _seam_provider(db_session, monkeypatch, _inspection_output(partial), second_calls)
    _run_inspection(db_session, second_job)
    db_session.commit()
    db_session.expire(candidate)
    db_session.expire(page)
    assert candidate.status == "INSPECTED"
    assert page.continuity_status == "PASSED"
    assert page.status == PageStatus.FINAL_READY


def test_delete_committed_during_call_discards_verdicts(db_session, monkeypatch):
    """The post-call backstop: a soft-delete committing between the provider
    response and the completion writes must cancel the inspection — the
    verdicts were only flushed, so the shell's rollback discards them and the
    job stamps CANCELLED instead of writing INSPECTED onto the tombstone."""
    from sqlalchemy import select

    from app.models import InspectionResult

    project, _page, candidate, _generate_job = _ready_candidate(db_session)
    job = _leased_inspect_job(db_session, project, candidate)

    calls: list = []

    def tombstoning_invoke(_db, _binding, _callback):
        calls.append(1)
        candidate.deleted_at = utcnow()
        db_session.commit()
        return _inspection_output(INSPECT_CATEGORIES)

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
    monkeypatch.setattr(provider, "_invoke_provider", tombstoning_invoke)

    with pytest.raises(JobCancelledError):
        _run_inspection(db_session, job)

    # Exactly one provider dispatch happened; the shell's rollback then
    # discards the flushed verdicts.
    assert calls == [1]
    db_session.rollback()
    persisted = db_session.scalars(
        select(InspectionResult).where(InspectionResult.candidate_id == candidate.id)
    )
    assert list(persisted) == []

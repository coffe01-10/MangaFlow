"""Race guards around the workflow engine's claim paths.

Three verified review findings, each reproduced by forcing the concurrent
writer to land between the guard's read and its commit (the
``_steal_lease_then_succeed`` pattern from the lease-reclaim fence tests):

- reset_for_retry's revival was not claim-guarded: after the initial job-row
  CAS a concurrent cancel_job (whose run read is FAILED, so cancel_run's
  terminal claim no-ops) claims the very WAITING row the retry created via
  mark_job_cancelled; committing the run/node revival on top left a zombie
  RUNNING run with a CANCELLED node. The pre-commit re-verify must roll the
  whole revival back with the stale-claim 409.
- _candidate_for_run picked "most recent" via reversed() of an unordered
  SELECT; the adopted/selected path (control.approval) must win regardless of
  row order.
- approve_node's APPROVE barrier completion was not node-CAS'd: the run claim
  accepts RUNNING→RUNNING, so two concurrent approves both wrote
  node_run COMPLETED. The loser must lose on the node row itself.

The queue stays disabled throughout — no provider call can run.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services import job_service
from app.services.workflow_engine import approve_node
from app.services.workflow_engine import lifecycle as workflow_lifecycle
from app.services.workflow_engine.catalog import _node, graph_checksum
from app.services.workflow_engine.scope import _candidate_for_run


def _session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)


def _definition_and_version(db, project: Project, name: str, graph: dict):
    workflow = WorkflowDefinition(project_id=project.id, name=name, draft_graph=graph)
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph,
        graph_checksum=graph_checksum(graph),
        validation_report={"valid": True},
    )
    db.add(version)
    db.flush()
    return workflow, version


def test_reset_for_retry_revival_is_claim_guarded(db_session, monkeypatch):
    """Retry holds the WAITING claim; a concurrent cancel_job reads the run
    FAILED (cancel_run no-ops) and mark_job_cancelled claims the revived row
    between the claim and the commit. The revival must not commit on top —
    no zombie RUNNING run with a CANCELLED node — and the caller sees the
    stale-claim 409."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = Project(name="重试复活围栏")
    db_session.add(project)
    db_session.flush()
    graph = {"schema_version": 2, "nodes": [], "edges": []}
    _, version = _definition_and_version(db_session, project, "重试围栏流程", graph)
    run = WorkflowRun(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id="scope-retry-guard",
        status="FAILED",
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="inspect",
        node_type="quality.inspect",
        status="FAILED",
        output_refs={},
    )
    db_session.add(node_run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="candidate-retry-guard",
        job_type="PAGE_INSPECT",
        status=JobStatus.FAILED,
        error_code="UPSTREAM",
        request_parameters={"workflow_run_id": run.id},
    )
    db_session.add(job)
    db_session.flush()
    node_run.job_id = job.id
    db_session.commit()
    job_id, run_id, node_run_id = job.id, run.id, node_run.id

    factory = _session_factory(db_session)
    real_has_active_job = job_service.has_active_job

    def _cancel_between_claim_and_commit(db, **kwargs):
        # The concurrent cancel_job, running after the retry's CAS moved the
        # row back to WAITING but before its commit: the run read is FAILED so
        # cancel_run's terminal claim no-ops and mark_job_cancelled claims the
        # WAITING row instead (visible on the test's shared connection).
        with factory() as other:
            job_service.cancel_job(other, other.get(GenerationJob, job_id))
        return real_has_active_job(db, **kwargs)

    monkeypatch.setattr(job_service, "has_active_job", _cancel_between_claim_and_commit)

    with pytest.raises(HTTPException) as exc_info:
        job_service.reset_for_retry(db_session, db_session.get(GenerationJob, job_id))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "任务状态已变化，请刷新后重试"
    db_session.expire_all()
    # The revival was rolled back: the run stays FAILED (not a zombie RUNNING
    # run) and the node stays FAILED while the cancel owns the job row.
    assert db_session.get(WorkflowRun, run_id).status == "FAILED"
    assert db_session.get(WorkflowNodeRun, node_run_id).status == "FAILED"
    cancelled = db_session.get(GenerationJob, job_id)
    assert cancelled.status == JobStatus.CANCELLED
    assert cancelled.cancelled_at is not None


def _page_with_candidates(
    db, name: str
) -> tuple[Project, MangaPage, PageCandidate, PageCandidate]:
    project = Project(name=name)
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db.add(page)
    db.flush()
    generated = PageCandidate(
        batch_id=_batch(db, project, chapter, page, ordinal=1).id,
        page_id=page.id,
        ordinal=1,
        model_alias="draft",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        # #223: CURRENT stamps keep the approve path's currency gate green so
        # the race assertions below still exercise the node claim itself.
        based_on_storyboard_version=page.storyboard_version,
    )
    adopted = PageCandidate(
        batch_id=_batch(db, project, chapter, page, ordinal=2).id,
        page_id=page.id,
        ordinal=1,
        model_alias="final",
        resolution=Resolution.STANDARD_2K,
        status="READY",
        is_selected=True,
        based_on_storyboard_version=page.storyboard_version,
    )
    db.add_all([generated, adopted])
    db.flush()
    page.selected_candidate_id = adopted.id
    db.commit()
    return project, page, generated, adopted


def _batch(db, project, chapter, page, *, ordinal: int) -> GenerationBatch:
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=ordinal,
    )
    db.add(batch)
    db.flush()
    return batch


@pytest.mark.parametrize("approval_first", [True, False])
def test_candidate_for_run_prefers_adopted_over_generated(db_session, approval_first):
    """Both a generator.page run and a control.approval run recorded a
    candidate_id; the adopted candidate (the approval barrier's record — the
    writer of the adoption fact) must win regardless of insertion order."""

    project, page, generated, adopted = _page_with_candidates(db_session, "候选选择确定性")
    _, version = _definition_and_version(
        db_session,
        project,
        "候选选择流程",
        {"schema_version": 2, "nodes": [], "edges": []},
    )
    run = WorkflowRun(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status="RUNNING",
    )
    db_session.add(run)
    db_session.commit()
    generator_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="generate",
        node_type="generator.page",
        status="COMPLETED",
        output_refs={"candidate_id": generated.id},
        started_at=datetime.now(UTC) - timedelta(minutes=2),
        finished_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    approval_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="adopt",
        node_type="control.approval",
        status="COMPLETED",
        output_refs={"candidate_id": adopted.id, "page_id": page.id},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    node_runs = (
        [approval_run, generator_run] if approval_first else [generator_run, approval_run]
    )

    candidate = _candidate_for_run(db_session, run, node_runs)

    assert candidate is not None
    assert candidate.id == adopted.id


def test_candidate_for_run_orders_same_tier_by_started_at(db_session):
    """Without an approval record the latest generator run wins by the
    monotonic (started_at, id) order — not by accidental row order."""

    project, page, first, second = _page_with_candidates(db_session, "候选同层排序")
    _, version = _definition_and_version(
        db_session,
        project,
        "候选排序流程",
        {"schema_version": 2, "nodes": [], "edges": []},
    )
    run = WorkflowRun(
        workflow_id=version.workflow_id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status="RUNNING",
    )
    db_session.add(run)
    db_session.commit()
    early = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="generate_1",
        node_type="generator.page",
        status="COMPLETED",
        output_refs={"candidate_id": first.id},
        started_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    late = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="generate_2",
        node_type="generator.page",
        status="COMPLETED",
        output_refs={"candidate_id": second.id},
        started_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    for node_runs in ([early, late], [late, early]):
        candidate = _candidate_for_run(db_session, run, node_runs)
        assert candidate is not None
        assert candidate.id == second.id


def test_concurrent_approve_loses_on_node_claim(db_session, monkeypatch):
    """Two concurrent APPROVE-barrier approvals both passed the
    WAITING_APPROVAL read check (the run claim accepts RUNNING→RUNNING, so it
    cannot fence them): the loser must no-op on the node-row CAS instead of
    writing a second COMPLETED transition over the winner's record."""

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, page, _, adopted = _page_with_candidates(db_session, "并发审批围栏")
    project_id = project.id
    graph = {
        "schema_version": 2,
        "nodes": [_node("adopt", "control.approval", "采用候选", 0, 0)],
        "edges": [],
    }
    workflow = WorkflowDefinition(project_id=project_id, name="审批围栏流程", draft_graph=graph)
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=graph,
        graph_checksum=graph_checksum(graph),
        validation_report={"valid": True},
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project_id,
        scope_type="PAGE",
        scope_id=page.id,
        status="PAUSED",
        started_at=utcnow(),
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="adopt",
        node_type="control.approval",
        status="WAITING_APPROVAL",
        output_refs={},
    )
    db_session.add(node_run)
    db_session.commit()
    run_id, node_run_id = run.id, node_run.id

    factory = _session_factory(db_session)
    real_graph_for_run = workflow_lifecycle._graph_for_run

    def _graph_with_concurrent_winner(db, winner_run):
        graph = real_graph_for_run(db, winner_run)
        # The winning approver commits between the loser's WAITING_APPROVAL
        # read and its own commit: node COMPLETED + run RUNNING (its run claim
        # already accepted PAUSED→RUNNING).
        with factory() as other:
            other.execute(
                update(WorkflowNodeRun)
                .where(WorkflowNodeRun.id == node_run_id)
                .values(
                    status="COMPLETED",
                    finished_at=utcnow(),
                    output_refs={
                        "candidate_id": adopted.id,
                        "page_id": page.id,
                        "winner": True,
                    },
                )
                .execution_options(synchronize_session=False)
            )
            other.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == winner_run.id)
                .values(status="RUNNING")
                .execution_options(synchronize_session=False)
            )
            other.commit()
        return graph

    monkeypatch.setattr(
        workflow_lifecycle, "_graph_for_run", _graph_with_concurrent_winner
    )

    with pytest.raises(ValueError, match="节点当前不等待人工确认"):
        approve_node(db_session, run_id, "adopt", candidate_id=adopted.id)

    db_session.expire_all()
    row = db_session.get(WorkflowNodeRun, node_run_id)
    assert row.status == "COMPLETED"
    assert row.output_refs.get("winner") is True  # the winner's record survives
    assert db_session.get(WorkflowRun, run_id).status == "RUNNING"
    # Exactly one approval won: no second COMPLETED write clobbered the row.
    assert row.output_refs == {
        "candidate_id": adopted.id,
        "page_id": page.id,
        "winner": True,
    }

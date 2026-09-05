from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.states import JobStatus
from app.models import (
    GenerationJob,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.workflow_engine.catalog import NODE_TYPE_MAP
from app.services.workflow_engine.reconciliation import get_run, reconcile_run
from app.services.workflow_engine.scope import (
    _scope_chapter,
    _scope_snapshot,
    _selected_nodes,
    _source_output_refs,
    _validate_scope,
)
from app.services.workflow_engine.validation import validate_graph
from app.workflow_schemas import (
    WorkflowGraph,
    WorkflowNodeDefinition,
)

LOGGER = logging.getLogger("mangaflow.workflow")


def create_workflow_run(
    db: Session,
    workflow: WorkflowDefinition,
    *,
    scope_type: str,
    scope_id: str | None,
    start_node_ids: list[str],
    stop_node_ids: list[str],
) -> WorkflowRun:
    # `create_job` 是模块级 monkeypatch 接缝，必须在调用时经 facade 解析。
    from app.services import workflow_engine as engine

    if not workflow.published_version_id:
        raise ValueError("请先发布工作流")
    version = db.get(WorkflowVersion, workflow.published_version_id)
    graph = WorkflowGraph.model_validate(version.graph)
    report = validate_graph(graph)
    if not report.valid:
        raise ValueError("已发布版本校验失败")
    if scope_type != "PROJECT" and not scope_id:
        raise ValueError("章节、页面或候选范围必须提供 scope_id")
    _validate_scope(db, workflow.project_id, scope_type, scope_id)
    # One active run per workflow+scope: a double-click or a client retry of
    # the start request otherwise mints a second run whose per-run job keys
    # (`workflow:{run}:...`) never collide, and both runs execute paid jobs on
    # the same target. Locking the definition row serializes concurrent starts
    # before the check-then-insert below. Terminal runs (FAILED/CANCELLED/
    # COMPLETED) never block retry_run or a fresh start.
    from app.services.ordinal_allocator import lock_entity

    lock_entity(db, WorkflowDefinition, workflow.id)
    active_run = db.scalar(
        select(WorkflowRun.id).where(
            WorkflowRun.workflow_id == workflow.id,
            WorkflowRun.scope_type == scope_type,
            WorkflowRun.scope_id == scope_id,
            WorkflowRun.status.not_in({"COMPLETED", "CANCELLED", "FAILED"}),
        )
    )
    if active_run is not None:
        raise ValueError("该范围已有进行中的运行，请先取消或等待完成")
    selected = _selected_nodes(graph, start_node_ids, stop_node_ids)
    selected_types = {node.type for node in graph.nodes if node.id in selected}
    page_types = {"generator.page", "control.approval", "quality.inspect", "output.page"}
    chapter_types = {"source.approved_pages", "output.chapter_export"}
    if selected_types & page_types and scope_type != "PAGE":
        raise ValueError("单页生产流程必须选择页面运行范围")
    if (
        selected_types & chapter_types
        and not selected_types & page_types
        and scope_type != "CHAPTER"
    ):
        raise ValueError("整章导出流程必须选择章节运行范围")
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=workflow.project_id,
        scope_type=scope_type,
        scope_id=scope_id,
        status="RUNNING",
        start_node_ids=start_node_ids,
        stop_node_ids=stop_node_ids,
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    node_map = {node.id: node for node in graph.nodes}
    job_by_node: dict[str, GenerationJob] = {}
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        incoming[edge.target_node].append(edge.source_node)

    for node_id in report.topological_order:
        if node_id not in selected:
            continue
        node = node_map[node_id]
        spec = NODE_TYPE_MAP[node.type]
        node_run = WorkflowNodeRun(
            workflow_run_id=run.id,
            node_id=node.id,
            node_type=node.type,
            status="WAITING",
            input_snapshot=_scope_snapshot(run, node),
            output_refs={},
            attempt_count=1,
        )
        db.add(node_run)
        db.flush()
        if not node.inputs:
            node_run.status = "COMPLETED"
            node_run.started_at = run.started_at
            node_run.finished_at = utcnow()
            node_run.output_refs = _source_output_refs(db, run, node)
            job = engine.create_job(
                db,
                project_id=run.project_id,
                target_type="WORKFLOW_NODE",
                target_id=node_run.id,
                job_type="WORKFLOW_NODE",
                request_parameters={
                    "workflow_run_id": run.id,
                    "node_id": node.id,
                    "node_type": node.type,
                },
                idempotency_key=f"workflow:{run.id}:{node.id}:1",
                auto_commit=False,
            )
            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.started_at = run.started_at
            job.finished_at = node_run.finished_at
            node_run.job_id = job.id
            job_by_node[node.id] = job
        elif spec.barrier or node.type == "quality.inspect":
            continue
        else:
            dependencies = [
                job_by_node[item].id for item in incoming[node.id] if item in job_by_node
            ]
            job = _create_node_job(db, run, node_run, node, dependencies)
            node_run.job_id = job.id
            job_by_node[node.id] = job
    db.commit()
    # The run is already committed RUNNING; a scheduling failure inside the
    # first reconcile (e.g. _submit_local re-raising on executor shutdown)
    # must not turn the start into a 500 whose client retry then hits the
    # duplicate-run guard (409) and locks the scope until recovery heals the
    # run or it is cancelled. Mirror the completion path (worker_tasks) and
    # the recovery loop (job_service): log and continue — the committed run
    # self-heals via the next reconcile trigger, which routes WAITING-node
    # jobs back to reconcile_run.
    try:
        reconcile_run(db, run.id)
    except Exception:
        LOGGER.exception("workflow run %s reconcile failed after creation", run.id)
        # Drop partial writes from the failed reconcile so they cannot leak
        # into the returned snapshot or a later use of this session; the
        # committed run row itself is untouched.
        db.rollback()
    return get_run(db, run.id)


def _create_node_job(
    db: Session,
    run: WorkflowRun,
    node_run: WorkflowNodeRun,
    node: WorkflowNodeDefinition,
    dependency_ids: list[str],
) -> GenerationJob:
    # `create_job` 是模块级 monkeypatch 接缝，必须在调用时经 facade 解析。
    from app.services import workflow_engine as engine

    target_type = "WORKFLOW_NODE"
    target_id = node_run.id
    job_type = "WORKFLOW_NODE"
    if node.type == "agent.parse":
        chapter = _scope_chapter(db, run)
        if not chapter or not chapter.current_source_revision_id:
            raise ValueError("剧情解析节点需要包含原文修订的章节运行范围")
        target_type = "CHAPTER"
        target_id = chapter.id
        job_type = "SOURCE_PARSE"
    return engine.create_job(
        db,
        project_id=run.project_id,
        target_type=target_type,
        target_id=target_id,
        job_type=job_type,
        model_alias=node.config.model_alias,
        request_parameters={
            "workflow_run_id": run.id,
            "workflow_node_run_id": node_run.id,
            "node_id": node.id,
            "node_type": node.type,
            "config": node.config.model_dump(mode="json"),
        },
        max_attempts=node.config.max_attempts,
        idempotency_key=f"workflow:{run.id}:{node.id}:1",
        dependency_ids=dependency_ids,
        auto_commit=False,
    )

from __future__ import annotations

from collections import defaultdict, deque

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.states import JobStatus
from app.models import (
    GenerationJob,
    InspectionResult,
    JobDependency,
    MangaPage,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.services.page_completion import build_page_production_readiness
from app.services.workflow_engine.catalog import NODE_TYPE_MAP
from app.services.workflow_engine.scope import (
    _candidate_for_run,
    _graph_for_run,
    _latest_script,
)
from app.services.workflow_engine.validation import validate_graph
from app.workflow_schemas import (
    WorkflowGraph,
    WorkflowNodeDefinition,
)


def get_run(db: Session, run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if not run:
        raise LookupError("工作流运行不存在")
    run.node_runs = list(
        db.scalars(
            select(WorkflowNodeRun)
            .where(WorkflowNodeRun.workflow_run_id == run.id)
            .order_by(WorkflowNodeRun.started_at, WorkflowNodeRun.node_id)
        )
    )
    return run


def _completed_output_refs(
    db: Session,
    run: WorkflowRun,
    item: WorkflowNodeRun,
    job: GenerationJob,
    node_runs: list[WorkflowNodeRun],
) -> dict:
    base = {**item.output_refs, "job_id": job.id, "node_type": item.node_type}
    if item.node_type in {"agent.parse", "agent.adapt"}:
        script = _latest_script(db, run)
        if not script:
            raise ValueError("剧本节点没有产生可追溯的 ScriptRevision")
        if script.status != "READY":
            raise ValueError("原文覆盖不完整，禁止继续生成图片")
        return {
            **base,
            "script_revision_id": script.id,
            "chapter_id": script.chapter_id,
            "coverage": script.coverage,
        }
    if item.node_type == "generator.page":
        candidate = _candidate_for_run(db, run, node_runs)
        if not candidate or candidate.status not in {"READY", "INSPECTED", "NEEDS_REVIEW"}:
            raise ValueError("图片节点没有产生可用的页面候选")
        return {**base, "candidate_id": candidate.id, "asset_id": candidate.asset_id}
    if item.node_type == "quality.inspect":
        candidate = _candidate_for_run(db, run, node_runs)
        if not candidate:
            raise ValueError("质量检查节点找不到候选图片")
        page = db.get(MangaPage, candidate.page_id)
        if not page:
            raise ValueError("质量检查节点找不到候选所属页面")
        production = build_page_production_readiness(db, page)
        if not production.ready:
            messages = "；".join(item.message for item in production.blockers)
            raise ValueError(f"质量检查未通过：{messages}")
        inspection_ids = list(
            db.scalars(
                select(InspectionResult.id).where(InspectionResult.candidate_id == candidate.id)
            )
        )
        return {
            **base,
            "candidate_id": candidate.id,
            "inspection_result_ids": inspection_ids,
            "candidate_status": candidate.status,
        }
    return base


def _create_inspection_job(
    db: Session,
    run: WorkflowRun,
    graph: WorkflowGraph,
    node: WorkflowNodeDefinition,
    node_run: WorkflowNodeRun,
    node_runs: list[WorkflowNodeRun],
) -> GenerationJob:
    # `create_job` 是模块级 monkeypatch 接缝，必须在调用时经 facade 解析。
    from app.services import workflow_engine as engine

    candidate = _candidate_for_run(db, run, node_runs)
    if not candidate or not candidate.asset_id:
        raise ValueError("质量检查必须等待已生成并采用的页面候选")
    job = engine.create_job(
        db,
        project_id=run.project_id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        model_alias=node.config.model_alias or "auto",
        request_parameters={
            "categories": ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"],
            "workflow_run_id": run.id,
            "workflow_node_run_id": node_run.id,
            "node_id": node.id,
            "node_type": node.type,
        },
        max_attempts=node.config.max_attempts,
        idempotency_key=f"workflow:{run.id}:{node.id}:1",
        dependency_ids=_parent_job_ids(db, run, graph, node.id),
        auto_commit=False,
    )
    node_run.job_id = job.id
    node_run.input_snapshot = {
        **node_run.input_snapshot,
        "candidate_id": candidate.id,
        "asset_id": candidate.asset_id,
    }
    return job


def reconcile_run(db: Session, run_id: str) -> WorkflowRun:
    # `enqueue_job` 是模块级 monkeypatch 接缝，必须在调用时经 facade 解析。
    from app.services import workflow_engine as engine

    run = db.get(WorkflowRun, run_id)
    if not run or run.status in {"COMPLETED", "CANCELLED", "FAILED"}:
        return get_run(db, run_id)
    graph = _graph_for_run(db, run)
    report = validate_graph(graph)
    node_map = {node.id: node for node in graph.nodes}
    node_runs = list(
        db.scalars(select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id))
    )
    by_node = {item.node_id: item for item in node_runs}
    parents: dict[str, list[str]] = defaultdict(list)
    incoming_edges: dict[str, list] = defaultdict(list)
    for edge in graph.edges:
        parents[edge.target_node].append(edge.source_node)
        incoming_edges[edge.target_node].append(edge)

    paused = False
    failed = False
    for node_id in report.topological_order:
        item = by_node.get(node_id)
        if not item:
            continue
        job = db.get(GenerationJob, item.job_id) if item.job_id else None
        if (
            job
            and job.status == JobStatus.COMPLETED
            and item.status not in {"COMPLETED", "CANCELLED", "SKIPPED"}
        ):
            try:
                item.output_refs = _completed_output_refs(db, run, item, job, node_runs)
            except ValueError as error:
                item.status = "FAILED"
                item.error_code = "INVALID_NODE_OUTPUT"
                item.error_message = str(error)
                failed = True
            else:
                item.status = "COMPLETED"
                item.started_at = job.started_at
                item.finished_at = job.finished_at or utcnow()
        elif job and job.status == JobStatus.FAILED:
            item.status = "FAILED"
            item.error_code = job.error_code
            item.error_message = job.error_message
            failed = True
        if item.status != "WAITING":
            if item.status == "WAITING_APPROVAL":
                paused = True
            continue
        if not all(
            by_node[parent].status in {"COMPLETED", "SKIPPED"}
            for parent in parents[node_id]
            if parent in by_node
        ):
            continue
        disabled_branch = any(
            by_node[edge.source_node].node_type == "control.condition"
            and by_node[edge.source_node].status == "COMPLETED"
            and by_node[edge.source_node].output_refs.get("selected_port") != edge.source_port
            for edge in incoming_edges[node_id]
            if edge.source_node in by_node
        )
        if disabled_branch:
            item.status = "SKIPPED"
            item.finished_at = utcnow()
            item.output_refs = {"reason": "CONDITION_BRANCH_NOT_SELECTED"}
            continue
        spec = NODE_TYPE_MAP[node_map[node_id].type]
        if spec.barrier:
            item.status = "WAITING_APPROVAL"
            item.input_snapshot = {**item.input_snapshot, "action": spec.barrier}
            paused = True
            continue
        db.refresh(run, attribute_names=["status"])
        if run.status in {"COMPLETED", "CANCELLED", "FAILED"}:
            return get_run(db, run.id)
        if node_map[node_id].type == "quality.inspect" and not job:
            try:
                job = _create_inspection_job(
                    db, run, graph, node_map[node_id], item, node_runs
                )
            except ValueError as error:
                item.status = "FAILED"
                item.error_code = "MISSING_CANDIDATE"
                item.error_message = str(error)
                failed = True
                continue
        if job:
            db.refresh(run, attribute_names=["status"])
            if run.status in {"COMPLETED", "CANCELLED", "FAILED"}:
                from app.services.job_service import mark_job_cancelled

                mark_job_cancelled(db, job)
                item.status = "CANCELLED"
                item.finished_at = utcnow()
                db.commit()
                return get_run(db, run.id)
            _sync_job_dependencies(db, job, _parent_job_ids(db, run, graph, node_id))
            item.status = "RUNNING"
            item.started_at = utcnow()
            engine.enqueue_job(db, job)
            db.refresh(run, attribute_names=["status"])
            if run.status in {"COMPLETED", "CANCELLED", "FAILED"}:
                from app.services.job_service import mark_job_cancelled

                mark_job_cancelled(db, job)
                item.status = "CANCELLED"
                item.finished_at = utcnow()
                db.commit()
                return get_run(db, run.id)

    db.refresh(run, attribute_names=["status"])
    if run.status in {"COMPLETED", "CANCELLED", "FAILED"}:
        return get_run(db, run.id)
    if failed:
        desired = "FAILED"
    elif all(item.status in {"COMPLETED", "SKIPPED"} for item in node_runs):
        desired = "COMPLETED"
    elif paused:
        desired = "PAUSED"
    else:
        desired = "RUNNING"
    if desired == "RUNNING":
        run.version += 1
        db.commit()
        return get_run(db, run.id)
    # Terminal and paused transitions must not overwrite a concurrently
    # written terminal state: two reconcilers race routinely (worker
    # finalize, recovery, approve), and a stale RUNNING write resurrects a
    # FAILED/CANCELLED run that retry then refuses to touch (zombie run).
    claimed = db.execute(
        update(WorkflowRun)
        .where(
            WorkflowRun.id == run.id,
            WorkflowRun.status.not_in(["COMPLETED", "CANCELLED", "FAILED"]),
        )
        .values(status=desired, version=WorkflowRun.version + 1,
                finished_at=utcnow() if desired in {"COMPLETED", "FAILED"} else None)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return get_run(db, run.id)
    db.commit()
    # synchronize_session=False leaves the identity-map instance stale on
    # sessions configured with expire_on_commit=False.
    db.refresh(run)
    return get_run(db, run.id)


def _parent_job_ids(db: Session, run: WorkflowRun, graph: WorkflowGraph, node_id: str) -> list[str]:
    """Return the nearest upstream jobs, traversing manual barrier nodes."""

    parent_nodes: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        parent_nodes[edge.target_node].append(edge.source_node)
    jobs_by_node = {
        item.node_id: item.job_id
        for item in db.scalars(
            select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
        )
        if item.job_id
    }
    pending = deque(parent_nodes[node_id])
    visited: set[str] = set()
    job_ids: list[str] = []
    while pending:
        parent_id = pending.popleft()
        if parent_id in visited:
            continue
        visited.add(parent_id)
        job_id = jobs_by_node.get(parent_id)
        if job_id:
            job_ids.append(job_id)
        else:
            pending.extend(parent_nodes[parent_id])
    return list(dict.fromkeys(job_ids))


def _sync_job_dependencies(
    db: Session, job: GenerationJob, dependency_ids: list[str]
) -> None:
    existing = set(
        db.scalars(
            select(JobDependency.depends_on_job_id).where(JobDependency.job_id == job.id)
        )
    )
    for dependency_id in dependency_ids:
        if dependency_id != job.id and dependency_id not in existing:
            db.add(JobDependency(job_id=job.id, depends_on_job_id=dependency_id))
            existing.add(dependency_id)
    db.flush()

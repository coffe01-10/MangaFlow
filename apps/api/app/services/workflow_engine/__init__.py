from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    ExportBundle,
    GenerationJob,
    InspectionResult,
    JobDependency,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.job_service import create_job, enqueue_job, mark_job_cancelled
from app.services.model_router import model_supports_resolution, resolve_model
from app.services.ordinal_allocator import (
    BatchOrdinalConflictError,
    commit_ordinal_transaction,
    create_generation_batch,
)
from app.services.page_completion import build_page_production_readiness
from app.services.workflow_engine.catalog import (
    CONDITION_OPERATORS,
    NODE_TYPE_MAP,
    NODE_TYPES,
    NodeTypeSpec,
    blank_graph,
    canonical_graph,
    chapter_export_graph,
    default_graph,
    graph_checksum,
    node_type_catalog,
)
from app.services.workflow_engine.scope import (
    _candidate_for_run,
    _graph_for_run,
    _latest_script,
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

__all__ = [
    "CONDITION_OPERATORS",
    "NODE_TYPES",
    "NODE_TYPE_MAP",
    "NodeTypeSpec",
    "PUBLISH_REVISION_MAX_ATTEMPTS",
    "PublishRevisionConflictError",
    "approve_node",
    "blank_graph",
    "cancel_run",
    "canonical_graph",
    "chapter_export_graph",
    "create_workflow_run",
    "create_job",
    "default_graph",
    "enqueue_job",
    "execute_workflow_node",
    "get_run",
    "graph_checksum",
    "mark_job_cancelled",
    "node_type_catalog",
    "publish_workflow",
    "reconcile_run",
    "retry_run",
    "validate_graph",
]


class PublishRevisionConflictError(Exception):
    """Raised when concurrent publishes cannot allocate a unique revision."""


PUBLISH_REVISION_MAX_ATTEMPTS = 3


def _lock_workflow(db: Session, workflow_id: str) -> WorkflowDefinition | None:
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    query = (
        select(WorkflowDefinition)
        .where(WorkflowDefinition.id == workflow_id)
        .execution_options(populate_existing=True)
    )
    if dialect_name == "postgresql":
        query = query.with_for_update()
    return db.scalar(query)


def _next_revision(db: Session, workflow_id: str) -> int:
    current = db.scalar(
        select(func.max(WorkflowVersion.revision)).where(WorkflowVersion.workflow_id == workflow_id)
    )
    return (current or 0) + 1


def publish_workflow(
    db: Session,
    workflow: WorkflowDefinition,
    *,
    max_attempts: int = PUBLISH_REVISION_MAX_ATTEMPTS,
) -> WorkflowVersion:
    workflow_id = workflow.id
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            with db.begin_nested():
                locked = _lock_workflow(db, workflow_id)
                if locked is None or locked.deleted_at is not None:
                    raise ValueError("工作流不存在")
                graph = canonical_graph(locked.draft_graph)
                report = validate_graph(graph)
                if not report.valid:
                    raise ValueError("工作流校验失败，不能发布")
                revision = _next_revision(db, locked.id)
                version = WorkflowVersion(
                    workflow_id=locked.id,
                    revision=revision,
                    graph=deepcopy(graph),
                    graph_checksum=graph_checksum(graph),
                    validation_report=report.model_dump(mode="json"),
                )
                db.add(version)
                db.flush()
                locked.published_version_id = version.id
                locked.version += 1
            db.commit()
        except IntegrityError as error:
            last_error = error
            db.rollback()
        except OperationalError as error:
            # SQLite readers can race while upgrading to a write transaction.
            # Retry from a fresh transaction, not the same stale read snapshot.
            code = getattr(error.orig, "sqlite_errorcode", None)
            db.rollback()
            if not isinstance(error.orig, sqlite3.OperationalError) or code is None:
                raise
            if code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            last_error = error
        else:
            db.refresh(version)
            return version
    raise PublishRevisionConflictError("工作流正在被其他请求发布，请稍后重试") from last_error


def create_workflow_run(
    db: Session,
    workflow: WorkflowDefinition,
    *,
    scope_type: str,
    scope_id: str | None,
    start_node_ids: list[str],
    stop_node_ids: list[str],
) -> WorkflowRun:
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
            job = create_job(
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
    reconcile_run(db, run.id)
    return get_run(db, run.id)


def _create_node_job(
    db: Session,
    run: WorkflowRun,
    node_run: WorkflowNodeRun,
    node: WorkflowNodeDefinition,
    dependency_ids: list[str],
) -> GenerationJob:
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
    return create_job(
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


def _condition_value(payload: dict[str, Any], path: str) -> Any:
    normalized = path.removeprefix("$").lstrip(".")
    value: Any = payload
    if not normalized:
        return value
    for part in normalized.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _condition_matches(value: Any, operator: str, expected: Any) -> bool:
    if operator == "exists":
        return value is not None
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
    if operator == "contains":
        return expected in value if isinstance(value, (str, list, tuple, dict)) else False
    if operator in {"gt", "gte", "lt", "lte"}:
        try:
            return {
                "gt": value > expected,
                "gte": value >= expected,
                "lt": value < expected,
                "lte": value <= expected,
            }[operator]
        except TypeError:
            return False
    raise ValueError("不支持的条件比较符")


def _parent_payloads(
    graph: WorkflowGraph,
    by_node: dict[str, WorkflowNodeRun],
    node_id: str,
) -> dict[str, dict]:
    return {
        edge.target_port: by_node[edge.source_node].output_refs
        for edge in graph.edges
        if edge.target_node == node_id
        and edge.source_node in by_node
        and by_node[edge.source_node].status == "COMPLETED"
    }


def _create_inspection_job(
    db: Session,
    run: WorkflowRun,
    graph: WorkflowGraph,
    node: WorkflowNodeDefinition,
    node_run: WorkflowNodeRun,
    node_runs: list[WorkflowNodeRun],
) -> GenerationJob:
    candidate = _candidate_for_run(db, run, node_runs)
    if not candidate or not candidate.asset_id:
        raise ValueError("质量检查必须等待已生成并采用的页面候选")
    job = create_job(
        db,
        project_id=run.project_id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        model_alias=node.config.model_alias or "text.fast",
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
    )
    node_run.job_id = job.id
    node_run.input_snapshot = {
        **node_run.input_snapshot,
        "candidate_id": candidate.id,
        "asset_id": candidate.asset_id,
    }
    return job


def reconcile_run(db: Session, run_id: str) -> WorkflowRun:
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
            _sync_job_dependencies(db, job, _parent_job_ids(db, run, graph, node_id))
            item.status = "RUNNING"
            item.started_at = utcnow()
            enqueue_job(db, job)

    if failed:
        run.status = "FAILED"
        run.finished_at = utcnow()
    elif all(item.status in {"COMPLETED", "SKIPPED"} for item in node_runs):
        run.status = "COMPLETED"
        run.finished_at = utcnow()
    elif paused:
        run.status = "PAUSED"
    else:
        run.status = "RUNNING"
    run.version += 1
    db.commit()
    return get_run(db, run.id)


def execute_workflow_node(db: Session, job: GenerationJob) -> None:
    node_run = db.get(WorkflowNodeRun, job.target_id)
    if not node_run:
        raise RuntimeError("工作流节点运行不存在")
    node_run.status = "RUNNING"
    node_run.started_at = node_run.started_at or utcnow()
    run = db.get(WorkflowRun, node_run.workflow_run_id)
    if not run:
        raise RuntimeError("工作流运行不存在")
    graph = _graph_for_run(db, run)
    by_node = {
        item.node_id: item
        for item in db.scalars(
            select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
        )
    }
    parent_payloads = _parent_payloads(graph, by_node, node_run.node_id)
    if node_run.node_type == "agent.adapt":
        script = _latest_script(db, run)
        if not script or script.status != "READY":
            raise RuntimeError("UNSUPPORTED_INPUT: 剧本改编需要完整的 ScriptRevision")
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "script_revision_id": script.id,
            "coverage": script.coverage,
        }
    elif node_run.node_type == "director.storyboard":
        from app.services.content_workflow import plan_chapter_pages

        chapter = _scope_chapter(db, run)
        if not chapter:
            raise RuntimeError("分页与分镜节点必须使用章节、页面或候选范围")
        pages = plan_chapter_pages(db, chapter, replace_existing=False)
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "chapter_id": chapter.id,
            "page_ids": [page.id for page in pages],
        }
    elif node_run.node_type == "control.condition":
        node = next(item for item in graph.nodes if item.id == node_run.node_id)
        condition = node.config.condition
        payload = next(iter(parent_payloads.values()), {})
        actual = _condition_value(payload, condition["path"])
        matched = _condition_matches(actual, condition["operator"], condition.get("value"))
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "matched": matched,
            "selected_port": "true" if matched else "false",
            "value": actual,
            "input": payload,
        }
    elif node_run.node_type == "control.merge":
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "merged": parent_payloads,
        }
    elif node_run.node_type == "output.page" or (
        node_run.node_type == "output.export" and run.scope_type == "PAGE"
    ):
        page = db.get(MangaPage, run.scope_id) if run.scope_id else None
        if not page:
            raise RuntimeError("UNSUPPORTED_INPUT: 单页成品节点需要页面运行范围")
        production = build_page_production_readiness(db, page)
        if not production.ready:
            messages = "；".join(item.message for item in production.blockers)
            raise RuntimeError(f"PAGE_NOT_PRODUCTION_READY: {messages}")
        candidate = db.get(PageCandidate, page.selected_candidate_id)
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "page_id": page.id,
            "candidate_id": candidate.id,
            "asset_id": candidate.asset_id,
            "download_url": f"/api/v1/pages/{page.id}/export.png",
        }
    elif node_run.node_type in {"output.export", "output.chapter_export"}:
        from app.api.routes.exports import create_export
        from app.schemas import ExportRequest

        chapter = _scope_chapter(db, run)
        if not chapter:
            raise RuntimeError("UNSUPPORTED_INPUT: 整章导出节点需要章节运行范围")
        bundle = create_export(chapter.id, ExportRequest(export_type="JSON"), db)
        if not isinstance(bundle, ExportBundle):
            raise RuntimeError("导出节点没有产生 ExportBundle")
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "export_id": bundle.id,
            "export_type": bundle.export_type,
            "storage_key": bundle.storage_key,
        }
    else:
        raise RuntimeError(f"UNSUPPORTED_NODE_TYPE: {node_run.node_type}")
    db.flush()


def approve_node(
    db: Session,
    run_id: str,
    node_id: str,
    candidate_id: str | None = None,
    image_model_alias: str | None = None,
    resolution: str | None = None,
) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if not run or run.status not in {"PAUSED", "RUNNING"}:
        raise ValueError("当前运行不等待人工确认")
    node_run = db.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.node_id == node_id,
            WorkflowNodeRun.status == "WAITING_APPROVAL",
        )
    )
    if not node_run:
        raise ValueError("节点当前不等待人工确认")
    graph = _graph_for_run(db, run)
    node = next(item for item in graph.nodes if item.id == node_id)
    spec = NODE_TYPE_MAP[node.type]
    if spec.barrier == "GENERATE":
        if not image_model_alias or image_model_alias.casefold() == "auto":
            raise ValueError("每次生成候选都必须明确选择图片模型")
        selected_resolution = resolution or node.config.resolution
        if selected_resolution not in {"1K", "2K", "4K"}:
            raise ValueError("每次生成候选都必须明确选择 1K、2K 或 4K")
        if node_run.job_id:
            raise ValueError("该节点本次运行已经生成过一个候选")
        if run.scope_type != "PAGE" or not run.scope_id:
            raise ValueError("单页生成节点必须使用 PAGE 运行范围")
        page = db.get(MangaPage, run.scope_id)
        if not page:
            raise ValueError("页面不存在")
        chapter = db.get(Chapter, page.chapter_id)
        if not chapter or chapter.project_id != run.project_id:
            raise ValueError("页面不属于当前项目")
        resolved_model = resolve_model(
            db,
            get_settings(),
            operation="image_edit",
            explicit_reference=image_model_alias,
            project_id=run.project_id,
            task_kind="PAGE_GENERATE",
        )
        if not model_supports_resolution(resolved_model.model, selected_resolution):
            raise ValueError("所选图片模型不支持当前输出清晰度")
        panels = list(db.scalars(select(Panel).where(Panel.page_id == page.id)))
        visible_character_ids = list(
            dict.fromkeys(
                character_id for panel in panels for character_id in panel.characters
            )
        )
        reference_selections: dict[str, dict[str, str | None]] = {}
        reference_asset_ids: list[str] = []
        for character_id in visible_character_ids:
            character_reference = db.scalar(
                select(CharacterReference)
                .join(Asset, Asset.id == CharacterReference.asset_id)
                .where(
                    CharacterReference.character_id == character_id,
                    Asset.deleted_at.is_(None),
                )
                .order_by(CharacterReference.is_canonical.desc())
            )
            if not character_reference:
                character = db.get(Character, character_id)
                raise ValueError(
                    f"人物 {character.primary_name if character else character_id} 缺少参考图"
                )
            outfit_ids = {
                panel.outfits.get(character_id)
                for panel in panels
                if panel.outfits.get(character_id)
            }
            if len(outfit_ids) > 1:
                raise ValueError("同一页同一人物存在多套服装，请先拆页")
            outfit_id = next(iter(outfit_ids), None)
            outfit = db.get(Outfit, outfit_id) if outfit_id else None
            outfit_asset_id = None
            if outfit:
                outfit_asset_id = db.scalar(
                    select(Asset.id).where(
                        Asset.id.in_(outfit.reference_asset_ids),
                        Asset.deleted_at.is_(None),
                    )
                )
                if not outfit_asset_id:
                    raise ValueError(f"服装 {outfit.name} 缺少可用参考图")
            reference_selections[character_id] = {
                "character_asset_id": character_reference.asset_id,
                "outfit_id": outfit_id,
                "outfit_asset_id": outfit_asset_id,
            }
            reference_asset_ids.append(character_reference.asset_id)
            if outfit_asset_id:
                reference_asset_ids.append(outfit_asset_id)
        batch = create_generation_batch(
            db,
            project_id=run.project_id,
            chapter_id=chapter.id,
            page_id=page.id,
            generation_kind="PAGE",
        )
        candidate = PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias=image_model_alias,
            catalog_model_id=resolved_model.model.id,
            resolution=Resolution(selected_resolution),
            status="QUEUED",
            based_on_storyboard_version=page.storyboard_version,
            prompt_snapshot={
                "storyboard_version": page.storyboard_version,
                "reference_selections": reference_selections,
            },
        )
        db.add(candidate)
        db.flush()
        dependency_ids = _parent_job_ids(db, run, graph, node_id)
        job = create_job(
            db,
            project_id=run.project_id,
            target_type="PAGE_CANDIDATE",
            target_id=candidate.id,
            job_type="PAGE_GENERATE",
            model_alias=candidate.model_alias,
            catalog_model_id=resolved_model.model.id,
            request_parameters={
                "resolution": candidate.resolution.value,
                "storyboard_version": page.storyboard_version,
                "workflow_run_id": run.id,
                "workflow_node_id": node_id,
                "reference_selections": reference_selections,
            },
            reference_asset_ids=reference_asset_ids,
            max_attempts=node.config.max_attempts,
            idempotency_key=f"workflow:{run.id}:{node_id}:candidate",
            dependency_ids=dependency_ids,
            auto_commit=False,
        )
        candidate.job_id = job.id
        project = db.get(Project, run.project_id)
        if project:
            project.last_image_model_alias = image_model_alias
            project.image_model_alias = image_model_alias
            project.last_image_model_id = resolved_model.model.id
            project.version += 1
        node_run.job_id = job.id
        node_run.status = "RUNNING"
        node_run.started_at = utcnow()
        node_run.output_refs = {"candidate_id": candidate.id, "batch_id": batch.id}
        run.status = "RUNNING"
        commit_ordinal_transaction(db, BatchOrdinalConflictError)
        enqueue_job(db, job)
    elif spec.barrier == "APPROVE":
        if run.scope_type != "PAGE" or not run.scope_id:
            raise ValueError("采用候选节点必须使用 PAGE 运行范围")
        page = db.get(MangaPage, run.scope_id)
        selected = candidate_id or (page.selected_candidate_id if page else None)
        candidate = db.get(PageCandidate, selected) if selected else None
        if not page or not candidate or candidate.page_id != page.id or not candidate.is_selected:
            raise ValueError("请先在单页生成页采用当前页的一个候选")
        node_run.status = "COMPLETED"
        node_run.started_at = node_run.started_at or utcnow()
        node_run.finished_at = utcnow()
        node_run.output_refs = {"candidate_id": candidate.id, "page_id": page.id}
        run.status = "RUNNING"
        db.commit()
    return reconcile_run(db, run.id)


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


def cancel_run(db: Session, run: WorkflowRun) -> WorkflowRun:
    if run.status in {"COMPLETED", "CANCELLED"}:
        return get_run(db, run.id)
    node_runs = list(
        db.scalars(select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id))
    )
    for item in node_runs:
        if item.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            item.status = "CANCELLED"
            item.finished_at = utcnow()
        job = db.get(GenerationJob, item.job_id) if item.job_id else None
        if job and job.status not in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
            mark_job_cancelled(db, job)
    run.status = "CANCELLED"
    run.finished_at = utcnow()
    run.version += 1
    db.commit()
    return get_run(db, run.id)


def retry_run(db: Session, run: WorkflowRun) -> WorkflowRun:
    if run.status not in {"FAILED", "CANCELLED"}:
        raise ValueError("只有失败或已取消的运行可以重试")
    workflow = db.get(WorkflowDefinition, run.workflow_id)
    return create_workflow_run(
        db,
        workflow,
        scope_type=run.scope_type,
        scope_id=run.scope_id,
        start_node_ids=run.start_node_ids,
        stop_node_ids=run.stop_node_ids,
    )

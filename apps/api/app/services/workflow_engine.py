from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    ScriptRevision,
    SourceSegment,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.services.job_service import create_job, enqueue_job
from app.workflow_schemas import (
    WorkflowGraph,
    WorkflowNodeDefinition,
    WorkflowNodeTypeRead,
    WorkflowPortDefinition,
    WorkflowValidationIssue,
    WorkflowValidationRead,
)


@dataclass(frozen=True)
class NodeTypeSpec:
    type: str
    label: str
    category: str
    description: str
    inputs: tuple[tuple[str, str, str, bool], ...]
    outputs: tuple[tuple[str, str, str, bool], ...]
    configurable_fields: tuple[str, ...] = ()
    model_family: str | None = None
    barrier: str | None = None


NODE_TYPES: tuple[NodeTypeSpec, ...] = (
    NodeTypeSpec(
        "source.chapter",
        "原作章节",
        "INPUT",
        "读取项目中的章节原文与不可变修订。",
        (),
        (("source", "原始文本", "text", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "source.assets",
        "参考资产",
        "INPUT",
        "读取角色、服装与漫画风格参考资产。",
        (),
        (("assets", "资产包", "asset", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "agent.parse",
        "剧情解析",
        "AGENT",
        "识别场景、角色、事实与来源区间。",
        (("source", "原始文本", "text", True),),
        (("story", "结构化剧情", "json", False),),
        (
            "model_alias",
            "prompt_template",
            "temperature",
            "timeout_seconds",
            "max_attempts",
            "notes",
        ),
        "text",
    ),
    NodeTypeSpec(
        "agent.adapt",
        "漫画改编",
        "AGENT",
        "逐片段生成完整漫画剧本，不压缩原文。",
        (("story", "结构化剧情", "json", True),),
        (("script", "漫画剧本", "json", False),),
        (
            "model_alias",
            "prompt_template",
            "temperature",
            "timeout_seconds",
            "max_attempts",
            "notes",
        ),
        "text",
    ),
    NodeTypeSpec(
        "director.storyboard",
        "分页与分镜",
        "AGENT",
        "动态分页并生成右至左分镜数据。",
        (("script", "漫画剧本", "json", True),),
        (("panels", "分页分镜", "json", False),),
        (
            "model_alias",
            "prompt_template",
            "temperature",
            "timeout_seconds",
            "max_attempts",
            "notes",
        ),
        "text",
    ),
    NodeTypeSpec(
        "control.condition",
        "条件分支",
        "CONTROL",
        "按安全 JSON 路径和预定义比较符选择分支。",
        (("value", "待判断数据", "json", True),),
        (("true", "满足条件", "json", False), ("false", "不满足条件", "json", False)),
        ("condition", "notes"),
    ),
    NodeTypeSpec(
        "control.merge",
        "合并",
        "CONTROL",
        "合并两个结构化输入。",
        (("left", "输入 A", "json", True), ("right", "输入 B", "json", True)),
        (("merged", "合并结果", "json", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "generator.page",
        "单页生成",
        "OUTPUT",
        "显式确认后只生成当前页的一个候选。",
        (("panels", "分页分镜", "json", True), ("assets", "参考资产", "asset", True)),
        (("page", "页面候选", "image", False),),
        ("model_alias", "resolution", "timeout_seconds", "max_attempts", "notes"),
        "image",
        "GENERATE",
    ),
    NodeTypeSpec(
        "control.approval",
        "采用候选",
        "CONTROL",
        "人工确认当前页采用版本后再继续。",
        (("page", "页面候选", "image", True),),
        (("approved", "采用页面", "image", False),),
        ("notes",),
        None,
        "APPROVE",
    ),
    NodeTypeSpec(
        "quality.inspect",
        "质量检查",
        "AGENT",
        "检查文字、说话人、角色、服装与连续性。",
        (("page", "采用页面", "image", True),),
        (("report", "检查报告", "report", False), ("approved", "通过页面", "image", False)),
        ("model_alias", "timeout_seconds", "max_attempts", "notes"),
        "text",
    ),
    NodeTypeSpec(
        "output.export",
        "连续导出",
        "OUTPUT",
        "输出 PNG、PDF、JSON 与素材清单。",
        (("page", "通过页面", "image", True),),
        (("files", "导出文件", "asset", False),),
        ("notes",),
    ),
)

NODE_TYPE_MAP = {item.type: item for item in NODE_TYPES}
TEXT_MODELS = {"text.fast"}
IMAGE_MODELS = {"image.nano_banana_2", "image.nano_banana_pro"}
CONDITION_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists"}


def _ports(items: tuple[tuple[str, str, str, bool], ...]) -> list[WorkflowPortDefinition]:
    return [
        WorkflowPortDefinition(id=item[0], label=item[1], data_type=item[2], required=item[3])
        for item in items
    ]


def node_type_catalog() -> list[WorkflowNodeTypeRead]:
    return [
        WorkflowNodeTypeRead(
            type=item.type,
            label=item.label,
            category=item.category,
            description=item.description,
            inputs=_ports(item.inputs),
            outputs=_ports(item.outputs),
            configurable_fields=list(item.configurable_fields),
        )
        for item in NODE_TYPES
    ]


def _node(node_id: str, node_type: str, name: str, x: float, y: float, **config: Any) -> dict:
    spec = NODE_TYPE_MAP[node_type]
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "position": {"x": x, "y": y},
        "inputs": [port.model_dump() for port in _ports(spec.inputs)],
        "outputs": [port.model_dump() for port in _ports(spec.outputs)],
        "config": config,
    }


def _edge(source: str, source_port: str, target: str, target_port: str) -> dict:
    return {
        "id": f"{source}:{source_port}-{target}:{target_port}",
        "source_node": source,
        "source_port": source_port,
        "target_node": target,
        "target_port": target_port,
    }


def default_graph() -> dict:
    nodes = [
        _node("chapter", "source.chapter", "原作章节", 40, 180, notes="当前章节不可变修订"),
        _node("assets", "source.assets", "参考资产", 610, 430, notes="人物、服装、风格"),
        _node("parse", "agent.parse", "剧情解析", 330, 160, model_alias="text.fast"),
        _node("adapt", "agent.adapt", "漫画改编", 610, 160, model_alias="text.fast"),
        _node("storyboard", "director.storyboard", "分页与分镜", 890, 160, model_alias="text.fast"),
        _node(
            "generate",
            "generator.page",
            "单页生成",
            1180,
            250,
            model_alias="image.nano_banana_2",
            resolution="1K",
            requires_approval=True,
        ),
        _node("adopt", "control.approval", "采用候选", 1470, 250, requires_approval=True),
        _node("inspect", "quality.inspect", "质量检查", 1760, 250, model_alias="text.fast"),
        _node("export", "output.export", "连续导出", 2050, 250),
    ]
    edges = [
        _edge("chapter", "source", "parse", "source"),
        _edge("parse", "story", "adapt", "story"),
        _edge("adapt", "script", "storyboard", "script"),
        _edge("storyboard", "panels", "generate", "panels"),
        _edge("assets", "assets", "generate", "assets"),
        _edge("generate", "page", "adopt", "page"),
        _edge("adopt", "approved", "inspect", "page"),
        _edge("inspect", "approved", "export", "page"),
    ]
    return WorkflowGraph(nodes=nodes, edges=edges).model_dump(mode="json")


def blank_graph() -> dict:
    return WorkflowGraph().model_dump(mode="json")


def canonical_graph(graph: WorkflowGraph | dict) -> dict:
    value = graph if isinstance(graph, WorkflowGraph) else WorkflowGraph.model_validate(graph)
    return value.model_dump(mode="json")


def graph_checksum(graph: WorkflowGraph | dict) -> str:
    payload = json.dumps(
        canonical_graph(graph), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_graph(graph_value: WorkflowGraph | dict) -> WorkflowValidationRead:
    graph = (
        graph_value
        if isinstance(graph_value, WorkflowGraph)
        else WorkflowGraph.model_validate(graph_value)
    )
    issues: list[WorkflowValidationIssue] = []
    nodes = {node.id: node for node in graph.nodes}
    inbound: dict[str, list] = defaultdict(list)
    outbound: dict[str, list] = defaultdict(list)
    indegree = {node.id: 0 for node in graph.nodes}
    seen_targets: set[tuple[str, str]] = set()

    if not nodes:
        issues.append(
            WorkflowValidationIssue(
                severity="ERROR", code="EMPTY_GRAPH", message="工作流至少需要一个节点"
            )
        )

    for node in graph.nodes:
        spec = NODE_TYPE_MAP.get(node.type)
        if not spec:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="UNKNOWN_NODE_TYPE",
                    message=f"不支持的节点类型：{node.type}",
                    node_id=node.id,
                )
            )
            continue
        declared_inputs = {item.id: item for item in node.inputs}
        declared_outputs = {item.id: item for item in node.outputs}
        expected_inputs = {item[0]: item[2] for item in spec.inputs}
        expected_outputs = {item[0]: item[2] for item in spec.outputs}
        if {key: item.data_type for key, item in declared_inputs.items()} != expected_inputs or {
            key: item.data_type for key, item in declared_outputs.items()
        } != expected_outputs:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="PORT_SCHEMA_MISMATCH",
                    message="节点端口与节点类型目录不一致",
                    node_id=node.id,
                )
            )
        alias = node.config.model_alias
        if spec.model_family == "text" and alias not in TEXT_MODELS:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="TEXT_MODEL_REQUIRED",
                    message="该节点必须选择 Gemini 文本模型",
                    node_id=node.id,
                )
            )
        if spec.model_family == "image" and alias not in IMAGE_MODELS:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="IMAGE_MODEL_REQUIRED",
                    message="该节点必须选择 Nano Banana 2 或 Nano Banana Pro",
                    node_id=node.id,
                )
            )
        if spec.model_family == "image" and node.config.resolution not in {"1K", "2K", "4K"}:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="RESOLUTION_REQUIRED",
                    message="图片节点必须选择 1K、2K 或 4K",
                    node_id=node.id,
                )
            )
        if node.type == "control.condition":
            condition = node.config.condition
            if condition.get("operator") not in CONDITION_OPERATORS or not isinstance(
                condition.get("path"), str
            ):
                issues.append(
                    WorkflowValidationIssue(
                        severity="ERROR",
                        code="INVALID_CONDITION",
                        message="条件仅支持安全 JSON 路径和预定义比较符",
                        node_id=node.id,
                    )
                )

    for edge in graph.edges:
        source = nodes.get(edge.source_node)
        target = nodes.get(edge.target_node)
        if not source or not target:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="DANGLING_EDGE",
                    message="连线引用了不存在的节点",
                    edge_id=edge.id,
                )
            )
            continue
        source_port = next((item for item in source.outputs if item.id == edge.source_port), None)
        target_port = next((item for item in target.inputs if item.id == edge.target_port), None)
        if not source_port or not target_port:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="UNKNOWN_PORT",
                    message="连线引用了不存在的端口",
                    edge_id=edge.id,
                )
            )
            continue
        if source_port.data_type != target_port.data_type:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="PORT_TYPE_MISMATCH",
                    message=f"端口类型不匹配：{source_port.data_type} → {target_port.data_type}",
                    edge_id=edge.id,
                )
            )
        target_key = (edge.target_node, edge.target_port)
        if target_key in seen_targets:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="MULTIPLE_INPUTS",
                    message="同一输入端口只能连接一条边",
                    edge_id=edge.id,
                )
            )
        seen_targets.add(target_key)
        inbound[edge.target_node].append(edge)
        outbound[edge.source_node].append(edge)
        indegree[edge.target_node] += 1

    for node in graph.nodes:
        connected = {edge.target_port for edge in inbound[node.id]}
        for port in node.inputs:
            if port.required and port.id not in connected:
                issues.append(
                    WorkflowValidationIssue(
                        severity="ERROR",
                        code="MISSING_REQUIRED_INPUT",
                        message=f"必需输入“{port.label}”尚未连接",
                        node_id=node.id,
                    )
                )

    ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        node_id = ready.popleft()
        order.append(node_id)
        for edge in outbound[node_id]:
            indegree[edge.target_node] -= 1
            if indegree[edge.target_node] == 0:
                ready.append(edge.target_node)
    if len(order) != len(nodes):
        issues.append(
            WorkflowValidationIssue(
                severity="ERROR", code="CYCLE_DETECTED", message="工作流不允许形成循环"
            )
        )
        order = []
    return WorkflowValidationRead(
        valid=not any(item.severity == "ERROR" for item in issues),
        issues=issues,
        topological_order=order,
    )


def publish_workflow(db: Session, workflow: WorkflowDefinition) -> WorkflowVersion:
    report = validate_graph(workflow.draft_graph)
    if not report.valid:
        raise ValueError("工作流校验失败，不能发布")
    revision = (
        db.scalar(
            select(func.max(WorkflowVersion.revision)).where(
                WorkflowVersion.workflow_id == workflow.id
            )
        )
        or 0
    ) + 1
    graph = canonical_graph(workflow.draft_graph)
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=revision,
        graph=deepcopy(graph),
        graph_checksum=graph_checksum(graph),
        validation_report=report.model_dump(mode="json"),
    )
    db.add(version)
    db.flush()
    workflow.published_version_id = version.id
    workflow.version += 1
    db.commit()
    db.refresh(version)
    return version


def _selected_nodes(graph: WorkflowGraph, start_ids: list[str], stop_ids: list[str]) -> set[str]:
    node_ids = {node.id for node in graph.nodes}
    if any(item not in node_ids for item in [*start_ids, *stop_ids]):
        raise ValueError("运行范围包含不存在的节点")
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        outgoing[edge.source_node].append(edge.target_node)
    selected: set[str] = set()
    queue = deque(
        start_ids
        or [
            node.id
            for node in graph.nodes
            if not any(edge.target_node == node.id for edge in graph.edges)
        ]
    )
    while queue:
        item = queue.popleft()
        if item in selected:
            continue
        selected.add(item)
        if item not in stop_ids:
            queue.extend(outgoing[item])
    return selected


def _scope_snapshot(run: WorkflowRun, node: WorkflowNodeDefinition) -> dict:
    return {
        "scope_type": run.scope_type,
        "scope_id": run.scope_id,
        "node_id": node.id,
        "node_type": node.type,
    }


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


def _source_output_refs(db: Session, run: WorkflowRun, node: WorkflowNodeDefinition) -> dict:
    if node.type == "source.chapter":
        chapter = _scope_chapter(db, run)
        if not chapter or not chapter.current_source_revision_id:
            return {"chapter_id": None, "kind": "source", "available": False}
        segment_ids = list(
            db.scalars(
                select(SourceSegment.id).where(
                    SourceSegment.source_revision_id == chapter.current_source_revision_id
                )
            )
        )
        return {
            "chapter_id": chapter.id,
            "source_revision_id": chapter.current_source_revision_id,
            "segment_ids": segment_ids,
            "kind": "source",
            "available": True,
        }
    if node.type == "source.assets":
        asset_ids = list(
            db.scalars(
                select(Asset.id).where(
                    Asset.project_id == run.project_id,
                    Asset.deleted_at.is_(None),
                )
            )
        )
        return {
            "project_id": run.project_id,
            "asset_ids": asset_ids,
            "kind": "assets",
        }
    return {"kind": "source"}


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


def _scope_chapter(db: Session, run: WorkflowRun) -> Chapter | None:
    if run.scope_type == "CHAPTER" and run.scope_id:
        return db.get(Chapter, run.scope_id)
    if run.scope_type == "PAGE" and run.scope_id:
        page = db.get(MangaPage, run.scope_id)
        return db.get(Chapter, page.chapter_id) if page else None
    if run.scope_type == "CANDIDATE" and run.scope_id:
        candidate = db.get(PageCandidate, run.scope_id)
        page = db.get(MangaPage, candidate.page_id) if candidate else None
        return db.get(Chapter, page.chapter_id) if page else None
    return None


def _validate_scope(
    db: Session,
    project_id: str,
    scope_type: str,
    scope_id: str | None,
) -> None:
    if scope_type == "PROJECT":
        if scope_id and scope_id != project_id:
            raise ValueError("项目运行范围与工作流项目不一致")
        return
    probe = WorkflowRun(project_id=project_id, scope_type=scope_type, scope_id=scope_id)
    chapter = _scope_chapter(db, probe)
    if not chapter or chapter.project_id != project_id or chapter.deleted_at is not None:
        raise ValueError("运行范围不属于当前项目或已经删除")


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


def _graph_for_run(db: Session, run: WorkflowRun) -> WorkflowGraph:
    version = db.get(WorkflowVersion, run.workflow_version_id)
    return WorkflowGraph.model_validate(version.graph)


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
    for edge in graph.edges:
        parents[edge.target_node].append(edge.source_node)

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
            and item.status not in {"COMPLETED", "CANCELLED"}
        ):
            item.status = "COMPLETED"
            item.started_at = job.started_at
            item.finished_at = job.finished_at or utcnow()
            item.output_refs = {
                "job_id": job.id,
                "target_id": job.target_id,
                "node_type": item.node_type,
            }
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
            by_node[parent].status == "COMPLETED"
            for parent in parents[node_id]
            if parent in by_node
        ):
            continue
        spec = NODE_TYPE_MAP[node_map[node_id].type]
        if spec.barrier:
            item.status = "WAITING_APPROVAL"
            item.input_snapshot = {**item.input_snapshot, "action": spec.barrier}
            paused = True
            continue
        if job:
            item.status = "RUNNING"
            item.started_at = utcnow()
            enqueue_job(db, job)

    if failed:
        run.status = "FAILED"
        run.finished_at = utcnow()
    elif all(item.status == "COMPLETED" for item in node_runs):
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
    if node_run.node_type == "director.storyboard":
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
    else:
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "completed": True,
        }
    db.flush()


def approve_node(
    db: Session, run_id: str, node_id: str, candidate_id: str | None = None
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
        ordinal = (
            db.scalar(
                select(func.max(GenerationBatch.ordinal)).where(
                    GenerationBatch.project_id == run.project_id
                )
            )
            or 0
        ) + 1
        batch = GenerationBatch(
            project_id=run.project_id,
            chapter_id=chapter.id,
            page_id=page.id,
            ordinal=ordinal,
            generation_kind="PAGE",
            status="OPEN",
        )
        db.add(batch)
        db.flush()
        candidate = PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias=node.config.model_alias or "image.nano_banana_2",
            resolution=Resolution(node.config.resolution or "1K"),
            status="QUEUED",
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
            request_parameters={
                "resolution": candidate.resolution.value,
                "workflow_run_id": run.id,
                "workflow_node_id": node_id,
            },
            max_attempts=node.config.max_attempts,
            idempotency_key=f"workflow:{run.id}:{node_id}:candidate",
            dependency_ids=dependency_ids,
        )
        candidate.job_id = job.id
        node_run.job_id = job.id
        node_run.status = "RUNNING"
        node_run.started_at = utcnow()
        node_run.output_refs = {"candidate_id": candidate.id, "batch_id": batch.id}
        run.status = "RUNNING"
        db.commit()
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
    parent_ids = [edge.source_node for edge in graph.edges if edge.target_node == node_id]
    return list(
        db.scalars(
            select(WorkflowNodeRun.job_id).where(
                WorkflowNodeRun.workflow_run_id == run.id,
                WorkflowNodeRun.node_id.in_(parent_ids),
                WorkflowNodeRun.job_id.is_not(None),
            )
        )
    )


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
            job.status = JobStatus.CANCELLED
            job.cancelled_at = utcnow()
            job.finished_at = utcnow()
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

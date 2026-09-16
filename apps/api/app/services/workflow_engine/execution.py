from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExportBundle,
    GenerationJob,
    MangaPage,
    PageCandidate,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.services.page_completion import build_page_production_readiness
from app.services.workflow_engine.scope import _graph_for_run, _latest_script, _scope_chapter
from app.workflow_schemas import WorkflowGraph


class _MissingType:
    """Sentinel for a JSON path that is not present.

    #798: `_condition_value` used to return None for both a missing key and
    JSON null, so `exists` and `eq null` could not tell them apart. Missing
    must stay distinct from None (JSON null).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<missing>"

    def __bool__(self) -> bool:
        return False


MISSING = _MissingType()


class WorkflowNodeExecutionError(RuntimeError):
    """Deterministic, non-retryable workflow-node failure.

    These conditions (missing scope, incomplete inputs, page not
    production-ready, unsupported node type) cannot change outcome on a
    retry, and their messages state the blocking condition in user terms —
    unlike unclassified exceptions whose raw text must stay out of
    user-visible error fields (see worker_tasks' sanitization rule). Retrying
    them by max_attempts used to burn three no-op attempts and replace the
    actionable message with the generic「未分类异常」text.
    """

    error_code = "NODE_PRECONDITION_FAILED"


def _condition_value(payload: dict[str, Any], path: str) -> Any:
    normalized = path.removeprefix("$").lstrip(".")
    value: Any = payload
    if not normalized:
        return value
    for part in normalized.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def _condition_matches(value: Any, operator: str, expected: Any) -> bool:
    if operator == "exists":
        # Present including JSON null. A missing path is not "exists".
        return value is not MISSING
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
    if operator == "contains":
        # A non-container value simply cannot contain anything. Inside real
        # containers a non-matching expected type must answer False instead of
        # raising: ``None in "text"`` (missing "value" key on an unpublished
        # legacy graph, #224) and an unhashable lookup into a dict are TypeErrors
        # a worker must not crash on.
        if not isinstance(value, (str, list, tuple, dict)):
            return False
        if isinstance(value, str) and not isinstance(expected, str):
            return False
        try:
            return expected in value
        except TypeError:
            return False
    if operator in {"gt", "gte", "lt", "lte"}:
        # Only the requested comparison is evaluated; an incomparable pair
        # (None vs number, container vs scalar) is a False branch, not a crash
        # (#224).
        try:
            if operator == "gt":
                return value > expected
            if operator == "gte":
                return value >= expected
            if operator == "lt":
                return value < expected
            return value <= expected
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


def execute_workflow_node(db: Session, job: GenerationJob) -> None:
    node_run = db.get(WorkflowNodeRun, job.target_id)
    if not node_run:
        raise WorkflowNodeExecutionError("工作流节点运行不存在")
    node_run.status = "RUNNING"
    node_run.started_at = node_run.started_at or utcnow()
    # Job-level retries re-claim the row (attempt_count + 1 in _claim_job);
    # mirror that onto the node run so the exposed count reflects scheduling
    # attempts instead of staying pinned at the planning-time 1.
    node_run.attempt_count = max(node_run.attempt_count, job.attempt_count)
    run = db.get(WorkflowRun, node_run.workflow_run_id)
    if not run:
        raise WorkflowNodeExecutionError("工作流运行不存在")
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
            raise WorkflowNodeExecutionError(
                "UNSUPPORTED_INPUT: 剧本改编需要完整的 ScriptRevision"
            )
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
            raise WorkflowNodeExecutionError("分页与分镜节点必须使用章节、页面或候选范围")
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
            "value": None if actual is MISSING else actual,
            "present": actual is not MISSING,
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
            raise WorkflowNodeExecutionError(
                "UNSUPPORTED_INPUT: 单页成品节点需要页面运行范围"
            )
        production = build_page_production_readiness(db, page)
        if not production.ready:
            messages = "；".join(item.message for item in production.blockers)
            raise WorkflowNodeExecutionError(f"PAGE_NOT_PRODUCTION_READY: {messages}")
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
            raise WorkflowNodeExecutionError(
                "UNSUPPORTED_INPUT: 整章导出节点需要章节运行范围"
            )
        # Job reclaim / RQ redelivery re-executes this handler after a previous
        # attempt already committed the bundle for the same deterministic
        # artifact; reuse that row instead of duplicating it. (Only sequential
        # re-execution is covered; truly concurrent double-execution would need
        # a DB unique constraint, which requires a migration.)
        try:
            bundle = create_export(
                chapter.id,
                ExportRequest(export_type="JSON"),
                db,
                reuse_existing=True,
            )
        except HTTPException as error:
            # The route's 4xx detail (e.g. 第 N 页尚未达到生产通过状态) is the
            # actionable cause; as a deterministic precondition failure it must
            # surface on the node instead of being retried and masked as an
            # unclassified exception.
            raise WorkflowNodeExecutionError(str(error.detail)) from error
        if not isinstance(bundle, ExportBundle):
            raise WorkflowNodeExecutionError("导出节点没有产生 ExportBundle")
        node_run.output_refs = {
            "job_id": job.id,
            "node_type": node_run.node_type,
            "export_id": bundle.id,
            "export_type": bundle.export_type,
            "storage_key": bundle.storage_key,
        }
    else:
        raise WorkflowNodeExecutionError(f"UNSUPPORTED_NODE_TYPE: {node_run.node_type}")
    db.flush()

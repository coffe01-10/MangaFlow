from __future__ import annotations

from typing import Any

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

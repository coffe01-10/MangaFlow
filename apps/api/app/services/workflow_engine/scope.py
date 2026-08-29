from __future__ import annotations

from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    Chapter,
    MangaPage,
    PageCandidate,
    ScriptRevision,
    SourceSegment,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.page_completion import build_chapter_production_readiness
from app.workflow_schemas import (
    WorkflowGraph,
    WorkflowNodeDefinition,
)


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
    if node.type == "source.approved_pages":
        chapter = _scope_chapter(db, run)
        if not chapter:
            return {"chapter_id": None, "page_ids": [], "kind": "pages", "available": False}
        production = build_chapter_production_readiness(db, chapter)
        return {
            "chapter_id": chapter.id,
            "page_ids": [item.page_id for item in production.pages if item.ready],
            "kind": "pages",
            "available": production.ready,
            "ready_pages": production.ready_pages,
            "total_pages": production.total_pages,
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


def _graph_for_run(db: Session, run: WorkflowRun) -> WorkflowGraph:
    version = db.get(WorkflowVersion, run.workflow_version_id)
    return WorkflowGraph.model_validate(version.graph)


def _latest_script(db: Session, run: WorkflowRun) -> ScriptRevision | None:
    chapter = _scope_chapter(db, run)
    if not chapter:
        return None
    return db.scalar(
        select(ScriptRevision)
        .where(ScriptRevision.chapter_id == chapter.id)
        .order_by(ScriptRevision.revision_no.desc())
    )


def _candidate_for_run(
    db: Session,
    run: WorkflowRun,
    node_runs: list[WorkflowNodeRun],
) -> PageCandidate | None:
    for item in reversed(node_runs):
        candidate_id = item.output_refs.get("candidate_id")
        candidate = db.get(PageCandidate, candidate_id) if candidate_id else None
        if candidate:
            return candidate
    if run.scope_type == "CANDIDATE" and run.scope_id:
        return db.get(PageCandidate, run.scope_id)
    if run.scope_type == "PAGE" and run.scope_id:
        page = db.get(MangaPage, run.scope_id)
        if page and page.selected_candidate_id:
            return db.get(PageCandidate, page.selected_candidate_id)
    return None

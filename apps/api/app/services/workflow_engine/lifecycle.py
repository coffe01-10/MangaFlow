from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    GenerationJob,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.services.character_packages import resolve_package_selections
from app.services.job_service import mark_job_cancelled
from app.services.model_router import model_supports_resolution, resolve_model
from app.services.ordinal_allocator import (
    BatchOrdinalConflictError,
    commit_ordinal_transaction,
    create_generation_batch,
)
from app.services.page_readiness import ensure_page_ready
from app.services.scene_assets import scene_asset_snapshot, scene_reference_assets
from app.services.workflow_engine.catalog import NODE_TYPE_MAP
from app.services.workflow_engine.planning import create_workflow_run
from app.services.workflow_engine.reconciliation import (
    _parent_job_ids,
    get_run,
    reconcile_run,
)
from app.services.workflow_engine.scope import _graph_for_run


def approve_node(
    db: Session,
    run_id: str,
    node_id: str,
    candidate_id: str | None = None,
    image_model_alias: str | None = None,
    resolution: str | None = None,
) -> WorkflowRun:
    # `create_job`/`enqueue_job` 是模块级 monkeypatch 接缝（审批回滚回归依赖），
    # 必须在调用时经 facade 解析。
    from app.services import workflow_engine as engine

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
        # Lock order matches create_page_candidate: project/page (batch) then
        # package rows inside resolve_package_selections.
        batch = create_generation_batch(
            db,
            project_id=run.project_id,
            chapter_id=chapter.id,
            page_id=page.id,
            generation_kind="PAGE",
        )
        project = db.get(Project, run.project_id)
        package_batch = resolve_package_selections(
            db,
            project=project,
            page=page,
            selections={},
            style_id=page.style_id,
        )
        # Same unified readiness gate as create_page_candidate (architecture
        # §6): style activation, palette confirmation, test image approval,
        # chapter status and worker availability must all hold before a paid
        # workflow generation is queued.
        ensure_page_ready(db, page, get_settings(), package_gate=package_batch.gate)
        reference_selections: dict[str, dict[str, str | None]] = dict(
            package_batch.normalized
        )
        reference_asset_ids: list[str] = []
        for character_id in visible_character_ids:
            if character_id in package_batch.normalized:
                selection = package_batch.normalized[character_id]
                if selection.get("character_asset_id"):
                    reference_asset_ids.append(selection["character_asset_id"])
                if selection.get("outfit_asset_id"):
                    reference_asset_ids.append(selection["outfit_asset_id"])
                continue
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
        snapshot = {
            "storyboard_version": page.storyboard_version,
            "reference_selections": reference_selections,
            # Freeze the queue-time scene asset facts like create_page_candidate
            # so the worker compiles the background from the frozen snapshot
            # instead of silently falling back to Panel.background.
            "scene_asset": scene_asset_snapshot(db, page),
        }
        if package_batch.snapshot:
            snapshot["character_packages"] = package_batch.snapshot
        candidate = PageCandidate(
            batch_id=batch.id,
            page_id=page.id,
            ordinal=1,
            model_alias=image_model_alias,
            catalog_model_id=resolved_model.model.id,
            resolution=Resolution(selected_resolution),
            status="QUEUED",
            based_on_storyboard_version=page.storyboard_version,
            prompt_snapshot=snapshot,
        )
        db.add(candidate)
        db.flush()
        dependency_ids = _parent_job_ids(db, run, graph, node_id)
        # Lease the scene reference images alongside character/outfit refs
        # (contract §6): deleting a scene reference between queueing and
        # execution must fail the job with 409 semantics, not silently
        # generate against a degraded reference set.
        scene_reference_ids = [item.id for item in scene_reference_assets(db, page)]
        job = engine.create_job(
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
            reference_asset_ids=[*reference_asset_ids, *scene_reference_ids],
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
        engine.enqueue_job(db, job)
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

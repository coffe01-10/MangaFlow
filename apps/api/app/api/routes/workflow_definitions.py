from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, WorkflowDefinition, WorkflowRun, WorkflowVersion, utcnow
from app.services.ordinal_allocator import OrdinalConflictError
from app.services.workflow_engine import (
    PublishRevisionConflictError,
    approve_node,
    blank_graph,
    cancel_run,
    canonical_graph,
    chapter_export_graph,
    create_workflow_run,
    default_graph,
    get_run,
    node_type_catalog,
    publish_workflow,
    reconcile_run,
    retry_run,
    validate_graph,
)
from app.workflow_schemas import (
    WorkflowCreate,
    WorkflowImportRequest,
    WorkflowNodeApproveRequest,
    WorkflowNodeTypeRead,
    WorkflowRead,
    WorkflowRestoreRequest,
    WorkflowRunCreate,
    WorkflowRunRead,
    WorkflowUpdate,
    WorkflowValidationRead,
    WorkflowVersionRead,
)

router = APIRouter()


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _workflow(db: Session, workflow_id: str) -> WorkflowDefinition:
    workflow = db.get(WorkflowDefinition, workflow_id)
    if not workflow or workflow.deleted_at is not None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return workflow


def _run(db: Session, run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="工作流运行不存在")
    return run


def _value_error(error: ValueError, *, status_code: int = 409) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(error))


@router.get("/workflow-node-types", response_model=list[WorkflowNodeTypeRead])
def list_node_types() -> list[WorkflowNodeTypeRead]:
    return node_type_catalog()


@router.get("/projects/{project_id}/workflows", response_model=list[WorkflowRead])
def list_workflows(project_id: str, db: Session = Depends(get_db)) -> list[WorkflowDefinition]:
    _project(db, project_id)
    return list(
        db.scalars(
            select(WorkflowDefinition)
            .where(
                WorkflowDefinition.project_id == project_id,
                WorkflowDefinition.deleted_at.is_(None),
            )
            .order_by(WorkflowDefinition.created_at)
        )
    )


@router.post(
    "/projects/{project_id}/workflows",
    response_model=WorkflowRead,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow(
    project_id: str,
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
) -> WorkflowDefinition:
    _project(db, project_id)
    template_graphs = {
        "manga_default": default_graph,
        "chapter_export": chapter_export_graph,
        "blank": blank_graph,
    }
    workflow = WorkflowDefinition(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        draft_graph=template_graphs[payload.template](),
    )
    db.add(workflow)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="项目内工作流名称不能重复") from error
    db.refresh(workflow)
    return workflow


@router.post(
    "/projects/{project_id}/workflows/import",
    response_model=WorkflowRead,
    status_code=status.HTTP_201_CREATED,
)
def import_workflow(
    project_id: str,
    payload: WorkflowImportRequest,
    db: Session = Depends(get_db),
) -> WorkflowDefinition:
    _project(db, project_id)
    workflow = WorkflowDefinition(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        draft_graph=canonical_graph(payload.graph),
    )
    db.add(workflow)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="项目内工作流名称不能重复") from error
    db.refresh(workflow)
    return workflow


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
def get_workflow(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowDefinition:
    return _workflow(db, workflow_id)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowRead)
def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
) -> WorkflowDefinition:
    workflow = _workflow(db, workflow_id)
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "draft_graph" in values:
        values["draft_graph"] = canonical_graph(values["draft_graph"])
    # Claim the row with an atomic conditional update so concurrent PATCHes
    # (and concurrent restores) cannot both pass an in-memory version
    # comparison (same pattern as _claim_panel_version / scene asset PATCH).
    claimed = db.execute(
        update(WorkflowDefinition)
        .where(
            WorkflowDefinition.id == workflow.id,
            WorkflowDefinition.version == payload.version,
        )
        .values(version=WorkflowDefinition.version + 1)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        db.rollback()
        raise HTTPException(status_code=409, detail="工作流已被其他页面修改，请刷新后重试")
    if "draft_graph" in values:
        workflow.draft_version += 1
    for field, value in values.items():
        setattr(workflow, field, value)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="项目内工作流名称不能重复") from error
    db.refresh(workflow)
    return workflow


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(workflow_id: str, db: Session = Depends(get_db)) -> Response:
    workflow = _workflow(db, workflow_id)
    # Soft-deleting a definition that still has live runs would orphan them:
    # reconcile/approve keep executing paid jobs for a workflow the studio no
    # longer lists (#139). Refuse like delete_script — cancelling the runs is
    # the caller's decision, never a delete side effect.
    active_run = db.scalar(
        select(WorkflowRun.id)
        .where(
            WorkflowRun.workflow_id == workflow.id,
            WorkflowRun.status.not_in({"COMPLETED", "CANCELLED", "FAILED"}),
        )
        .limit(1)
    )
    if active_run is not None:
        raise HTTPException(status_code=409, detail="工作流仍有进行中的运行，请先取消")
    workflow.deleted_at = utcnow()
    workflow.is_active = False
    workflow.version += 1
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workflows/{workflow_id}/export")
def export_workflow(workflow_id: str, db: Session = Depends(get_db)) -> dict:
    workflow = _workflow(db, workflow_id)
    return {
        "schema": "mangaflow.workflow.v2",
        "name": workflow.name,
        "description": workflow.description,
        "graph": deepcopy(workflow.draft_graph),
    }


@router.post("/workflows/{workflow_id}/validate", response_model=WorkflowValidationRead)
def validate_workflow(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowValidationRead:
    return validate_graph(_workflow(db, workflow_id).draft_graph)


@router.post("/workflows/{workflow_id}/publish", response_model=WorkflowVersionRead)
def publish(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowVersion:
    try:
        return publish_workflow(db, _workflow(db, workflow_id))
    except PublishRevisionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise _value_error(error, status_code=422) from error


@router.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionRead])
def list_versions(workflow_id: str, db: Session = Depends(get_db)) -> list[WorkflowVersion]:
    _workflow(db, workflow_id)
    return list(
        db.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.revision.desc())
        )
    )


@router.post("/workflow-versions/{version_id}/restore", response_model=WorkflowRead)
def restore_version(
    version_id: str,
    payload: WorkflowRestoreRequest,
    db: Session = Depends(get_db),
) -> WorkflowDefinition:
    version = db.get(WorkflowVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="工作流版本不存在")
    workflow = _workflow(db, version.workflow_id)
    # Claim the workflow row with an atomic conditional update so a concurrent
    # restore or PATCH cannot both pass an in-memory version comparison and
    # silently overwrite each other's graph (same pattern as update_workflow).
    claimed = db.execute(
        update(WorkflowDefinition)
        .where(
            WorkflowDefinition.id == workflow.id,
            WorkflowDefinition.version == payload.version,
        )
        .values(version=WorkflowDefinition.version + 1)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        db.rollback()
        raise HTTPException(status_code=409, detail="工作流已被其他页面修改，请刷新后重试")
    workflow.draft_graph = deepcopy(version.graph)
    workflow.draft_version += 1
    db.commit()
    db.refresh(workflow)
    return workflow


@router.get("/workflows/{workflow_id}/runs", response_model=list[WorkflowRunRead])
def list_runs(workflow_id: str, db: Session = Depends(get_db)) -> list[WorkflowRun]:
    _workflow(db, workflow_id)
    runs = list(
        db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow_id)
            .order_by(WorkflowRun.created_at.desc())
        )
    )
    return [get_run(db, item.id) for item in runs]


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    workflow_id: str,
    payload: WorkflowRunCreate,
    db: Session = Depends(get_db),
) -> WorkflowRun:
    try:
        return create_workflow_run(
            db,
            _workflow(db, workflow_id),
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            start_node_ids=payload.start_node_ids,
            stop_node_ids=payload.stop_node_ids,
        )
    except OrdinalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise _value_error(error) from error


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunRead)
def read_run(run_id: str, db: Session = Depends(get_db)) -> WorkflowRun:
    _run(db, run_id)
    return reconcile_run(db, run_id)


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunRead)
def stop_run(run_id: str, db: Session = Depends(get_db)) -> WorkflowRun:
    return cancel_run(db, _run(db, run_id))


@router.post(
    "/workflow-runs/{run_id}/retry",
    response_model=WorkflowRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def rerun(run_id: str, db: Session = Depends(get_db)) -> WorkflowRun:
    try:
        return retry_run(db, _run(db, run_id))
    except OrdinalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise _value_error(error) from error


@router.post(
    "/workflow-runs/{run_id}/nodes/{node_id}/approve",
    response_model=WorkflowRunRead,
)
def approve(
    run_id: str,
    node_id: str,
    payload: WorkflowNodeApproveRequest,
    db: Session = Depends(get_db),
) -> WorkflowRun:
    try:
        return approve_node(
            db,
            run_id,
            node_id,
            payload.candidate_id,
            payload.image_model_alias,
            payload.resolution,
        )
    except OrdinalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise _value_error(error) from error

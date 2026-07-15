from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.states import JobStatus
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    Scene,
    StyleProfile,
    StyleStatus,
    WorkflowDefinition,
)
from app.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.settings_schemas import ProjectSummaryRead

router = APIRouter()


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(
        db.scalars(
            select(Project).where(Project.deleted_at.is_(None)).order_by(Project.updated_at.desc())
        )
    )


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    values = payload.model_dump()
    project = Project(
        **values,
        image_model_alias=values["last_image_model_alias"] or "image.nano_banana_2",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.get("/{project_id}/summary", response_model=ProjectSummaryRead)
def get_project_summary(project_id: str, db: Session = Depends(get_db)) -> ProjectSummaryRead:
    """Return the small, shared project shell payload in one database round trip."""

    pending_statuses = (
        JobStatus.WAITING,
        JobStatus.QUEUED,
        JobStatus.PREPARING,
        JobStatus.UPLOADING_REFERENCES,
        JobStatus.GENERATING,
        JobStatus.OCR_CHECKING,
        JobStatus.CONSISTENCY_CHECKING,
        JobStatus.REPAIRING,
    )

    chapter_count = (
        select(func.count(Chapter.id))
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    page_count = (
        select(func.count(MangaPage.id))
        .join(Chapter, Chapter.id == MangaPage.chapter_id)
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    ready_page_count = (
        select(func.count(MangaPage.id))
        .join(Chapter, Chapter.id == MangaPage.chapter_id)
        .where(
            Chapter.project_id == Project.id,
            Chapter.deleted_at.is_(None),
            func.json_array_length(MangaPage.scene_ids) > 0,
            func.json_array_length(MangaPage.beat_ids) > 0,
            MangaPage.source_coverage["complete"].as_boolean().is_(True),
        )
        .correlate(Project)
        .scalar_subquery()
    )
    asset_count = (
        select(func.count(Asset.id))
        .where(Asset.project_id == Project.id, Asset.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    scene_count = (
        select(func.count(Scene.id))
        .join(Chapter, Chapter.id == Scene.chapter_id)
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    candidate_count = (
        select(func.count(PageCandidate.id))
        .join(GenerationBatch, GenerationBatch.id == PageCandidate.batch_id)
        .where(
            GenerationBatch.project_id == Project.id,
            PageCandidate.deleted_at.is_(None),
        )
        .correlate(Project)
        .scalar_subquery()
    )
    pending_job_count = (
        select(func.count(GenerationJob.id))
        .where(
            GenerationJob.project_id == Project.id,
            GenerationJob.status.in_(pending_statuses),
        )
        .correlate(Project)
        .scalar_subquery()
    )
    failed_job_count = (
        select(func.count(GenerationJob.id))
        .where(
            GenerationJob.project_id == Project.id,
            GenerationJob.status == JobStatus.FAILED,
        )
        .correlate(Project)
        .scalar_subquery()
    )
    active_style_name = (
        select(StyleProfile.name)
        .where(
            StyleProfile.project_id == Project.id,
            StyleProfile.status == StyleStatus.ACTIVE,
        )
        .order_by(StyleProfile.updated_at.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )
    active_workflow_id = (
        select(WorkflowDefinition.id)
        .where(
            WorkflowDefinition.project_id == Project.id,
            WorkflowDefinition.is_active.is_(True),
            WorkflowDefinition.deleted_at.is_(None),
        )
        .order_by(WorkflowDefinition.updated_at.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )
    active_workflow_status = (
        select(
            case(
                (WorkflowDefinition.published_version_id.is_not(None), "PUBLISHED"),
                else_="DRAFT",
            )
        )
        .where(
            WorkflowDefinition.project_id == Project.id,
            WorkflowDefinition.is_active.is_(True),
            WorkflowDefinition.deleted_at.is_(None),
        )
        .order_by(WorkflowDefinition.updated_at.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )

    row = db.execute(
        select(
            Project.id,
            chapter_count.label("chapter_count"),
            page_count.label("page_count"),
            ready_page_count.label("ready_page_count"),
            asset_count.label("asset_count"),
            scene_count.label("scene_count"),
            candidate_count.label("candidate_count"),
            pending_job_count.label("pending_job_count"),
            failed_job_count.label("failed_job_count"),
            active_style_name.label("active_style_name"),
            active_workflow_id.label("active_workflow_id"),
            active_workflow_status.label("active_workflow_status"),
        ).where(Project.id == project_id, Project.deleted_at.is_(None))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    statuses = {
        "source": "READY" if row.chapter_count else "EMPTY",
        "assets": "READY" if row.asset_count else "EMPTY",
        "script": "READY" if row.scene_count else "NOT_STARTED",
        "storyboard": (
            "NOT_STARTED"
            if not row.page_count
            else "READY"
            if row.ready_page_count == row.page_count
            else "NEEDS_REVIEW"
        ),
        "generate": (
            "RUNNING"
            if row.pending_job_count
            else "READY"
            if row.candidate_count
            else "NEEDS_REVIEW"
            if row.page_count and row.ready_page_count < row.page_count
            else "NOT_STARTED"
        ),
        "library": "READY" if row.candidate_count or row.asset_count else "EMPTY",
        "jobs": (
            "FAILED"
            if row.failed_job_count
            else "RUNNING"
            if row.pending_job_count
            else "IDLE"
        ),
        "workflow": row.active_workflow_status or "NOT_CONFIGURED",
    }
    return ProjectSummaryRead(
        project_id=row.id,
        chapter_count=row.chapter_count,
        page_count=row.page_count,
        asset_count=row.asset_count,
        pending_job_count=row.pending_job_count,
        failed_job_count=row.failed_job_count,
        active_style_name=row.active_style_name,
        active_workflow_id=row.active_workflow_id,
        active_workflow_status=row.active_workflow_status,
        section_statuses=statuses,
    )


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)
) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.version != payload.version:
        raise HTTPException(status_code=409, detail="项目已被其他操作更新，请刷新后重试")

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    for key, value in changes.items():
        setattr(project, key, value)
    if changes.get("last_image_model_alias"):
        project.image_model_alias = changes["last_image_model_alias"]
    project.version += 1
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_project(project_id: str, db: Session = Depends(get_db)) -> None:
    from app.models import utcnow

    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.deleted_at = utcnow()
    project.version += 1
    db.commit()

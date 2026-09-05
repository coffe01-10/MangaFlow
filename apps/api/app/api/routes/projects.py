from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.helpers import reject_required_nulls
from app.config import get_settings
from app.database import get_db
from app.domain.states import JobStatus
from app.models import (
    AIModel,
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    ProviderConnection,
    ProviderKey,
    Scene,
    StyleProfile,
    StyleStatus,
    WorkflowDefinition,
)
from app.schemas import (
    DashboardAIOverview,
    DashboardNextAction,
    DashboardTotals,
    ProjectCreate,
    ProjectDashboardItem,
    ProjectDashboardRead,
    ProjectRead,
    ProjectUpdate,
)
from app.services.credential_source import (
    CLI_SESSION,
    ENV_SERVICE_ACCOUNT,
    credential_source_for_protocol,
    environment_credentials_ready,
)
from app.services.job_service import mark_job_cancelled
from app.services.model_availability import count_available_catalog_models
from app.settings_schemas import ProjectSummaryRead

router = APIRouter()


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(
        db.scalars(
            select(Project).where(Project.deleted_at.is_(None)).order_by(Project.updated_at.desc())
        )
    )


@router.get("/dashboard", response_model=ProjectDashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> ProjectDashboardRead:
    return _get_dashboard_snapshot(db)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    values = payload.model_dump()
    project = Project(
        **values,
        image_model_alias=values["last_image_model_alias"],
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


def _ai_overview(db: Session) -> DashboardAIOverview:
    """Aggregate the provider/model facts shown on the homepage badges."""

    settings = get_settings()
    enabled_model_count = count_available_catalog_models(db, settings)
    connections = db.execute(
        select(
            ProviderConnection.protocol,
            ProviderConnection.health_state,
            func.max(case((ProviderKey.enabled.is_(True), 1), else_=0)),
        )
        .outerjoin(ProviderKey, ProviderKey.connection_id == ProviderConnection.id)
        .group_by(ProviderConnection.id)
    ).all()
    healthy = sum(state in {"HEALTHY", "AVAILABLE"} for _, state, _ in connections)
    configured = sum(
        bool(has_enabled_key)
        or (
            credential_source_for_protocol(protocol) == ENV_SERVICE_ACCOUNT
            and environment_credentials_ready(settings, protocol)
        )
        or (
            credential_source_for_protocol(protocol) == CLI_SESSION
            and state == "AVAILABLE"
        )
        for protocol, state, has_enabled_key in connections
    )
    return DashboardAIOverview(
        enabled_model_count=enabled_model_count,
        healthy_connection_count=healthy,
        configured_connection_count=configured,
    )


def _get_dashboard_snapshot(db: Session) -> ProjectDashboardRead:
    """Return a consistent dashboard snapshot without per-project request waterfalls."""

    ai_overview = _ai_overview(db)
    projects = list(
        db.scalars(
            select(Project).where(Project.deleted_at.is_(None)).order_by(Project.updated_at.desc())
        )
    )
    if not projects:
        return ProjectDashboardRead(
            totals=DashboardTotals(
                project_count=0,
                page_count=0,
                selected_page_count=0,
                review_page_count=0,
                pending_job_count=0,
            ),
            ai_overview=ai_overview,
            projects=[],
        )

    project_ids = [project.id for project in projects]
    chapter_rows = list(
        db.execute(
            select(Chapter.id, Chapter.project_id).where(
                Chapter.project_id.in_(project_ids), Chapter.deleted_at.is_(None)
            )
        )
    )
    project_by_chapter = {row.id: row.project_id for row in chapter_rows}
    chapter_ids = list(project_by_chapter)
    pages = (
        list(db.scalars(select(MangaPage).where(MangaPage.chapter_id.in_(chapter_ids))))
        if chapter_ids
        else []
    )
    selected_ids = [page.selected_candidate_id for page in pages if page.selected_candidate_id]
    selected_candidates = {
        candidate.id: candidate
        for candidate in (
            db.scalars(select(PageCandidate).where(PageCandidate.id.in_(selected_ids)))
            if selected_ids
            else []
        )
    }
    candidate_counts = dict(
        db.execute(
            select(GenerationBatch.project_id, func.count(PageCandidate.id))
            .join(PageCandidate, PageCandidate.batch_id == GenerationBatch.id)
            .where(
                GenerationBatch.project_id.in_(project_ids),
                PageCandidate.deleted_at.is_(None),
            )
            .group_by(GenerationBatch.project_id)
        ).all()
    )
    job_rows = list(
        db.execute(
            select(GenerationJob.project_id, GenerationJob.status, func.count(GenerationJob.id))
            .where(GenerationJob.project_id.in_(project_ids))
            .group_by(GenerationJob.project_id, GenerationJob.status)
        )
    )

    pending_statuses = {
        JobStatus.WAITING,
        JobStatus.QUEUED,
        JobStatus.PREPARING,
        JobStatus.UPLOADING_REFERENCES,
        JobStatus.GENERATING,
        JobStatus.OCR_CHECKING,
        JobStatus.CONSISTENCY_CHECKING,
        JobStatus.REPAIRING,
    }
    chapters_by_project: dict[str, int] = {project_id: 0 for project_id in project_ids}
    pages_by_project: dict[str, list[MangaPage]] = {project_id: [] for project_id in project_ids}
    jobs_by_project: dict[str, dict[str, int]] = {
        project_id: {"pending": 0, "failed": 0} for project_id in project_ids
    }
    for row in chapter_rows:
        chapters_by_project[row.project_id] += 1
    for page in pages:
        pages_by_project[project_by_chapter[page.chapter_id]].append(page)
    for project_id, job_status, count in job_rows:
        if job_status in pending_statuses:
            jobs_by_project[project_id]["pending"] += count
        elif job_status == JobStatus.FAILED:
            jobs_by_project[project_id]["failed"] += count

    items: list[ProjectDashboardItem] = []
    for project in projects:
        project_pages = pages_by_project[project.id]
        selected_page_count = sum(bool(page.selected_candidate_id) for page in project_pages)
        stale_selected_page_count = 0
        review_page_ids: set[str] = set()
        for page in project_pages:
            if page.continuity_status in {"NEEDS_REVIEW", "NEEDS_RECHECK"}:
                review_page_ids.add(page.id)
            selected = selected_candidates.get(page.selected_candidate_id or "")
            selected_is_stale = bool(
                selected
                and (
                    selected.based_on_storyboard_version is None
                    or selected.based_on_storyboard_version != page.storyboard_version
                )
            )
            if selected_is_stale:
                stale_selected_page_count += 1
                if page.selected_candidate_ack_version != page.storyboard_version:
                    review_page_ids.add(page.id)

        chapter_count = chapters_by_project[project.id]
        page_count = len(project_pages)
        candidate_count = candidate_counts.get(project.id, 0)
        job_counts = jobs_by_project[project.id]
        if not chapter_count:
            next_action = DashboardNextAction(
                section="source", label="导入第一章", reason="项目还没有原作章节"
            )
        elif not page_count:
            next_action = DashboardNextAction(
                section="storyboard", label="创建分页分镜", reason="章节尚未规划漫画页面"
            )
        elif review_page_ids:
            next_action = DashboardNextAction(
                section="storyboard",
                label=f"复查 {len(review_page_ids)} 页",
                reason="分镜或已采用候选需要确认",
            )
        elif selected_page_count < page_count:
            next_action = DashboardNextAction(
                section="generate",
                label="继续生成",
                reason=f"还有 {page_count - selected_page_count} 页未采用候选",
            )
        elif job_counts["failed"]:
            next_action = DashboardNextAction(
                section="jobs", label="处理失败任务", reason="任务中心存在失败记录"
            )
        else:
            next_action = DashboardNextAction(
                section="library", label="查看已采用页面", reason="当前页面均已有采用版本"
            )

        items.append(
            ProjectDashboardItem(
                project=ProjectRead.model_validate(project),
                chapter_count=chapter_count,
                page_count=page_count,
                selected_page_count=selected_page_count,
                review_page_count=len(review_page_ids),
                stale_selected_page_count=stale_selected_page_count,
                candidate_count=candidate_count,
                pending_job_count=job_counts["pending"],
                failed_job_count=job_counts["failed"],
                next_action=next_action,
            )
        )

    return ProjectDashboardRead(
        totals=DashboardTotals(
            project_count=len(projects),
            page_count=sum(item.page_count for item in items),
            selected_page_count=sum(item.selected_page_count for item in items),
            review_page_count=sum(item.review_page_count for item in items),
            pending_job_count=sum(item.pending_job_count for item in items),
        ),
        ai_overview=ai_overview,
        projects=items,
    )


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
    reject_required_nulls(Project, changes)
    for field, model_type in (
        ("default_text_model_id", "TEXT"),
        ("last_image_model_id", "IMAGE"),
    ):
        model_id = changes.get(field)
        if not model_id:
            continue
        model = db.get(AIModel, model_id)
        if not model or model.model_type != model_type or not model.enabled:
            raise HTTPException(status_code=422, detail=f"{field} 指向的模型不可用")
    for key, value in changes.items():
        setattr(project, key, value)
    if changes.get("last_image_model_alias"):
        project.image_model_alias = changes["last_image_model_alias"]
    project.version += 1
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_project(
    project_id: str,
    confirm_name: str = Query(min_length=1, max_length=120),
    db: Session = Depends(get_db),
) -> None:
    from app.models import utcnow

    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if confirm_name.strip() != project.name:
        raise HTTPException(status_code=409, detail="项目名称不匹配，未执行删除")
    terminal_statuses = {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.NEEDS_REVIEW,
    }
    active_jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.status.not_in(terminal_statuses),
            )
        )
    )
    for job in active_jobs:
        mark_job_cancelled(db, job)
    project.deleted_at = utcnow()
    project.version += 1
    db.commit()

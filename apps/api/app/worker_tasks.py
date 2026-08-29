import json
import os
import socket
from datetime import timedelta
from threading import Event, Lock, Thread
from uuid import uuid4

from sqlalchemy import func, or_, select, update

from app.config import get_settings
from app.database import SessionLocal
from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import (
    MultimodalRequest,
    ProviderAdapterError,
)
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
    StyleProfile,
    WorkflowNodeRun,
    utcnow,
)
from app.services.ai_schemas import PageInspectionOutput, StyleAnalysisOutput
from app.services.page_completion import (
    PASSING_QUALITY_OUTCOMES,
    REQUIRED_QUALITY_CATEGORIES,
    latest_inspections_by_category,
)
from app.services.prompt_compiler import compile_page_prompt
from app.services.worker_handlers import provider
from app.services.worker_handlers.asset_generate import _run_asset_generate
from app.services.worker_handlers.execution import (
    JobCancelledError,
    JobLeaseLostError,
    StaleStoryboardVersionError,
    _commit_owned_progress,
    _ensure_job_not_cancelled,
    _lease_is_expired,
)
from app.services.worker_handlers.page_generate import _run_page_generate
from app.services.worker_handlers.provider import (
    _asset_path,
    _binding,
    _invoke_provider,
    _lease_reference_assets,
    _text_model_reference,
)
from app.services.worker_handlers.story_parse import _run_story_parse

ACTIVE_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}
CLAIMABLE_STATUSES = {JobStatus.WAITING, JobStatus.QUEUED}
EXECUTION_RESERVATION_LOCK = Lock()


def _adapter(_alias: str):
    """Legacy test seam retained while production calls use catalog bindings."""

    return None


# Handlers bind models through ``provider._binding``; bridge this module's
# ``_adapter`` seam at call time so existing monkeypatches keep steering it.
provider.install_legacy_adapter_lookup(lambda alias: _adapter(alias))


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex}"


def _lease_duration() -> timedelta:
    return timedelta(seconds=get_settings().job_lease_seconds)


class _LeaseHeartbeat:
    """Refresh a job lease without sharing the worker's SQLAlchemy session."""

    def __init__(self, job_id: str, owner: str):
        self.job_id = job_id
        self.owner = owner
        self.duration = _lease_duration()
        self.interval = max(5.0, min(30.0, self.duration.total_seconds() / 3))
        self.stop = Event()
        self.lost = False
        self.thread: Thread | None = None

    def __enter__(self):
        self.thread = Thread(
            target=self._run,
            name=f"mangaflow-lease-{self.job_id[:8]}",
            daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=max(1.0, self.interval))

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                now = utcnow()
                with SessionLocal() as db:
                    updated = db.execute(
                        update(GenerationJob)
                        .where(
                            GenerationJob.id == self.job_id,
                            GenerationJob.lease_owner == self.owner,
                            GenerationJob.lease_expires_at.is_not(None),
                            GenerationJob.lease_expires_at > now,
                            GenerationJob.status.in_(ACTIVE_STATUSES),
                            GenerationJob.status.not_in(
                                {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
                            ),
                        )
                        .values(lease_expires_at=now + self.duration)
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
                    if updated.rowcount != 1:
                        self.lost = True
                        return
            except Exception:
                # A transient heartbeat failure should not turn a healthy
                # provider call into a second paid request.  The lease itself
                # remains the source of truth and will be reclaimed if it
                # eventually expires.
                if self.stop.wait(1.0):
                    return


def _claim_job(db, job_id: str, owner: str) -> GenerationJob | None:
    """Atomically claim one queued/retryable job for this worker."""

    job = db.get(GenerationJob, job_id)
    if not job or job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
        return None
    now = utcnow()
    expired = job.status in ACTIVE_STATUSES and _lease_is_expired(job.lease_expires_at)
    if job.status not in CLAIMABLE_STATUSES and not expired:
        return None
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    is_postgres = dialect_name == "postgresql"

    if is_postgres:
        project = db.scalar(
            select(Project)
            .where(Project.id == job.project_id)
            .with_for_update()
        )
    else:
        project = db.get(Project, job.project_id)

    if not project or project.deleted_at is not None:
        return None

    expected_status = job.status
    active_subquery = (
        select(func.count(GenerationJob.id))
        .where(
            GenerationJob.project_id == project.id,
            GenerationJob.id != job.id,
            GenerationJob.status.in_(ACTIVE_STATUSES),
            or_(
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at > now,
            ),
        )
        .scalar_subquery()
    )

    claim_filter = [
        GenerationJob.id == job.id,
        GenerationJob.attempt_count < GenerationJob.max_attempts,
        active_subquery < project.default_concurrency,
    ]
    if expired:
        claim_filter.append(GenerationJob.status == expected_status)
        if job.lease_owner is not None:
            claim_filter.append(GenerationJob.lease_owner == job.lease_owner)
        else:
            claim_filter.append(GenerationJob.lease_owner.is_(None))
        if job.lease_expires_at is not None:
            claim_filter.append(GenerationJob.lease_expires_at <= now)
        else:
            claim_filter.append(GenerationJob.lease_expires_at.is_(None))
    else:
        claim_filter.append(GenerationJob.status == expected_status)
    updated = db.execute(
        update(GenerationJob)
        .where(*claim_filter)
        .values(
            status=JobStatus.PREPARING,
            progress=5,
            attempt_count=GenerationJob.attempt_count + 1,
            started_at=func.coalesce(GenerationJob.started_at, now),
            error_code=None,
            error_message=None,
            lease_owner=owner,
            lease_expires_at=now + _lease_duration(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        # 在仍持有锁的事务中通过严格条件更新标记等待状态，绝不释放锁后无条件覆盖新租约
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.status == expected_status,
                GenerationJob.lease_owner.is_(None),
                GenerationJob.lease_expires_at.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="CONCURRENCY_LIMIT",
                error_message="等待项目并发名额",
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return None
    db.commit()
    db.expire_all()
    return db.get(GenerationJob, job_id)


def _build_style_prompt_summary(analyzed: dict, color_mode: str) -> str:
    """Compile visual language without leaking subjects from the reference page."""

    prefix = "彩色日式漫画" if color_mode == "color" else "黑白日式漫画"
    visual_parts = [
        analyzed.get("line_art", ""),
        analyzed.get("screentone", ""),
        analyzed.get("contrast", ""),
        analyzed.get("panel_language", ""),
        analyzed.get("lighting", ""),
    ]
    return "；".join([prefix, *(part for part in visual_parts if part)])


def _build_color_palette(analyzed: dict) -> dict[str, str]:
    """Recover an editable palette when the model omits the optional palette object."""

    palette = analyzed.get("palette")
    if isinstance(palette, dict) and palette:
        return {str(key): str(value) for key, value in palette.items() if str(value).strip()}

    color_rules = [str(rule) for rule in analyzed.get("color_rules", []) if str(rule).strip()]
    return {
        "主色": color_rules[0] if color_rules else "低饱和冷灰蓝，保持克制与潮湿感",
        "辅助色": "低明度卡其灰与雾紫，只用于小面积识别和层次",
        "肤色": "偏冷的自然肤色，保留血色但避免过度红润",
        "发色": "深黑与低明度识别色，保留发丝层次和角色辨识度",
        "环境色": "潮湿京都的蓝灰、纸门米灰与深木色",
        "光影色": analyzed.get("lighting") or "柔和冷色散射光，阴影不使用纯黑硬切",
    }


def _run_style_analyze(db, job: GenerationJob) -> None:
    style = db.get(StyleProfile, job.target_id)
    if not style:
        raise RuntimeError("风格档案不存在")
    reference_ids = style.profile.get("reference_asset_ids", [])
    references = list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(reference_ids),
                Asset.deleted_at.is_(None),
                Asset.kind == "STYLE_REFERENCE",
            )
        )
    )
    if not references:
        raise RuntimeError("风格档案没有可用漫画参考图")
    _commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=35)
    visual_dimensions = (
        "线稿、网点、黑白对比、留白、人物画法、背景画法、光影"
        if style.color_mode == "monochrome"
        else "线稿、主辅色板、肤色与发色、上色方式、色彩光影、人物画法、背景画法"
    )
    atmosphere = job.request_parameters.get("palette_atmosphere", "")
    prompt = f"""分析这些漫画参考页的视觉风格，只总结可复用的画面语言，不识别作者姓名或作品名。
目标输出类型是{'黑白漫画' if style.color_mode == 'monochrome' else '彩色漫画'}。
输出{visual_dimensions}、日式分格语言、构图规则、禁止项，
以及一段可直接用于生图的中文 prompt_summary。彩色模式必须额外输出 palette，包含
主色、辅助色、肤色、发色、环境色和光影色，并输出 color_rules。
章节氛围补充：{atmosphere or '葬礼后的克制、潮湿京都与低饱和情绪'}。
不要复制参考页中的文字或剧情。"""
    _lease_reference_assets(db, job, [asset.id for asset in references[:8]])
    project = db.get(Project, style.project_id)
    binding = _binding(
        db,
        operation="multimodal_analysis",
        project_id=style.project_id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=tuple(_asset_path(asset).read_bytes() for asset in references[:8]),
                mime_types=tuple(asset.mime_type for asset in references[:8]),
            ),
            StyleAnalysisOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    analyzed = output.model_dump()
    analyzed["prompt_summary"] = _build_style_prompt_summary(analyzed, style.color_mode)
    analyzed["reference_asset_ids"] = reference_ids
    analyzed["palette_draft"] = (
        _build_color_palette(analyzed) if style.color_mode == "color" else {}
    )
    analyzed.pop("palette", None)
    analyzed["palette_confirmed"] = False
    analyzed["test_image_approved"] = False
    style.profile = analyzed
    if style.color_mode == "color":
        style.locked_fields = [
            "细腻线稿" if field == "黑白墨线" else field
            for field in style.locked_fields
            if field != "禁止彩色"
        ]
        if "低饱和色板" not in style.locked_fields:
            style.locked_fields = [*style.locked_fields, "低饱和色板"]
    style.status = "DRAFT"
    style.version += 1
    job.progress = 90


def _run_inspection(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate or not candidate.asset_id:
        raise RuntimeError("候选图片尚未生成")
    page = db.get(MangaPage, candidate.page_id)
    asset = db.get(Asset, candidate.asset_id)
    project = db.get(Project, db.get(Chapter, page.chapter_id).project_id)
    inspection_storyboard_version = page.storyboard_version
    _, snapshot = compile_page_prompt(db, page, project)
    categories = job.request_parameters.get(
        "categories",
        ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"],
    )
    prompt = f"""你是漫画成片质检员。对照结构化目标检查这张生成漫画页。
只检查这些类别：{json.dumps(categories, ensure_ascii=False)}。
目标剧本、格位、说话人、角色、服装与风格上下文：
{json.dumps(snapshot["input"], ensure_ascii=False, separators=(",", ":"))}
SPEAKER 检查气泡归属；
CHARACTER 检查脸、发型、体型和标志特征；OUTFIT 检查场景指定服装；
PROP 检查关键道具；CONTINUITY 检查与页面结构、场景状态和前后逻辑的一致性。
每个请求类别至少输出一项，字段为 category、outcome、score、severity、details、regions；
outcome 只能用 PASS、ACCEPTABLE、MISMATCH、MISSING、EXTRA；
details 必须写清 expected、observed 和 differences。
regions 使用 0 到 1 的归一化 x/y/width/height。"""
    _commit_owned_progress(
        db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=45
    )
    _lease_reference_assets(db, job, [asset.id])
    binding = _binding(
        db,
        operation="multimodal_analysis",
        project_id=project.id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=(_asset_path(asset).read_bytes(),),
                mime_types=(asset.mime_type,),
            ),
            PageInspectionOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    valid_outcomes = {
        "MATCH",
        "PASS",
        "ACCEPTABLE",
        "MISMATCH",
        "MISSING",
        "EXTRA",
    }
    passing_outcomes = {"MATCH", "PASS", "ACCEPTABLE"}
    requested = [str(item) for item in categories]
    seen: dict[str, object] = {}
    needs_review = False
    for item in output.items:
        category = str(item.category)
        if category not in requested:
            continue
        if item.outcome not in valid_outcomes:
            raise RuntimeError("质检结果包含非法 outcome")
        seen[category] = item
        if item.outcome not in passing_outcomes:
            needs_review = True
        db.add(
            InspectionResult(
                generation_record_id=candidate.generation_record_id,
                candidate_id=candidate.id,
                storyboard_version=inspection_storyboard_version,
                category=item.category,
                outcome=item.outcome,
                score=item.score,
                details=item.details.model_dump(),
                regions=item.regions,
                severity=item.severity,
            )
        )
    db.flush()
    db.refresh(page)
    if page.storyboard_version != inspection_storyboard_version:
        # Preserve the audit result, but never pass a newer storyboard with an old response.
        _commit_owned_progress(db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=85)
        return
    latest = latest_inspections_by_category(db, candidate.id, inspection_storyboard_version)
    complete = (
        bool(seen)
        and set(requested) <= set(seen)
        and set(REQUIRED_QUALITY_CATEGORIES) <= set(latest)
    )
    needs_review = needs_review or any(
        latest[category].outcome not in PASSING_QUALITY_OUTCOMES
        for category in REQUIRED_QUALITY_CATEGORIES
        if category in latest
    )
    if not complete:
        candidate.status = "READY"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            page.continuity_status = "NOT_CHECKED"
            page.version += 1
    elif needs_review:
        candidate.status = "NEEDS_REVIEW"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            page.continuity_status = "NEEDS_REVIEW"
            page.status = PageStatus.NEEDS_REPAIR
            page.version += 1
    else:
        candidate.status = "INSPECTED"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            page.continuity_status = "PASSED"
            page.status = PageStatus.FINAL_READY
            page.version += 1
    _commit_owned_progress(
        db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=85
    )


def _mark_worker_failure(
    db,
    job_id: str,
    owner: str,
    error_code: str,
    error_message: str,
    *,
    candidate_status: str = "FAILED",
    retryable: bool = False,
) -> tuple[bool, str | None, bool]:
    """Persist failure output or reset for retry while worker holds valid lease.

    Returns (updated, workflow_run_id, is_final_failure).
    """

    now = utcnow()
    job = db.get(GenerationJob, job_id)
    if not job:
        return False, None, False

    is_retryable = bool(retryable and (job.attempt_count < job.max_attempts))
    target_status = JobStatus.WAITING if is_retryable else JobStatus.FAILED

    updated = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.lease_owner == owner,
            GenerationJob.lease_expires_at.is_not(None),
            GenerationJob.lease_expires_at > now,
            GenerationJob.status.in_(ACTIVE_STATUSES),
            GenerationJob.status != JobStatus.CANCELLED,
        )
        .values(
            status=target_status,
            error_code=error_code,
            error_message=error_message[:500],
            finished_at=None if is_retryable else now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        return False, None, False

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id

    if not is_retryable:
        page_candidate = db.get(PageCandidate, job.target_id)
        if page_candidate:
            page_candidate.status = candidate_status
        asset_candidate = db.get(AssetCandidate, job.target_id)
        if asset_candidate:
            asset_candidate.status = "FAILED"
        style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
        if style:
            style.status = "DRAFT"

        if node_run and node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            node_run.status = "FAILED"
            node_run.error_code = error_code
            node_run.error_message = error_message[:500]
            node_run.finished_at = now

    db.commit()
    db.expire_all()
    return True, workflow_run_id, not is_retryable


def _defer_concurrency_wait(job_id: str) -> None:
    """Keep a slot-wait job schedulable instead of silently succeeding out of RQ."""

    from rq import Queue, get_current_job

    from app.services.job_service import rq_retry_policy

    current = get_current_job()
    if current is None:
        # LOCAL/AUTO's local executor already owns a bounded backoff loop.
        return
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if (
            job is None
            or job.status != JobStatus.WAITING
            or job.error_code != "CONCURRENCY_LIMIT"
        ):
            return
        retry = rq_retry_policy(job)
    # Use the running worker's queue/connection. A child-local thread cannot survive RQ exit.
    # Let a scheduling failure reach RQ's retry/error handling instead of hiding it.
    Queue(current.origin, connection=current.connection).enqueue_in(
        timedelta(seconds=3),
        "app.worker_tasks.execute_job",
        job_id,
        job_id=f"{job_id}-slot-{uuid4().hex}",
        job_timeout=settings.job_timeout_seconds,
        retry=retry,
    )


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    owner = _worker_id()
    db.info["job_lease_owner"] = owner
    try:
        job = db.get(GenerationJob, job_id)
        if not job or job.status == JobStatus.CANCELLED:
            return
        project = db.get(Project, job.project_id)
        if not project or project.deleted_at is not None:
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
            return
        db.info["job_id"] = job_id
        with EXECUTION_RESERVATION_LOCK:
            job = _claim_job(db, job_id, owner)
        if not job:
            _defer_concurrency_wait(job_id)
            return
        with _LeaseHeartbeat(job.id, owner) as heartbeat:
            if job.job_type in {"PAGE_GENERATE", "PAGE_REPAIR", "PAGE_UPSCALE"}:
                _run_page_generate(db, job)
            elif job.job_type == "ASSET_GENERATE":
                _run_asset_generate(db, job)
            elif job.job_type == "SOURCE_PARSE":
                _run_story_parse(db, job)
            elif job.job_type == "STYLE_ANALYZE":
                _run_style_analyze(db, job)
            elif job.job_type == "PAGE_INSPECT":
                _run_inspection(db, job)
            elif job.job_type == "WORKFLOW_NODE":
                from app.services.workflow_engine import execute_workflow_node

                execute_workflow_node(db, job)
            else:
                raise RuntimeError(f"未知任务类型：{job.job_type}")
            _ensure_job_not_cancelled(db, job)
            if heartbeat.lost:
                raise JobLeaseLostError("任务租约已被其他执行器接管")
            workflow_run_id = job.request_parameters.get("workflow_run_id")
            db.expire(
                job,
                attribute_names=[
                    "status",
                    "progress",
                    "finished_at",
                    "error_code",
                    "error_message",
                    "lease_owner",
                    "lease_expires_at",
                ],
            )
            with db.no_autoflush:
                completed = db.execute(
                    update(GenerationJob)
                    .where(
                        GenerationJob.id == job.id,
                        GenerationJob.lease_owner == owner,
                        GenerationJob.status != JobStatus.CANCELLED,
                    )
                    .values(
                        status=JobStatus.COMPLETED,
                        progress=100,
                        finished_at=utcnow(),
                        error_code=None,
                        error_message=None,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            if completed.rowcount != 1:
                current = db.get(GenerationJob, job.id)
                if current and current.status == JobStatus.CANCELLED:
                    raise JobCancelledError("任务已取消，完成状态不再写入")
                raise JobLeaseLostError("任务租约已被其他执行器接管")
            db.commit()
        if workflow_run_id:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
    except JobLeaseLostError:
        db.rollback()
        return
    except JobCancelledError:
        db.rollback()
        db.expire_all()
        job = db.get(GenerationJob, job_id)
        if job and job.status != JobStatus.COMPLETED and (
            job.status == JobStatus.CANCELLED or job.lease_owner == owner
        ):
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
        return
    except StaleStoryboardVersionError as error:
        db.rollback()
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            "STALE_STORYBOARD_VERSION",
            str(error),
            candidate_status="STALE",
            retryable=False,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    except ProviderAdapterError as error:
        db.rollback()
        is_retryable = getattr(error, "retryable", True)
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            error.code,
            error.user_message,
            retryable=is_retryable,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    except Exception as error:
        db.rollback()
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            "WORKER_ERROR",
            str(error),
            retryable=True,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    finally:
        db.close()

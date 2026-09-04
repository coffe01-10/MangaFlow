"""PAGE_INSPECT handler.

Owns quality inspection of generated page candidates: prompt building against
the compiled page snapshot, the paid multimodal call, inspection result
persistence and candidate/page status convergence.
"""

import json

from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import MultimodalRequest
from app.models import (
    Asset,
    Chapter,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
)
from app.services.ai_schemas import PageInspectionOutput
from app.services.page_completion import (
    PASSING_QUALITY_OUTCOMES,
    REQUIRED_QUALITY_CATEGORIES,
    latest_inspections_by_category,
)
from app.services.prompt_compiler import compile_page_prompt
from app.services.worker_handlers import execution, provider


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
    execution._commit_owned_progress(
        db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=45
    )
    provider._lease_reference_assets(db, job, [asset.id])
    binding = provider._binding(
        db,
        operation="multimodal_analysis",
        project_id=project.id,
        explicit_reference=provider._text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = provider._invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=(provider._asset_path(asset).read_bytes(),),
                mime_types=(asset.mime_type,),
            ),
            PageInspectionOutput,
        ),
    )
    execution._ensure_job_not_cancelled(db, job)
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
        raise execution.StaleStoryboardVersionError(
            "分镜版本已变化，已在调用模型前取消本次检查；请按当前分镜重新检查"
        )
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
    # Do not commit inspection rows / page status here. execute_job CAS to
    # COMPLETED and commits the same session; a concurrent cancel then
    # rolls this unit back instead of leaving INSPECTED + CANCELLED.

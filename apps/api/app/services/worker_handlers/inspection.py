"""PAGE_INSPECT handler.

Owns quality inspection of generated page candidates: prompt building against
the compiled page snapshot, the paid multimodal call, inspection result
persistence and candidate/page status convergence.
"""

import json
import logging

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
    GATED_QUALITY_CATEGORIES,
    PASSING_QUALITY_OUTCOMES,
    REQUIRED_QUALITY_CATEGORIES,
    latest_inspections_by_category,
)
from app.services.prompt_compiler import compile_page_prompt
from app.services.worker_handlers import execution, provider

LOGGER = logging.getLogger("mangaflow.worker.inspection")

DEFAULT_INSPECTION_CATEGORIES = [
    "SPEAKER",
    "CHARACTER",
    "OUTFIT",
    "PROP",
    "CONTINUITY",
    "PRESENCE",
]


def _normalize_presence_name(value: str) -> str:
    return "".join(str(value).split()).casefold()


def _presence_compliance(snapshot_input: dict, detected_names: set[str]) -> dict | None:
    """Deterministic cast-compliance cross-check (#164).

    The model is only trusted as the "eyes" — ``details.detected_characters``
    on PRESENCE items lists what it actually sees. The subset relations are
    computed here from the compiled snapshot: every VISIBLE character must be
    detected, and characters whose only page presence is OFFSCREEN/MENTIONED
    must not be. Returns a failing result dict, or None when compliant.
    """

    id_names: dict[str, str] = {}
    alias_index: dict[str, str] = {}
    for entry in snapshot_input.get("characters") or []:
        name = str(entry.get("primary_name") or "").strip()
        if not name:
            continue
        id_names[str(entry.get("id"))] = name
        alias_index.setdefault(_normalize_presence_name(name), name)
        for alias in entry.get("aliases") or []:
            alias = str(alias).strip()
            if alias:
                alias_index.setdefault(_normalize_presence_name(alias), name)
    visible: set[str] = set()
    offscreen: set[str] = set()
    for panel in (snapshot_input.get("page") or {}).get("layout") or []:
        for character_id, value in (panel.get("character_presence") or {}).items():
            name = id_names.get(str(character_id))
            if not name:
                continue
            if str(value).upper() == "VISIBLE":
                visible.add(name)
            else:
                offscreen.add(name)
        for character_id in panel.get("characters") or []:
            name = id_names.get(str(character_id))
            if name:
                visible.add(name)
    forbidden = offscreen - visible
    detected = {
        alias_index.get(_normalize_presence_name(value), value.strip())
        for value in detected_names
        if value.strip()
    }
    missing_visible = sorted(visible - detected)
    drawn_forbidden = sorted(forbidden & detected)
    if not missing_visible and not drawn_forbidden:
        return None
    differences = []
    if missing_visible:
        differences.append(f"VISIBLE 角色未在画面中检出：{'、'.join(missing_visible)}")
    if drawn_forbidden:
        differences.append(f"OFFSCREEN/MENTIONED 角色出现于画面：{'、'.join(drawn_forbidden)}")
    return {
        "outcome": "MISSING" if missing_visible else "EXTRA",
        "expected": (
            f"必须画出：{'、'.join(sorted(visible)) or '（无）'}；"
            f"不得出现：{'、'.join(sorted(forbidden)) or '（无）'}"
        ),
        "observed": f"画面中识别到：{'、'.join(sorted(detected)) or '（无）'}",
        "differences": differences,
    }


def _run_inspection(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate or not candidate.asset_id:
        raise RuntimeError("候选图片尚未生成")
    page = db.get(MangaPage, candidate.page_id)
    asset = db.get(Asset, candidate.asset_id)
    project = db.get(Project, db.get(Chapter, page.chapter_id).project_id)
    inspection_storyboard_version = page.storyboard_version
    # Scene writes deliberately never bump storyboard_version, so a review flag
    # committed by mark_pages_for_review during the paid call (or an adoption
    # change from select/keep/retract) is invisible to the sbv guard below.
    # The page version is the baseline that catches those overwrites.
    baseline_page_version = page.version
    # select/retract on an upstream page flags downstream pages NEEDS_RECHECK
    # together with a page.version bump (generation.py, #136 route side), so
    # the version baseline above catches those writes mid-flight. The flag
    # snapshot below stays as defense in depth: any flag change that slips
    # past the version baseline must not be wiped by a completion writing
    # PASSED/NOT_CHECKED over it (#136).
    baseline_continuity_status = page.continuity_status
    _, snapshot = compile_page_prompt(db, page, project)
    categories = list(
        job.request_parameters.get("categories", DEFAULT_INSPECTION_CATEGORIES),
    )
    if "PRESENCE" not in {str(item).upper() for item in categories}:
        # #164: presence compliance joins every inspection run regardless of
        # the caller's list — the completion gate treats it as required for
        # newly inspected candidates.
        categories.append("PRESENCE")
    prompt = f"""你是漫画成片质检员。对照结构化目标检查这张生成漫画页。
只检查这些类别：{json.dumps(categories, ensure_ascii=False)}。
目标剧本、格位、说话人、角色、服装与风格上下文：
{json.dumps(snapshot["input"], ensure_ascii=False, separators=(",", ":"))}
SPEAKER 检查气泡归属；
CHARACTER 检查脸、发型、体型和标志特征；OUTFIT 检查场景指定服装；
PROP 检查关键道具；CONTINUITY 检查与页面结构、场景状态和前后逻辑的一致性。
PRESENCE 检查出镜合规：每个 character_presence 标记 VISIBLE 的角色都必须画出，
OFFSCREEN/MENTIONED 的角色不得出现在画面中；并把画面中实际看到的角色名完整
写入该类别 details.detected_characters（旁白不算角色）。
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
    detected_names = {
        name
        for item in output.items
        if str(item.category).upper() == "PRESENCE"
        for name in (item.details.detected_characters or [])
    }
    compliance = _presence_compliance(snapshot["input"], detected_names)
    if compliance is not None:
        # Deterministic cross-check failed (#164): persist a synthesized
        # PRESENCE row so the failure surfaces in the candidate's inspection
        # history and blocks the completion gate even when the model's own
        # PRESENCE verdict said PASS (equal-timestamp conflict resolution in
        # latest_inspections_by_category fails closed on mixed outcomes).
        db.add(
            InspectionResult(
                generation_record_id=candidate.generation_record_id,
                candidate_id=candidate.id,
                storyboard_version=inspection_storyboard_version,
                category="PRESENCE",
                outcome=compliance["outcome"],
                score=None,
                details={
                    "expected": compliance["expected"],
                    "observed": compliance["observed"],
                    "differences": compliance["differences"],
                },
                regions=[],
                severity="ERROR",
            )
        )
        needs_review = True
    db.flush()
    db.refresh(page)
    if page.storyboard_version != inspection_storyboard_version:
        raise execution.StaleStoryboardVersionError(
            "分镜版本已变化，已在调用模型前取消本次检查；请按当前分镜重新检查"
        )
    if page.version != baseline_page_version:
        raise execution.StaleStoryboardVersionError(
            "页面内容在检查期间已变化（场景或采用状态），已取消本次检查；请按当前页面状态重新检查"
        )
    continuity_drifted = page.continuity_status != baseline_continuity_status
    if continuity_drifted:
        LOGGER.warning(
            "PAGE_INSPECT job %s 完成时连续性旗标已从 %s 变为 %s（无版本变化的并发写入，"
            "疑似上游重选触发的 NEEDS_RECHECK）；本次过检结果标记为 NEEDS_REVIEW，不再回写 PASSED",
            job.id,
            baseline_continuity_status,
            page.continuity_status,
        )
    latest = latest_inspections_by_category(db, candidate.id, inspection_storyboard_version)
    complete = (
        bool(seen)
        and set(requested) <= set(seen)
        and set(REQUIRED_QUALITY_CATEGORIES) <= set(latest)
    )
    needs_review = needs_review or any(
        latest[category].outcome not in PASSING_QUALITY_OUTCOMES
        for category in GATED_QUALITY_CATEGORIES
        if category in latest
    )
    if not complete:
        candidate.status = "READY"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            # A drifted flag must not be wiped by NOT_CHECKED either (#136).
            page.continuity_status = (
                "NEEDS_REVIEW" if continuity_drifted else "NOT_CHECKED"
            )
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
            if continuity_drifted:
                # Stale pass over a mid-flight NEEDS_RECHECK: keep the
                # inspection rows for audit but converge to NEEDS_REVIEW so
                # the export gate stays blocked until a fresh inspection
                # runs against the changed upstream inputs (#136).
                page.continuity_status = "NEEDS_REVIEW"
                page.status = PageStatus.NEEDS_REPAIR
                page.version += 1
            else:
                page.continuity_status = "PASSED"
                page.status = PageStatus.FINAL_READY
                page.version += 1
    # Do not commit inspection rows / page status here. execute_job CAS to
    # COMPLETED and commits the same session; a concurrent cancel then
    # rolls this unit back instead of leaving INSPECTED + CANCELLED.

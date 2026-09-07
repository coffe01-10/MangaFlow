"""STYLE_ANALYZE handler.

Owns style reference loading, the visual-language analysis prompt, the paid
multimodal call and style profile draft persistence (prompt summary and color
palette recovery included).
"""

import logging

from sqlalchemy import select

from app.domain.states import JobStatus
from app.model_adapters.base import MultimodalRequest, ProviderAdapterError
from app.models import Asset, GenerationJob, Project, StyleProfile, StyleStatus
from app.services.ai_schemas import StyleAnalysisOutput
from app.services.worker_handlers import execution, provider

LOGGER = logging.getLogger("mangaflow.worker.style_analyze")


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


def _asset_blob_bytes(asset: Asset) -> bytes:
    """Read an asset blob with a missing-file preflight (#210-5).

    A vanished blob must fail TERMINALLY (non-retryable INVALID_INPUT) instead
    of raising FileNotFoundError, which the worker classifies as retryable
    WORKER_ERROR and re-pays. The ``is_file`` probe tolerates the legacy
    ``_asset_path`` test seam (bare namespaces exposing only ``read_bytes``).
    """

    path = provider._asset_path(asset)
    is_file = getattr(path, "is_file", None)
    if callable(is_file) and not is_file():
        raise ProviderAdapterError(
            "INVALID_INPUT",
            f"风格参考图文件缺失，已终止任务：{asset.original_name}",
            retryable=False,
        )
    return path.read_bytes()


def _run_style_analyze(db, job: GenerationJob) -> None:
    style = db.get(StyleProfile, job.target_id)
    if not style:
        raise RuntimeError("风格档案不存在")
    # Oldest-wins arbitration (the story_parse pattern): the route guard is
    # check-then-act and the analyze/palette-draft idempotency keys are
    # disjoint, so a concurrent analyze + palette-draft can commit two ACTIVE
    # jobs for one style row. The younger claimant fails terminally here —
    # before the paid multimodal call — instead of double-paying and
    # clobbering the winner's profile write.
    from app.services.job_service import oldest_active_job_id

    oldest_id = oldest_active_job_id(
        db,
        job_type="STYLE_ANALYZE",
        target_id=style.id,
        target_type="STYLE",
    )
    if oldest_id is not None and oldest_id != job.id:
        raise ProviderAdapterError(
            "STYLE_ANALYSIS_CONFLICT",
            "该风格档案已有进行中的分析任务，本次重复分析已在调用模型前取消",
            retryable=False,
        )
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
    execution._commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=35)
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
    provider._lease_reference_assets(db, job, [asset.id for asset in references[:8]])
    project = db.get(Project, style.project_id)
    binding = provider._binding(
        db,
        operation="multimodal_analysis",
        project_id=style.project_id,
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
                images=tuple(
                    _asset_blob_bytes(asset) for asset in references[:8]
                ),
                mime_types=tuple(asset.mime_type for asset in references[:8]),
            ),
            StyleAnalysisOutput,
        ),
    )
    execution._ensure_job_not_cancelled(db, job)
    # #231 completion guard (the STYLE_TEST pattern in asset_generate): the
    # paid call can outlive a palette confirmation/activation committed
    # mid-flight. Re-read fresh state and, when the style has already moved
    # past the analyze-awaiting states, do NOT demote it back to DRAFT, do NOT
    # reset palette_confirmed/test_image_approved, and do NOT overwrite the
    # confirmed profile — the late completion is skipped with a logged note
    # and the job still completes (the paid result is not user-recoverable
    # here; the route can re-run analyze explicitly if wanted).
    db.refresh(style, attribute_names=["status", "version"])
    current_status = str(getattr(style.status, "value", style.status) or "")
    if current_status not in {StyleStatus.DRAFT.value, StyleStatus.ANALYZING.value}:
        LOGGER.warning(
            "STYLE_ANALYZE job %s completed after style %s moved to %s; "
            "analyzed profile discarded (confirmed/active state kept)",
            job.id,
            style.id,
            current_status,
        )
        job.progress = 90
        return
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

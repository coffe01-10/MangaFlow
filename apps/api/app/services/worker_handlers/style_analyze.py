"""STYLE_ANALYZE handler.

Owns style reference loading, the visual-language analysis prompt, the paid
multimodal call and style profile draft persistence (prompt summary and color
palette recovery included).
"""

from sqlalchemy import select

from app.domain.states import JobStatus
from app.model_adapters.base import MultimodalRequest
from app.models import Asset, GenerationJob, Project, StyleProfile
from app.services.ai_schemas import StyleAnalysisOutput
from app.services.worker_handlers import execution, provider


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
                images=tuple(provider._asset_path(asset).read_bytes() for asset in references[:8]),
                mime_types=tuple(asset.mime_type for asset in references[:8]),
            ),
            StyleAnalysisOutput,
        ),
    )
    execution._ensure_job_not_cancelled(db, job)
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

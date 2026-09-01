import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Character, Dialogue, MangaPage, Outfit, Panel, Project, Scene, StyleProfile

PAGE_TEMPLATE_VERSION = "page-v2.1.0"


def compile_page_prompt(
    db: Session,
    page: MangaPage,
    project: Project,
    scene_background: str | None = None,
) -> tuple[str, dict]:
    """Compile the generation prompt for one page.

    ``scene_background`` replaces the panel-bound background text when the
    queue-time snapshot holds scene asset facts: the frozen snapshot is the
    compile-time contract (docs/v02-scene-asset-contract.md §5), while
    ``Panel.background`` stays untouched as the storyboard snapshot.
    """

    characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .order_by(Character.primary_name)
        )
    )
    source_ranges = page.source_coverage.get("ranges", [])
    source_text = "\n".join(item.get("text", "") for item in source_ranges)
    character_bible = [
        {
            "id": item.id,
            "primary_name": item.primary_name,
            "aliases": item.aliases,
            "description": item.canonical_description,
            "locked_features": item.locked_features,
            "forbidden_changes": item.forbidden_changes,
        }
        for item in characters
    ]
    character_names = {item.id: item.primary_name for item in characters}
    scenes = (
        list(db.scalars(select(Scene).where(Scene.id.in_(page.scene_ids))))
        if page.scene_ids
        else []
    )
    outfit_ids = {
        outfit_id
        for scene in scenes
        for outfit_id in scene.outfit_assignments.values()
        if outfit_id
    }
    outfits = (
        list(db.scalars(select(Outfit).where(Outfit.id.in_(outfit_ids)))) if outfit_ids else []
    )
    style = (
        db.get(StyleProfile, page.style_id or project.default_style_id)
        if (page.style_id or project.default_style_id)
        else None
    )
    color_mode = style.color_mode if style else "monochrome"
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    panel_script = []
    for panel in panels:
        dialogues = list(
            db.scalars(
                select(Dialogue)
                .where(Dialogue.panel_id == panel.id)
                .order_by(Dialogue.reading_order)
            )
        )
        panel_script.append(
            {
                "reading_order": panel.reading_order,
                "bounds": panel.bounds,
                "shot_type": panel.shot_type,
                "camera_angle": panel.camera_angle,
                "characters": panel.characters,
                "character_presence": panel.character_presence,
                "props": panel.props,
                "actions": panel.actions,
                "expressions": panel.expressions,
                "background": scene_background or panel.background,
                "dialogues": [
                    {
                        "speaker": character_names.get(item.speaker_character_id, "旁白"),
                        "text": item.target_text,
                        "reading_order": item.reading_order,
                        "text_direction": item.text_direction,
                        "region": item.region,
                        "rewrite_forbidden": item.rewrite_forbidden,
                    }
                    for item in dialogues
                ],
                "bleed": panel.bleed,
                "borderless": panel.borderless,
            }
        )
    payload = {
        "project": {
            "language": project.language,
            "reading_direction": project.reading_direction,
            "page_ratio": project.page_ratio,
            "workflow_mode": project.workflow_mode.value,
        },
        "page": {
            "number": page.page_number,
            "panel_count": page.panel_count,
            "estimated_text_chars": page.estimated_text_chars,
            "estimated_bubbles": page.estimated_bubbles,
            "source_text": source_text,
            "layout": panel_script,
        },
        "characters": character_bible,
        "scene_outfits": [
            {"scene_id": scene.id, "assignments": scene.outfit_assignments} for scene in scenes
        ],
        "outfits": [
            {
                "id": outfit.id,
                "character_id": outfit.character_id,
                "name": outfit.name,
                "components": outfit.components,
                "state_rules": outfit.state_rules,
                "locked_fields": outfit.locked_fields,
            }
            for outfit in outfits
        ],
        "style": (
            {
                "id": style.id,
                "name": style.name,
                "color_mode": style.color_mode,
                "profile": style.profile,
            }
            if style
            else None
        ),
    }
    mode_instruction = {
        "AUTO": "自动模式：在不改变剧情的前提下主动补足镜头、表演、环境和过场细节。",
        "DIRECTOR": "导演模式：严格执行已给出的格位、镜头、人物、服装和动作，不擅自改动。",
        "SEMI_AUTO": "半自动模式：严格保持剧情、人物与服装，允许补足不影响剧情的环境和表演细节。",
    }[project.workflow_mode.value]
    director_role = "黑白网点日式漫画" if color_mode == "monochrome" else "彩色日式漫画"
    render_rules = (
        "使用干净墨线、专业网点、明确黑白对比与克制留白；"
        if color_mode == "monochrome"
        else "使用统一色彩脚本、稳定肤色发色与服装配色、清晰光影层次；不得擅自改变跨格固有色；"
    )
    style_dimensions = (
        "线稿、网点、黑白对比和构图规则"
        if color_mode == "monochrome"
        else "线稿、色板、上色方式、光影和构图规则"
    )
    prompt = f"""你是{director_role}的单页导演。请只生成第 {page.page_number} 页，不生成相邻页面。
阅读方向必须为从右到左，严格按照 layout 中的格位、阅读顺序和镜头生成 {page.panel_count} 格。
中文文字必须严格保留，不得总结、改写或遗漏。
{mode_instruction}
原文与页面结构如下：
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
要求：采用专业日本漫画页面语言，格子大小有节奏变化，右上开始、左下结束；
{render_rules}使用清晰格线；严格使用 scene_outfits 指定服装；角色身份、服装、道具和场景连续；
若存在 style.profile，按其总结的{style_dimensions}执行；
禁止加入原文没有的关键剧情；输出一张完整竖版漫画页。
"""
    snapshot = {
        "template": PAGE_TEMPLATE_VERSION,
        "checksum": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "input": payload,
    }
    return prompt, snapshot

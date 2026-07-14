import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Character, MangaPage, Project

PAGE_TEMPLATE_VERSION = "page-v1.0.0"


def compile_page_prompt(db: Session, page: MangaPage, project: Project) -> tuple[str, dict]:
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
    payload = {
        "project": {
            "language": project.language,
            "reading_direction": project.reading_direction,
            "page_ratio": project.page_ratio,
        },
        "page": {
            "number": page.page_number,
            "panel_count": page.panel_count,
            "estimated_text_chars": page.estimated_text_chars,
            "estimated_bubbles": page.estimated_bubbles,
            "source_text": source_text,
        },
        "characters": character_bible,
    }
    prompt = f"""你是黑白网点日式漫画的单页导演。请只生成第 {page.page_number} 页，不生成相邻页面。
阅读方向必须为从右到左，页面包含 {page.panel_count} 格以内。
中文文字必须严格保留，不得总结、改写或遗漏。
原文与页面结构如下：
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
要求：黑白墨线、网点、清晰格线、角色身份与服装一致；禁止加入原文没有的关键剧情；输出一张完整竖版漫画页。
"""
    snapshot = {
        "template": PAGE_TEMPLATE_VERSION,
        "checksum": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "input": payload,
    }
    return prompt, snapshot

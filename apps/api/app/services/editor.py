from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chapter, Character, Dialogue, MangaPage, Panel, Scene


def mark_storyboard_changed(page: MangaPage) -> None:
    page.storyboard_version += 1
    page.selected_candidate_ack_version = None


def _normalize_name(value: str) -> str:
    return "".join(value.split()).casefold()


def canonical_speaker_name(db: Session, project_id: str, value: str) -> str:
    name = value.strip()
    if not name:
        return ""
    token = _normalize_name(name)
    matches = [
        character
        for character in db.scalars(select(Character).where(Character.project_id == project_id))
        if token
        in {
            _normalize_name(character.primary_name),
            *(_normalize_name(alias) for alias in character.aliases),
        }
    ]
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="说话人绰号存在歧义，请改用主要姓名")
    return matches[0].primary_name if matches else name


def validate_character_ids(
    db: Session,
    project_id: str,
    character_ids: list[str],
) -> list[str]:
    unique_ids = list(dict.fromkeys(character_ids))
    if not unique_ids:
        return []
    valid_ids = set(
        db.scalars(
            select(Character.id).where(
                Character.project_id == project_id,
                Character.id.in_(unique_ids),
            )
        )
    )
    if len(valid_ids) != len(unique_ids):
        raise HTTPException(status_code=409, detail="分镜中包含不属于当前项目的角色")
    return unique_ids


def project_id_for_page(db: Session, page: MangaPage) -> str:
    chapter = db.get(Chapter, page.chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter.project_id


def mark_pages_for_review(
    db: Session,
    chapter_id: str,
    *,
    reference_id: str | None = None,
    reference_kind: str | None = None,
    from_page_number: int | None = None,
) -> None:
    pages = list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter_id)
            .order_by(MangaPage.page_number)
        )
    )
    if not pages:
        return
    start = from_page_number
    if start is None and reference_id:
        if reference_kind == "scene_asset":
            scene_ids = set(
                db.scalars(
                    select(Scene.id).where(
                        Scene.chapter_id == chapter_id,
                        Scene.scene_asset_id == reference_id,
                    )
                )
            )
            referenced = [
                page.page_number
                for page in pages
                if scene_ids & set(page.scene_ids)
            ]
        else:
            field = "scene_ids" if reference_kind == "scene" else "beat_ids"
            referenced = [
                page.page_number for page in pages if reference_id in getattr(page, field)
            ]
        start = min(referenced) if referenced else pages[0].page_number
    start = start or pages[0].page_number
    for page in pages:
        if page.page_number >= start:
            page.continuity_status = "NEEDS_REVIEW"
            page.version += 1


def refresh_page_text_metrics(db: Session, page: MangaPage) -> None:
    panel_ids = list(db.scalars(select(Panel.id).where(Panel.page_id == page.id)))
    dialogues = (
        list(db.scalars(select(Dialogue).where(Dialogue.panel_id.in_(panel_ids))))
        if panel_ids
        else []
    )
    text_chars = sum(len("".join(item.target_text.split())) for item in dialogues)
    bubble_count = len(dialogues)
    if text_chars > 180:
        raise HTTPException(status_code=422, detail="本页文字超过 180 字硬上限，请拆到下一页")
    if bubble_count > 8:
        raise HTTPException(status_code=422, detail="本页气泡超过 8 个硬上限，请拆到下一页")
    page.estimated_text_chars = text_chars
    page.estimated_bubbles = bubble_count

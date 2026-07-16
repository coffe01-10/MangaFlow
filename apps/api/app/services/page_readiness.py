from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.states import CharacterPresence
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    MangaPage,
    Outfit,
    Panel,
    Project,
    ProviderHealth,
    StyleProfile,
)
from app.schemas import (
    PageReadinessBlocker,
    PageReadinessCharacter,
    PageReadinessProvider,
    PageReadinessRead,
    PageReadinessStyle,
    PageReadinessWorker,
)
from app.services.runtime_settings import queue_execution_state

FORMAL_IMAGE_MODEL = "image.nano_banana_2"
FORMAL_RESOLUTION = "1K"
_PRESENCE_PRIORITY = {
    CharacterPresence.MENTIONED.value: 1,
    CharacterPresence.OFFSCREEN.value: 2,
    CharacterPresence.VISIBLE.value: 3,
}


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _block(
    code: str,
    message: str,
    stage: str,
    *,
    target_id: str | None = None,
) -> PageReadinessBlocker:
    return PageReadinessBlocker(
        code=code,
        message=message,
        stage=stage,
        target_id=target_id,
    )


def _active_asset_ids(db: Session, asset_ids: list[str]) -> list[str]:
    if not asset_ids:
        return []
    return list(
        db.scalars(
            select(Asset.id).where(
                Asset.id.in_(asset_ids),
                Asset.deleted_at.is_(None),
            )
        )
    )


def _page_cast(
    db: Session, page: MangaPage
) -> tuple[list[PageReadinessCharacter], list[PageReadinessCharacter], list[str]]:
    chapter = db.get(Chapter, page.chapter_id)
    panels = list(db.scalars(select(Panel).where(Panel.page_id == page.id)))
    presence_by_character: dict[str, str] = {}
    outfit_by_character: dict[str, set[str]] = defaultdict(set)
    props: list[str] = []

    for panel in panels:
        panel_presence = dict(panel.character_presence or {})
        # Compatibility for pages planned before structured presence existed.
        for character_id in panel.characters or []:
            panel_presence.setdefault(character_id, CharacterPresence.VISIBLE.value)
        for character_id, raw_presence in panel_presence.items():
            presence = _enum_value(raw_presence).upper()
            if presence not in _PRESENCE_PRIORITY:
                continue
            previous = presence_by_character.get(character_id)
            if previous is None or _PRESENCE_PRIORITY[presence] > _PRESENCE_PRIORITY[previous]:
                presence_by_character[character_id] = presence
        for character_id, outfit_id in (panel.outfits or {}).items():
            if outfit_id:
                outfit_by_character[character_id].add(outfit_id)
        props.extend(str(item).strip() for item in (panel.props or []) if str(item).strip())

    character_ids = list(presence_by_character)
    characters = {
        character.id: character
        for character in db.scalars(
            select(Character).where(
                Character.id.in_(character_ids),
                Character.project_id == chapter.project_id,
            )
        )
    } if character_ids else {}
    reference_ids: dict[str, list[str]] = defaultdict(list)
    if character_ids:
        for reference in db.scalars(
            select(CharacterReference).where(CharacterReference.character_id.in_(character_ids))
        ):
            reference_ids[reference.character_id].append(reference.asset_id)

    visible: list[PageReadinessCharacter] = []
    non_visible: list[PageReadinessCharacter] = []
    for character_id, presence in presence_by_character.items():
        character = characters.get(character_id)
        if not character:
            continue
        assigned_ids = outfit_by_character.get(character_id, set())
        outfit = db.get(Outfit, next(iter(assigned_ids))) if len(assigned_ids) == 1 else None
        item = PageReadinessCharacter(
            character_id=character.id,
            primary_name=character.primary_name,
            presence=CharacterPresence(presence),
            character_reference_ids=_active_asset_ids(db, reference_ids[character.id]),
            outfit_id=outfit.id if outfit else None,
            outfit_name=outfit.name if outfit else None,
            outfit_reference_ids=(
                _active_asset_ids(db, list(outfit.reference_asset_ids or [])) if outfit else []
            ),
        )
        (visible if presence == CharacterPresence.VISIBLE.value else non_visible).append(item)

    visible.sort(key=lambda item: item.primary_name)
    non_visible.sort(key=lambda item: item.primary_name)
    return visible, non_visible, list(dict.fromkeys(props))


def build_page_readiness(
    db: Session,
    page: MangaPage,
    settings: Settings,
) -> PageReadinessRead:
    chapter = db.get(Chapter, page.chapter_id)
    project = db.get(Project, chapter.project_id)
    blockers: list[PageReadinessBlocker] = []

    source_complete = bool(page.source_coverage.get("complete"))
    if not source_complete:
        blockers.append(
            _block("SOURCE_INCOMPLETE", "本页原文覆盖不完整", "source", target_id=chapter.id)
        )
    script_complete = bool(
        chapter.status in {"SCRIPT_READY", "PAGES_PLANNED"}
        and page.scene_ids
        and page.beat_ids
    )
    if not script_complete:
        blockers.append(
            _block(
                "SCRIPT_INCOMPLETE",
                "本页缺少可追溯的剧本与分镜",
                "script",
                target_id=chapter.id,
            )
        )

    visible, non_visible, props = _page_cast(db, page)
    if not visible:
        blockers.append(
            _block(
                "VISIBLE_CAST_EMPTY",
                "请先确认本页实际出镜人物",
                "storyboard",
                target_id=page.id,
            )
        )
    for character in visible:
        if not character.character_reference_ids:
            blockers.append(
                _block(
                    "MISSING_CHARACTER_REFERENCE",
                    f"出镜人物“{character.primary_name}”缺少已确认的人物参考图",
                    "assets",
                    target_id=character.character_id,
                )
            )
        if not character.outfit_id:
            blockers.append(
                _block(
                    "MISSING_OUTFIT_ASSIGNMENT",
                    f"出镜人物“{character.primary_name}”尚未指定本页服装",
                    "storyboard",
                    target_id=character.character_id,
                )
            )
        elif not character.outfit_reference_ids:
            blockers.append(
                _block(
                    "MISSING_OUTFIT_REFERENCE",
                    f"“{character.primary_name}”的服装“{character.outfit_name}”缺少已确认参考图",
                    "assets",
                    target_id=character.outfit_id,
                )
            )

    style_id = page.style_id or project.default_style_id
    style = db.get(StyleProfile, style_id) if style_id else None
    style_profile = dict(style.profile or {}) if style else {}
    palette_confirmed = bool(
        style_profile.get("palette_confirmed")
        or style_profile.get("palette_confirmed_at")
        or style_profile.get("approved_palette")
    )
    test_image_approved = bool(
        style_profile.get("test_image_approved")
        or style_profile.get("test_image_approved_at")
        or style_profile.get("approved_test_candidate_id")
    )
    style_read = PageReadinessStyle(
        style_id=style.id if style else None,
        name=style.name if style else None,
        color_mode=style.color_mode if style else None,
        status=_enum_value(style.status) if style else None,
        palette_confirmed=palette_confirmed,
        test_image_approved=test_image_approved,
    )
    if not style:
        blockers.append(_block("STYLE_MISSING", "请为项目选择彩色漫画风格", "assets"))
    else:
        if style.color_mode != "color":
            blockers.append(
                _block(
                    "STYLE_NOT_COLOR",
                    "正式输出为彩色漫画，请将当前风格切换为彩色",
                    "assets",
                    target_id=style.id,
                )
            )
        if not palette_confirmed:
            blockers.append(
                _block(
                    "STYLE_PALETTE_UNCONFIRMED",
                    "请先确认彩色色板",
                    "assets",
                    target_id=style.id,
                )
            )
        if not test_image_approved:
            blockers.append(
                _block(
                    "STYLE_TEST_UNAPPROVED",
                    "请先人工通过 1K 风格测试图",
                    "assets",
                    target_id=style.id,
                )
            )
        if _enum_value(style.status) != "ACTIVE":
            blockers.append(
                _block(
                    "STYLE_NOT_ACTIVE",
                    "彩色风格档案尚未激活",
                    "assets",
                    target_id=style.id,
                )
            )

    health = db.scalar(select(ProviderHealth).where(ProviderHealth.provider == "vertex-ai"))
    configured = bool(
        settings.vertex_configured
        and health
        and health.configured
        and health.credential_file_present
    )
    health_state = health.health_state if health else "NOT_CHECKED"
    text_access = health.text_model_access if health else "NOT_CHECKED"
    image_access = (
        (health.image_model_access or {}).get(FORMAL_IMAGE_MODEL, "NOT_CHECKED")
        if health
        else "NOT_CHECKED"
    )
    provider = PageReadinessProvider(
        configured=configured,
        health_state=health_state,
        text_model_access=text_access,
        image_model_access=image_access,
    )
    if not configured:
        blockers.append(_block("VERTEX_NOT_CONFIGURED", "Vertex 凭据尚未就绪", "settings"))
    if text_access != "GRANTED":
        blockers.append(
            _block(
                "TEXT_MODEL_UNVERIFIED",
                "Gemini 3.5 Flash 需要重新验证",
                "settings",
            )
        )
    if image_access != "GRANTED":
        blockers.append(
            _block(
                "IMAGE_MODEL_UNVERIFIED",
                "Nano Banana 2 需要执行 1K 模型验证",
                "settings",
            )
        )

    execution = queue_execution_state(db, settings, probe_redis=True)
    worker = PageReadinessWorker(
        queue_mode=execution.queue_mode,
        executor=execution.actual_executor,
        can_execute=execution.can_execute,
        redis_state=execution.redis_state,
    )
    if not worker.can_execute:
        blockers.append(
            _block("WORKER_UNAVAILABLE", "当前队列模式无法执行新任务", "settings")
        )

    return PageReadinessRead(
        page_id=page.id,
        ready=not blockers,
        source_complete=source_complete,
        script_complete=script_complete,
        visible_characters=visible,
        mentioned_characters=non_visible,
        props=props,
        style=style_read,
        provider=provider,
        worker=worker,
        blockers=blockers,
    )


def ensure_page_ready(db: Session, page: MangaPage, settings: Settings) -> PageReadinessRead:
    readiness = build_page_readiness(db, page, settings)
    if readiness.ready:
        return readiness
    from fastapi import HTTPException

    raise HTTPException(
        status_code=409,
        detail={
            "code": "PAGE_NOT_READY",
            "message": "页面生产准备尚未完成",
            "blockers": [item.model_dump(mode="json") for item in readiness.blockers],
        },
    )

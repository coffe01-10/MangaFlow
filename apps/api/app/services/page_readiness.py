from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.states import CharacterPresence
from app.models import (
    AIModel,
    Asset,
    Chapter,
    Character,
    CharacterReference,
    MangaPage,
    Outfit,
    Panel,
    Project,
    ProviderConnection,
    ProviderProfile,
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
from app.services.credential_source import environment_credentials_ready
from app.services.model_availability import (
    catalog_model_is_available,
    connection_ids_with_usable_keys,
)
from app.services.model_router import model_operation_verified
from app.services.provider_presets import ensure_provider_presets
from app.services.runtime_settings import queue_execution_state

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
    severity: str = "BLOCKING",
) -> PageReadinessBlocker:
    return PageReadinessBlocker(
        code=code,
        message=message,
        stage=stage,
        target_id=target_id,
        severity=severity,
    )


def _catalog_model_availability(db: Session, settings: Settings) -> dict[str, int]:
    usable_key_connections = connection_ids_with_usable_keys(db)
    credentials_writable = settings.provider_credentials_writable
    rows = (
        db.query(AIModel, ProviderConnection, ProviderProfile)
        .join(ProviderConnection, AIModel.connection_id == ProviderConnection.id)
        .join(ProviderProfile, ProviderConnection.provider_id == ProviderProfile.id)
        .all()
    )
    counts = {"text": 0, "image": 0, "auto_text": 0, "auto_image": 0}
    for model, connection, profile in rows:
        available = catalog_model_is_available(
            model,
            connection,
            profile,
            credentials_writable=credentials_writable,
            has_usable_key=connection.id in usable_key_connections,
            environment_credentials_ready=environment_credentials_ready(
                settings, connection.protocol
            ),
        )
        if not available:
            continue
        kind = None
        if model.model_type == "IMAGE" and "image_edit" in (model.operations or []):
            kind = "image"
        elif model.model_type == "TEXT" and "structured_text" in (model.operations or []):
            kind = "text"
        if kind is None:
            continue
        counts[kind] += 1
        operation = "image_edit" if kind == "image" else "structured_text"
        if model_operation_verified(model, operation) and connection.health_state == "HEALTHY":
            counts[f"auto_{kind}"] += 1
    return counts


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
    package_gate: dict[str, bool] | None = None,
) -> PageReadinessRead:
    ensure_provider_presets(db, settings, auto_commit=False)
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

    # Beats without a panel slot contribute no dialogue, presence or props,
    # so a page carrying more beats than panels is silently losing story
    # content (#163). Surfaces both the recorded overflow (set by
    # _populate_page_storyboard on every rebuild) and the structural
    # condition (legacy pages / manual edits) as a NEEDS_REVIEW-level hint:
    # visible, but it does not by itself block production because a human
    # may have deliberately moved those beats to dialogue editing.
    orphan_beat_ids = list((page.source_coverage or {}).get("orphan_beat_ids") or [])
    beat_overflow = len(page.beat_ids or []) - page.panel_count
    if orphan_beat_ids or beat_overflow > 0:
        orphan_count = max(len(orphan_beat_ids), beat_overflow)
        blockers.append(
            _block(
                "ORPHANED_PAGE_BEATS",
                (
                    f"有 {orphan_count} 个情节拍未入板（分格数少于本页拍数），"
                    "对应台词与出镜不会被生成，请调整分页或格数"
                ),
                "storyboard",
                target_id=page.id,
                severity="WARNING",
            )
        )

    visible, non_visible, props = _page_cast(db, page)
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
            if not (package_gate or {}).get(character.character_id):
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

    model_counts = _catalog_model_availability(db, settings)
    configured = model_counts["image"] > 0
    health_state = (
        "HEALTHY"
        if model_counts["auto_image"] > 0
        else "AVAILABLE"
        if configured
        else "UNCONFIGURED"
    )
    text_access = "GRANTED" if model_counts["text"] > 0 else "NOT_CONFIGURED"
    image_access = "GRANTED" if configured else "NOT_CONFIGURED"
    provider = PageReadinessProvider(
        configured=configured,
        health_state=health_state,
        text_model_access=text_access,
        image_model_access=image_access,
        image_model_alias="explicit",
        usable_image_model_count=model_counts["image"],
        auto_image_model_count=model_counts["auto_image"],
    )
    if not configured:
        blockers.append(
            _block(
                "IMAGE_MODEL_UNAVAILABLE",
                "尚无已启用且支持参考图编辑的图片模型",
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
        # Only BLOCKING-severity findings gate generation; WARNING entries
        # (e.g. ORPHANED_PAGE_BEATS) are surfaced for review without
        # blocking (#163).
        ready=not any(item.severity == "BLOCKING" for item in blockers),
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


def ensure_page_ready(
    db: Session, page: MangaPage, settings: Settings, package_gate: dict[str, bool] | None = None
) -> PageReadinessRead:
    readiness = build_page_readiness(db, page, settings, package_gate=package_gate)
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

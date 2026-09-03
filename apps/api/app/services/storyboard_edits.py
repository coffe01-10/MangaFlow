"""Shared storyboard mutation helpers used by PATCH routes and V02-40.

These functions flush but never commit. Callers own the transaction.
"""

import copy

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.states import CharacterPresence, ensure_unlocked
from app.domain.storyboard_layout import canonical_sound_effects, resolve_panel_shape
from app.models import Chapter, Dialogue, MangaPage, Outfit, Panel, Scene
from app.services.editor import (
    mark_pages_for_review,
    mark_storyboard_changed,
    refresh_page_text_metrics,
    validate_character_ids,
)


def apply_panel_fields(
    db: Session,
    panel: Panel,
    page: MangaPage,
    project_id: str,
    values: dict,
) -> Panel:
    """Apply the same field rules as PATCH /panels/{id}. No commit."""
    try:
        ensure_unlocked(panel.locked_fields, list(values))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if "reading_order" in values and values["reading_order"] != panel.reading_order:
        occupant = db.scalar(
            select(Panel).where(
                Panel.page_id == page.id,
                Panel.reading_order == values["reading_order"],
                Panel.id != panel.id,
            )
        )
        if occupant is not None:
            raise HTTPException(status_code=409, detail="分镜格阅读顺序冲突")
    if "character_presence" in values:
        requested_presence = values.get("character_presence") or {}
        validate_character_ids(db, project_id, list(requested_presence))
        values["character_presence"] = {
            character_id: str(getattr(presence, "value", presence))
            for character_id, presence in requested_presence.items()
        }
        values["characters"] = [
            character_id
            for character_id, presence in values["character_presence"].items()
            if presence == CharacterPresence.VISIBLE.value
        ]
    elif "characters" in values:
        values["characters"] = validate_character_ids(db, project_id, values["characters"] or [])
        values["character_presence"] = {
            **{
                character_id: presence
                for character_id, presence in (panel.character_presence or {}).items()
                if presence != CharacterPresence.VISIBLE.value
            },
            **{
                character_id: CharacterPresence.VISIBLE.value
                for character_id in values["characters"]
            },
        }
    if "props" in values:
        values["props"] = list(
            dict.fromkeys(
                str(item).strip()
                for item in (values["props"] or [])
                if str(item).strip()
            )
        )
    if "sound_effects" in values:
        values["sound_effects"] = canonical_sound_effects(values["sound_effects"] or [])
    if "bounds" in values or "geometry" in values:
        resolved_bounds, resolved_geometry = resolve_panel_shape(
            stored_bounds=panel.bounds,
            stored_geometry=panel.geometry,
            reading_order=panel.reading_order,
            bounds=values.get("bounds"),
            geometry=values.get("geometry"),
            bounds_given="bounds" in values,
            geometry_given="geometry" in values,
        )
        values["bounds"] = resolved_bounds
        values["geometry"] = resolved_geometry
    character_ids = values.get("characters", panel.characters)
    if "outfits" in values:
        assignments = values["outfits"] or {}
        if any(character_id not in character_ids for character_id in assignments):
            raise HTTPException(status_code=409, detail="服装只能指定给本格出现的角色")
        for character_id, outfit_id in assignments.items():
            outfit = db.get(Outfit, outfit_id)
            if not outfit or outfit.project_id != project_id or outfit.character_id != character_id:
                raise HTTPException(status_code=409, detail="分镜服装与角色或项目不匹配")
    if "expressions" in values and any(
        character_id not in character_ids for character_id in (values["expressions"] or {})
    ):
        raise HTTPException(status_code=409, detail="表情只能指定给本格出现的角色")
    if "characters" in values:
        if "outfits" not in values:
            values["outfits"] = {
                character_id: outfit_id
                for character_id, outfit_id in (panel.outfits or {}).items()
                if character_id in character_ids
            }
        if "expressions" not in values:
            values["expressions"] = {
                character_id: expression
                for character_id, expression in (panel.expressions or {}).items()
                if character_id in character_ids
            }
    if "actions" in values:
        values["actions"] = {
            **panel.actions,
            **(values["actions"] or {}),
            "source_text": panel.actions.get("source_text", ""),
        }
    for key, value in values.items():
        setattr(panel, key, value.strip() if isinstance(value, str) else value)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.flush()
    return panel


def apply_dialogue_fields(
    db: Session,
    dialogue: Dialogue,
    panel: Panel,
    page: MangaPage,
    project_id: str,
    values: dict,
) -> Dialogue:
    """Apply the same field rules as PATCH /dialogues/{id}. No commit."""
    if "target_text" in values and not (values["target_text"] or "").strip():
        raise HTTPException(status_code=422, detail="气泡文字不能为空")
    if "speaker_character_id" in values:
        speaker = values["speaker_character_id"]
        if not speaker:
            values["speaker_character_id"] = None
        else:
            values["speaker_character_id"] = validate_character_ids(
                db, project_id, [speaker]
            )[0]
    if "reading_order" in values and values["reading_order"] != dialogue.reading_order:
        occupant = db.scalar(
            select(Dialogue).where(
                Dialogue.panel_id == panel.id,
                Dialogue.reading_order == values["reading_order"],
                Dialogue.id != dialogue.id,
            )
        )
        if occupant is not None:
            raise HTTPException(status_code=409, detail="气泡阅读顺序冲突")
    for key, value in values.items():
        setattr(dialogue, key, value.strip() if isinstance(value, str) else value)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.flush()
    return dialogue


def apply_scene_fields(
    db: Session,
    scene: Scene,
    values: dict,
    *,
    bump_storyboard: bool = False,
) -> Scene:
    """Apply the same field rules as PATCH /scenes/{id}. No commit."""
    try:
        ensure_unlocked(scene.locked_fields, list(values))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    for key, value in values.items():
        setattr(scene, key, value.strip() if isinstance(value, str) else value)
    scene.version += 1
    mark_pages_for_review(
        db,
        scene.chapter_id,
        reference_id=scene.id,
        reference_kind="scene",
    )
    if bump_storyboard:
        _bump_scene_storyboard(db, scene)
    db.flush()
    return scene


def _bump_scene_storyboard(db: Session, scene: Scene) -> None:
    chapter_pages = list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == scene.chapter_id)
            .order_by(MangaPage.page_number)
        )
    )
    referenced = [item for item in chapter_pages if scene.id in (item.scene_ids or [])]
    start = min((item.page_number for item in referenced), default=None)
    if start is None:
        return
    for item in chapter_pages:
        if item.page_number >= start and scene.id in (item.scene_ids or []):
            mark_storyboard_changed(item)


PANEL_RESTORE_FIELDS = (
    "reading_order",
    "bounds",
    "shot_type",
    "camera_angle",
    "camera_height",
    "characters",
    "character_presence",
    "props",
    "outfits",
    "actions",
    "expressions",
    "background",
    "sound_effects",
    "bleed",
    "borderless",
    "geometry",
    "bubble_regions",
)
DIALOGUE_RESTORE_FIELDS = (
    "speaker_character_id",
    "target_text",
    "reading_order",
    "text_direction",
    "region",
    "rewrite_forbidden",
    "bubble",
)
SCENE_RESTORE_FIELDS = ("location", "time_label", "weather")


def snapshot_fields(entity, fields: tuple[str, ...]) -> dict:
    return {name: copy_json(getattr(entity, name)) for name in fields}


def copy_json(value):
    return copy.deepcopy(value)


def restore_entity_fields(entity, snapshot: dict, fields: tuple[str, ...]) -> None:
    for name in fields:
        if name in snapshot:
            setattr(entity, name, copy_json(snapshot[name]))


def restore_panel_snapshot(
    db: Session, panel: Panel, page: MangaPage, snapshot: dict
) -> None:
    restore_entity_fields(panel, snapshot, PANEL_RESTORE_FIELDS)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.flush()


def restore_dialogue_snapshot(
    db: Session,
    dialogue: Dialogue,
    panel: Panel,
    page: MangaPage,
    snapshot: dict,
) -> None:
    restore_entity_fields(dialogue, snapshot, DIALOGUE_RESTORE_FIELDS)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.flush()


def restore_scene_snapshot(
    db: Session, scene: Scene, snapshot: dict, *, bump_storyboard: bool
) -> None:
    restore_entity_fields(scene, snapshot, SCENE_RESTORE_FIELDS)
    scene.version += 1
    mark_pages_for_review(
        db,
        scene.chapter_id,
        reference_id=scene.id,
        reference_kind="scene",
    )
    if bump_storyboard:
        _bump_scene_storyboard(db, scene)
    db.flush()


def project_id_for_scene(db: Session, scene: Scene) -> str:
    chapter = db.get(Chapter, scene.chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter.project_id

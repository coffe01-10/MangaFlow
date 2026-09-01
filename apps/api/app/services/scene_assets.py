"""Scene asset service: background resolution, snapshots and reference lease.

Consumption rule follows docs/v02-scene-asset-contract.md §5: structured
fields (variant overrides applied) -> asset description -> Scene.location
historical text. The compiled text is never stored back on the Scene; it only
upgrades prompt input quality at storyboard build time.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    Chapter,
    MangaPage,
    Scene,
    SceneAsset,
    SceneAssetReference,
    SceneAssetVariant,
    SceneAssetVariantReference,
)

TIME_OF_DAY_LABELS = {"dawn": "黎明", "day": "白天", "dusk": "黄昏", "night": "夜晚"}
VARIANT_OVERRIDE_KEYS = {"time_of_day", "weather", "lighting", "palette", "season"}


def normalized_name(value: str) -> str:
    return "".join(value.split()).casefold()


def validate_variant_overrides(value: dict) -> None:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="变体覆盖必须是对象")
    unknown = set(value) - VARIANT_OVERRIDE_KEYS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="变体只允许覆盖时间、天气、光照、色调或季节字段",
        )


def _structured_background(structured: dict) -> str:
    place = str(structured.get("place") or "").strip()
    interior = structured.get("interior")
    time_of_day = TIME_OF_DAY_LABELS.get(str(structured.get("time_of_day") or ""), "")
    weather = str(structured.get("weather") or "").strip()
    lighting = str(structured.get("lighting") or "").strip()
    fixed_props = [
        str(item).strip()
        for item in (structured.get("fixed_props") or [])
        if str(item).strip()
    ]
    if not any(
        (place, interior is not None, time_of_day, weather, lighting, fixed_props)
    ):
        return ""
    parts = [place] if place else []
    if interior is True:
        parts.append("室内")
    elif interior is False:
        parts.append("室外")
    if time_of_day:
        parts.append(time_of_day)
    if weather:
        parts.append(f"天气{weather}")
    if lighting:
        parts.append(f"光照{lighting}")
    if fixed_props:
        parts.append(f"固定物件：{'、'.join(fixed_props[:8])}")
    return "；".join(parts)


def resolve_scene_background(db: Session, scene: Scene | None) -> str:
    """Compile the background text used for a scene in generation.

    Priority (high -> low): structured fields compiled -> asset description ->
    historical Scene.location text. A soft-deleted or missing asset behaves
    exactly like an unbound scene.
    """

    if not scene or not scene.scene_asset_id:
        return scene.location if scene else "按原文场景"
    asset = db.get(SceneAsset, scene.scene_asset_id)
    if not asset or asset.deleted_at is not None:
        return scene.location
    structured = dict(asset.structured or {})
    if scene.scene_asset_variant_id:
        variant = db.get(SceneAssetVariant, scene.scene_asset_variant_id)
        if (
            variant
            and variant.scene_asset_id == asset.id
            and variant.deleted_at is None
        ):
            structured.update(dict(variant.structured_overrides or {}))
    background = _structured_background(structured)
    if background:
        return background
    if asset.description:
        return asset.description
    return scene.location


def page_primary_scene(db: Session, page: MangaPage) -> Scene | None:
    """Return the first scene of a page, honoring page.scene_ids order."""

    if not page.scene_ids:
        return None
    scenes = list(db.scalars(select(Scene).where(Scene.id.in_(page.scene_ids))))
    scene_order = {scene_id: index for index, scene_id in enumerate(page.scene_ids)}
    scenes.sort(key=lambda item: scene_order.get(item.id, len(scene_order)))
    return scenes[0] if scenes else None


def scene_asset_snapshot(db: Session, page: MangaPage) -> dict:
    """Snapshot the asset/version facts used at the generation boundary.

    The snapshot is a copy: later asset revisions never change it, mirroring
    ``based_on_storyboard_version``. Scenes without a bound asset record a
    ``scene_asset_id=None`` marker so consumers can distinguish "no asset"
    from "asset snapshot failed to load".
    """

    snapshot: dict = {"scene_asset_id": None, "scene_asset_version": None}
    scene = page_primary_scene(db, page)
    if not scene:
        return snapshot
    snapshot["scene_id"] = scene.id
    if not scene.scene_asset_id:
        snapshot["compiled_background"] = scene.location
        return snapshot
    asset = db.get(SceneAsset, scene.scene_asset_id)
    if not asset or asset.deleted_at is not None:
        snapshot["compiled_background"] = scene.location
        return snapshot
    snapshot["scene_asset_id"] = asset.id
    snapshot["scene_asset_version"] = asset.version
    snapshot["compiled_background"] = resolve_scene_background(db, scene)
    if scene.scene_asset_variant_id:
        variant = db.get(SceneAssetVariant, scene.scene_asset_variant_id)
        if variant and variant.scene_asset_id == asset.id and variant.deleted_at is None:
            snapshot["scene_asset_variant_id"] = variant.id
            snapshot["scene_asset_variant_version"] = variant.version
            snapshot["variant_structured_overrides"] = dict(
                variant.structured_overrides or {}
            )
    return snapshot


def scene_reference_assets(db: Session, page: MangaPage) -> list[Asset]:
    """Reference files leased for the page's primary scene.

    The lease set covers both asset-level and bound-variant-level references
    (contract §8): a running job must keep every scene reference image locked.
    """

    scene = page_primary_scene(db, page)
    if not scene or not scene.scene_asset_id:
        return []
    asset = db.get(SceneAsset, scene.scene_asset_id)
    if not asset or asset.deleted_at is not None:
        return []
    asset_ids = set(
        db.scalars(
            select(SceneAssetReference.asset_id).where(
                SceneAssetReference.scene_asset_id == asset.id
            )
        )
    )
    if scene.scene_asset_variant_id:
        variant = db.get(SceneAssetVariant, scene.scene_asset_variant_id)
        if variant and variant.scene_asset_id == asset.id and variant.deleted_at is None:
            asset_ids.update(
                db.scalars(
                    select(SceneAssetVariantReference.asset_id).where(
                        SceneAssetVariantReference.variant_id == variant.id
                    )
                )
            )
    if not asset_ids:
        return []
    return list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(asset_ids),
                Asset.deleted_at.is_(None),
            )
        )
    )


def mark_pages_for_scene_asset_review(db: Session, scene_asset_id: str) -> None:
    """Flag every page whose scenes bind the given asset as needing review."""

    from app.services.editor import mark_pages_for_review

    chapter_ids = list(
        db.scalars(
            select(Scene.chapter_id).where(Scene.scene_asset_id == scene_asset_id)
        )
    )
    for chapter_id in dict.fromkeys(chapter_ids):
        chapter = db.get(Chapter, chapter_id)
        if not chapter or chapter.deleted_at is not None:
            continue
        mark_pages_for_review(
            db,
            chapter_id,
            reference_id=scene_asset_id,
            reference_kind="scene_asset",
        )

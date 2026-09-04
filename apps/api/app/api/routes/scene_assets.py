"""Scene asset management and scene binding routes.

All endpoints are project-scoped and validate project existence/soft-delete,
mirroring the character asset endpoints. Binding invariants (project match,
variant ownership, soft-delete state) return 422; duplicate reference bindings
and name conflicts return 409.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.uploads import _ensure_asset_not_in_active_job
from app.database import get_db
from app.models import (
    Asset,
    AssetStatus,
    Chapter,
    Project,
    Scene,
    SceneAsset,
    SceneAssetReference,
    SceneAssetVariant,
    SceneAssetVariantReference,
    utcnow,
)
from app.schemas import (
    SceneAssetCreate,
    SceneAssetRead,
    SceneAssetReferenceCreate,
    SceneAssetReferenceRead,
    SceneAssetUpdate,
    SceneAssetVariantCreate,
    SceneAssetVariantRead,
    SceneAssetVariantReferenceCreate,
    SceneAssetVariantReferenceRead,
    SceneAssetVariantUpdate,
    SceneBindAssetRequest,
    SceneRead,
)
from app.services.editor import mark_pages_for_review
from app.services.scene_assets import (
    mark_pages_for_scene_asset_review,
    normalized_name,
    validate_variant_overrides,
)

router = APIRouter()


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _scene_asset(db: Session, project_id: str, asset_id: str) -> SceneAsset:
    _project(db, project_id)
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="场景资产不存在")
    return asset


def _variant_read(db: Session, variant: SceneAssetVariant) -> SceneAssetVariantRead:
    references = list(
        db.scalars(
            select(SceneAssetVariantReference)
            .where(SceneAssetVariantReference.variant_id == variant.id)
            .order_by(
                SceneAssetVariantReference.sort_order,
                SceneAssetVariantReference.created_at,
            )
        )
    )
    return SceneAssetVariantRead.model_validate(variant).model_copy(
        update={
            "references": [
                SceneAssetVariantReferenceRead.model_validate(item) for item in references
            ]
        }
    )


def _scene_asset_read(db: Session, asset: SceneAsset) -> SceneAssetRead:
    references = list(
        db.scalars(
            select(SceneAssetReference)
            .where(SceneAssetReference.scene_asset_id == asset.id)
            .order_by(SceneAssetReference.created_at)
        )
    )
    variants = [
        _variant_read(db, variant)
        for variant in db.scalars(
            select(SceneAssetVariant)
            .where(SceneAssetVariant.scene_asset_id == asset.id)
            .order_by(SceneAssetVariant.created_at, SceneAssetVariant.id)
        )
    ]
    return SceneAssetRead.model_validate(asset).model_copy(
        update={
            "references": [
                SceneAssetReferenceRead.model_validate(item) for item in references
            ],
            "variants": variants,
        }
    )


@router.get("/projects/{project_id}/scene-assets", response_model=list[SceneAssetRead])
def list_scene_assets(
    project_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    include_deleted: bool = Query(default=False, alias="include_deleted"),
    place: str | None = Query(default=None, max_length=120),
    interior: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[SceneAssetRead]:
    _project(db, project_id)
    query = select(SceneAsset).where(SceneAsset.project_id == project_id)
    if not include_deleted:
        query = query.where(SceneAsset.deleted_at.is_(None))
    assets = list(
        db.scalars(
            query.order_by(
                SceneAsset.deleted_at.is_(None).desc(),
                SceneAsset.name,
                SceneAsset.id,
            )
        )
    )
    if status_filter:
        assets = [item for item in assets if item.status.value == status_filter]
    if place:
        assets = [
            item
            for item in assets
            if str((item.structured or {}).get("place") or "").startswith(place)
        ]
    if interior is not None:
        assets = [
            item for item in assets if (item.structured or {}).get("interior") is interior
        ]
    page = assets[offset : offset + limit]
    return [_scene_asset_read(db, item) for item in page]


@router.post(
    "/projects/{project_id}/scene-assets",
    response_model=SceneAssetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scene_asset(
    project_id: str,
    payload: SceneAssetCreate,
    db: Session = Depends(get_db),
) -> SceneAssetRead:
    _project(db, project_id)
    name = payload.name.strip()
    asset = SceneAsset(
        project_id=project_id,
        name=name,
        normalized_name=normalized_name(name),
        description=payload.description,
        location_hint=payload.location_hint,
        structured=payload.structured.model_dump(exclude_unset=True),
        status=AssetStatus.UPLOADED,
    )
    db.add(asset)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同项目已存在同名场景资产") from None
    db.refresh(asset)
    return _scene_asset_read(db, asset)


@router.get("/projects/{project_id}/scene-assets/{asset_id}", response_model=SceneAssetRead)
def get_scene_asset(
    project_id: str, asset_id: str, db: Session = Depends(get_db)
) -> SceneAssetRead:
    asset = _scene_asset(db, project_id, asset_id)
    return _scene_asset_read(db, asset)


@router.patch("/projects/{project_id}/scene-assets/{asset_id}", response_model=SceneAssetRead)
def update_scene_asset(
    project_id: str,
    asset_id: str,
    payload: SceneAssetUpdate,
    db: Session = Depends(get_db),
) -> SceneAssetRead:
    asset = _scene_asset(db, project_id, asset_id)
    # Claim the row with an atomic conditional update so concurrent PATCHes
    # cannot both pass an in-memory version comparison.
    claimed = db.execute(
        update(SceneAsset)
        .where(SceneAsset.id == asset.id, SceneAsset.version == payload.version)
        .values(version=payload.version + 1)
    )
    if not claimed.rowcount:
        raise HTTPException(status_code=409, detail="场景资产已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "name" in values:
        values["name"] = values["name"].strip()
        values["normalized_name"] = normalized_name(values["name"])
    if "structured" in values and values["structured"] is not None:
        values["structured"] = values["structured"].model_dump(exclude_unset=True)
    if "status" in values and values["status"] is not None:
        values["status"] = AssetStatus(values["status"])
    for key, value in values.items():
        if value is not None:
            setattr(asset, key, value)
    asset.version = payload.version + 1
    mark_pages_for_scene_asset_review(db, asset.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同项目已存在同名场景资产") from None
    db.refresh(asset)
    return _scene_asset_read(db, asset)


@router.post(
    "/projects/{project_id}/scene-assets/{asset_id}/restore",
    response_model=SceneAssetRead,
)
def restore_scene_asset(
    project_id: str, asset_id: str, db: Session = Depends(get_db)
) -> SceneAssetRead:
    asset = _scene_asset(db, project_id, asset_id)
    if asset.deleted_at is None:
        return _scene_asset_read(db, asset)
    asset.deleted_at = None
    asset.version += 1
    mark_pages_for_scene_asset_review(db, asset.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同项目已存在同名场景资产") from None
    db.refresh(asset)
    return _scene_asset_read(db, asset)


@router.delete(
    "/projects/{project_id}/scene-assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_scene_asset(project_id: str, asset_id: str, db: Session = Depends(get_db)) -> None:
    _project(db, project_id)
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="场景资产不存在")
    reference_asset_ids = list(
        db.scalars(
            select(SceneAssetReference.asset_id).where(
                SceneAssetReference.scene_asset_id == asset.id
            )
        )
    )
    reference_asset_ids.extend(
        db.scalars(
            select(SceneAssetVariantReference.asset_id)
            .join(
                SceneAssetVariant,
                SceneAssetVariant.id == SceneAssetVariantReference.variant_id,
            )
            .where(SceneAssetVariant.scene_asset_id == asset.id)
        )
    )
    for reference_asset_id in dict.fromkeys(reference_asset_ids):
        reference_asset = db.get(Asset, reference_asset_id)
        if reference_asset is not None:
            _ensure_asset_not_in_active_job(db, reference_asset)
    mark_pages_for_scene_asset_review(db, asset.id)
    asset.deleted_at = utcnow()
    asset.version += 1
    db.commit()


@router.post(
    "/projects/{project_id}/scene-assets/{asset_id}/references",
    response_model=SceneAssetReferenceRead,
    status_code=status.HTTP_201_CREATED,
)
def bind_scene_asset_reference(
    project_id: str,
    asset_id: str,
    payload: SceneAssetReferenceCreate,
    db: Session = Depends(get_db),
) -> SceneAssetReference:
    asset = _scene_asset(db, project_id, asset_id)
    reference_asset = _scene_reference_file(db, project_id, payload.asset_id)
    binding = SceneAssetReference(
        scene_asset_id=asset.id,
        asset_id=reference_asset.id,
        role=payload.role,
        is_canonical=payload.is_canonical,
    )
    db.add(binding)
    # Asset-level reference changes alter the scene reference set for later
    # generations; flag bound pages for review like the variant-level path
    # (architecture §6: bind/unbind marks related pages NEEDS_REVIEW).
    mark_pages_for_scene_asset_review(db, asset.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该参考图已绑定在当前场景资产") from None
    db.refresh(binding)
    return binding


@router.delete(
    "/projects/{project_id}/scene-assets/{asset_id}/references/{reference_asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unbind_scene_asset_reference(
    project_id: str,
    asset_id: str,
    reference_asset_id: str,
    db: Session = Depends(get_db),
) -> None:
    asset = _scene_asset(db, project_id, asset_id)
    deleted = db.execute(
        delete(SceneAssetReference).where(
            SceneAssetReference.scene_asset_id == asset.id,
            SceneAssetReference.asset_id == reference_asset_id,
        )
    )
    if not deleted.rowcount:
        raise HTTPException(status_code=404, detail="场景参考绑定不存在")
    mark_pages_for_scene_asset_review(db, asset.id)
    db.commit()


@router.post(
    "/projects/{project_id}/scene-assets/{asset_id}/variants",
    response_model=SceneAssetVariantRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scene_asset_variant(
    project_id: str,
    asset_id: str,
    payload: SceneAssetVariantCreate,
    db: Session = Depends(get_db),
) -> SceneAssetVariantRead:
    asset = _scene_asset(db, project_id, asset_id)
    validate_variant_overrides(payload.structured_overrides)
    if payload.is_canonical:
        db.execute(
            update(SceneAssetVariant)
            .where(
                SceneAssetVariant.scene_asset_id == asset.id,
                SceneAssetVariant.deleted_at.is_(None),
                SceneAssetVariant.is_canonical.is_(True),
            )
            .values(is_canonical=False, version=SceneAssetVariant.version + 1)
        )
        # Installing a default variant changes the effective scene input for
        # every scene without an explicit variant binding.
        mark_pages_for_scene_asset_review(db, asset.id)
    variant = SceneAssetVariant(
        scene_asset_id=asset.id,
        name=payload.name.strip(),
        structured_overrides=payload.structured_overrides,
        is_canonical=payload.is_canonical,
    )
    db.add(variant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="当前资产已有默认变体") from None
    db.refresh(variant)
    return _variant_read(db, variant)


@router.patch(
    "/projects/{project_id}/scene-assets/{asset_id}/variants/{variant_id}",
    response_model=SceneAssetVariantRead,
)
def update_scene_asset_variant(
    project_id: str,
    asset_id: str,
    variant_id: str,
    payload: SceneAssetVariantUpdate,
    db: Session = Depends(get_db),
) -> SceneAssetVariantRead:
    asset = _scene_asset(db, project_id, asset_id)
    variant = db.get(SceneAssetVariant, variant_id)
    if not variant or variant.scene_asset_id != asset.id:
        raise HTTPException(status_code=404, detail="场景变体不存在")
    # Atomic conditional version claim so concurrent PATCHes cannot both pass.
    claimed = db.execute(
        update(SceneAssetVariant)
        .where(
            SceneAssetVariant.id == variant.id,
            SceneAssetVariant.version == payload.version,
        )
        .values(version=payload.version + 1)
    )
    if not claimed.rowcount:
        raise HTTPException(status_code=409, detail="场景变体已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    if values.get("structured_overrides") is not None:
        validate_variant_overrides(values["structured_overrides"])
    if values.get("is_canonical") is True:
        db.execute(
            update(SceneAssetVariant)
            .where(
                SceneAssetVariant.scene_asset_id == asset.id,
                SceneAssetVariant.id != variant.id,
                SceneAssetVariant.deleted_at.is_(None),
                SceneAssetVariant.is_canonical.is_(True),
            )
            .values(is_canonical=False, version=SceneAssetVariant.version + 1)
        )
    for key, value in values.items():
        if value is not None:
            setattr(variant, key, value.strip() if isinstance(value, str) else value)
    variant.version = payload.version + 1
    mark_pages_for_scene_asset_review(db, asset.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="当前资产已有默认变体") from None
    db.refresh(variant)
    return _variant_read(db, variant)


@router.delete(
    "/projects/{project_id}/scene-assets/{asset_id}/variants/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_scene_asset_variant(
    project_id: str,
    asset_id: str,
    variant_id: str,
    db: Session = Depends(get_db),
) -> None:
    _scene_asset(db, project_id, asset_id)
    variant = db.get(SceneAssetVariant, variant_id)
    if not variant or variant.scene_asset_id != asset_id:
        raise HTTPException(status_code=404, detail="场景变体不存在")
    variant.deleted_at = utcnow()
    variant.version += 1
    mark_pages_for_scene_asset_review(db, asset_id)
    db.commit()


def _scene_reference_file(db: Session, project_id: str, asset_id: str) -> Asset:
    reference_asset = db.get(Asset, asset_id)
    if not reference_asset or reference_asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail="素材不存在")
    if reference_asset.project_id != project_id:
        raise HTTPException(status_code=422, detail="参考图和场景资产不属于同一项目")
    allowed_generated_sources = {"AI_GENERATED", "VERTEX_GENERATED"}
    if (
        reference_asset.kind != "SCENE_REFERENCE"
        and reference_asset.source not in allowed_generated_sources
    ):
        raise HTTPException(
            status_code=409,
            detail="只有场景参考图或已生成的图片可以绑定为场景参考",
        )
    return reference_asset


@router.post(
    "/projects/{project_id}/scene-assets/{asset_id}/variants/{variant_id}/references",
    response_model=SceneAssetVariantReferenceRead,
    status_code=status.HTTP_201_CREATED,
)
def bind_scene_asset_variant_reference(
    project_id: str,
    asset_id: str,
    variant_id: str,
    payload: SceneAssetVariantReferenceCreate,
    db: Session = Depends(get_db),
) -> SceneAssetVariantReference:
    asset = _scene_asset(db, project_id, asset_id)
    variant = db.get(SceneAssetVariant, variant_id)
    if not variant or variant.scene_asset_id != asset.id:
        raise HTTPException(status_code=404, detail="场景变体不存在")
    if variant.deleted_at is not None:
        raise HTTPException(status_code=422, detail="场景变体已归档，请先恢复")
    reference_asset = _scene_reference_file(db, project_id, payload.asset_id)
    binding = SceneAssetVariantReference(
        variant_id=variant.id,
        asset_id=reference_asset.id,
        role=payload.role,
        sort_order=payload.sort_order,
    )
    db.add(binding)
    mark_pages_for_scene_asset_review(db, asset.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该参考图已绑定在当前场景变体") from None
    db.refresh(binding)
    return binding


@router.delete(
    "/projects/{project_id}/scene-assets/{asset_id}/variants/{variant_id}/references/{reference_asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unbind_scene_asset_variant_reference(
    project_id: str,
    asset_id: str,
    variant_id: str,
    reference_asset_id: str,
    db: Session = Depends(get_db),
) -> None:
    asset = _scene_asset(db, project_id, asset_id)
    variant = db.get(SceneAssetVariant, variant_id)
    if not variant or variant.scene_asset_id != asset.id:
        raise HTTPException(status_code=404, detail="场景变体不存在")
    deleted = db.execute(
        delete(SceneAssetVariantReference).where(
            SceneAssetVariantReference.variant_id == variant_id,
            SceneAssetVariantReference.asset_id == reference_asset_id,
        )
    )
    if not deleted.rowcount:
        raise HTTPException(status_code=404, detail="变体参考绑定不存在")
    mark_pages_for_scene_asset_review(db, asset.id)
    db.commit()


@router.patch("/scenes/{scene_id}/bind-asset", response_model=SceneRead)
def bind_scene_asset(
    scene_id: str,
    payload: SceneBindAssetRequest,
    db: Session = Depends(get_db),
) -> SceneRead:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")
    chapter = db.get(Chapter, scene.chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="场景所属章节不存在")
    asset_id = payload.scene_asset_id
    variant_id = payload.scene_asset_variant_id
    if variant_id is not None and asset_id is None:
        raise HTTPException(status_code=422, detail="请先选择场景资产")
    if asset_id is None:
        scene.scene_asset_id = None
        scene.scene_asset_variant_id = None
    else:
        asset = db.get(SceneAsset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="场景资产不存在")
        if asset.project_id != chapter.project_id:
            raise HTTPException(status_code=422, detail="场景资产不属于当前项目")
        if asset.deleted_at is not None:
            raise HTTPException(status_code=422, detail="场景资产已归档，请先恢复")
        scene.scene_asset_id = asset.id
        if variant_id is not None:
            variant = db.get(SceneAssetVariant, variant_id)
            if not variant:
                raise HTTPException(status_code=404, detail="场景变体不存在")
            if variant.scene_asset_id != asset.id:
                raise HTTPException(status_code=422, detail="变体不属于所选场景资产")
            if variant.deleted_at is not None:
                raise HTTPException(status_code=422, detail="场景变体已归档，请先恢复")
            scene.scene_asset_variant_id = variant.id
        else:
            scene.scene_asset_variant_id = None
    scene.version += 1
    mark_pages_for_review(db, chapter.id, reference_id=scene.id, reference_kind="scene")
    db.commit()
    db.refresh(scene)
    return SceneRead.model_validate(scene)

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, character_references
from app.config import get_settings
from app.database import get_db
from app.models import (
    Asset,
    AssetCandidate,
    AssetStatus,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    JobAssetReference,
    MangaPage,
    Outfit,
    Panel,
    Project,
    Scene,
    StyleProfile,
    StyleStatus,
)
from app.schemas import (
    AssetBatchCreate,
    AssetCandidateCreate,
    AssetReferenceApproval,
    CandidateQueuedRead,
    CharacterSheetCreate,
    GenerationBatchRead,
    JobRead,
    OutfitCreate,
    OutfitRead,
    OutfitUpdate,
    SceneOutfitUpdate,
    StylePaletteApproval,
    StylePaletteDraftRequest,
    StyleProfileCreate,
    StyleProfileRead,
    StyleProfileUpdate,
    StyleTestApproval,
)
from app.services.job_service import create_job, enqueue_job
from app.services.model_router import model_supports_resolution, resolve_model

router = APIRouter()

ACTIVE_OUTFIT_JOB_STATUSES = {
    "WAITING",
    "QUEUED",
    "PREPARING",
    "UPLOADING_REFERENCES",
    "GENERATING",
    "OCR_CHECKING",
    "CONSISTENCY_CHECKING",
    "REPAIRING",
}


def _validate_reference_assets(
    db: Session,
    project_id: str,
    asset_ids: list[str],
    expected_kind: str,
    label: str,
) -> None:
    for asset_id in asset_ids:
        asset = db.get(Asset, asset_id)
        if (
            not asset
            or asset.deleted_at is not None
            or asset.project_id != project_id
            or (
                asset.kind != expected_kind
                and not (
                    expected_kind == "OUTFIT_REFERENCE"
                    and asset.source in {"AI_GENERATED", "VERTEX_GENERATED"}
                )
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=f"{label}不存在、用途错误或不属于当前项目",
            )


def _has_active_reference_assets(
    db: Session, project_id: str, asset_ids: list[str]
) -> bool:
    if not asset_ids:
        return False
    return (
        db.scalar(
            select(func.count(Asset.id)).where(
                Asset.id.in_(asset_ids),
                Asset.project_id == project_id,
                Asset.deleted_at.is_(None),
            )
        )
        or 0
    ) > 0


def _generation_reference_ids(db: Session, batch: GenerationBatch) -> list[str]:
    if batch.target_type == "CHARACTER":
        return [item.asset_id for item in character_references(db, batch.target_id)]
    if batch.target_type == "OUTFIT":
        outfit = db.get(Outfit, batch.target_id)
        if not outfit:
            return []
        character_ids = [
            item.asset_id for item in character_references(db, outfit.character_id)
        ]
        return list(dict.fromkeys([*character_ids, *outfit.reference_asset_ids]))
    if batch.target_type == "STYLE":
        style = db.get(StyleProfile, batch.target_id)
        return list(style.profile.get("reference_asset_ids", [])) if style else []
    return []


@router.get("/projects/{project_id}/outfits", response_model=list[OutfitRead])
def list_outfits(project_id: str, db: Session = Depends(get_db)) -> list[Outfit]:
    return list(
        db.scalars(
            select(Outfit).where(Outfit.project_id == project_id).order_by(Outfit.created_at)
        )
    )


@router.post(
    "/projects/{project_id}/outfits",
    response_model=OutfitRead,
    status_code=status.HTTP_201_CREATED,
)
def create_outfit(
    project_id: str,
    payload: OutfitCreate,
    db: Session = Depends(get_db),
) -> Outfit:
    project = db.get(Project, project_id)
    character = db.get(Character, payload.character_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not character or character.project_id != project_id:
        raise HTTPException(status_code=409, detail="服装角色不属于当前项目")
    _validate_reference_assets(
        db,
        project_id,
        payload.reference_asset_ids,
        "OUTFIT_REFERENCE",
        "服装参考图",
    )
    outfit = Outfit(project_id=project_id, **payload.model_dump())
    db.add(outfit)
    db.commit()
    db.refresh(outfit)
    return outfit


@router.patch("/outfits/{outfit_id}", response_model=OutfitRead)
def update_outfit(
    outfit_id: str,
    payload: OutfitUpdate,
    db: Session = Depends(get_db),
) -> Outfit:
    outfit = db.get(Outfit, outfit_id)
    if not outfit:
        raise HTTPException(status_code=404, detail="服装档案不存在")
    if outfit.version != payload.version:
        raise HTTPException(status_code=409, detail="服装档案已更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    reference_asset_ids = values.get("reference_asset_ids")
    if reference_asset_ids is not None:
        _validate_reference_assets(
            db,
            outfit.project_id,
            reference_asset_ids,
            "OUTFIT_REFERENCE",
            "服装参考图",
        )
        values["reference_asset_ids"] = list(dict.fromkeys(reference_asset_ids))
    if "name" in values:
        values["name"] = values["name"].strip()
    for key, value in values.items():
        setattr(outfit, key, value)
    outfit.version += 1
    db.commit()
    db.refresh(outfit)
    return outfit


@router.delete("/outfits/{outfit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_outfit(outfit_id: str, db: Session = Depends(get_db)) -> None:
    outfit = db.get(Outfit, outfit_id)
    if not outfit:
        raise HTTPException(status_code=404, detail="服装档案不存在")

    batches = list(
        db.scalars(
            select(GenerationBatch).where(
                GenerationBatch.target_type == "OUTFIT",
                GenerationBatch.target_id == outfit.id,
            )
        )
    )
    batch_ids = [batch.id for batch in batches]
    candidates = (
        list(db.scalars(select(AssetCandidate).where(AssetCandidate.batch_id.in_(batch_ids))))
        if batch_ids
        else []
    )
    candidate_job_ids = [candidate.job_id for candidate in candidates if candidate.job_id]
    active_candidate_job = (
        db.scalar(
            select(GenerationJob.id).where(
                GenerationJob.id.in_(candidate_job_ids),
                GenerationJob.status.in_(ACTIVE_OUTFIT_JOB_STATUSES),
            )
        )
        if candidate_job_ids
        else None
    )
    reference_ids = set(outfit.reference_asset_ids or [])
    active_reference_job = (
        db.scalar(
            select(JobAssetReference.job_id)
            .join(GenerationJob, GenerationJob.id == JobAssetReference.job_id)
            .where(
                JobAssetReference.asset_id.in_(reference_ids),
                GenerationJob.status.in_(ACTIVE_OUTFIT_JOB_STATUSES),
            )
            .limit(1)
        )
        if reference_ids
        else None
    )
    if active_candidate_job or active_reference_job:
        raise HTTPException(
            status_code=409,
            detail="服装档案或参考图正被生成任务使用，请先取消任务后再删除",
        )

    other_reference_ids = {
        asset_id
        for other in db.scalars(
            select(Outfit).where(
                Outfit.project_id == outfit.project_id,
                Outfit.id != outfit.id,
            )
        )
        for asset_id in (other.reference_asset_ids or [])
    }
    character_reference_ids = set(
        db.scalars(
            select(CharacterReference.asset_id)
            .join(Character, Character.id == CharacterReference.character_id)
            .where(Character.project_id == outfit.project_id)
        )
    )
    style_reference_ids = {
        asset_id
        for style in db.scalars(
            select(StyleProfile).where(StyleProfile.project_id == outfit.project_id)
        )
        for asset_id in style.profile.get("reference_asset_ids", [])
    }
    protected_reference_ids = other_reference_ids | character_reference_ids | style_reference_ids
    exclusive_reference_ids = reference_ids - protected_reference_ids
    generated_asset_ids = {
        candidate.asset_id for candidate in candidates if candidate.asset_id
    }
    deleted_at = datetime.now(UTC)
    for candidate in candidates:
        if candidate.deleted_at is None:
            candidate.deleted_at = deleted_at
            candidate.version += 1
    user_owned_reference_ids = {
        asset.id
        for asset in db.scalars(select(Asset).where(Asset.id.in_(exclusive_reference_ids)))
        if asset.source == "USER_UPLOAD"
    }
    asset_ids_to_delete = user_owned_reference_ids | (
        generated_asset_ids - protected_reference_ids
    )
    if asset_ids_to_delete:
        for asset in db.scalars(select(Asset).where(Asset.id.in_(asset_ids_to_delete))):
            if asset.deleted_at is None:
                asset.deleted_at = deleted_at
                asset.version += 1

    scenes = db.scalars(
        select(Scene)
        .join(Chapter, Chapter.id == Scene.chapter_id)
        .where(Chapter.project_id == outfit.project_id)
    )
    for scene in scenes:
        assignments = dict(scene.outfit_assignments or {})
        cleaned = {
            character_id: assigned_outfit_id
            for character_id, assigned_outfit_id in assignments.items()
            if assigned_outfit_id != outfit.id
        }
        if cleaned != assignments:
            scene.outfit_assignments = cleaned
            scene.version += 1
    panels = db.scalars(
        select(Panel)
        .join(MangaPage, MangaPage.id == Panel.page_id)
        .join(Chapter, Chapter.id == MangaPage.chapter_id)
        .where(Chapter.project_id == outfit.project_id)
    )
    for panel in panels:
        assignments = dict(panel.outfits or {})
        cleaned = {
            character_id: assigned_outfit_id
            for character_id, assigned_outfit_id in assignments.items()
            if assigned_outfit_id != outfit.id
        }
        if cleaned != assignments:
            panel.outfits = cleaned
            panel.version += 1

    db.delete(outfit)
    db.commit()


@router.get("/projects/{project_id}/styles", response_model=list[StyleProfileRead])
def list_styles(project_id: str, db: Session = Depends(get_db)) -> list[StyleProfile]:
    return list(
        db.scalars(
            select(StyleProfile)
            .where(StyleProfile.project_id == project_id)
            .order_by(StyleProfile.created_at)
        )
    )


@router.post(
    "/projects/{project_id}/styles",
    response_model=StyleProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_style(
    project_id: str,
    payload: StyleProfileCreate,
    db: Session = Depends(get_db),
) -> StyleProfile:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    _validate_reference_assets(
        db,
        project_id,
        payload.reference_asset_ids,
        "STYLE_REFERENCE",
        "漫画风格参考图",
    )
    profile = dict(payload.profile)
    profile["reference_asset_ids"] = payload.reference_asset_ids
    style = StyleProfile(
        project_id=project_id,
        name=payload.name,
        color_mode=payload.color_mode,
        profile=profile,
        locked_fields=payload.locked_fields,
        status="DRAFT",
    )
    db.add(style)
    db.flush()
    if not project.default_style_id:
        project.default_style_id = style.id
    db.commit()
    db.refresh(style)
    return style


@router.patch("/styles/{style_id}", response_model=StyleProfileRead)
def update_style(
    style_id: str,
    payload: StyleProfileUpdate,
    db: Session = Depends(get_db),
) -> StyleProfile:
    style = db.get(StyleProfile, style_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格档案不存在")
    if style.version != payload.version:
        raise HTTPException(status_code=409, detail="风格档案已更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    reference_ids = values.pop("reference_asset_ids", None)
    if reference_ids is not None:
        _validate_reference_assets(
            db,
            style.project_id,
            reference_ids,
            "STYLE_REFERENCE",
            "漫画风格参考图",
        )
    color_changed = bool(
        values.get("color_mode") and values["color_mode"] != style.color_mode
    )
    profile_patch = values.pop("profile", None)
    for key, value in values.items():
        setattr(style, key, value.strip() if key == "name" else value)
    profile = dict(style.profile)
    if profile_patch is not None:
        profile.update(profile_patch)
    if reference_ids is not None:
        profile["reference_asset_ids"] = list(dict.fromkeys(reference_ids))
    if color_changed:
        profile.pop("palette", None)
        profile.pop("palette_draft", None)
        profile["palette_confirmed"] = False
        profile["test_image_approved"] = False
        profile.pop("test_candidate_id", None)
    style.profile = profile
    style.status = StyleStatus.DRAFT
    style.version += 1
    db.commit()
    db.refresh(style)
    return style


@router.post("/projects/{project_id}/styles/{style_id}/activate", response_model=StyleProfileRead)
def activate_style(project_id: str, style_id: str, db: Session = Depends(get_db)) -> StyleProfile:
    project = db.get(Project, project_id)
    style = db.get(StyleProfile, style_id)
    if not project or not style or style.project_id != project_id:
        raise HTTPException(status_code=404, detail="项目或风格档案不存在")
    if style.color_mode != "color":
        raise HTTPException(status_code=409, detail="正式页面要求使用彩色漫画风格")
    if not style.profile.get("palette_confirmed"):
        raise HTTPException(status_code=409, detail="请先确认彩色色板")
    if not style.profile.get("test_image_approved"):
        raise HTTPException(status_code=409, detail="请先人工通过风格测试图")
    previous = db.get(StyleProfile, project.default_style_id) if project.default_style_id else None
    if previous and previous.id != style.id and previous.status == "ACTIVE":
        previous.status = "CONFIRMED"
        previous.version += 1
    project.default_style_id = style.id
    project.version += 1
    style.status = "ACTIVE"
    style.version += 1
    db.commit()
    db.refresh(style)
    return style


@router.post(
    "/styles/{style_id}/analyze",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def analyze_style(style_id: str, db: Session = Depends(get_db)):
    style = db.get(StyleProfile, style_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格档案不存在")
    reference_ids = style.profile.get("reference_asset_ids", [])
    if not reference_ids:
        raise HTTPException(status_code=409, detail="请先给风格档案绑定至少一张漫画参考图")
    style.status = "ANALYZING"
    style.version += 1
    db.commit()
    job = create_job(
        db,
        project_id=style.project_id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        model_alias="text.fast",
        reference_asset_ids=reference_ids,
        idempotency_key=f"style-analyze:{style.id}:{style.version}",
    )
    return enqueue_job(db, job)


@router.post(
    "/styles/{style_id}/palette-draft",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def draft_style_palette(
    style_id: str,
    payload: StylePaletteDraftRequest,
    db: Session = Depends(get_db),
):
    style = db.get(StyleProfile, style_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格档案不存在")
    if style.color_mode != "color":
        raise HTTPException(status_code=409, detail="请先将风格档案切换为彩色漫画")
    if not style.profile.get("reference_asset_ids"):
        raise HTTPException(status_code=409, detail="请先绑定至少一张风格参考图")
    style.status = StyleStatus.ANALYZING
    style.version += 1
    db.commit()
    job = create_job(
        db,
        project_id=style.project_id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        model_alias="text.fast",
        request_parameters={"palette_atmosphere": payload.atmosphere},
        reference_asset_ids=list(style.profile.get("reference_asset_ids", [])),
        idempotency_key=f"style-palette:{style.id}:{style.version}",
    )
    return enqueue_job(db, job)


@router.post("/styles/{style_id}/palette-approve", response_model=StyleProfileRead)
def approve_style_palette(
    style_id: str,
    payload: StylePaletteApproval,
    db: Session = Depends(get_db),
) -> StyleProfile:
    style = db.get(StyleProfile, style_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格档案不存在")
    if style.version != payload.version:
        raise HTTPException(status_code=409, detail="风格档案已更新，请刷新后重试")
    if style.color_mode != "color" or not payload.palette:
        raise HTTPException(status_code=409, detail="彩色色板不能为空")
    profile = dict(style.profile)
    profile["palette"] = payload.palette
    profile["palette_confirmed"] = True
    profile["test_image_approved"] = False
    profile.pop("test_candidate_id", None)
    style.profile = profile
    style.status = StyleStatus.DRAFT
    style.version += 1
    db.commit()
    db.refresh(style)
    return style


@router.post("/styles/{style_id}/style-test-approve", response_model=StyleProfileRead)
def approve_style_test(
    style_id: str,
    payload: StyleTestApproval,
    db: Session = Depends(get_db),
) -> StyleProfile:
    style = db.get(StyleProfile, style_id)
    candidate = db.get(AssetCandidate, payload.candidate_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格档案不存在")
    if style.version != payload.version:
        raise HTTPException(status_code=409, detail="风格档案已更新，请刷新后重试")
    batch = db.get(GenerationBatch, candidate.batch_id) if candidate else None
    if (
        not candidate
        or not batch
        or batch.target_type != "STYLE"
        or batch.target_id != style.id
        or candidate.variant != "STYLE_TEST"
        or candidate.status != "READY"
        or not candidate.asset_id
    ):
        raise HTTPException(status_code=409, detail="请选择已生成完成的风格测试图")
    profile = dict(style.profile)
    profile["test_candidate_id"] = candidate.id
    profile["test_image_approved"] = payload.approved
    style.profile = profile
    style.status = "CONFIRMED" if payload.approved else StyleStatus.DRAFT
    style.version += 1
    db.commit()
    db.refresh(style)
    return style


@router.patch("/scenes/{scene_id}/outfits", response_model=dict)
def assign_scene_outfits(
    scene_id: str,
    payload: SceneOutfitUpdate,
    db: Session = Depends(get_db),
) -> dict:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")
    chapter = db.get(Chapter, scene.chapter_id)
    assignments = {
        character_id: outfit_id
        for character_id, outfit_id in payload.assignments.items()
        if outfit_id.strip()
    }
    for character_id, outfit_id in assignments.items():
        character = db.get(Character, character_id)
        outfit = db.get(Outfit, outfit_id)
        if not character or character.project_id != chapter.project_id:
            raise HTTPException(status_code=409, detail="场景角色不属于当前项目")
        if (
            not outfit
            or outfit.project_id != chapter.project_id
            or outfit.character_id != character_id
        ):
            raise HTTPException(status_code=409, detail="服装必须属于指定角色")
    scene.outfit_assignments = assignments
    scene.version += 1
    db.commit()
    return {"scene_id": scene.id, "assignments": scene.outfit_assignments}


def _target_project(db: Session, target_type: str, target_id: str) -> tuple[str, object]:
    classes = {"CHARACTER": Character, "OUTFIT": Outfit, "STYLE": StyleProfile}
    target = db.get(classes[target_type], target_id)
    if not target:
        raise HTTPException(status_code=404, detail="生成目标不存在")
    return target.project_id, target


@router.post(
    "/asset-generation-batches",
    response_model=GenerationBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def start_asset_batch(
    payload: AssetBatchCreate,
    db: Session = Depends(get_db),
) -> GenerationBatch:
    expected_kind = {"CHARACTER": "CHARACTER", "OUTFIT": "OUTFIT", "STYLE": "STYLE_TEST"}
    if payload.generation_kind != expected_kind[payload.target_type]:
        raise HTTPException(status_code=422, detail="资产生成类型与目标档案不匹配")
    project_id, target = _target_project(db, payload.target_type, payload.target_id)
    if payload.target_type == "CHARACTER" and not character_references(db, target.id):
        raise HTTPException(status_code=409, detail="请先给角色绑定至少一张人物参考图")
    if payload.target_type == "OUTFIT":
        if not _has_active_reference_assets(db, project_id, target.reference_asset_ids):
            raise HTTPException(status_code=409, detail="请先给服装档案绑定至少一张服装参考图")
        if not character_references(db, target.character_id):
            raise HTTPException(status_code=409, detail="请先给服装所属角色绑定人物参考图")
    if payload.target_type == "STYLE" and not _has_active_reference_assets(
        db, project_id, target.profile.get("reference_asset_ids", [])
    ):
        raise HTTPException(status_code=409, detail="请先给风格档案绑定至少一张漫画参考图")
    if payload.target_type == "STYLE" and not target.profile.get("palette_confirmed"):
        raise HTTPException(status_code=409, detail="请先确认彩色色板，再生成风格测试图")
    ordinal = (
        db.scalar(
            select(func.max(GenerationBatch.ordinal)).where(
                GenerationBatch.project_id == project_id
            )
        )
        or 0
    ) + 1
    batch = GenerationBatch(
        project_id=project_id,
        ordinal=ordinal,
        generation_kind=payload.generation_kind,
        target_type=payload.target_type,
        target_id=payload.target_id,
        status="OPEN",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/asset-generation-batches", response_model=list[GenerationBatchRead])
def list_asset_batches(
    target_type: str,
    target_id: str,
    limit: int = 10,
    db: Session = Depends(get_db),
) -> list[GenerationBatch]:
    if target_type not in {"CHARACTER", "OUTFIT", "STYLE"}:
        raise HTTPException(status_code=422, detail="资产生成目标类型无效")
    _target_project(db, target_type, target_id)
    return list(
        db.scalars(
            select(GenerationBatch)
            .where(
                GenerationBatch.target_type == target_type,
                GenerationBatch.target_id == target_id,
            )
            .order_by(GenerationBatch.created_at.desc())
            .limit(min(max(limit, 1), 30))
        )
    )


@router.post(
    "/asset-generation-batches/{batch_id}/candidates",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_asset_candidate(
    batch_id: str,
    payload: AssetCandidateCreate,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    if payload.model_alias.lower() == "auto":
        raise HTTPException(
            status_code=422,
            detail="参考资产必须显式选择图片模型，以保持项目画风一致",
        )
    batch = db.get(GenerationBatch, batch_id)
    if not batch or batch.status != "OPEN" or not batch.target_type or not batch.target_id:
        raise HTTPException(status_code=409, detail="资产生成批次不存在或已关闭")
    reference_asset_ids = _generation_reference_ids(db, batch)
    resolved_model = resolve_model(
        db,
        get_settings(),
        operation="image_edit" if reference_asset_ids else "image_generate",
        explicit_reference=payload.model_alias,
        project_id=batch.project_id,
        task_kind="ASSET_GENERATE",
    )
    if not model_supports_resolution(resolved_model.model, payload.resolution.value):
        raise HTTPException(status_code=422, detail="所选模型不支持该输出清晰度")
    allowed_variants = {
        "CHARACTER": {"FRONT", "SIDE", "BACK", "EXPRESSION", "SHEET"},
        "OUTFIT": {"OUTFIT", "OUTFIT_SHEET"},
        "STYLE": {"STYLE_TEST"},
    }
    if payload.variant not in allowed_variants[batch.target_type]:
        raise HTTPException(status_code=422, detail="资产生成角度与目标档案不匹配")
    ordinal = (
        db.scalar(
            select(func.max(AssetCandidate.ordinal)).where(AssetCandidate.batch_id == batch.id)
        )
        or 0
    ) + 1
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=ordinal,
        model_alias=payload.model_alias,
        catalog_model_id=resolved_model.model.id,
        resolution=payload.resolution,
        variant=payload.variant,
        instruction=payload.instruction,
        status="QUEUED",
    )
    db.add(candidate)
    project = db.get(Project, batch.project_id)
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.last_image_model_id = resolved_model.model.id
    project.version += 1
    db.flush()
    job = create_job(
        db,
        project_id=batch.project_id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        model_alias=payload.model_alias,
        catalog_model_id=resolved_model.model.id,
        request_parameters={
            "variant": payload.variant,
            "instruction": payload.instruction,
            "resolution": payload.resolution.value,
        },
        reference_asset_ids=reference_asset_ids,
        idempotency_key=f"asset-candidate:{candidate.id}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=asset_candidate_read(candidate),
    )


@router.post(
    "/characters/{character_id}/complete-sheet",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_complete_character_sheet(
    character_id: str,
    payload: CharacterSheetCreate,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    has_reference = bool(character_references(db, character_id))
    if payload.generation_mode == "REFERENCE" and not has_reference:
        raise HTTPException(status_code=409, detail="请先给角色绑定至少一张人物参考图")
    ordinal = (
        db.scalar(
            select(func.max(GenerationBatch.ordinal)).where(
                GenerationBatch.project_id == character.project_id
            )
        )
        or 0
    ) + 1
    batch = GenerationBatch(
        project_id=character.project_id,
        ordinal=ordinal,
        generation_kind="CHARACTER",
        target_type="CHARACTER",
        target_id=character.id,
        status="OPEN",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    appearance = (
        payload.appearance_description.strip()
        or character.canonical_description
        or "依据剧本身份、年龄和气质设计稳定外观"
    )
    outfit_name = payload.outfit_name.strip() or "深色葬礼正装"
    outfit_description = (
        payload.outfit_description.strip() or "深色葬礼正装，克制、庄重、适合京都葬礼场景"
    )
    mode_instruction = (
        "这是没有既有人物参考图的概念草稿。"
        if payload.generation_mode == "CONCEPT"
        else "严格保持已有角色参考图的身份。"
    )
    alias_context = "、".join(character.aliases or []) or "无"
    return generate_asset_candidate(
        batch.id,
        AssetCandidateCreate(
            model_alias=payload.model_alias,
            resolution=payload.resolution,
            variant="SHEET",
            instruction=(
                f"{mode_instruction}角色主要姓名：{character.primary_name}；"
                f"绰号与关系称谓：{alias_context}。"
                "必须把哥哥、弟弟、姐姐、妹妹、父亲、母亲等关系称谓视为身份与性别约束，"
                "不得画成与称谓冲突的性别。"
                f"角色外观：{appearance}。"
                f"服装名称：{outfit_name}。服装要求：{outfit_description}。"
                "在一张彩色设定页中同时展示正面、侧面、背面、代表性表情、"
                "服装剪裁和配饰细节；这是一个综合版面，不生成四张独立图片。"
            ),
        ),
        db,
    )


@router.post("/asset-candidates/{candidate_id}/approve-reference", response_model=dict)
def approve_asset_reference(
    candidate_id: str,
    payload: AssetReferenceApproval,
    db: Session = Depends(get_db),
) -> dict:
    candidate = db.get(AssetCandidate, candidate_id)
    character = db.get(Character, payload.character_id)
    batch = db.get(GenerationBatch, candidate.batch_id) if candidate else None
    if (
        not candidate
        or not batch
        or batch.target_type != "CHARACTER"
        or batch.target_id != payload.character_id
        or candidate.status != "READY"
        or not candidate.asset_id
    ):
        raise HTTPException(status_code=409, detail="角色设定草稿尚未生成完成")
    if not character or character.id != batch.target_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    asset = db.get(Asset, candidate.asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=409, detail="设定草稿图片不存在")

    reference = db.scalar(
        select(CharacterReference).where(
            CharacterReference.character_id == character.id,
            CharacterReference.asset_id == asset.id,
        )
    )
    if payload.bind_character_reference:
        db.execute(
            CharacterReference.__table__.delete().where(
                CharacterReference.asset_id == asset.id,
                CharacterReference.character_id != character.id,
            )
        )
        if payload.set_canonical:
            db.execute(
                update(CharacterReference)
                .where(CharacterReference.character_id == character.id)
                .values(is_canonical=False)
            )
        if reference:
            reference.is_canonical = payload.set_canonical
        else:
            db.add(
                CharacterReference(
                    character_id=character.id,
                    asset_id=asset.id,
                    angle="complete_sheet",
                    is_canonical=payload.set_canonical,
                )
            )
        asset.kind = "CHARACTER_REFERENCE"
        character.status = "CANONICAL"
        character.version += 1

    outfit = None
    if payload.outfit_name:
        outfit_name = payload.outfit_name.strip()
        outfit = db.scalar(
            select(Outfit).where(
                Outfit.character_id == character.id,
                Outfit.name == outfit_name,
            )
        )
        if not outfit:
            outfit = Outfit(
                project_id=character.project_id,
                character_id=character.id,
                name=outfit_name,
                components={"description": payload.outfit_description},
                locked_fields=payload.outfit_locked_fields,
                reference_asset_ids=[asset.id],
                status="CANONICAL",
            )
            db.add(outfit)
        else:
            outfit.components = {
                **outfit.components,
                "description": payload.outfit_description,
            }
            outfit.locked_fields = payload.outfit_locked_fields
            outfit.reference_asset_ids = list(
                dict.fromkeys([*outfit.reference_asset_ids, asset.id])
            )
            outfit.status = "CANONICAL"
            outfit.version += 1

    snapshot = dict(candidate.prompt_snapshot)
    snapshot["reference_approval"] = {
        "approved": True,
        "character_id": character.id,
        "outfit_name": payload.outfit_name,
    }
    candidate.prompt_snapshot = snapshot
    candidate.version += 1
    db.commit()
    if outfit:
        db.refresh(outfit)
    return {
        "candidate_id": candidate.id,
        "asset_id": asset.id,
        "character_id": character.id,
        "outfit_id": outfit.id if outfit else None,
        "approved": True,
    }


@router.delete("/asset-candidates/{candidate_id}/approve-reference", response_model=dict)
def retract_asset_reference(candidate_id: str, db: Session = Depends(get_db)) -> dict:
    candidate = db.get(AssetCandidate, candidate_id)
    batch = db.get(GenerationBatch, candidate.batch_id) if candidate else None
    if not candidate or not batch or batch.target_type != "CHARACTER" or not candidate.asset_id:
        raise HTTPException(status_code=404, detail="角色设定候选不存在")
    snapshot = dict(candidate.prompt_snapshot)
    approval = snapshot.get("reference_approval")
    if not isinstance(approval, dict) or not approval.get("approved"):
        return {"candidate_id": candidate.id, "approved": False}

    character_id = str(approval.get("character_id") or batch.target_id or "")
    db.execute(
        CharacterReference.__table__.delete().where(
            CharacterReference.character_id == character_id,
            CharacterReference.asset_id == candidate.asset_id,
        )
    )
    outfit_name = str(approval.get("outfit_name") or "").strip()
    if outfit_name:
        outfit = db.scalar(
            select(Outfit).where(
                Outfit.character_id == character_id,
                Outfit.name == outfit_name,
            )
        )
        if outfit and candidate.asset_id in outfit.reference_asset_ids:
            outfit.reference_asset_ids = [
                asset_id
                for asset_id in outfit.reference_asset_ids
                if asset_id != candidate.asset_id
            ]
            outfit.status = (
                AssetStatus.CANONICAL
                if outfit.reference_asset_ids
                else AssetStatus.NEEDS_CONFIRMATION
            )
            outfit.version += 1
    character = db.get(Character, character_id)
    if character:
        has_other_reference = db.scalar(
            select(CharacterReference.id).where(CharacterReference.character_id == character_id)
        )
        if not has_other_reference:
            character.status = AssetStatus.NEEDS_CONFIRMATION
        character.version += 1

    snapshot["reference_approval"] = {
        **approval,
        "approved": False,
        "retracted": True,
    }
    candidate.prompt_snapshot = snapshot
    candidate.version += 1
    db.commit()
    return {"candidate_id": candidate.id, "approved": False}

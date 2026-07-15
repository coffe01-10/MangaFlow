from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, character_references
from app.config import get_settings
from app.database import get_db
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    Character,
    GenerationBatch,
    Outfit,
    Project,
    Scene,
    StyleProfile,
)
from app.schemas import (
    AssetBatchCreate,
    AssetCandidateCreate,
    CandidateQueuedRead,
    CharacterSheetCreate,
    GenerationBatchRead,
    JobRead,
    OutfitCreate,
    OutfitRead,
    SceneOutfitUpdate,
    StyleProfileCreate,
    StyleProfileRead,
)
from app.services.job_service import create_job, enqueue_job
from app.services.model_registry import build_registry

router = APIRouter()


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
    for asset_id in payload.reference_asset_ids:
        asset = db.get(Asset, asset_id)
        if (
            not asset
            or asset.deleted_at is not None
            or asset.project_id != project_id
            or asset.kind != "OUTFIT_REFERENCE"
        ):
            raise HTTPException(
                status_code=409, detail="服装参考图不存在、用途错误或不属于当前项目"
            )
    outfit = Outfit(project_id=project_id, **payload.model_dump())
    db.add(outfit)
    db.commit()
    db.refresh(outfit)
    return outfit


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
    for asset_id in payload.reference_asset_ids:
        asset = db.get(Asset, asset_id)
        if (
            not asset
            or asset.deleted_at is not None
            or asset.project_id != project_id
            or asset.kind != "STYLE_REFERENCE"
        ):
            raise HTTPException(
                status_code=409, detail="漫画风格参考图不存在、用途错误或不属于当前项目"
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


@router.post("/projects/{project_id}/styles/{style_id}/activate", response_model=StyleProfileRead)
def activate_style(project_id: str, style_id: str, db: Session = Depends(get_db)) -> StyleProfile:
    project = db.get(Project, project_id)
    style = db.get(StyleProfile, style_id)
    if not project or not style or style.project_id != project_id:
        raise HTTPException(status_code=404, detail="项目或风格档案不存在")
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
        idempotency_key=f"style-analyze:{style.id}:{style.version}",
    )
    return enqueue_job(db, job)


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
    for character_id, outfit_id in payload.assignments.items():
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
    scene.outfit_assignments = payload.assignments
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
        if not target.reference_asset_ids:
            raise HTTPException(status_code=409, detail="请先给服装档案绑定至少一张服装参考图")
        if not character_references(db, target.character_id):
            raise HTTPException(status_code=409, detail="请先给服装所属角色绑定人物参考图")
    if payload.target_type == "STYLE" and not target.profile.get("reference_asset_ids", []):
        raise HTTPException(status_code=409, detail="请先给风格档案绑定至少一张漫画参考图")
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
    batch = db.get(GenerationBatch, batch_id)
    if not batch or batch.status != "OPEN" or not batch.target_type or not batch.target_id:
        raise HTTPException(status_code=409, detail="资产生成批次不存在或已关闭")
    if payload.model_alias not in build_registry(get_settings()):
        raise HTTPException(status_code=422, detail="未识别的图像模型")
    allowed_variants = {
        "CHARACTER": {"FRONT", "SIDE", "BACK", "EXPRESSION"},
        "OUTFIT": {"OUTFIT"},
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
        resolution=payload.resolution,
        variant=payload.variant,
        instruction=payload.instruction,
        status="QUEUED",
    )
    db.add(candidate)
    project = db.get(Project, batch.project_id)
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.version += 1
    db.flush()
    job = create_job(
        db,
        project_id=batch.project_id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        model_alias=payload.model_alias,
        request_parameters={
            "variant": payload.variant,
            "instruction": payload.instruction,
            "resolution": payload.resolution.value,
        },
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
    response_model=list[CandidateQueuedRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_complete_character_sheet(
    character_id: str,
    payload: CharacterSheetCreate,
    db: Session = Depends(get_db),
) -> list[CandidateQueuedRead]:
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    if not character_references(db, character_id):
        raise HTTPException(status_code=409, detail="请先给角色绑定至少一张人物参考图")
    batch = start_asset_batch(
        AssetBatchCreate(
            target_type="CHARACTER",
            target_id=character_id,
            generation_kind="CHARACTER",
        ),
        db,
    )
    return [
        generate_asset_candidate(
            batch.id,
            AssetCandidateCreate(
                model_alias=payload.model_alias,
                resolution=payload.resolution,
                variant=variant,
                instruction="保持同一角色身份，生成标准角色设定资料",
            ),
            db,
        )
        for variant in payload.variants
    ]

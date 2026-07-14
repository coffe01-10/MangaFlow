from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read
from app.config import get_settings
from app.database import get_db
from app.models import (
    Asset,
    AssetCandidate,
    Character,
    GenerationBatch,
    Outfit,
    Project,
    StyleProfile,
)
from app.schemas import (
    AssetBatchCreate,
    AssetCandidateCreate,
    CandidateQueuedRead,
    GenerationBatchRead,
    OutfitCreate,
    OutfitRead,
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
        if not asset or asset.project_id != project_id:
            raise HTTPException(status_code=409, detail="风格参考素材不属于当前项目")
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
    db.commit()
    db.refresh(style)
    return style


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
    project_id, _ = _target_project(db, payload.target_type, payload.target_id)
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
    ordinal = (
        db.scalar(
            select(func.max(AssetCandidate.ordinal)).where(
                AssetCandidate.batch_id == batch.id
            )
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

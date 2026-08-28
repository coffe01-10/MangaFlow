import random
import sqlite3
import time
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    StyleProfile,
    utcnow,
)
from app.schemas import AssetCandidateCreate, CandidateCreate
from app.services.job_service import create_job
from app.services.model_router import model_supports_resolution, resolve_model

ORDINAL_ALLOCATION_MAX_ATTEMPTS = 5


class OrdinalConflictError(Exception):
    """Base exception for ordinal and revision allocation conflicts."""


class BatchOrdinalConflictError(OrdinalConflictError):
    """Raised when generation batch ordinal allocation fails after max attempts."""


class CandidateOrdinalConflictError(OrdinalConflictError):
    """Raised when page or asset candidate ordinal allocation fails after max attempts."""


class SourceRevisionConflictError(OrdinalConflictError):
    """Raised when source revision allocation fails after max attempts."""


class ChapterOrdinalConflictError(OrdinalConflictError):
    """Raised when chapter ordinal allocation fails after max attempts."""


def is_sqlite_lock_error(error: BaseException) -> bool:
    """Check if an OperationalError is caused by SQLite busy or locked database."""
    if not isinstance(error, OperationalError):
        return False
    orig = getattr(error, "orig", None)
    if not isinstance(orig, sqlite3.OperationalError):
        return False
    code = getattr(orig, "sqlite_errorcode", None)
    if code is not None and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    message = str(orig).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "busy" in message
    )


def lock_entity(db: Session, model_cls, entity_id: str):
    """Lock an entity with populate_existing and with_for_update on PostgreSQL."""
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    query = (
        select(model_cls)
        .where(model_cls.id == entity_id)
        .execution_options(populate_existing=True)
    )
    if dialect_name == "postgresql":
        query = query.with_for_update()
    return db.scalar(query)


def _next_batch_ordinal(db: Session, project_id: str) -> int:
    current = db.scalar(
        select(func.max(GenerationBatch.ordinal)).where(
            GenerationBatch.project_id == project_id
        )
    )
    return (current or 0) + 1


def _next_page_candidate_ordinal(db: Session, batch_id: str) -> int:
    current = db.scalar(
        select(func.max(PageCandidate.ordinal)).where(PageCandidate.batch_id == batch_id)
    )
    return (current or 0) + 1


def _next_asset_candidate_ordinal(db: Session, batch_id: str) -> int:
    current = db.scalar(
        select(func.max(AssetCandidate.ordinal)).where(AssetCandidate.batch_id == batch_id)
    )
    return (current or 0) + 1


def _generation_reference_ids(db: Session, batch: GenerationBatch) -> list[str]:
    if batch.target_type == "CHARACTER":
        refs = list(
            db.scalars(
                select(CharacterReference)
                .join(Asset, Asset.id == CharacterReference.asset_id)
                .where(
                    CharacterReference.character_id == batch.target_id,
                    Asset.deleted_at.is_(None),
                )
            )
        )
        return [item.asset_id for item in refs]
    if batch.target_type == "OUTFIT":
        outfit = db.get(Outfit, batch.target_id)
        if not outfit:
            return []
        refs = list(
            db.scalars(
                select(CharacterReference)
                .join(Asset, Asset.id == CharacterReference.asset_id)
                .where(
                    CharacterReference.character_id == outfit.character_id,
                    Asset.deleted_at.is_(None),
                )
            )
        )
        character_ids = [item.asset_id for item in refs]
        return list(dict.fromkeys([*character_ids, *outfit.reference_asset_ids]))
    if batch.target_type == "STYLE":
        style = db.get(StyleProfile, batch.target_id)
        return list(style.profile.get("reference_asset_ids", [])) if style else []
    return []


def create_generation_batch(
    db: Session,
    *,
    project_id: str,
    chapter_id: str | None = None,
    page_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    generation_kind: str = "PAGE",
    close_open_page_batches: bool = False,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
) -> GenerationBatch:
    """Create a new GenerationBatch with unique ordinal under caller's transaction."""
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            project = lock_entity(db, Project, project_id)
            if project is None or project.deleted_at is not None:
                raise HTTPException(status_code=404, detail="项目不存在")
            if page_id:
                page = db.get(MangaPage, page_id)
                if page is None:
                    raise HTTPException(status_code=404, detail="页面不存在")
            if close_open_page_batches and page_id:
                db.execute(
                    update(GenerationBatch)
                    .where(
                        GenerationBatch.page_id == page_id,
                        GenerationBatch.status == "OPEN",
                    )
                    .values(status="CLOSED", closed_at=utcnow())
                )
            ordinal = _next_batch_ordinal(db, project_id)
            batch = GenerationBatch(
                project_id=project_id,
                chapter_id=chapter_id,
                page_id=page_id,
                target_type=target_type,
                target_id=target_id,
                ordinal=ordinal,
                generation_kind=generation_kind,
                status="OPEN",
            )
            db.add(batch)
            db.flush()
            return batch
        except IntegrityError as error:
            last_error = error
            time.sleep(0.01 * (2 ** _attempt) + random.uniform(0, 0.02))
        except OperationalError as error:
            if not is_sqlite_lock_error(error):
                raise
            last_error = error
            time.sleep(0.02 * (2 ** _attempt) + random.uniform(0, 0.03))
    raise BatchOrdinalConflictError("抽卡批次分配冲突，请稍后重试") from last_error


def validate_candidate_reference_selections(
    db: Session,
    page: MangaPage,
    project: Project,
    payload: CandidateCreate,
) -> dict[str, dict[str, str | None]]:
    """Validate character and outfit reference selections for page candidate generation."""
    panels = list(db.scalars(select(Panel).where(Panel.page_id == page.id)))
    visible_character_ids = list(
        dict.fromkeys(character_id for panel in panels for character_id in panel.characters)
    )
    normalized_selections: dict[str, dict[str, str | None]] = {}
    for character_id in visible_character_ids:
        selection = payload.reference_selections.get(character_id, {})
        character_asset_id = selection.get("character_asset_id")
        valid_character_reference = (
            db.scalar(
                select(CharacterReference).where(
                    CharacterReference.character_id == character_id,
                    CharacterReference.asset_id == character_asset_id,
                )
            )
            if character_asset_id
            else None
        )
        if not valid_character_reference:
            character = db.get(Character, character_id)
            raise HTTPException(
                status_code=409,
                detail=(
                    "请为画面人物 "
                    f"{character.primary_name if character else character_id} "
                    "选择一张人物参考图"
                ),
            )
        assigned_outfits = {
            panel.outfits.get(character_id)
            for panel in panels
            if panel.outfits.get(character_id)
        }
        if len(assigned_outfits) > 1:
            raise HTTPException(status_code=409, detail="同一页同一角色存在多套服装，请先拆页")
        assigned_outfit_id = next(iter(assigned_outfits), None)
        outfit_id = selection.get("outfit_id") or assigned_outfit_id
        outfit_asset_id = selection.get("outfit_asset_id")
        if assigned_outfit_id and outfit_id != assigned_outfit_id:
            raise HTTPException(status_code=409, detail="参考确认中的服装与分镜指定服装不一致")
        if outfit_id:
            outfit = db.get(Outfit, outfit_id)
            if not outfit or outfit.character_id != character_id or outfit.project_id != project.id:
                raise HTTPException(status_code=409, detail="所选服装不属于当前人物")
            if not outfit.reference_asset_ids:
                raise HTTPException(status_code=409, detail="分镜指定服装还没有绑定参考图")
            if outfit_asset_id not in outfit.reference_asset_ids:
                raise HTTPException(status_code=409, detail="请为分镜服装选择一张已绑定参考图")
        normalized_selections[character_id] = {
            "character_asset_id": character_asset_id,
            "outfit_id": outfit_id,
            "outfit_asset_id": outfit_asset_id,
        }
    return normalized_selections


def create_page_candidate(
    db: Session,
    *,
    batch_id: str | None = None,
    payload: CandidateCreate,
    batch: GenerationBatch | None = None,
    page: MangaPage | None = None,
    project: Project | None = None,
    resolved_model=None,
    normalized_selections: dict[str, dict[str, str | None]] | None = None,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
) -> tuple[PageCandidate, GenerationJob]:
    """Create a PageCandidate and its associated GenerationJob atomically."""
    target_batch_id = batch_id or (batch.id if batch else None)
    if not target_batch_id:
        raise HTTPException(status_code=400, detail="缺少 batch_id")
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            current_batch = lock_entity(db, GenerationBatch, target_batch_id)
            if not current_batch or current_batch.status != "OPEN" or not current_batch.page_id:
                raise HTTPException(status_code=409, detail="抽卡批次不存在或已经关闭")
            current_page = db.get(MangaPage, current_batch.page_id)
            if not current_page:
                raise HTTPException(status_code=404, detail="页面不存在")
            if payload.storyboard_version != current_page.storyboard_version:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "STALE_STORYBOARD_VERSION",
                        "message": "分镜已更新，请刷新页面后重新确认参考图",
                        "expected": payload.storyboard_version,
                        "current": current_page.storyboard_version,
                    },
                )
            from app.api.routes import workflow as workflow_route_module

            workflow_route_module.ensure_page_ready(db, current_page, get_settings())
            chapter = db.get(Chapter, current_page.chapter_id)
            if not chapter:
                raise HTTPException(status_code=404, detail="章节不存在")
            current_project = lock_entity(db, Project, chapter.project_id)
            if not current_project or current_project.deleted_at is not None:
                raise HTTPException(status_code=404, detail="项目不存在")
            current_resolved_model = resolve_model(
                db,
                get_settings(),
                operation="image_edit",
                explicit_reference=payload.model_alias,
                project_id=current_project.id,
                task_kind="PAGE_GENERATE",
            )
            if not model_supports_resolution(
                current_resolved_model.model, payload.resolution.value
            ):
                raise HTTPException(status_code=422, detail="所选模型不支持该输出清晰度")
            current_normalized_selections = validate_candidate_reference_selections(
                db, current_page, current_project, payload
            )
            ordinal = _next_page_candidate_ordinal(db, current_batch.id)
            candidate = PageCandidate(
                batch_id=current_batch.id,
                page_id=current_page.id,
                ordinal=ordinal,
                model_alias=payload.model_alias,
                catalog_model_id=current_resolved_model.model.id,
                resolution=payload.resolution,
                status="QUEUED",
                based_on_storyboard_version=current_page.storyboard_version,
                prompt_snapshot={
                    "reference_selections": current_normalized_selections,
                    "storyboard_version": current_page.storyboard_version,
                },
            )
            db.add(candidate)
            db.flush()
            current_project.last_image_model_alias = payload.model_alias
            current_project.image_model_alias = payload.model_alias
            current_project.last_image_model_id = current_resolved_model.model.id
            current_project.version += 1
            job = create_job(
                db,
                project_id=current_project.id,
                target_type="PAGE_CANDIDATE",
                target_id=candidate.id,
                job_type="PAGE_GENERATE",
                model_alias=payload.model_alias,
                catalog_model_id=current_resolved_model.model.id,
                request_parameters={
                    "resolution": payload.resolution.value,
                    "storyboard_version": current_page.storyboard_version,
                    "reference_selections": current_normalized_selections,
                },
                reference_asset_ids=[
                    asset_id
                    for selection in current_normalized_selections.values()
                    for asset_id in (
                        selection.get("character_asset_id"),
                        selection.get("outfit_asset_id"),
                    )
                    if asset_id
                ],
                idempotency_key=f"candidate:{candidate.id}",
                auto_commit=False,
            )
            candidate.job_id = job.id
            db.commit()
            db.refresh(candidate)
            return candidate, job
        except IntegrityError as error:
            last_error = error
            db.rollback()
            time.sleep(0.01 * (2 ** _attempt) + random.uniform(0, 0.02))
        except OperationalError as error:
            db.rollback()
            if not is_sqlite_lock_error(error):
                raise
            last_error = error
            time.sleep(0.02 * (2 ** _attempt) + random.uniform(0, 0.03))
    raise CandidateOrdinalConflictError("页面候选分配冲突，请稍后重试") from last_error


def create_asset_candidate(
    db: Session,
    *,
    batch_id: str | None = None,
    payload: AssetCandidateCreate,
    batch: GenerationBatch | None = None,
    project: Project | None = None,
    resolved_model=None,
    reference_asset_ids: Sequence[str] | None = None,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
) -> tuple[AssetCandidate, GenerationJob]:
    """Create an AssetCandidate and its associated GenerationJob atomically."""
    target_batch_id = batch_id or (batch.id if batch else None)
    if not target_batch_id:
        raise HTTPException(status_code=400, detail="缺少 batch_id")
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            current_batch = lock_entity(db, GenerationBatch, target_batch_id)
            if (
                not current_batch
                or current_batch.status != "OPEN"
                or not current_batch.target_type
                or not current_batch.target_id
            ):
                raise HTTPException(status_code=409, detail="资产生成批次不存在或已关闭")
            current_project = lock_entity(db, Project, current_batch.project_id)
            if not current_project or current_project.deleted_at is not None:
                raise HTTPException(status_code=404, detail="项目不存在")
            ref_ids = (
                list(reference_asset_ids)
                if reference_asset_ids is not None
                else _generation_reference_ids(db, current_batch)
            )
            from app.api.routes import asset_generation as asset_generation_module

            resolve_fn = getattr(asset_generation_module, "resolve_model", resolve_model)
            current_resolved_model = resolve_fn(
                db,
                get_settings(),
                operation="image_edit" if ref_ids else "image_generate",
                explicit_reference=payload.model_alias,
                project_id=current_project.id,
                task_kind="ASSET_GENERATE",
            )
            if not model_supports_resolution(
                current_resolved_model.model, payload.resolution.value
            ):
                raise HTTPException(status_code=422, detail="所选模型不支持该输出清晰度")
            allowed_variants = {
                "CHARACTER": {"FRONT", "SIDE", "BACK", "EXPRESSION", "SHEET"},
                "OUTFIT": {"OUTFIT", "OUTFIT_SHEET"},
                "STYLE": {"STYLE_TEST"},
            }
            if payload.variant not in allowed_variants.get(current_batch.target_type, set()):
                raise HTTPException(status_code=422, detail="资产生成角度与目标档案不匹配")
            ordinal = _next_asset_candidate_ordinal(db, current_batch.id)
            candidate = AssetCandidate(
                batch_id=current_batch.id,
                ordinal=ordinal,
                model_alias=payload.model_alias,
                catalog_model_id=current_resolved_model.model.id,
                resolution=payload.resolution,
                variant=payload.variant,
                instruction=payload.instruction,
                status="QUEUED",
            )
            db.add(candidate)
            db.flush()
            current_project.last_image_model_alias = payload.model_alias
            current_project.image_model_alias = payload.model_alias
            current_project.last_image_model_id = current_resolved_model.model.id
            current_project.version += 1
            job = create_job(
                db,
                project_id=current_project.id,
                target_type="ASSET_CANDIDATE",
                target_id=candidate.id,
                job_type="ASSET_GENERATE",
                model_alias=payload.model_alias,
                catalog_model_id=current_resolved_model.model.id,
                request_parameters={
                    "variant": payload.variant,
                    "instruction": payload.instruction,
                    "resolution": payload.resolution.value,
                },
                reference_asset_ids=ref_ids,
                idempotency_key=f"asset-candidate:{candidate.id}",
                auto_commit=False,
            )
            candidate.job_id = job.id
            db.commit()
            db.refresh(candidate)
            return candidate, job
        except IntegrityError as error:
            last_error = error
            db.rollback()
            time.sleep(0.01 * (2 ** _attempt) + random.uniform(0, 0.02))
        except OperationalError as error:
            db.rollback()
            if not is_sqlite_lock_error(error):
                raise
            last_error = error
            time.sleep(0.02 * (2 ** _attempt) + random.uniform(0, 0.03))
    raise CandidateOrdinalConflictError("素材候选分配冲突，请稍后重试") from last_error


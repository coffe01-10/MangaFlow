"""Generation batch, page candidate and selection routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read, candidate_version_state
from app.api.routes.workflow.common import _new_batch, _page
from app.config import get_settings
from app.database import get_db
from app.domain.states import JobStatus, PageStatus
from app.models import (
    Asset,
    AssetCandidate,
    AssetStatus,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    Outfit,
    PageCandidate,
    utcnow,
)
from app.schemas import (
    CandidateCreate,
    CandidateQueuedRead,
    FavoriteUpdate,
    GenerationBatchRead,
    KeepSelectedCandidateRequest,
    PageCandidateRead,
    PageRead,
    SelectCandidateRequest,
)
from app.services.character_packages import (
    default_package_gate_context,
    detach_draft_package_references_for_asset,
)
from app.services.job_service import cancel_job, enqueue_job
from app.services.ordinal_allocator import (
    CandidateOrdinalConflictError,
    create_page_candidate,
    lock_entity,
)
from app.services.page_completion import build_page_production_readiness, production_error_detail
from app.services.page_readiness import ensure_page_ready

router = APIRouter()


@router.post(
    "/pages/{page_id}/batches",
    response_model=GenerationBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def start_batch(page_id: str, db: Session = Depends(get_db)) -> GenerationBatch:
    page = _page(db, page_id)
    # Contract §8.1: batch start gates on the default-inheritance package
    # context (ACTIVE package + published version) when no payload exists yet.
    ensure_page_ready(
        db, page, get_settings(), package_gate=default_package_gate_context(db, page)
    )
    return _new_batch(db, page)


@router.get("/pages/{page_id}/batches", response_model=list[GenerationBatchRead])
def list_batches(page_id: str, db: Session = Depends(get_db)) -> list[GenerationBatch]:
    _page(db, page_id)
    return list(
        db.scalars(
            select(GenerationBatch)
            .where(GenerationBatch.page_id == page_id)
            .order_by(GenerationBatch.ordinal.desc())
        )
    )


@router.post(
    "/batches/{batch_id}/candidates",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_candidate(
    batch_id: str,
    payload: CandidateCreate,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    if payload.model_alias.lower() == "auto":
        raise HTTPException(
            status_code=422,
            detail="生图模型必须显式选择，不能使用 auto",
        )
    try:
        candidate, job = create_page_candidate(
            db,
            batch_id=batch_id,
            payload=payload,
        )
    except CandidateOrdinalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    job = enqueue_job(db, job)
    page = db.get(MangaPage, candidate.page_id)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate, page),
    )


@router.get("/batches/{batch_id}/candidates", response_model=list[PageCandidateRead])
def list_candidates(batch_id: str, db: Session = Depends(get_db)) -> list[PageCandidateRead]:
    batch = db.get(GenerationBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="抽卡批次不存在")
    if batch.target_type:
        candidates = list(
            db.scalars(
                select(AssetCandidate)
                .where(
                    AssetCandidate.batch_id == batch_id,
                    AssetCandidate.deleted_at.is_(None),
                )
                .order_by(AssetCandidate.ordinal.desc())
            )
        )
        return [asset_candidate_read(item) for item in candidates]
    candidates = list(
        db.scalars(
            select(PageCandidate)
            .where(
                PageCandidate.batch_id == batch_id,
                PageCandidate.deleted_at.is_(None),
            )
            .order_by(PageCandidate.ordinal.desc())
        )
    )
    page = db.get(MangaPage, batch.page_id) if batch.page_id else None
    return [candidate_read(item, page) for item in candidates]


@router.patch("/candidates/{candidate_id}/favorite", response_model=PageCandidateRead)
def favorite_candidate(
    candidate_id: str,
    payload: FavoriteUpdate,
    db: Session = Depends(get_db),
) -> PageCandidateRead:
    candidate = db.get(PageCandidate, candidate_id) or db.get(AssetCandidate, candidate_id)
    if not candidate or candidate.deleted_at is not None:
        raise HTTPException(status_code=404, detail="候选不存在")
    candidate.is_favorite = payload.is_favorite
    candidate.version += 1
    db.commit()
    db.refresh(candidate)
    return candidate_read(candidate)


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_candidate(candidate_id: str, db: Session = Depends(get_db)) -> None:
    candidate = db.get(PageCandidate, candidate_id) or db.get(AssetCandidate, candidate_id)
    if not candidate or candidate.deleted_at is not None:
        raise HTTPException(status_code=404, detail="候选不存在")
    if isinstance(candidate, PageCandidate) and candidate.is_selected:
        raise HTTPException(status_code=409, detail="当前采用版本不能删除")
    deleted_at = utcnow()
    if isinstance(candidate, AssetCandidate) and candidate.asset_id:
        # Lock/cleanup first so SQLITE_BUSY can roll back this unit and retry
        # without discarding later writes in the same request.
        detach_draft_package_references_for_asset(db, candidate.asset_id)
        asset = db.get(Asset, candidate.asset_id)
        affected_character_ids = list(
            db.scalars(
                select(CharacterReference.character_id).where(
                    CharacterReference.asset_id == candidate.asset_id
                )
            )
        )
        db.execute(
            CharacterReference.__table__.delete().where(
                CharacterReference.asset_id == candidate.asset_id
            )
        )
        if asset:
            for outfit in db.scalars(
                select(Outfit).where(Outfit.project_id == asset.project_id)
            ):
                if candidate.asset_id not in (outfit.reference_asset_ids or []):
                    continue
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
            asset.deleted_at = deleted_at
            asset.version += 1
        for character_id in affected_character_ids:
            character = db.get(Character, character_id)
            if not character:
                continue
            has_other_reference = db.scalar(
                select(CharacterReference.id)
                .join(Asset, Asset.id == CharacterReference.asset_id)
                .where(
                    CharacterReference.character_id == character_id,
                    Asset.deleted_at.is_(None),
                )
                .limit(1)
            )
            if not has_other_reference:
                character.status = AssetStatus.NEEDS_CONFIRMATION
            character.version += 1
    candidate.deleted_at = deleted_at
    candidate.version += 1
    # The worker resolves its generation target without a deleted_at filter
    # and would attach the paid result to this soft-deleted row, so an active
    # job must be cancelled here (same guard for PageCandidate and
    # AssetCandidate via their shared job_id).  Soft-delete first, then cancel:
    # cancel_job commits, persisting the soft-delete above and the CANCELLED
    # stamp as one final committed state.  The trailing commit is a no-op on
    # that path and the real commit on the cancel_run path, which returns
    # without committing.
    job = db.get(GenerationJob, candidate.job_id) if candidate.job_id else None
    if job is not None and job.status not in {
        JobStatus.COMPLETED,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    }:
        cancel_job(db, job)
    db.commit()


@router.post("/pages/{page_id}/select-candidate", response_model=PageRead)
def select_candidate(
    page_id: str,
    payload: SelectCandidateRequest,
    db: Session = Depends(get_db),
) -> MangaPage:
    page = _page(db, page_id)
    # Agreed deletion-vs-selection convention (mirrors delete_asset): take the
    # page lock before reading the candidate so a concurrent soft-delete on the
    # same page serializes against this selection.
    page = lock_entity(db, MangaPage, page.id)
    candidate = db.get(PageCandidate, payload.candidate_id)
    if (
        not candidate
        or candidate.page_id != page.id
        or candidate.deleted_at is not None
        or not candidate.asset_id
        or candidate.status not in {"READY", "INSPECTED", "NEEDS_REVIEW"}
    ):
        raise HTTPException(status_code=409, detail="该候选尚不能采用")
    asset = db.get(Asset, candidate.asset_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status_code=409, detail="该候选的图片素材已删除，请重新生成")
    inspections = list(
        db.scalars(
            select(InspectionResult)
            .where(InspectionResult.candidate_id == candidate.id)
            .order_by(InspectionResult.created_at.desc())
        )
    )
    latest_by_category: dict[str, InspectionResult] = {}
    for inspection in inspections:
        latest_by_category.setdefault(inspection.category.upper(), inspection)
    blockers: list[dict] = []
    if not payload.manual_text_confirmed:
        blockers.append(
            {
                "code": "TEXT_REVIEW_REQUIRED",
                "message": "请先人工校对页面中文并确认采用",
                "recommended_action": "MANUAL_TEXT_REVIEW",
            }
        )
    version_state, reasons = candidate_version_state(candidate, page)
    if version_state != "CURRENT" and not payload.accept_stale:
        blockers.append(
            {
                "code": "STALE_CANDIDATE_CONFIRMATION_REQUIRED",
                "message": "该候选不是基于当前分镜生成，请明确选择继续使用旧候选",
                "version_state": version_state,
                "reasons": reasons,
                "recommended_action": "KEEP_STALE_CANDIDATE",
            }
        )
    for category in ("CHARACTER", "OUTFIT", "CONTINUITY"):
        inspection = latest_by_category.get(category)
        if not inspection:
            # The default DAG performs visual QA after the human adoption gate.
            # Missing automatic checks therefore cannot block the first adoption.
            continue
        outcome = inspection.outcome.upper()
        severity = inspection.severity.upper()
        if outcome not in {"MATCH", "PASS", "ACCEPTABLE"} and severity in {
            "HIGH",
            "ERROR",
            "CRITICAL",
        }:
            blockers.append(
                {
                    "code": f"SEVERE_{category}_ISSUE",
                    "message": f"{category} 存在严重问题，不能采用",
                    "inspection_result_id": inspection.id,
                    "recommended_action": "REPAIR_OR_MANUAL_REVIEW",
                }
            )
    if blockers:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CANDIDATE_NOT_APPROVABLE",
                "message": "候选尚未达到采用标准",
                "blockers": blockers,
            },
        )
    db.execute(
        update(PageCandidate).where(PageCandidate.page_id == page.id).values(is_selected=False)
    )
    candidate.is_selected = True
    candidate.version += 1
    changed = page.selected_candidate_id and page.selected_candidate_id != candidate.id
    page.selected_candidate_id = candidate.id
    page.selected_candidate_ack_version = page.storyboard_version
    # An inspection result is only valid for the storyboard version it was run
    # against.  Explicitly accepting a stale/legacy candidate acknowledges the
    # selection, but still requires a fresh inspection before production.
    # A page flagged NEEDS_REVIEW (scene asset/scene/beat change) or
    # NEEDS_RECHECK (an upstream page re-selected) must also re-run
    # inspection: this shortcut previously cleared either flag and pushed the
    # page straight to FINAL_READY/PASSED, exporting against stale inputs.
    if (
        candidate.status == "INSPECTED"
        and version_state == "CURRENT"
        and page.continuity_status not in {"NEEDS_REVIEW", "NEEDS_RECHECK"}
    ):
        page.status = PageStatus.FINAL_READY
        page.continuity_status = "PASSED"
    elif candidate.status == "NEEDS_REVIEW":
        page.status = PageStatus.NEEDS_REPAIR
        page.continuity_status = "NEEDS_REVIEW"
    elif page.continuity_status in {"NEEDS_REVIEW", "NEEDS_RECHECK"}:
        # Keep the review flag so production readiness stays blocked until a
        # fresh inspection runs against the changed inputs.
        page.status = PageStatus.FINAL_CHECKING
        page.continuity_status = page.continuity_status
    else:
        page.status = PageStatus.FINAL_CHECKING
        page.continuity_status = "NOT_CHECKED"
    page.version += 1
    if changed:
        db.execute(
            update(MangaPage)
            .where(
                MangaPage.chapter_id == page.chapter_id,
                MangaPage.page_number > page.page_number,
                MangaPage.selected_candidate_id.is_not(None),
            )
            .values(continuity_status="NEEDS_RECHECK")
        )
    db.commit()
    db.refresh(page)
    return page


@router.post("/pages/{page_id}/selected-candidate/keep", response_model=PageRead)
def keep_selected_candidate(
    page_id: str,
    payload: KeepSelectedCandidateRequest,
    db: Session = Depends(get_db),
) -> MangaPage:
    page = _page(db, page_id)
    if page.storyboard_version != payload.storyboard_version:
        raise HTTPException(status_code=409, detail="分镜已再次更新，请刷新后重试")
    candidate = db.get(PageCandidate, payload.candidate_id)
    if (
        not candidate
        or candidate.page_id != page.id
        or page.selected_candidate_id != candidate.id
        or not candidate.is_selected
    ):
        raise HTTPException(status_code=409, detail="只能继续使用当前已采用的候选")
    if not payload.manual_text_confirmed:
        raise HTTPException(status_code=409, detail="请先人工校对页面文字")
    # The candidate's version is part of the inspection idempotency key.  A
    # storyboard edit makes the previous inspection obsolete, so bumping this
    # version ensures the next inspection is submitted as a new job rather than
    # reusing the already-completed job for the old storyboard.
    candidate.version += 1
    page.selected_candidate_ack_version = page.storyboard_version
    page.status = PageStatus.FINAL_CHECKING
    page.continuity_status = "NOT_CHECKED"
    page.version += 1
    db.commit()
    db.refresh(page)
    return page


@router.delete("/pages/{page_id}/selected-candidate", response_model=PageRead)
def retract_selected_candidate(
    page_id: str,
    db: Session = Depends(get_db),
) -> MangaPage:
    page = _page(db, page_id)
    if not page.selected_candidate_id:
        raise HTTPException(status_code=409, detail="当前页面没有已采用候选")

    candidate = db.get(PageCandidate, page.selected_candidate_id)
    if candidate and candidate.page_id == page.id:
        candidate.is_selected = False
        candidate.version += 1

    page.selected_candidate_id = None
    page.selected_candidate_ack_version = None
    page.status = PageStatus.REVIEW_REQUIRED
    page.version += 1
    db.execute(
        update(MangaPage)
        .where(
            MangaPage.chapter_id == page.chapter_id,
            MangaPage.page_number > page.page_number,
            MangaPage.selected_candidate_id.is_not(None),
        )
        .values(continuity_status="NEEDS_RECHECK")
    )
    db.commit()
    db.refresh(page)
    return page


@router.post("/pages/{page_id}/next", response_model=PageRead)
def next_page(page_id: str, db: Session = Depends(get_db)) -> MangaPage:
    page = _page(db, page_id)
    production = build_page_production_readiness(db, page)
    if not production.ready:
        raise HTTPException(status_code=409, detail=production_error_detail(production))
    db.execute(
        update(GenerationBatch)
        .where(
            GenerationBatch.page_id == page.id,
            GenerationBatch.status == "OPEN",
        )
        .values(status="CLOSED", closed_at=utcnow())
    )
    following = db.scalar(
        select(MangaPage).where(
            MangaPage.chapter_id == page.chapter_id,
            MangaPage.page_number == page.page_number + 1,
        )
    )
    db.commit()
    if not following:
        raise HTTPException(status_code=409, detail="当前页已经是本章最后一页")
    return following

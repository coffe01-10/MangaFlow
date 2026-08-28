import base64
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read, candidate_version_state
from app.config import get_settings
from app.database import get_db
from app.domain.states import CharacterPresence, PageStatus, Resolution, ensure_unlocked
from app.models import (
    Asset,
    AssetCandidate,
    AssetStatus,
    Chapter,
    Character,
    CharacterReference,
    Dialogue,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    JobDependency,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    RepairPlan,
    WorkflowNodeRun,
    utcnow,
)
from app.schemas import (
    CandidateCreate,
    CandidateQueuedRead,
    ChapterProductionReadinessRead,
    DialogueCreate,
    DialogueDelete,
    DialogueRead,
    DialogueUpdate,
    FavoriteUpdate,
    GenerationBatchRead,
    GenerationWorkbenchRead,
    InspectionRead,
    InspectionRequest,
    JobArchiveResult,
    JobBulkArchiveRequest,
    JobRead,
    JobResultRead,
    KeepSelectedCandidateRequest,
    LibraryBatchRead,
    LibraryRead,
    PageCandidateRead,
    PageLayoutUpdate,
    PageProductionReadinessRead,
    PageRead,
    PageReadinessRead,
    PanelRead,
    PanelUpdate,
    RepairRequest,
    SelectCandidateRequest,
    StoryboardRead,
    UpscaleRequest,
)
from app.services.content_workflow import update_page_layout
from app.services.editor import (
    mark_pages_for_review,
    mark_storyboard_changed,
    project_id_for_page,
    refresh_page_text_metrics,
    validate_character_ids,
)
from app.services.job_service import cancel_job, create_job, enqueue_job, reset_for_retry
from app.services.model_router import model_supports_resolution, resolve_model
from app.services.ordinal_allocator import (
    BatchOrdinalConflictError,
    CandidateOrdinalConflictError,
    commit_ordinal_transaction,
    create_generation_batch,
    create_page_candidate,
)
from app.services.page_completion import (
    build_chapter_production_readiness,
    build_page_production_readiness,
    production_error_detail,
)
from app.services.page_readiness import (
    build_page_readiness,
    ensure_page_ready,
)

router = APIRouter()

TERMINAL_JOB_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}
DELETABLE_JOB_STATUSES = {"FAILED", "CANCELLED"}


def _job_reads(db: Session, jobs: list[GenerationJob]) -> list[JobRead]:
    job_ids = [job.id for job in jobs]
    target_ids = [job.target_id for job in jobs]
    records = (
        list(
            db.scalars(
                select(GenerationRecord).where(GenerationRecord.job_id.in_(job_ids))
            )
        )
        if job_ids
        else []
    )
    usage_by_job = {record.job_id: record.usage for record in records}
    page_candidates = (
        list(
            db.scalars(
                select(PageCandidate).where(
                    or_(
                        PageCandidate.job_id.in_(job_ids),
                        PageCandidate.id.in_(target_ids),
                    )
                )
            )
        )
        if job_ids
        else []
    )
    asset_candidates = (
        list(
            db.scalars(
                select(AssetCandidate).where(
                    or_(
                        AssetCandidate.job_id.in_(job_ids),
                        AssetCandidate.id.in_(target_ids),
                    )
                )
            )
        )
        if job_ids
        else []
    )
    page_by_job = {item.job_id: item for item in page_candidates if item.job_id}
    page_by_id = {item.id: item for item in page_candidates}
    asset_by_job = {item.job_id: item for item in asset_candidates if item.job_id}
    asset_by_id = {item.id: item for item in asset_candidates}

    def job_result(job: GenerationJob) -> JobResultRead | None:
        page_candidate = page_by_job.get(job.id) or page_by_id.get(job.target_id)
        if page_candidate and page_candidate.asset_id:
            value = candidate_read(page_candidate)
            return JobResultRead(
                kind="IMAGE",
                label=f"页面候选 {page_candidate.ordinal} · {page_candidate.resolution.value}",
                candidate_id=page_candidate.id,
                page_id=page_candidate.page_id,
                content_url=value.content_url,
                thumbnail_url=value.thumbnail_url,
            )
        asset_candidate = asset_by_job.get(job.id) or asset_by_id.get(job.target_id)
        if asset_candidate and asset_candidate.asset_id:
            value = asset_candidate_read(asset_candidate)
            return JobResultRead(
                kind="IMAGE",
                label=f"素材候选 {asset_candidate.variant} · {asset_candidate.resolution.value}",
                candidate_id=asset_candidate.id,
                content_url=value.content_url,
                thumbnail_url=value.thumbnail_url,
            )
        return None

    return [
        JobRead.model_validate(job).model_copy(
            update={
                "usage_summary": usage_by_job.get(job.id, {}),
                "estimated_cost": None,
                "result": job_result(job),
            }
        )
        for job in jobs
    ]


def _page(db: Session, page_id: str) -> MangaPage:
    page = db.get(MangaPage, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    return page


def _project_for_page(db: Session, page: MangaPage) -> Project:
    chapter = db.get(Chapter, page.chapter_id)
    return db.get(Project, chapter.project_id)


def _new_batch(
    db: Session,
    page: MangaPage,
    *,
    generation_kind: str = "PAGE",
) -> GenerationBatch:
    project = _project_for_page(db, page)
    try:
        batch = create_generation_batch(
            db,
            project_id=project.id,
            chapter_id=page.chapter_id,
            page_id=page.id,
            generation_kind=generation_kind,
            close_open_page_batches=True,
        )
        commit_ordinal_transaction(db, BatchOrdinalConflictError)
        db.refresh(batch)
        return batch
    except BatchOrdinalConflictError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/chapters/{chapter_id}/pages", response_model=list[PageRead])
def list_pages(chapter_id: str, db: Session = Depends(get_db)) -> list[MangaPage]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter_id)
            .order_by(MangaPage.page_number, MangaPage.revision_no)
        )
    )


@router.get("/pages/{page_id}", response_model=PageRead)
def get_page(page_id: str, db: Session = Depends(get_db)) -> MangaPage:
    return _page(db, page_id)


@router.get("/pages/{page_id}/readiness", response_model=PageReadinessRead)
def get_page_readiness(
    page_id: str,
    db: Session = Depends(get_db),
) -> PageReadinessRead:
    return build_page_readiness(db, _page(db, page_id), get_settings())


@router.get(
    "/chapters/{chapter_id}/production-readiness",
    response_model=ChapterProductionReadinessRead,
)
def get_chapter_production_readiness(
    chapter_id: str,
    db: Session = Depends(get_db),
) -> ChapterProductionReadinessRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return build_chapter_production_readiness(db, chapter)


@router.get(
    "/pages/{page_id}/production-readiness",
    response_model=PageProductionReadinessRead,
)
def get_page_production_readiness(
    page_id: str,
    db: Session = Depends(get_db),
) -> PageProductionReadinessRead:
    return build_page_production_readiness(db, _page(db, page_id))


@router.get("/pages/{page_id}/generation-workbench", response_model=GenerationWorkbenchRead)
def get_generation_workbench(
    page_id: str,
    db: Session = Depends(get_db),
) -> GenerationWorkbenchRead:
    page = _page(db, page_id)
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    batch = db.scalar(
        select(GenerationBatch)
        .where(GenerationBatch.page_id == page.id)
        .order_by(
            (GenerationBatch.status == "OPEN").desc(),
            GenerationBatch.ordinal.desc(),
        )
        .limit(1)
    )
    candidates = (
        list(
            db.scalars(
                select(PageCandidate)
                .where(
                    PageCandidate.batch_id == batch.id,
                    PageCandidate.deleted_at.is_(None),
                )
                .order_by(PageCandidate.ordinal.desc())
            )
        )
        if batch
        else []
    )
    selected = (
        db.get(PageCandidate, page.selected_candidate_id) if page.selected_candidate_id else None
    )
    selected_read = candidate_read(selected, page) if selected else None
    return GenerationWorkbenchRead(
        page=PageRead.model_validate(page),
        storyboard=StoryboardRead(
            page=PageRead.model_validate(page),
            panels=[_panel_read(db, panel) for panel in panels],
            candidate_count=_page_candidate_count(db, page.id),
        ),
        readiness=build_page_readiness(db, page, get_settings()),
        production=build_page_production_readiness(db, page),
        current_batch=GenerationBatchRead.model_validate(batch) if batch else None,
        candidates=[candidate_read(item, page) for item in candidates],
        selected_candidate=selected_read,
        selected_candidate_state=selected_read.version_state if selected_read else "NONE",
    )


def _panel_read(db: Session, panel: Panel) -> PanelRead:
    panel.dialogues = list(
        db.scalars(
            select(Dialogue).where(Dialogue.panel_id == panel.id).order_by(Dialogue.reading_order)
        )
    )
    return PanelRead.model_validate(panel)


def _panel_context(db: Session, panel_id: str) -> tuple[Panel, MangaPage, str]:
    panel = db.get(Panel, panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail="分镜格不存在")
    page = _page(db, panel.page_id)
    return panel, page, project_id_for_page(db, page)


def _page_candidate_count(db: Session, page_id: str) -> int:
    return (
        db.scalar(
            select(func.count(PageCandidate.id)).where(
                PageCandidate.page_id == page_id,
                PageCandidate.deleted_at.is_(None),
            )
        )
        or 0
    )


def _validate_dialogue_speaker(
    db: Session,
    project_id: str,
    speaker_character_id: str | None,
) -> str | None:
    if not speaker_character_id:
        return None
    return validate_character_ids(db, project_id, [speaker_character_id])[0]


@router.get("/pages/{page_id}/storyboard", response_model=StoryboardRead)
def get_storyboard(page_id: str, db: Session = Depends(get_db)) -> StoryboardRead:
    page = _page(db, page_id)
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    return StoryboardRead(
        page=PageRead.model_validate(page),
        panels=[_panel_read(db, panel) for panel in panels],
        candidate_count=_page_candidate_count(db, page.id),
    )


@router.patch("/pages/{page_id}/layout", response_model=StoryboardRead)
def patch_page_layout(
    page_id: str,
    payload: PageLayoutUpdate,
    db: Session = Depends(get_db),
) -> StoryboardRead:
    page = update_page_layout(
        db,
        _page(db, page_id),
        panel_count=payload.panel_count,
        layout_mode=payload.layout_mode,
    )
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    return StoryboardRead(
        page=PageRead.model_validate(page),
        panels=[_panel_read(db, panel) for panel in panels],
        candidate_count=_page_candidate_count(db, page.id),
    )


@router.patch("/panels/{panel_id}", response_model=PanelRead)
def update_panel(
    panel_id: str,
    payload: PanelUpdate,
    db: Session = Depends(get_db),
) -> PanelRead:
    panel, page, project_id = _panel_context(db, panel_id)
    if panel.version != payload.version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    try:
        ensure_unlocked(panel.locked_fields, list(values))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
    db.commit()
    db.refresh(panel)
    return _panel_read(db, panel)


@router.post(
    "/panels/{panel_id}/dialogues",
    response_model=DialogueRead,
    status_code=status.HTTP_201_CREATED,
)
def create_dialogue(
    panel_id: str,
    payload: DialogueCreate,
    db: Session = Depends(get_db),
) -> Dialogue:
    panel, page, project_id = _panel_context(db, panel_id)
    if panel.version != payload.panel_version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    if not payload.target_text.strip():
        raise HTTPException(status_code=422, detail="气泡文字不能为空")
    reading_order = (
        db.scalar(select(func.max(Dialogue.reading_order)).where(Dialogue.panel_id == panel.id))
        or 0
    ) + 1
    dialogue = Dialogue(
        panel_id=panel.id,
        speaker_character_id=_validate_dialogue_speaker(
            db, project_id, payload.speaker_character_id
        ),
        target_text=payload.target_text.strip(),
        reading_order=reading_order,
        text_direction=payload.text_direction,
        region=payload.region,
        rewrite_forbidden=payload.rewrite_forbidden,
    )
    db.add(dialogue)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()
    db.refresh(dialogue)
    return dialogue


@router.patch("/dialogues/{dialogue_id}", response_model=DialogueRead)
def update_dialogue(
    dialogue_id: str,
    payload: DialogueUpdate,
    db: Session = Depends(get_db),
) -> Dialogue:
    dialogue = db.get(Dialogue, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="对白不存在")
    panel, page, project_id = _panel_context(db, dialogue.panel_id)
    if panel.version != payload.panel_version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"panel_version"})
    if "target_text" in values and not (values["target_text"] or "").strip():
        raise HTTPException(status_code=422, detail="气泡文字不能为空")
    if "speaker_character_id" in values:
        values["speaker_character_id"] = _validate_dialogue_speaker(
            db, project_id, values["speaker_character_id"]
        )
    for key, value in values.items():
        setattr(dialogue, key, value.strip() if isinstance(value, str) else value)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()
    db.refresh(dialogue)
    return dialogue


@router.delete("/dialogues/{dialogue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dialogue(
    dialogue_id: str,
    payload: DialogueDelete,
    db: Session = Depends(get_db),
) -> None:
    dialogue = db.get(Dialogue, dialogue_id)
    if not dialogue:
        raise HTTPException(status_code=404, detail="对白不存在")
    panel, page, _ = _panel_context(db, dialogue.panel_id)
    if panel.version != payload.panel_version:
        raise HTTPException(status_code=409, detail="分镜格已被更新，请刷新后重试")
    db.delete(dialogue)
    db.flush()
    refresh_page_text_metrics(db, page)
    panel.version += 1
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()


@router.post(
    "/pages/{page_id}/batches",
    response_model=GenerationBatchRead,
    status_code=status.HTTP_201_CREATED,
)
def start_batch(page_id: str, db: Session = Depends(get_db)) -> GenerationBatch:
    page = _page(db, page_id)
    ensure_page_ready(db, page, get_settings())
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
    db.commit()


@router.post("/pages/{page_id}/select-candidate", response_model=PageRead)
def select_candidate(
    page_id: str,
    payload: SelectCandidateRequest,
    db: Session = Depends(get_db),
) -> MangaPage:
    page = _page(db, page_id)
    candidate = db.get(PageCandidate, payload.candidate_id)
    if (
        not candidate
        or candidate.page_id != page.id
        or candidate.deleted_at is not None
        or not candidate.asset_id
        or candidate.status not in {"READY", "INSPECTED", "NEEDS_REVIEW"}
    ):
        raise HTTPException(status_code=409, detail="该候选尚不能采用")
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
    if candidate.status == "INSPECTED" and version_state == "CURRENT":
        page.status = PageStatus.FINAL_READY
        page.continuity_status = "PASSED"
    elif candidate.status == "NEEDS_REVIEW":
        page.status = PageStatus.NEEDS_REPAIR
        page.continuity_status = "NEEDS_REVIEW"
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
    page.continuity_status = "NEEDS_REVIEW"
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


@router.get("/projects/{project_id}/library", response_model=LibraryRead)
def library(
    project_id: str,
    group_by: str = Query(default="batch", pattern="^batch$"),
    chapter_id: str | None = None,
    model_alias: str | None = None,
    favorite: bool | None = None,
    generation_kind: str | None = None,
    character_id: str | None = None,
    resolution: Resolution | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LibraryRead:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    batch_query = select(GenerationBatch).where(GenerationBatch.project_id == project_id)
    if chapter_id:
        chapter = db.get(Chapter, chapter_id)
        if (
            not chapter
            or chapter.project_id != project_id
            or chapter.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="筛选章节不存在或不属于当前项目")
        batch_query = batch_query.where(GenerationBatch.chapter_id == chapter_id)
    if generation_kind:
        batch_query = batch_query.where(GenerationBatch.generation_kind == generation_kind.upper())
    if date_from:
        batch_query = batch_query.where(GenerationBatch.created_at >= date_from)
    if date_to:
        batch_query = batch_query.where(GenerationBatch.created_at <= date_to)
    if character_id:
        character = db.get(Character, character_id)
        if not character or character.project_id != project_id:
            raise HTTPException(status_code=404, detail="筛选角色不存在或不属于当前项目")
        outfit_ids = set(db.scalars(select(Outfit.id).where(Outfit.character_id == character_id)))
        page_ids = {
            panel.page_id
            for panel in db.scalars(
                select(Panel)
                .join(MangaPage, MangaPage.id == Panel.page_id)
                .join(Chapter, Chapter.id == MangaPage.chapter_id)
                .where(Chapter.project_id == project_id)
            )
            if character_id in panel.characters
        }
        character_filters = [
            and_(
                GenerationBatch.target_type == "CHARACTER",
                GenerationBatch.target_id == character_id,
            )
        ]
        if outfit_ids:
            character_filters.append(
                and_(
                    GenerationBatch.target_type == "OUTFIT",
                    GenerationBatch.target_id.in_(outfit_ids),
                )
            )
        if page_ids:
            character_filters.append(GenerationBatch.page_id.in_(page_ids))
        batch_query = batch_query.where(or_(*character_filters))

    page_filters = [
        PageCandidate.batch_id == GenerationBatch.id,
        PageCandidate.deleted_at.is_(None),
        PageCandidate.status.not_in({"FAILED", "CANCELLED"}),
    ]
    asset_filters = [
        AssetCandidate.batch_id == GenerationBatch.id,
        AssetCandidate.deleted_at.is_(None),
        AssetCandidate.status.not_in({"FAILED", "CANCELLED"}),
    ]
    if model_alias:
        page_filters.append(PageCandidate.model_alias == model_alias)
        asset_filters.append(AssetCandidate.model_alias == model_alias)
    if favorite is not None:
        page_filters.append(PageCandidate.is_favorite == favorite)
        asset_filters.append(AssetCandidate.is_favorite == favorite)
    if resolution:
        page_filters.append(PageCandidate.resolution == resolution)
        asset_filters.append(AssetCandidate.resolution == resolution)
    batch_query = batch_query.where(
        or_(
            exists(select(1).where(*page_filters)),
            exists(select(1).where(*asset_filters)),
        )
    )

    if cursor:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            cursor_time = datetime.fromisoformat(payload["created_at"])
            cursor_id = payload["id"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=422, detail="素材库游标无效") from error
        batch_query = batch_query.where(
            or_(
                GenerationBatch.created_at < cursor_time,
                and_(
                    GenerationBatch.created_at == cursor_time,
                    GenerationBatch.id < cursor_id,
                ),
            )
        )

    batches = list(
        db.scalars(
            batch_query.order_by(
                GenerationBatch.created_at.desc(), GenerationBatch.id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(batches) > limit
    batches = batches[:limit]
    next_cursor = None
    if has_more and batches:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "created_at": batches[-1].created_at.isoformat(),
                    "id": batches[-1].id,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")

    batch_ids = [batch.id for batch in batches]
    candidates_by_batch: dict[str, list[PageCandidateRead]] = {
        batch_id: [] for batch_id in batch_ids
    }
    if batch_ids:
        page_query = select(PageCandidate).where(
            PageCandidate.batch_id.in_(batch_ids),
            PageCandidate.deleted_at.is_(None),
            PageCandidate.status.not_in({"FAILED", "CANCELLED"}),
        )
        asset_query = select(AssetCandidate).where(
            AssetCandidate.batch_id.in_(batch_ids),
            AssetCandidate.deleted_at.is_(None),
            AssetCandidate.status.not_in({"FAILED", "CANCELLED"}),
        )
        if model_alias:
            page_query = page_query.where(PageCandidate.model_alias == model_alias)
            asset_query = asset_query.where(AssetCandidate.model_alias == model_alias)
        if favorite is not None:
            page_query = page_query.where(PageCandidate.is_favorite == favorite)
            asset_query = asset_query.where(AssetCandidate.is_favorite == favorite)
        if resolution:
            page_query = page_query.where(PageCandidate.resolution == resolution)
            asset_query = asset_query.where(AssetCandidate.resolution == resolution)
        for item in db.scalars(
            page_query.order_by(PageCandidate.batch_id, PageCandidate.ordinal.desc())
        ):
            candidates_by_batch[item.batch_id].append(candidate_read(item))
        for item in db.scalars(
            asset_query.order_by(AssetCandidate.batch_id, AssetCandidate.ordinal.desc())
        ):
            candidates_by_batch[item.batch_id].append(asset_candidate_read(item))

    groups = [
        LibraryBatchRead(
            batch=GenerationBatchRead.model_validate(batch),
            candidates=candidates_by_batch[batch.id],
        )
        for batch in batches
    ]
    all_candidates = [candidate for group in groups for candidate in group.candidates]
    return LibraryRead(
        groups=groups,
        total_candidates=len(all_candidates),
        favorite_count=sum(item.is_favorite for item in all_candidates),
        next_cursor=next_cursor,
        limit=limit,
    )


@router.get("/projects/{project_id}/jobs", response_model=list[JobRead])
def list_jobs(
    project_id: str,
    archived: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[JobRead]:
    jobs = list(
        db.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.archived_at.is_not(None)
                if archived
                else GenerationJob.archived_at.is_(None),
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(100)
        )
    )
    return _job_reads(db, jobs)


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobRead:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _job_reads(db, [job])[0]


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return cancel_job(db, job)


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.attempt_count >= job.max_attempts:
        raise HTTPException(status_code=409, detail="任务已达到最大重试次数")
    return reset_for_retry(db, job)


@router.post("/jobs/{job_id}/archive", response_model=JobRead)
def archive_job(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status.value not in TERMINAL_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="运行中的任务不能归档，请先取消")
    if job.archived_at is None:
        job.archived_at = utcnow()
        job.version += 1
        db.commit()
        db.refresh(job)
    return job


@router.post("/jobs/{job_id}/restore", response_model=JobRead)
def restore_job(job_id: str, db: Session = Depends(get_db)) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.archived_at is not None:
        job.archived_at = None
        job.version += 1
        db.commit()
        db.refresh(job)
    return job


@router.post(
    "/projects/{project_id}/jobs/archive-completed",
    response_model=JobArchiveResult,
)
def archive_completed_jobs(project_id: str, db: Session = Depends(get_db)) -> JobArchiveResult:
    jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.archived_at.is_(None),
                GenerationJob.status.in_(TERMINAL_JOB_STATUSES),
            )
        )
    )
    archived_at = utcnow()
    for job in jobs:
        job.archived_at = archived_at
        job.version += 1
    db.commit()
    return JobArchiveResult(archived_count=len(jobs))


@router.post(
    "/projects/{project_id}/jobs/bulk-archive",
    response_model=JobArchiveResult,
)
def bulk_archive_jobs(
    project_id: str,
    payload: JobBulkArchiveRequest,
    db: Session = Depends(get_db),
) -> JobArchiveResult:
    jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.id.in_(payload.job_ids),
                GenerationJob.project_id == project_id,
            )
        )
    )
    if len(jobs) != len(set(payload.job_ids)):
        raise HTTPException(status_code=404, detail="部分任务不存在或不属于当前项目")
    non_terminal = [job.id for job in jobs if job.status.value not in TERMINAL_JOB_STATUSES]
    if non_terminal:
        raise HTTPException(status_code=409, detail="运行中的任务不能批量归档")
    archived_at = utcnow()
    archived_count = 0
    for job in jobs:
        if job.archived_at is not None:
            continue
        job.archived_at = archived_at
        job.version += 1
        archived_count += 1
    db.commit()
    return JobArchiveResult(archived_count=archived_count)


def _job_has_references(db: Session, job_id: str) -> bool:
    checks = (
        select(GenerationRecord.id).where(GenerationRecord.job_id == job_id),
        select(PageCandidate.id).where(PageCandidate.job_id == job_id),
        select(AssetCandidate.id).where(AssetCandidate.job_id == job_id),
        select(WorkflowNodeRun.id).where(WorkflowNodeRun.job_id == job_id),
        select(JobDependency.id).where(
            or_(
                JobDependency.job_id == job_id,
                JobDependency.depends_on_job_id == job_id,
            )
        ),
    )
    return any(db.scalar(query.limit(1)) is not None for query in checks)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = db.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status.value not in DELETABLE_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="只有失败或已取消任务可以彻底删除")
    if _job_has_references(db, job.id):
        raise HTTPException(status_code=409, detail="任务仍被候选、生成记录或工作流引用，只能归档")
    db.delete(job)
    db.commit()


@router.post(
    "/candidates/{candidate_id}/inspect",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def inspect_candidate(
    candidate_id: str,
    payload: InspectionRequest,
    db: Session = Depends(get_db),
) -> GenerationJob:
    candidate = db.get(PageCandidate, candidate_id)
    if not candidate or not candidate.asset_id:
        raise HTTPException(status_code=409, detail="候选图片尚未生成")
    page = _page(db, candidate.page_id)
    project = _project_for_page(db, page)
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        model_alias="text.fast",
        request_parameters={"categories": payload.categories},
        reference_asset_ids=[candidate.asset_id],
        idempotency_key=f"inspect:{candidate.id}:{candidate.version}",
    )
    return enqueue_job(db, job)


@router.get("/candidates/{candidate_id}/inspections", response_model=list[InspectionRead])
def list_inspections(candidate_id: str, db: Session = Depends(get_db)) -> list[InspectionResult]:
    return list(
        db.scalars(
            select(InspectionResult)
            .where(InspectionResult.candidate_id == candidate_id)
            .order_by(InspectionResult.created_at.desc())
        )
    )


@router.post(
    "/candidates/{candidate_id}/repairs",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def repair_candidate(
    candidate_id: str,
    payload: RepairRequest,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    original = db.get(PageCandidate, candidate_id)
    inspection = db.get(InspectionResult, payload.inspection_result_id)
    if not original or not original.asset_id:
        raise HTTPException(status_code=409, detail="原始候选图片不存在")
    if not inspection or inspection.candidate_id != original.id:
        raise HTTPException(status_code=409, detail="检查结果与候选不匹配")
    inspection_ids = select(InspectionResult.id).where(InspectionResult.candidate_id == original.id)
    previous_repairs = list(
        db.scalars(select(RepairPlan).where(RepairPlan.inspection_result_id.in_(inspection_ids)))
    )
    attempts = max((item.automatic_attempts for item in previous_repairs), default=0)
    if attempts >= get_settings().max_auto_repairs:
        raise HTTPException(status_code=409, detail="已达到最大自动修复次数，请人工处理")
    if inspection.category.upper() in {"TEXT", "OCR"}:
        raise HTTPException(
            status_code=409, detail="历史文字检查仅供查看，文字问题不再创建修复任务"
        )
    repair_rank = {"BUBBLE_REGION": 0, "PANEL": 1, "PAGE": 2}
    previous_rank = max(
        (repair_rank[item.repair_type] for item in previous_repairs),
        default=-1,
    )
    if repair_rank[payload.repair_type] < previous_rank:
        raise HTTPException(status_code=409, detail="修复范围只能保持或逐步扩大，不能退回更小范围")
    page = _page(db, original.page_id)
    try:
        ensure_unlocked(page.locked_fields, payload.target_fields)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    batch = _new_batch(db, page, generation_kind="REPAIR")
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"storyboard_version": page.storyboard_version},
    )
    db.add(candidate)
    repair = RepairPlan(
        inspection_result_id=inspection.id,
        repair_type=payload.repair_type,
        target_regions=payload.target_regions or inspection.regions,
        target_fields=payload.target_fields,
        lock_conflicts=[],
        automatic_attempts=attempts + 1,
        max_automatic_attempts=get_settings().max_auto_repairs,
    )
    db.add(repair)
    db.flush()
    project = _project_for_page(db, page)
    resolved_model = resolve_model(
        db,
        get_settings(),
        operation="image_edit",
        explicit_reference=payload.model_alias,
        project_id=project.id,
        task_kind="PAGE_REPAIR",
    )
    if not model_supports_resolution(resolved_model.model, payload.resolution.value):
        raise HTTPException(status_code=422, detail="所选模型不支持该输出清晰度")
    candidate.catalog_model_id = resolved_model.model.id
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.last_image_model_id = resolved_model.model.id
    project.version += 1
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_REPAIR",
        model_alias=payload.model_alias,
        catalog_model_id=resolved_model.model.id,
        request_parameters={
            "original_candidate_id": original.id,
            "repair_plan_id": repair.id,
            "repair_type": payload.repair_type,
            "target_regions": payload.target_regions,
            "storyboard_version": page.storyboard_version,
        },
        reference_asset_ids=[original.asset_id],
        idempotency_key=f"repair:{repair.id}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate, page),
    )


@router.post(
    "/candidates/{candidate_id}/upscale",
    response_model=CandidateQueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def upscale_candidate(
    candidate_id: str,
    payload: UpscaleRequest,
    db: Session = Depends(get_db),
) -> CandidateQueuedRead:
    original = db.get(PageCandidate, candidate_id)
    if not original or not original.asset_id:
        raise HTTPException(status_code=409, detail="原始候选图片不存在")
    resolution_rank = {"1K": 1, "2K": 2, "4K": 4}
    if resolution_rank[payload.resolution.value] <= resolution_rank[original.resolution.value]:
        raise HTTPException(status_code=409, detail="升清目标必须高于当前候选清晰度")
    page = _page(db, original.page_id)
    batch = _new_batch(db, page, generation_kind="UPSCALE")
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=payload.model_alias,
        resolution=payload.resolution,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"storyboard_version": page.storyboard_version},
    )
    db.add(candidate)
    db.flush()
    project = _project_for_page(db, page)
    resolved_model = resolve_model(
        db,
        get_settings(),
        operation="image_edit",
        explicit_reference=payload.model_alias,
        project_id=project.id,
        task_kind="PAGE_UPSCALE",
    )
    if not model_supports_resolution(resolved_model.model, payload.resolution.value):
        raise HTTPException(status_code=422, detail="所选模型不支持目标升清规格")
    candidate.catalog_model_id = resolved_model.model.id
    project.last_image_model_alias = payload.model_alias
    project.image_model_alias = payload.model_alias
    project.last_image_model_id = resolved_model.model.id
    project.version += 1
    job = create_job(
        db,
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_UPSCALE",
        model_alias=payload.model_alias,
        catalog_model_id=resolved_model.model.id,
        request_parameters={
            "original_candidate_id": original.id,
            "preserve_structure": True,
            "source_resolution": original.resolution.value,
            "target_resolution": payload.resolution.value,
            "storyboard_version": page.storyboard_version,
        },
        reference_asset_ids=[original.asset_id],
        idempotency_key=f"upscale:{batch.id}:{payload.resolution.value}",
    )
    candidate.job_id = job.id
    db.commit()
    db.refresh(candidate)
    job = enqueue_job(db, job)
    return CandidateQueuedRead(
        job_id=job.id,
        job_status=job.status,
        candidate=candidate_read(candidate, page),
    )

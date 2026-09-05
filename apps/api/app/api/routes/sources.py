from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.helpers import reject_required_nulls
from app.config import get_settings
from app.database import get_db
from app.domain.states import ensure_unlocked
from app.models import (
    Beat,
    CandidateLineage,
    Chapter,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
)
from app.request_limits import SOURCE_UPLOAD_OPENAPI, ParsedUpload, parse_single_file_form
from app.schemas import (
    BeatRead,
    BeatUpdate,
    ChapterRead,
    JobRead,
    PlanRead,
    PlanRequest,
    SceneRead,
    SceneUpdate,
    ScriptRead,
    SourceImportRead,
    SourceImportRequest,
    SourceRevisionCreate,
    SourceRevisionRead,
    SourceSegmentRead,
)
from app.services.content_workflow import (
    chapter_metrics,
    import_source,
    normalize_chapter_title,
    normalize_source_text,
    plan_chapter_pages,
    revise_chapter_source,
)
from app.services.editor import canonical_speaker_name, mark_pages_for_review
from app.services.job_service import ACTIVE_JOB_STATUSES, create_job, enqueue_job
from app.services.ordinal_allocator import (
    ChapterOrdinalConflictError,
    SourceRevisionConflictError,
)

router = APIRouter()


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _chapter_read(db: Session, chapter: Chapter) -> ChapterRead:
    return ChapterRead.model_validate(chapter).model_copy(update=chapter_metrics(db, chapter))


@router.post(
    "/projects/{project_id}/sources/import",
    response_model=SourceImportRead,
    status_code=status.HTTP_201_CREATED,
)
def import_pasted_source(
    project_id: str,
    payload: SourceImportRequest,
    db: Session = Depends(get_db),
) -> SourceImportRead:
    _project(db, project_id)
    try:
        chapters = import_source(
            db,
            project_id=project_id,
            title=payload.title,
            text=payload.text,
            source_type=payload.source_type,
        )
    except ChapterOrdinalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SourceImportRead(
        chapters=[_chapter_read(db, chapter) for chapter in chapters],
        total_characters=sum(
            chapter_metrics(db, item)["source_character_count"] for item in chapters
        ),
    )


async def _parse_source_upload(request: Request) -> AsyncIterator[ParsedUpload]:
    parsed = await parse_single_file_form(request, required_fields=(), optional_fields=("title",))
    try:
        yield parsed
    finally:
        await parsed.file.close()


@router.post(
    "/projects/{project_id}/sources/upload",
    response_model=SourceImportRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=SOURCE_UPLOAD_OPENAPI,
)
def upload_source(
    project_id: str,
    parsed: ParsedUpload = Depends(_parse_source_upload),
    db: Session = Depends(get_db),
) -> SourceImportRead:
    _project(db, project_id)
    settings = get_settings()
    title = normalize_chapter_title(parsed.texts.get("title") or "正文") or "正文"
    file = parsed.file
    suffix = Path(file.filename or "source.txt").suffix.lower()
    if suffix not in {".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=415, detail="仅支持 TXT 和 Markdown 原文")
    data = file.file.read(settings.max_upload_bytes + 1)
    file.file.close()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="原文文件超过上传上限")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail="原文文件必须使用 UTF-8 编码") from error
    text = normalize_source_text(text)
    source_type = "MARKDOWN" if suffix in {".md", ".markdown"} else "TXT"
    try:
        chapters = import_source(
            db,
            project_id=project_id,
            title=title,
            text=text,
            source_type=source_type,
        )
    except ChapterOrdinalConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SourceImportRead(
        chapters=[_chapter_read(db, chapter) for chapter in chapters],
        total_characters=sum(
            chapter_metrics(db, item)["source_character_count"] for item in chapters
        ),
    )


@router.get("/projects/{project_id}/chapters", response_model=list[ChapterRead])
def list_chapters(project_id: str, db: Session = Depends(get_db)) -> list[ChapterRead]:
    _project(db, project_id)
    chapters = list(
        db.scalars(
            select(Chapter)
            .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
            .order_by(Chapter.ordinal)
        )
    )
    return [_chapter_read(db, chapter) for chapter in chapters]


@router.get("/chapters/{chapter_id}", response_model=ChapterRead)
def get_chapter(chapter_id: str, db: Session = Depends(get_db)) -> ChapterRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return _chapter_read(db, chapter)


@router.get("/chapters/{chapter_id}/segments", response_model=list[SourceSegmentRead])
def list_segments(chapter_id: str, db: Session = Depends(get_db)) -> list[SourceSegment]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None or not chapter.current_source_revision_id:
        raise HTTPException(status_code=404, detail="章节原文不存在")
    return list(
        db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == chapter.current_source_revision_id)
            .order_by(SourceSegment.ordinal)
        )
    )


@router.post(
    "/chapters/{chapter_id}/parse",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def parse_chapter(chapter_id: str, db: Session = Depends(get_db)):
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    existing_page = db.scalar(
        select(MangaPage.id).where(MangaPage.chapter_id == chapter.id).limit(1)
    )
    if existing_page:
        raise HTTPException(
            status_code=409,
            detail="本章已有分页，请先删除分页后再重新生成剧本",
        )
    job = create_job(
        db,
        project_id=chapter.project_id,
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        model_alias="auto",
        idempotency_key=f"source-parse:{chapter.id}:{chapter.version}",
    )
    return enqueue_job(db, job)


@router.post("/chapters/{chapter_id}/plan", response_model=PlanRead)
def plan_chapter(
    chapter_id: str,
    payload: PlanRequest,
    db: Session = Depends(get_db),
) -> PlanRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    pages = plan_chapter_pages(
        db,
        chapter,
        replace_existing=payload.replace_existing,
        from_page_number=payload.from_page_number,
    )
    metrics = chapter_metrics(db, chapter)
    covered = round(metrics["coverage_ratio"] * metrics["segment_count"])
    return PlanRead(
        chapter_id=chapter.id,
        page_count=len(pages),
        source_segment_count=metrics["segment_count"],
        covered_segment_count=covered,
        coverage_ratio=metrics["coverage_ratio"],
        pages=pages,
    )


@router.get("/chapters/{chapter_id}/revisions", response_model=list[SourceRevisionRead])
def list_revisions(chapter_id: str, db: Session = Depends(get_db)) -> list[SourceRevision]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return list(
        db.scalars(
            select(SourceRevision)
            .where(SourceRevision.chapter_id == chapter_id)
            .order_by(SourceRevision.revision.desc())
        )
    )


@router.post(
    "/chapters/{chapter_id}/revisions",
    response_model=SourceRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
def revise_source(
    chapter_id: str, payload: SourceRevisionCreate, db: Session = Depends(get_db)
) -> SourceRevision:
    try:
        return revise_chapter_source(
            db,
            chapter_id=chapter_id,
            title=payload.title,
            text=payload.text,
            source_type=payload.source_type,
        )
    except SourceRevisionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chapter(chapter_id: str, db: Session = Depends(get_db)) -> None:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    chapter.deleted_at = datetime.now(UTC)
    chapter.version += 1
    db.commit()


@router.post("/chapters/{chapter_id}/restore", response_model=ChapterRead)
def restore_chapter(chapter_id: str, db: Session = Depends(get_db)) -> ChapterRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    chapter.deleted_at = None
    chapter.version += 1
    db.commit()
    db.refresh(chapter)
    return _chapter_read(db, chapter)


@router.get("/chapters/{chapter_id}/script", response_model=ScriptRead)
def get_script(chapter_id: str, db: Session = Depends(get_db)) -> ScriptRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.ordinal))
    )
    for scene in scenes:
        scene.beats = list(
            db.scalars(select(Beat).where(Beat.scene_id == scene.id).order_by(Beat.ordinal))
        )
    revision = db.scalar(
        select(ScriptRevision)
        .where(ScriptRevision.chapter_id == chapter_id)
        .order_by(ScriptRevision.revision_no.desc())
    )
    return ScriptRead(
        chapter_id=chapter_id,
        status=revision.status if revision else "NOT_CREATED",
        revision_no=revision.revision_no if revision else None,
        coverage=revision.coverage if revision else {},
        scenes=[SceneRead.model_validate(scene) for scene in scenes],
    )


@router.delete("/chapters/{chapter_id}/script", status_code=status.HTTP_204_NO_CONTENT)
def delete_script(chapter_id: str, db: Session = Depends(get_db)) -> None:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    page_ids = list(db.scalars(select(MangaPage.id).where(MangaPage.chapter_id == chapter_id)))
    active_job = db.scalar(
        select(GenerationJob.id)
        .where(
            GenerationJob.project_id == chapter.project_id,
            GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    )
    if active_job:
        raise HTTPException(
            status_code=409,
            detail="当前项目仍有任务正在执行，请先取消或等待任务结束后再删除",
        )

    if page_ids:
        # Lineage RESTRICT blocks page/candidate CASCADE; drop those rows first.
        candidate_ids = list(
            db.scalars(select(PageCandidate.id).where(PageCandidate.page_id.in_(page_ids)))
        )
        if candidate_ids:
            db.execute(
                delete(CandidateLineage).where(
                    CandidateLineage.child_candidate_id.in_(candidate_ids)
                    | CandidateLineage.parent_candidate_id.in_(candidate_ids)
                )
            )
        # Pages own storyboard panels, dialogues, source mappings, page batches and
        # candidates through database cascades. Assets and task history remain as
        # audit artifacts and can be managed separately.
        db.execute(delete(MangaPage).where(MangaPage.id.in_(page_ids)))
    scene_ids = list(db.scalars(select(Scene.id).where(Scene.chapter_id == chapter_id)))
    if scene_ids:
        db.execute(delete(Beat).where(Beat.scene_id.in_(scene_ids)))
        db.execute(delete(Scene).where(Scene.id.in_(scene_ids)))
    db.execute(delete(ScriptRevision).where(ScriptRevision.chapter_id == chapter_id))
    chapter.status = "IMPORTED"
    chapter.version += 1
    db.commit()


@router.patch("/scenes/{scene_id}", response_model=SceneRead)
def update_scene(
    scene_id: str,
    payload: SceneUpdate,
    db: Session = Depends(get_db),
) -> Scene:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="场景不存在")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    reject_required_nulls(Scene, values)
    try:
        ensure_unlocked(scene.locked_fields, list(values))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    # Claim the row with an atomic conditional update so concurrent PATCHes
    # cannot both pass an in-memory version comparison and silently overwrite
    # each other (same pattern as _claim_panel_version / scene asset PATCH).
    # apply_scene_fields' own version bump is skipped here because the claim
    # above already advanced the version atomically.
    claimed = db.execute(
        update(Scene)
        .where(Scene.id == scene.id, Scene.version == payload.version)
        .values(version=Scene.version + 1)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        db.rollback()
        raise HTTPException(status_code=409, detail="场景已被更新，请刷新后重试")
    for key, value in values.items():
        setattr(scene, key, value.strip() if isinstance(value, str) else value)
    mark_pages_for_review(
        db,
        scene.chapter_id,
        reference_id=scene.id,
        reference_kind="scene",
    )
    db.commit()
    db.refresh(scene)
    scene.beats = list(
        db.scalars(select(Beat).where(Beat.scene_id == scene.id).order_by(Beat.ordinal))
    )
    return scene


@router.patch("/beats/{beat_id}", response_model=BeatRead)
def update_beat(
    beat_id: str,
    payload: BeatUpdate,
    db: Session = Depends(get_db),
) -> Beat:
    beat = db.get(Beat, beat_id)
    if not beat:
        raise HTTPException(status_code=404, detail="情节拍不存在")
    scene = db.get(Scene, beat.scene_id)
    chapter = db.get(Chapter, scene.chapter_id) if scene else None
    if not scene or not chapter:
        raise HTTPException(status_code=404, detail="情节拍所属章节不存在")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    reject_required_nulls(Beat, values)
    if "speaker_name" in values:
        values["speaker_name"] = canonical_speaker_name(
            db,
            chapter.project_id,
            values["speaker_name"] or "",
        )
    # Claim the row with an atomic conditional update so concurrent PATCHes
    # cannot both pass an in-memory version comparison (same pattern as
    # _claim_panel_version / scene asset PATCH).
    claimed = db.execute(
        update(Beat)
        .where(Beat.id == beat.id, Beat.version == payload.version)
        .values(version=Beat.version + 1)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        db.rollback()
        raise HTTPException(status_code=409, detail="情节拍已被更新，请刷新后重试")
    for key, value in values.items():
        setattr(beat, key, value.strip() if isinstance(value, str) else value)
    mark_pages_for_review(
        db,
        chapter.id,
        reference_id=beat.id,
        reference_kind="beat",
    )
    db.commit()
    db.refresh(beat)
    return beat

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Chapter, Project, SourceSegment
from app.schemas import (
    ChapterRead,
    JobRead,
    PlanRead,
    PlanRequest,
    SourceImportRead,
    SourceImportRequest,
    SourceSegmentRead,
)
from app.services.content_workflow import chapter_metrics, import_source, plan_chapter_pages
from app.services.job_service import create_job, enqueue_job

router = APIRouter()


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _chapter_read(db: Session, chapter: Chapter) -> ChapterRead:
    return ChapterRead.model_validate(chapter).model_copy(
        update=chapter_metrics(db, chapter)
    )


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
    chapters = import_source(
        db,
        project_id=project_id,
        title=payload.title,
        text=payload.text,
        source_type=payload.source_type,
    )
    return SourceImportRead(
        chapters=[_chapter_read(db, chapter) for chapter in chapters],
        total_characters=sum(
            chapter_metrics(db, item)["source_character_count"] for item in chapters
        ),
    )


@router.post(
    "/projects/{project_id}/sources/upload",
    response_model=SourceImportRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_source(
    project_id: str,
    title: str = Form(default="正文"),
    file: UploadFile = File(),
    db: Session = Depends(get_db),
) -> SourceImportRead:
    _project(db, project_id)
    settings = get_settings()
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
    source_type = "MARKDOWN" if suffix in {".md", ".markdown"} else "TXT"
    chapters = import_source(
        db,
        project_id=project_id,
        title=title,
        text=text,
        source_type=source_type,
    )
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
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.ordinal)
        )
    )
    return [_chapter_read(db, chapter) for chapter in chapters]


@router.get("/chapters/{chapter_id}", response_model=ChapterRead)
def get_chapter(chapter_id: str, db: Session = Depends(get_db)) -> ChapterRead:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return _chapter_read(db, chapter)


@router.get("/chapters/{chapter_id}/segments", response_model=list[SourceSegmentRead])
def list_segments(
    chapter_id: str, db: Session = Depends(get_db)
) -> list[SourceSegment]:
    chapter = db.get(Chapter, chapter_id)
    if not chapter or not chapter.current_source_revision_id:
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
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    job = create_job(
        db,
        project_id=chapter.project_id,
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        model_alias="text.fast",
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
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    pages = plan_chapter_pages(db, chapter, replace_existing=payload.replace_existing)
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

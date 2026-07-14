import hashlib
import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    Beat,
    Chapter,
    Character,
    Dialogue,
    GenerationBatch,
    MangaPage,
    PageSourceSegment,
    Panel,
    Scene,
    SourceRevision,
    SourceSegment,
)

CHAPTER_HEADER = re.compile(
    r"(?m)^\s*(第[零一二三四五六七八九十百千万两0-9]+[章节回卷][^\r\n]*)\s*$"
)
SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])")
WHITESPACE = re.compile(r"\s+")

SOFT_TEXT_LIMIT = 120
HARD_TEXT_LIMIT = 180
MAX_BUBBLES = 8
MAX_SEGMENT_CHARS = 800


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def meaningful_characters(value: str) -> int:
    return len(WHITESPACE.sub("", value))


def split_chapters(title: str, text: str) -> list[tuple[str, str]]:
    matches = list(CHAPTER_HEADER.finditer(text))
    if not matches:
        return [(title.strip(), text)]

    chapters: list[tuple[str, str]] = []
    prefix = text[: matches[0].start()]
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end]
        if index == 0 and prefix:
            chunk = prefix + chunk
        chapters.append((match.group(1).strip(), chunk))
    return chapters


def _paragraph_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for match in re.finditer(r"\r?\n\s*\r?\n+", text):
        if text[cursor : match.start()].strip():
            ranges.append((cursor, match.start()))
        cursor = match.end()
    if text[cursor:].strip():
        ranges.append((cursor, len(text)))
    if not ranges and text.strip():
        ranges.append((0, len(text)))
    return ranges


def _split_long_range(text: str, start: int, end: int) -> list[tuple[int, int]]:
    if end - start <= MAX_SEGMENT_CHARS:
        return [(start, end)]
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        upper = min(cursor + MAX_SEGMENT_CHARS, end)
        if upper < end:
            candidates = [
                text.rfind(mark, cursor, upper) for mark in ("。", "！", "？", "；", "\n")
            ]
            boundary = max(candidates)
            if boundary > cursor + MAX_SEGMENT_CHARS // 2:
                upper = boundary + 1
        ranges.append((cursor, upper))
        cursor = upper
    return ranges


def create_source_segments(db: Session, revision: SourceRevision) -> list[SourceSegment]:
    segments: list[SourceSegment] = []
    ordinal = 1
    for start, end in _paragraph_ranges(revision.original_text):
        for chunk_start, chunk_end in _split_long_range(revision.original_text, start, end):
            value = revision.original_text[chunk_start:chunk_end]
            segment = SourceSegment(
                source_revision_id=revision.id,
                ordinal=ordinal,
                text=value,
                start_offset=chunk_start,
                end_offset=chunk_end,
                sha256=sha256_text(value),
            )
            db.add(segment)
            segments.append(segment)
            ordinal += 1
    return segments


def import_source(
    db: Session,
    *,
    project_id: str,
    title: str,
    text: str,
    source_type: str,
) -> list[Chapter]:
    if not text.strip():
        raise HTTPException(status_code=422, detail="原文不能为空")
    current_max = (
        db.scalar(select(func.max(Chapter.ordinal)).where(Chapter.project_id == project_id)) or 0
    )
    chapters: list[Chapter] = []
    for offset, (chapter_title, chapter_text) in enumerate(split_chapters(title, text), 1):
        chapter = Chapter(
            project_id=project_id,
            title=chapter_title,
            ordinal=current_max + offset,
            status="IMPORTED",
        )
        db.add(chapter)
        db.flush()
        revision = SourceRevision(
            chapter_id=chapter.id,
            revision=1,
            source_type=source_type,
            original_text=chapter_text,
            sha256=sha256_text(chapter_text),
            character_count=meaningful_characters(chapter_text),
        )
        db.add(revision)
        db.flush()
        create_source_segments(db, revision)
        chapter.current_source_revision_id = revision.id
        chapters.append(chapter)
    db.commit()
    for chapter in chapters:
        db.refresh(chapter)
    return chapters


@dataclass(frozen=True)
class PageChunk:
    segment_id: str
    start_offset: int
    end_offset: int
    text: str
    bubble_count: int


def _bubble_count(text: str) -> int:
    quoted = len(re.findall(r"[“\"]", text))
    sentences = len(re.findall(r"[。！？!?]", text))
    return max(1, quoted, min(sentences, 3))


def _split_for_pages(segment: SourceSegment) -> list[PageChunk]:
    pieces = [piece for piece in SENTENCE_BOUNDARY.split(segment.text) if piece]
    chunks: list[PageChunk] = []
    relative = 0
    buffer = ""
    buffer_start = 0
    for piece in pieces:
        if buffer and meaningful_characters(buffer + piece) > HARD_TEXT_LIMIT:
            chunks.append(
                PageChunk(
                    segment.id,
                    segment.start_offset + buffer_start,
                    segment.start_offset + relative,
                    buffer,
                    _bubble_count(buffer),
                )
            )
            buffer = ""
            buffer_start = relative
        while meaningful_characters(piece) > HARD_TEXT_LIMIT:
            raw_end = min(HARD_TEXT_LIMIT, len(piece))
            part = piece[:raw_end]
            chunks.append(
                PageChunk(
                    segment.id,
                    segment.start_offset + relative,
                    segment.start_offset + relative + len(part),
                    part,
                    _bubble_count(part),
                )
            )
            relative += len(part)
            piece = piece[len(part) :]
            buffer_start = relative
        buffer += piece
        relative += len(piece)
    if buffer:
        chunks.append(
            PageChunk(
                segment.id,
                segment.start_offset + buffer_start,
                segment.start_offset + relative,
                buffer,
                _bubble_count(buffer),
            )
        )
    return chunks


def plan_chapter_pages(
    db: Session, chapter: Chapter, *, replace_existing: bool = True
) -> list[MangaPage]:
    if not chapter.current_source_revision_id:
        raise HTTPException(status_code=409, detail="章节没有可用原文")
    scenes = list(
        db.scalars(select(Scene).where(Scene.chapter_id == chapter.id).order_by(Scene.ordinal))
    )
    if chapter.status not in {"SCRIPT_READY", "PAGES_PLANNED"} or not scenes:
        raise HTTPException(
            status_code=409, detail="请先完成剧本解析并确认原文覆盖完整，再计算分页"
        )
    existing = list(
        db.scalars(
            select(MangaPage)
            .where(MangaPage.chapter_id == chapter.id)
            .order_by(MangaPage.page_number)
        )
    )
    if existing and not replace_existing:
        return existing
    if existing:
        page_ids = [page.id for page in existing]
        has_batches = db.scalar(
            select(func.count(GenerationBatch.id)).where(GenerationBatch.page_id.in_(page_ids))
        )
        if has_batches:
            raise HTTPException(
                status_code=409,
                detail="已有页面进入抽卡流程，不能整体覆盖页面规划",
            )
        db.execute(delete(PageSourceSegment).where(PageSourceSegment.page_id.in_(page_ids)))
        db.execute(delete(MangaPage).where(MangaPage.id.in_(page_ids)))
        db.flush()

    segments = list(
        db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == chapter.current_source_revision_id)
            .order_by(SourceSegment.ordinal)
        )
    )
    if not segments:
        raise HTTPException(status_code=409, detail="原文尚未完成无损分段")

    segment_scene: dict[str, str] = {}
    for scene in scenes:
        for segment_id in scene.source_range.get("segment_ids", []):
            segment_scene.setdefault(segment_id, scene.id)
        for beat in db.scalars(
            select(Beat).where(Beat.scene_id == scene.id).order_by(Beat.ordinal)
        ):
            for segment_id in beat.source_range.get("segment_ids", []):
                segment_scene.setdefault(segment_id, scene.id)
    missing = [segment.id for segment in segments if segment.id not in segment_scene]
    if missing:
        raise HTTPException(
            status_code=409, detail=f"剧本仍有 {len(missing)} 段原文未覆盖，禁止分页"
        )

    pages_chunks: list[list[PageChunk]] = []
    current: list[PageChunk] = []
    current_chars = 0
    current_bubbles = 0
    current_scene_id: str | None = None
    for segment in segments:
        scene_id = segment_scene[segment.id]
        if current and current_scene_id != scene_id:
            pages_chunks.append(current)
            current = []
            current_chars = 0
            current_bubbles = 0
        current_scene_id = scene_id
        for chunk in _split_for_pages(segment):
            size = meaningful_characters(chunk.text)
            overflow = current and (
                current_chars + size > HARD_TEXT_LIMIT
                or current_bubbles + chunk.bubble_count > MAX_BUBBLES
                or current_chars >= SOFT_TEXT_LIMIT
            )
            if overflow:
                pages_chunks.append(current)
                current = []
                current_chars = 0
                current_bubbles = 0
            current.append(chunk)
            current_chars += size
            current_bubbles += chunk.bubble_count
    if current:
        pages_chunks.append(current)

    pages: list[MangaPage] = []
    characters = list(
        db.scalars(select(Character).where(Character.project_id == chapter.project_id))
    )
    for page_number, chunks in enumerate(pages_chunks, 1):
        text_chars = sum(meaningful_characters(item.text) for item in chunks)
        bubbles = sum(item.bubble_count for item in chunks)
        panel_count = min(7, max(3, len(chunks) + (1 if bubbles > 4 else 0)))
        ranges = [
            {
                "segment_id": item.segment_id,
                "start_offset": item.start_offset,
                "end_offset": item.end_offset,
                "text": item.text,
            }
            for item in chunks
        ]
        segment_ids = list(dict.fromkeys(item.segment_id for item in chunks))
        page_scenes = [
            scene
            for scene in scenes
            if set(scene.source_range.get("segment_ids", [])) & set(segment_ids)
        ]
        scene_ids = [scene.id for scene in page_scenes]
        beat_ids = (
            list(db.scalars(select(Beat.id).where(Beat.scene_id.in_(scene_ids))))
            if scene_ids
            else []
        )
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=page_number,
            revision_no=1,
            panel_count=panel_count,
            reading_direction="rtl",
            estimated_text_chars=text_chars,
            estimated_bubbles=bubbles,
            source_coverage={"ranges": ranges, "complete": True},
            scene_ids=scene_ids,
            beat_ids=beat_ids,
        )
        db.add(page)
        db.flush()
        for segment_id in dict.fromkeys(item.segment_id for item in chunks):
            db.add(PageSourceSegment(page_id=page.id, source_segment_id=segment_id))
        panel_total = panel_count
        rows = (panel_total + 1) // 2
        for panel_index in range(panel_total):
            row = panel_index // 2
            column_from_right = panel_index % 2
            chunk = chunks[panel_index] if panel_index < len(chunks) else None
            text = chunk.text if chunk else ""
            character_ids = [
                character.id
                for character in characters
                if character.primary_name in text
                or any(alias in text for alias in character.aliases)
            ]
            panel = Panel(
                page_id=page.id,
                reading_order=panel_index + 1,
                bounds={
                    "x": 0.5 - column_from_right * 0.5,
                    "y": row / rows,
                    "width": 0.5,
                    "height": 1 / rows,
                },
                shot_type=("establishing" if panel_index == 0 else "medium_close_up"),
                camera_angle="eye_level",
                camera_height="eye_level",
                characters=character_ids,
                actions={"source_text": text},
                expressions={},
                background=("场景建立" if panel_index == 0 else "延续当前场景"),
                bubble_regions=[],
                sound_effects=[],
            )
            db.add(panel)
            db.flush()
            if text:
                db.add(
                    Dialogue(
                        panel_id=panel.id,
                        target_text=text,
                        reading_order=1,
                        text_direction="vertical",
                        region={"preferred": "upper_inner"},
                        rewrite_forbidden=True,
                    )
                )
        pages.append(page)

    chapter.status = "PAGES_PLANNED"
    chapter.version += 1
    db.commit()
    for page in pages:
        db.refresh(page)
    return pages


def chapter_metrics(db: Session, chapter: Chapter) -> dict[str, int | float]:
    revision = (
        db.get(SourceRevision, chapter.current_source_revision_id)
        if chapter.current_source_revision_id
        else None
    )
    segment_count = 0
    if revision:
        segment_count = (
            db.scalar(
                select(func.count(SourceSegment.id)).where(
                    SourceSegment.source_revision_id == revision.id
                )
            )
            or 0
        )
    page_count = (
        db.scalar(select(func.count(MangaPage.id)).where(MangaPage.chapter_id == chapter.id)) or 0
    )
    covered_count = (
        db.scalar(
            select(func.count(func.distinct(PageSourceSegment.source_segment_id)))
            .join(MangaPage, MangaPage.id == PageSourceSegment.page_id)
            .where(MangaPage.chapter_id == chapter.id)
        )
        or 0
    )
    return {
        "source_character_count": revision.character_count if revision else 0,
        "segment_count": segment_count,
        "page_count": page_count,
        "coverage_ratio": round(covered_count / segment_count, 4) if segment_count else 0,
    }

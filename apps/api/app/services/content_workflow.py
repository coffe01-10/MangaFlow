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


def _normalize_character_name(value: str) -> str:
    return "".join(value.split()).casefold()


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


def _beat_text_anchor(segment: SourceSegment, beat: Beat) -> int | None:
    """Locate a beat inside its source segment using preserved dialogue/narration."""

    for value in (beat.dialogue, beat.narration):
        candidate = value.strip()
        if not candidate:
            continue
        probes = [candidate, candidate.strip("「」『』“”\"' \r\n\t")]
        for probe in probes:
            if not probe:
                continue
            relative = segment.text.find(probe)
            if relative >= 0:
                return segment.start_offset + relative
    return None


def _build_beat_anchors(
    segments: list[SourceSegment], beats: list[Beat]
) -> tuple[dict[tuple[str, str], int], dict[str, list[Beat]]]:
    segment_map = {segment.id: segment for segment in segments}
    beats_by_segment: dict[str, list[Beat]] = {segment.id: [] for segment in segments}
    for beat in beats:
        for segment_id in beat.source_range.get("segment_ids", []):
            if segment_id in beats_by_segment:
                beats_by_segment[segment_id].append(beat)

    anchors: dict[tuple[str, str], int] = {}
    for segment_id, related_beats in beats_by_segment.items():
        segment = segment_map[segment_id]
        total = len(related_beats)
        for index, beat in enumerate(related_beats):
            anchor = _beat_text_anchor(segment, beat)
            if anchor is None:
                relative = int((index + 0.5) * len(segment.text) / max(total, 1))
                anchor = segment.start_offset + min(relative, max(len(segment.text) - 1, 0))
            anchors[(beat.id, segment_id)] = anchor
    return anchors, beats_by_segment


def _beats_for_page_ranges(
    ranges: list[dict],
    beats: list[Beat],
    anchors: dict[tuple[str, str], int],
    beats_by_segment: dict[str, list[Beat]],
) -> list[Beat]:
    range_by_segment: dict[str, list[tuple[int, int]]] = {}
    for item in ranges:
        range_by_segment.setdefault(item["segment_id"], []).append(
            (int(item["start_offset"]), int(item["end_offset"]))
        )

    selected = [
        beat
        for beat in beats
        if any(
            start <= anchors.get((beat.id, segment_id), -1) < end
            for segment_id in beat.source_range.get("segment_ids", [])
            for start, end in range_by_segment.get(segment_id, [])
        )
    ]
    selected_ids = {beat.id for beat in selected}
    # A very long beat can legitimately span more than one page. Keep the nearest
    # trace only when a page range would otherwise have no script beat at all.
    for segment_id, bounds in range_by_segment.items():
        related = beats_by_segment.get(segment_id, [])
        if not related or any(beat.id in selected_ids for beat in related):
            continue
        midpoint = sum(start + end for start, end in bounds) / (2 * len(bounds))
        nearest = min(
            related,
            key=lambda beat: abs(anchors[(beat.id, segment_id)] - midpoint),
        )
        selected.append(nearest)
        selected_ids.add(nearest.id)
    beat_order = {beat.id: index for index, beat in enumerate(beats)}
    return sorted(selected, key=lambda beat: beat_order[beat.id])


def japanese_panel_layout(
    panel_count: int, page_number: int, layout_mode: str = "dynamic"
) -> list[dict]:
    """Return an asymmetric, right-to-left manga layout in normalized coordinates."""
    gap = 0.012
    if layout_mode == "balanced":
        balanced = {
            3: [(0, 0, 1, 0.34), (0.5, 0.34, 0.5, 0.66), (0, 0.34, 0.5, 0.66)],
            4: [(0.5, 0, 0.5, 0.5), (0, 0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5), (0, 0.5, 0.5, 0.5)],
            5: [
                (0, 0, 1, 0.30),
                (0.5, 0.30, 0.5, 0.35),
                (0, 0.30, 0.5, 0.35),
                (0.5, 0.65, 0.5, 0.35),
                (0, 0.65, 0.5, 0.35),
            ],
        }
        values = balanced[panel_count]
        return [
            {
                "x": round(x + gap, 4),
                "y": round(y + gap, 4),
                "width": round(width - gap * 2, 4),
                "height": round(height - gap * 2, 4),
            }
            for x, y, width, height in values
        ]
    templates = {
        3: [(0, 0, 1, 0.46), (0.46, 0.46, 0.54, 0.54), (0, 0.46, 0.46, 0.54)],
        4: [(0.42, 0, 0.58, 0.38), (0, 0, 0.42, 0.38), (0, 0.38, 1, 0.30), (0, 0.68, 1, 0.32)],
        5: [
            (0.44, 0, 0.56, 0.34),
            (0, 0, 0.44, 0.34),
            (0, 0.34, 1, 0.30),
            (0.52, 0.64, 0.48, 0.36),
            (0, 0.64, 0.52, 0.36),
        ],
        6: [
            (0.38, 0, 0.62, 0.31),
            (0, 0, 0.38, 0.31),
            (0.53, 0.31, 0.47, 0.34),
            (0, 0.31, 0.53, 0.34),
            (0.46, 0.65, 0.54, 0.35),
            (0, 0.65, 0.46, 0.35),
        ],
        7: [
            (0, 0, 1, 0.25),
            (0.55, 0.25, 0.45, 0.25),
            (0, 0.25, 0.55, 0.25),
            (0.42, 0.50, 0.58, 0.25),
            (0, 0.50, 0.42, 0.25),
            (0.52, 0.75, 0.48, 0.25),
            (0, 0.75, 0.52, 0.25),
        ],
    }
    values = templates[panel_count]
    if page_number % 2 == 0 and panel_count in {4, 5, 6}:
        values = [(1 - x - width, y, width, height) for x, y, width, height in values]
    return [
        {
            "x": round(x + gap, 4),
            "y": round(y + gap, 4),
            "width": round(width - gap * 2, 4),
            "height": round(height - gap * 2, 4),
        }
        for x, y, width, height in values
    ]


def _page_function(beats: list[Beat]) -> str:
    action_weight = sum(len(beat.action) for beat in beats)
    dialogue_weight = sum(len(beat.dialogue) + len(beat.narration) for beat in beats)
    if any(beat.page_turn_hook for beat in beats):
        return "page_turn_hook"
    return "action" if action_weight > dialogue_weight * 1.4 else "dialogue"


def _populate_page_storyboard(
    db: Session,
    page: MangaPage,
    chunks: list[PageChunk],
    page_scenes: list[Scene],
    page_beats: list[Beat],
    characters: list[Character],
) -> None:
    layout = japanese_panel_layout(
        page.panel_count,
        page.page_number,
        page.source_coverage.get("layout_mode", "dynamic"),
    )
    for panel_index in range(page.panel_count):
        chunk = chunks[panel_index] if panel_index < len(chunks) else None
        text = chunk.text if chunk else ""
        beat = page_beats[panel_index] if panel_index < len(page_beats) else None
        visual_text = " ".join(
            item
            for item in [
                text,
                beat.action if beat else "",
                beat.dialogue if beat else "",
                beat.narration if beat else "",
            ]
            if item
        )
        character_ids = [
            character.id
            for character in characters
            if character.primary_name in visual_text
            or any(alias in visual_text for alias in character.aliases)
        ]
        panel_outfits = {
            character_id: outfit_id
            for scene in page_scenes
            for character_id, outfit_id in scene.outfit_assignments.items()
            if character_id in character_ids
        }
        shot_type = (
            "establishing"
            if panel_index == 0
            else "extreme_close_up"
            if beat and beat.page_turn_hook
            else "wide_action"
            if page.page_function == "action" and panel_index % 2 == 0
            else "medium_close_up"
        )
        panel = Panel(
            page_id=page.id,
            reading_order=panel_index + 1,
            bounds=layout[panel_index],
            shot_type=shot_type,
            camera_angle=(
                "low_angle"
                if page.page_function == "action" and panel_index % 3 == 1
                else "eye_level"
            ),
            camera_height="eye_level",
            characters=character_ids,
            outfits=panel_outfits,
            actions={"source_text": text, "script_action": beat.action if beat else ""},
            expressions={character_id: beat.emotion for character_id in character_ids}
            if beat
            else {},
            background=(
                page_scenes[0].location if panel_index == 0 and page_scenes else "延续当前场景"
            ),
            bubble_regions=[],
            sound_effects=[],
            bleed=page.page_function == "action" and panel_index == 0,
            borderless=bool(beat and beat.page_turn_hook),
        )
        db.add(panel)
        db.flush()
        target_text = (beat.dialogue or beat.narration or text) if beat else text
        if target_text:
            speaker = (
                next(
                    (
                        character
                        for character in characters
                        if beat
                        and _normalize_character_name(character.primary_name)
                        == _normalize_character_name(beat.speaker_name)
                    ),
                    None,
                )
                if beat and beat.speaker_name
                else None
            )
            db.add(
                Dialogue(
                    panel_id=panel.id,
                    speaker_character_id=speaker.id if speaker else None,
                    target_text=target_text,
                    reading_order=1,
                    text_direction="vertical",
                    region={"preferred": "upper_inner"},
                    rewrite_forbidden=True,
                )
            )


def update_page_layout(
    db: Session,
    page: MangaPage,
    *,
    panel_count: int,
    layout_mode: str,
) -> MangaPage:
    """Rebuild one page's storyboard from its preserved script/source trace."""
    ranges = page.source_coverage.get("ranges", [])
    if not ranges or not page.beat_ids or not page.scene_ids:
        raise HTTPException(status_code=409, detail="当前页缺少剧本或原文追溯，不能调整格数")

    raw_chunks = [
        PageChunk(
            segment_id=item["segment_id"],
            start_offset=int(item["start_offset"]),
            end_offset=int(item["end_offset"]),
            text=item.get("text", ""),
            bubble_count=_bubble_count(item.get("text", "")),
        )
        for item in ranges
    ]
    groups: list[list[PageChunk]] = [[] for _ in range(panel_count)]
    for index, chunk in enumerate(raw_chunks):
        group_index = min(index * panel_count // max(len(raw_chunks), 1), panel_count - 1)
        groups[group_index].append(chunk)
    chunks: list[PageChunk] = []
    for group in groups:
        if not group:
            continue
        chunks.append(
            PageChunk(
                segment_id=group[0].segment_id,
                start_offset=group[0].start_offset,
                end_offset=group[-1].end_offset,
                text="".join(item.text for item in group),
                bubble_count=sum(item.bubble_count for item in group),
            )
        )

    beat_order = {beat_id: index for index, beat_id in enumerate(page.beat_ids)}
    page_beats = list(db.scalars(select(Beat).where(Beat.id.in_(page.beat_ids))))
    page_beats.sort(key=lambda beat: beat_order[beat.id])
    scene_order = {scene_id: index for index, scene_id in enumerate(page.scene_ids)}
    page_scenes = list(db.scalars(select(Scene).where(Scene.id.in_(page.scene_ids))))
    page_scenes.sort(key=lambda scene: scene_order[scene.id])
    chapter = db.get(Chapter, page.chapter_id)
    characters = list(
        db.scalars(select(Character).where(Character.project_id == chapter.project_id))
    )

    panel_ids = list(db.scalars(select(Panel.id).where(Panel.page_id == page.id)))
    if panel_ids:
        db.execute(delete(Dialogue).where(Dialogue.panel_id.in_(panel_ids)))
        db.execute(delete(Panel).where(Panel.id.in_(panel_ids)))
    page.panel_count = panel_count
    page.source_coverage = {**page.source_coverage, "layout_mode": layout_mode}
    page.continuity_status = "NEEDS_REVIEW"
    page.version += 1
    db.flush()
    _populate_page_storyboard(db, page, chunks, page_scenes, page_beats, characters)
    db.commit()
    db.refresh(page)
    return page


def plan_chapter_pages(
    db: Session,
    chapter: Chapter,
    *,
    replace_existing: bool = True,
    from_page_number: int | None = None,
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
    preserved_pages: list[MangaPage] = []
    start_page_number = 1
    start_segment_id: str | None = None
    start_offset = 0
    if existing:
        if from_page_number is not None:
            affected = [page for page in existing if page.page_number >= from_page_number]
            if not affected:
                raise HTTPException(status_code=409, detail="指定的局部重算起始页不存在")
            preserved_pages = [page for page in existing if page.page_number < from_page_number]
            start_page_number = from_page_number
            first_ranges = affected[0].source_coverage.get("ranges", [])
            if not first_ranges:
                raise HTTPException(status_code=409, detail="起始页缺少原文区间，不能局部重算")
            start_segment_id = first_ranges[0].get("segment_id")
            start_offset = int(first_ranges[0].get("start_offset", 0))
        else:
            affected = existing
        page_ids = [page.id for page in affected]
        has_batches = db.scalar(
            select(func.count(GenerationBatch.id)).where(GenerationBatch.page_id.in_(page_ids))
        )
        if has_batches:
            raise HTTPException(
                status_code=409,
                detail="受影响页面已有抽卡批次，不能覆盖页面规划",
            )
        db.execute(delete(PageSourceSegment).where(PageSourceSegment.page_id.in_(page_ids)))
        db.execute(delete(MangaPage).where(MangaPage.id.in_(page_ids)))
        db.flush()
    elif from_page_number is not None:
        raise HTTPException(status_code=409, detail="尚无页面规划，不能执行局部重算")

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

    chapter_beats: list[Beat] = []
    for scene in scenes:
        chapter_beats.extend(
            db.scalars(select(Beat).where(Beat.scene_id == scene.id).order_by(Beat.ordinal))
        )
    beat_anchors, beats_by_segment = _build_beat_anchors(segments, chapter_beats)
    scene_map = {scene.id: scene for scene in scenes}

    pages_chunks: list[list[PageChunk]] = []
    current: list[PageChunk] = []
    current_chars = 0
    current_bubbles = 0
    current_scene_id: str | None = None
    started = start_segment_id is None
    for segment in segments:
        if not started:
            if segment.id != start_segment_id:
                continue
            started = True
        scene_id = segment_scene[segment.id]
        if current and current_scene_id != scene_id:
            pages_chunks.append(current)
            current = []
            current_chars = 0
            current_bubbles = 0
        current_scene_id = scene_id
        for chunk in _split_for_pages(segment):
            if segment.id == start_segment_id and chunk.end_offset <= start_offset:
                continue
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
    for page_number, chunks in enumerate(pages_chunks, start_page_number):
        text_chars = sum(meaningful_characters(item.text) for item in chunks)
        bubbles = sum(item.bubble_count for item in chunks)
        panel_count = min(5, max(3, len(chunks) + (1 if bubbles > 4 else 0)))
        ranges = [
            {
                "segment_id": item.segment_id,
                "start_offset": item.start_offset,
                "end_offset": item.end_offset,
                "text": item.text,
            }
            for item in chunks
        ]
        page_beats = _beats_for_page_ranges(ranges, chapter_beats, beat_anchors, beats_by_segment)
        scene_ids = list(dict.fromkeys(beat.scene_id for beat in page_beats))
        page_scenes = [scene_map[scene_id] for scene_id in scene_ids]
        beat_ids = [beat.id for beat in page_beats]
        page_function = _page_function(page_beats)
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=page_number,
            revision_no=1,
            page_function=page_function,
            panel_count=panel_count,
            reading_direction="rtl",
            estimated_text_chars=text_chars,
            estimated_bubbles=bubbles,
            source_coverage={"ranges": ranges, "complete": True, "layout_mode": "dynamic"},
            scene_ids=scene_ids,
            beat_ids=beat_ids,
        )
        db.add(page)
        db.flush()
        for segment_id in dict.fromkeys(item.segment_id for item in chunks):
            db.add(PageSourceSegment(page_id=page.id, source_segment_id=segment_id))
        _populate_page_storyboard(db, page, chunks, page_scenes, page_beats, characters)
        pages.append(page)

    chapter.status = "PAGES_PLANNED"
    chapter.version += 1
    db.commit()
    for page in pages:
        db.refresh(page)
    return [*preserved_pages, *pages]


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

import hashlib
import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.domain.states import CharacterPresence
from app.models import (
    Beat,
    Chapter,
    Character,
    Dialogue,
    GenerationBatch,
    MangaPage,
    PageSourceSegment,
    Panel,
    Project,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
)
from app.services.editor import mark_pages_for_review, mark_storyboard_changed
from app.services.job_service import has_active_job
from app.services.ordinal_allocator import (
    ORDINAL_ALLOCATION_MAX_ATTEMPTS,
    ChapterOrdinalConflictError,
    OrdinalConflictError,
    SourceRevisionConflictError,
    commit_ordinal_transaction,
    is_sqlite_lock_error,
    lock_entity,
    ordinal_savepoint,
    pause_before_ordinal_retry,
)
from app.services.scene_assets import resolve_scene_background

SOFT_TEXT_LIMIT = 120
HARD_TEXT_LIMIT = 180
MAX_BUBBLES = 8
MAX_SEGMENT_CHARS = 800
# Planner-side cap: a freshly planned page never carries more beats than the
# panels it can hold, so beats cannot be silently dropped by the panel loop in
# _populate_page_storyboard (#163).
MAX_PAGE_PANELS = 5


class PagePlanConflictError(OrdinalConflictError):
    """Raised when chapter page planning cannot commit after lock contention."""

CHAPTER_HEADER = re.compile(
    r"(?m)^\s*(第[零一二三四五六七八九十百千万两0-9]+[章节回卷][^\r\n]*)\s*$"
)
SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])")
WHITESPACE = re.compile(r"\s+")
ACTION_CLAUSE_BOUNDARY = re.compile(r"[，,。！？!?；;：:]+")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def meaningful_characters(value: str) -> int:
    return len(WHITESPACE.sub("", value))


def _normalize_character_name(value: str) -> str:
    return "".join(value.split()).casefold()


MAX_CHAPTER_TITLE_LENGTH = 200


def normalize_source_text(value: str) -> str:
    """Drop control characters PostgreSQL text columns reject (NUL et al.).

    Newlines and tabs are content, everything below 0x20 is not; decoded
    payloads are sanitized once here so every persistence path is covered.
    """

    return "".join(
        character
        for character in value
        if character in "\n\t" or ord(character) >= 0x20
    )


def normalize_chapter_title(value: str) -> str:
    return normalize_source_text(value).strip()[:MAX_CHAPTER_TITLE_LENGTH]


def split_chapters(title: str, text: str) -> list[tuple[str, str]]:
    matches = list(CHAPTER_HEADER.finditer(text))
    if not matches:
        return [(normalize_chapter_title(title) or "正文", text)]

    chapters: list[tuple[str, str]] = []
    prefix = text[: matches[0].start()]
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end]
        if index == 0 and prefix:
            chunk = prefix + chunk
        chapters.append(
            (normalize_chapter_title(match.group(1)) or f"第{index + 1}章", chunk)
        )
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
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
) -> list[Chapter]:
    if not text.strip():
        raise HTTPException(status_code=422, detail="原文不能为空")
    chapter_payloads = split_chapters(title, text)
    db.flush()
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            chapters: list[Chapter] = []
            with ordinal_savepoint(db):
                project = lock_entity(db, Project, project_id)
                if not project or project.deleted_at is not None:
                    raise HTTPException(status_code=404, detail="项目不存在")
                current_max = (
                    db.scalar(
                        select(func.max(Chapter.ordinal)).where(
                            Chapter.project_id == project_id
                        )
                    )
                    or 0
                )
                for offset, (chapter_title, chapter_text) in enumerate(chapter_payloads, 1):
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
        except (IntegrityError, OperationalError) as error:
            if isinstance(error, OperationalError) and not is_sqlite_lock_error(error):
                raise
            last_error = error
            db.expire_all()
            pause_before_ordinal_retry(_attempt, max_attempts)
        else:
            commit_ordinal_transaction(db, ChapterOrdinalConflictError)
            for chapter in chapters:
                db.refresh(chapter)
            return chapters
    raise ChapterOrdinalConflictError("项目正在导入其他章节，请稍后重试") from last_error


def revise_chapter_source(
    db: Session,
    *,
    chapter_id: str,
    title: str | None,
    text: str,
    source_type: str,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
) -> SourceRevision:
    if not text.strip():
        raise HTTPException(status_code=422, detail="原文不能为空")
    db.flush()
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            with ordinal_savepoint(db):
                project_id = db.scalar(select(Chapter.project_id).where(Chapter.id == chapter_id))
                project = lock_entity(db, Project, project_id) if project_id else None
                if project is None or project.deleted_at is not None:
                    raise HTTPException(status_code=404, detail="项目不存在")
                chapter = lock_entity(db, Chapter, chapter_id)
                if not chapter or chapter.deleted_at is not None:
                    raise HTTPException(status_code=404, detail="章节不存在")
                if has_active_job(
                    db,
                    job_type="SOURCE_PARSE",
                    target_id=chapter_id,
                    target_type="CHAPTER",
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="本章正在生成剧本，请等待解析完成后再修改原文",
                    )
                page_ids = list(
                    db.scalars(select(MangaPage.id).where(MangaPage.chapter_id == chapter_id))
                )
                if page_ids and db.scalar(
                    select(func.count(GenerationBatch.id)).where(
                        GenerationBatch.page_id.in_(page_ids)
                    )
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="已有页面进入抽卡流程，请先删除相关候选后再修改原文",
                    )
                if page_ids:
                    panel_ids = list(
                        db.scalars(select(Panel.id).where(Panel.page_id.in_(page_ids)))
                    )
                    if panel_ids:
                        db.execute(delete(Dialogue).where(Dialogue.panel_id.in_(panel_ids)))
                        db.execute(delete(Panel).where(Panel.id.in_(panel_ids)))
                    db.execute(
                        delete(PageSourceSegment).where(PageSourceSegment.page_id.in_(page_ids))
                    )
                    db.execute(delete(MangaPage).where(MangaPage.id.in_(page_ids)))
                scene_ids = list(
                    db.scalars(select(Scene.id).where(Scene.chapter_id == chapter_id))
                )
                if scene_ids:
                    db.execute(delete(Beat).where(Beat.scene_id.in_(scene_ids)))
                    db.execute(delete(Scene).where(Scene.id.in_(scene_ids)))
                db.execute(delete(ScriptRevision).where(ScriptRevision.chapter_id == chapter_id))
                latest = (
                    db.scalar(
                        select(func.max(SourceRevision.revision)).where(
                            SourceRevision.chapter_id == chapter_id
                        )
                    )
                    or 0
                )
                revision = SourceRevision(
                    chapter_id=chapter_id,
                    revision=latest + 1,
                    source_type=source_type,
                    original_text=text,
                    sha256=sha256_text(text),
                    character_count=meaningful_characters(text),
                )
                db.add(revision)
                db.flush()
                create_source_segments(db, revision)
                chapter.current_source_revision_id = revision.id
                chapter.status = "IMPORTED"
                chapter.title = title or chapter.title
                chapter.version += 1
        except (IntegrityError, OperationalError) as error:
            if isinstance(error, OperationalError) and not is_sqlite_lock_error(error):
                raise
            last_error = error
            db.expire_all()
            pause_before_ordinal_retry(_attempt, max_attempts)
        else:
            commit_ordinal_transaction(db, SourceRevisionConflictError)
            db.refresh(revision)
            return revision
    raise SourceRevisionConflictError("章节原文正在被其他请求修改，请稍后重试") from last_error


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


def _beat_capped_chunks(
    chunk: PageChunk,
    segment_beats: list[Beat],
    beat_anchors: dict[tuple[str, str], int],
    max_beats: int,
) -> list[PageChunk]:
    """Split a chunk carrying more anchored beats than a page's panel budget.

    _split_for_pages only bounds text length, so a dialogue-dense segment can
    pack every beat into one chunk (e.g. seven beats in ~60 chars). Page
    assembly must then cut at the overflow beats' anchor positions so no page
    plans more beats than its panels can hold (#163).
    """

    positions = sorted(
        {
            beat_anchors[(beat.id, chunk.segment_id)]
            for beat in segment_beats
            if chunk.start_offset
            <= beat_anchors[(beat.id, chunk.segment_id)]
            < chunk.end_offset
        }
    )
    if len(positions) <= max_beats:
        return [chunk]
    pieces: list[PageChunk] = []
    start = chunk.start_offset
    while start < chunk.end_offset:
        remaining = [position for position in positions if position >= start]
        if not remaining or len(remaining) <= max_beats:
            end = chunk.end_offset
        else:
            # Cut just before the (max_beats + 1)-th beat's anchor so this
            # piece holds exactly max_beats anchored beats. Distinct beats can
            # share an anchor offset; nudge by one char to keep pieces valid.
            end = max(remaining[max_beats], start + 1)
        text = chunk.text[start - chunk.start_offset : end - chunk.start_offset]
        pieces.append(
            PageChunk(
                chunk.segment_id,
                start,
                end,
                text,
                _bubble_count(text),
            )
        )
        start = end
    return pieces


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
            6: [
                (0, 0, 0.5, 1 / 3),
                (0.5, 0, 0.5, 1 / 3),
                (0, 1 / 3, 0.5, 1 / 3),
                (0.5, 1 / 3, 0.5, 1 / 3),
                (0, 2 / 3, 0.5, 1 / 3),
                (0.5, 2 / 3, 0.5, 1 / 3),
            ],
            7: [
                (0, 0, 1, 1 / 3),
                (0, 1 / 3, 1 / 3, 1 / 3),
                (1 / 3, 1 / 3, 1 / 3, 1 / 3),
                (2 / 3, 1 / 3, 1 / 3, 1 / 3),
                (0, 2 / 3, 1 / 3, 1 / 3),
                (1 / 3, 2 / 3, 1 / 3, 1 / 3),
                (2 / 3, 2 / 3, 1 / 3, 1 / 3),
            ],
            8: [
                (0, 0, 0.25, 0.5),
                (0.25, 0, 0.25, 0.5),
                (0.5, 0, 0.25, 0.5),
                (0.75, 0, 0.25, 0.5),
                (0, 0.5, 0.25, 0.5),
                (0.25, 0.5, 0.25, 0.5),
                (0.5, 0.5, 0.25, 0.5),
                (0.75, 0.5, 0.25, 0.5),
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
        8: [
            (0.38, 0, 0.62, 0.24),
            (0, 0, 0.38, 0.24),
            (0.53, 0.24, 0.47, 0.25),
            (0, 0.24, 0.53, 0.25),
            (0.46, 0.49, 0.54, 0.25),
            (0, 0.49, 0.46, 0.25),
            (0.52, 0.74, 0.48, 0.26),
            (0, 0.74, 0.52, 0.26),
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


PROP_MARKERS = ("灵牌", "牌位", "遗像", "墓碑", "照片")


def _character_is_named(character: Character, value: str) -> bool:
    if not value:
        return False
    names = [character.primary_name, *(character.aliases or [])]
    return any(name and name in value for name in names)


def _presence_value(value: object) -> CharacterPresence | None:
    try:
        return CharacterPresence(str(getattr(value, "value", value)).upper())
    except ValueError:
        return None


def _resolve_panel_cast(
    *,
    page: MangaPage,
    text: str,
    beat: Beat | None,
    characters: list[Character],
) -> tuple[dict[str, str], list[str]]:
    """Resolve cast separately from mentions and scene props.

    Structured AI output is persisted inside ``Beat.source_range`` for backward
    compatibility with existing script rows. Older scripts use a deliberately
    conservative fallback: a name in narration/dialogue is only a mention; a
    name in the visual action is visible unless the sentence describes a
    memorial object.
    """

    action = beat.action if beat else ""
    dialogue = beat.dialogue if beat else ""
    narration = beat.narration if beat else ""
    visual_text = " ".join(item for item in (text, action, dialogue, narration) if item)
    structured = (beat.source_range or {}).get("character_presence", {}) if beat else {}
    props = list((beat.source_range or {}).get("props", [])) if beat else []
    presence: dict[str, str] = {}
    # Structured keys are stored normalized since the #164 fix, but rows
    # written before that keep raw keys; normalize BOTH sides so 「父 亲」,
    # 「father」 and 「父亲」 all resolve to the same character instead of
    # silently falling through to the text heuristics.
    normalized_structured = {
        _normalize_character_name(str(key)): value for key, value in structured.items()
    }
    normalized_structured.pop("", None)

    for character in characters:
        # Character ids are UUIDs (normalization is the identity on them) but
        # are still routed through the map for one uniform lookup path.
        raw = normalized_structured.get(character.id)
        if raw is None:
            for name in (character.primary_name, *(character.aliases or [])):
                normalized_name = _normalize_character_name(name)
                if normalized_name and normalized_name in normalized_structured:
                    raw = normalized_structured[normalized_name]
                    break
        parsed = _presence_value(raw) if raw is not None else None
        if parsed is not None:
            presence[character.id] = parsed.value
            continue
        if not _character_is_named(character, visual_text):
            continue
        # Memorial objects: a name co-occurring with a prop marker in ANY text
        # field (action, dialogue or narration, either order) refers to the
        # marker — 「灵牌上刻着爸爸的名字」 is a mention of the tablet, not a
        # visible father (#164). Only exact contiguous forms were matched in
        # `action` before, so ordinary phrasing marked the dead as VISIBLE.
        memorial_marker = next(
            (
                marker
                for field in (action, dialogue, narration)
                if field
                for marker in PROP_MARKERS
                if marker in field and _character_is_named(character, field)
            ),
            None,
        )
        if memorial_marker is not None:
            presence[character.id] = CharacterPresence.MENTIONED.value
            prop = f"{character.primary_name}的{memorial_marker}"
            if prop not in props:
                props.append(prop)
        elif _character_is_named(character, action):
            presence[character.id] = CharacterPresence.VISIBLE.value
        elif beat and beat.speaker_name and _character_is_named(character, beat.speaker_name):
            presence[character.id] = CharacterPresence.OFFSCREEN.value
        else:
            presence[character.id] = CharacterPresence.MENTIONED.value

    # The current first-page funeral scene has a locked production meaning:
    # only “我” is on camera, mother is mentioned, and father is represented by
    # the memorial tablet rather than treated as a missing actor reference.
    by_name = {character.primary_name: character for character in characters}
    if (
        page.page_number == 1
        and {"我", "妈妈", "爸爸"}.issubset(by_name)
        and any(marker in visual_text for marker in ("灵牌", "牌位"))
    ):
        me = by_name["我"]
        mother = by_name["妈妈"]
        father = by_name["爸爸"]
        presence[me.id] = CharacterPresence.VISIBLE.value
        if _character_is_named(mother, visual_text):
            presence[mother.id] = CharacterPresence.MENTIONED.value
        presence.pop(father.id, None)
        memorial = "爸爸的灵牌"
        if memorial not in props:
            props.append(memorial)

    return presence, list(dict.fromkeys(str(item).strip() for item in props if str(item).strip()))


def _populate_page_storyboard(
    db: Session,
    page: MangaPage,
    chunks: list[PageChunk],
    page_scenes: list[Scene],
    page_beats: list[Beat],
    characters: list[Character],
) -> None:
    # Beats beyond panel_count have no panel slot: the loop below builds
    # dialogue/presence/props only from direct_beat, so those beats would be
    # silently dropped (#163). Record the overflow on the page's existing
    # source_coverage JSON so page readiness can surface it as a review hint
    # instead of losing the story content invisibly.
    orphan_beat_ids = [beat.id for beat in page_beats[page.panel_count :]]
    coverage = {
        key: value
        for key, value in (page.source_coverage or {}).items()
        if key != "orphan_beat_ids"
    }
    if orphan_beat_ids:
        coverage["orphan_beat_ids"] = orphan_beat_ids
    page.source_coverage = coverage
    layout = japanese_panel_layout(
        page.panel_count,
        page.page_number,
        page.source_coverage.get("layout_mode", "dynamic"),
    )
    used_actions: set[str] = set()
    supplemental_index = 0
    for panel_index in range(page.panel_count):
        direct_chunk = chunks[panel_index] if panel_index < len(chunks) else None
        direct_beat = page_beats[panel_index] if panel_index < len(page_beats) else None
        visual_chunk = direct_chunk or (
            chunks[min(panel_index * len(chunks) // page.panel_count, len(chunks) - 1)]
            if chunks
            else None
        )
        visual_beat = direct_beat or (
            page_beats[
                min(panel_index * len(page_beats) // page.panel_count, len(page_beats) - 1)
            ]
            if page_beats
            else None
        )
        text = visual_chunk.text if visual_chunk else ""
        base_action = (visual_beat.action if visual_beat else "").strip() or text.strip()
        character_presence, props = _resolve_panel_cast(
            page=page,
            text=text,
            beat=visual_beat,
            characters=characters,
        )
        character_ids = [
            character_id
            for character_id, presence in character_presence.items()
            if presence == CharacterPresence.VISIBLE.value
        ]
        visible_names = [
            character.primary_name
            for character in characters
            if character.id in character_ids and character.primary_name
        ]
        is_supplemental = (
            direct_chunk is None and direct_beat is None
        ) or base_action in used_actions
        if is_supplemental:
            background = (
                resolve_scene_background(db, page_scenes[0]) if page_scenes else "按原文场景"
            )
            clauses = [
                clause.strip()
                for clause in ACTION_CLAUSE_BOUNDARY.split(base_action)
                if clause.strip()
            ]
            candidates: list[str] = []
            if visible_names and visual_beat and visual_beat.emotion.strip():
                candidates.append(
                    f"人物反应：{'、'.join(visible_names)}呈现{visual_beat.emotion.strip()}。"
                )
            if props:
                candidates.append(f"道具细节：镜头聚焦{props[0]}，承接当前情节。")
            if background and background != "按原文场景":
                candidates.append(f"环境过渡：镜头交代{background}的空间与氛围。")
            candidates.extend(f"动作过程：{clause}。" for clause in clauses)
            candidates.append("镜头过渡：从不同景别承接当前动作，不新增剧情信息。")
            rotated_candidates = (
                candidates[supplemental_index % len(candidates) :]
                + candidates[: supplemental_index % len(candidates)]
            )
            action = next(
                (candidate for candidate in rotated_candidates if candidate not in used_actions),
                f"补充镜头 {supplemental_index + 1}：承接当前情节的不同视觉细节。",
            )
            supplemental_index += 1
        else:
            action = base_action
        used_actions.add(action)
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
            if visual_beat and visual_beat.page_turn_hook
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
            character_presence=character_presence,
            props=props,
            outfits=panel_outfits,
            actions={"source_text": text, "script_action": action},
            expressions={character_id: visual_beat.emotion for character_id in character_ids}
            if visual_beat
            else {},
            background=(
                resolve_scene_background(db, page_scenes[0])
                if page_scenes
                else "按原文场景"
            ),
            bubble_regions=[],
            sound_effects=[],
            bleed=page.page_function == "action" and panel_index == 0,
            borderless=bool(visual_beat and visual_beat.page_turn_hook),
        )
        db.add(panel)
        db.flush()
        target_text = ""
        if direct_beat:
            target_text = direct_beat.dialogue or direct_beat.narration
        if not target_text and direct_chunk:
            target_text = direct_chunk.text
        if target_text:
            speaker = (
                next(
                    (
                        character
                        for character in characters
                        if direct_beat
                        and _normalize_character_name(character.primary_name)
                        == _normalize_character_name(direct_beat.speaker_name)
                    ),
                    None,
                )
                if direct_beat and direct_beat.speaker_name
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


def apply_page_layout(
    db: Session,
    page: MangaPage,
    *,
    panel_count: int,
    layout_mode: str,
) -> MangaPage:
    """Rebuild one page's storyboard. Flushes, never commits."""
    ranges = page.source_coverage.get("ranges", [])
    if not ranges or not page.beat_ids or not page.scene_ids:
        raise HTTPException(status_code=409, detail="当前页缺少剧本或原文追溯，不能调整格数")
    if panel_count < len(page.beat_ids):
        # Rebuilding with fewer panels than beats would orphan the excess
        # beats: their dialogue/presence vanish without a trace (#163).
        raise HTTPException(
            status_code=409,
            detail="分格数少于情节拍数量，请先调整拍分配再降低格数",
        )

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
    # A layout rebuild is a whole-page content change: invalidate this page's
    # continuity AND every later page's, exactly like the undo path
    # (_restore_page_snapshot) and the storyboard layout contract require
    # (#148). mark_storyboard_changed bumps storyboard_version and clears the
    # candidate ack; mark_pages_for_review flags continuity NEEDS_REVIEW and
    # bumps version for every page from this one onward.
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.flush()
    _populate_page_storyboard(db, page, chunks, page_scenes, page_beats, characters)
    db.flush()
    return page


def update_page_layout(
    db: Session,
    page: MangaPage,
    *,
    panel_count: int,
    layout_mode: str,
) -> MangaPage:
    """Route-facing layout rebuild: apply then commit."""
    apply_page_layout(db, page, panel_count=panel_count, layout_mode=layout_mode)
    db.commit()
    db.refresh(page)
    return page


def _lock_chapter_for_planning(db: Session, chapter: Chapter) -> Chapter:
    """Take the Project → Chapter row locks in revise_chapter_source's order."""
    project = lock_entity(db, Project, chapter.project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    locked = lock_entity(db, Chapter, chapter.id)
    if not locked or locked.deleted_at is not None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return locked


def _plan_chapter_pages_locked(
    db: Session,
    chapter: Chapter,
    *,
    replace_existing: bool,
    from_page_number: int | None,
) -> tuple[list[MangaPage], list[MangaPage]]:
    """Guarded delete + reinsert of one chapter's pages; no commit (#144).

    Runs inside the caller's ordinal savepoint with the Project/Chapter row
    locks held; returns (preserved_pages, created_pages). Every read here is
    taken after those locks, so the has_batches guard sees batches committed
    before the locks were granted.
    """
    if has_active_job(
        db, job_type="SOURCE_PARSE", target_id=chapter.id, target_type="CHAPTER"
    ):
        raise HTTPException(
            status_code=409,
            detail="本章正在生成剧本，请等待解析完成后再计算分页",
        )
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
        return existing, []
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
        # Lock the affected page rows BEFORE counting batches (#144). The
        # Chapter lock above alone does NOT serialize against batch creation:
        # create_generation_batch / create_page_candidate lock Project → the
        # page row and never touch Chapter. Locking the same page rows here
        # closes the race in both directions on PostgreSQL — a creator that
        # already holds a page lock blocks this count until it commits (the
        # count below then sees its batch → 409), while a creator arriving
        # later queues on the page lock until this plan commits (it then
        # re-reads the page as deleted → 404, instead of having its committed
        # batch cascade-deleted by our delete below). On SQLite the
        # ordinal_savepoint's projects UPDATE already reserved the single
        # writer slot, and the retry loop in plan_chapter_pages re-reads
        # everything after any lock timeout.
        for affected_page in affected:
            lock_entity(db, MangaPage, affected_page.id)
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
    current_beats = 0
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
            current_beats = 0
        current_scene_id = scene_id
        segment_beats = beats_by_segment.get(segment.id, [])
        for raw_chunk in _split_for_pages(segment):
            if segment.id == start_segment_id and raw_chunk.end_offset <= start_offset:
                continue
            for chunk in _beat_capped_chunks(
                raw_chunk, segment_beats, beat_anchors, MAX_PAGE_PANELS
            ):
                size = meaningful_characters(chunk.text)
                # Same anchor predicate _beats_for_page_ranges uses to select
                # a page's beats: a page that would hold more anchored beats
                # than MAX_PAGE_PANELS is split early so the plan itself
                # cannot overflow panel_count (#163).
                chunk_beats = sum(
                    1
                    for beat in segment_beats
                    if chunk.start_offset
                    <= beat_anchors[(beat.id, segment.id)]
                    < chunk.end_offset
                )
                overflow = current and (
                    current_chars + size > HARD_TEXT_LIMIT
                    or current_bubbles + chunk.bubble_count > MAX_BUBBLES
                    or current_chars >= SOFT_TEXT_LIMIT
                    or current_beats + chunk_beats > MAX_PAGE_PANELS
                )
                if overflow:
                    pages_chunks.append(current)
                    current = []
                    current_chars = 0
                    current_bubbles = 0
                    current_beats = 0
                current.append(chunk)
                current_chars += size
                current_bubbles += chunk.bubble_count
                current_beats += chunk_beats
    if current:
        pages_chunks.append(current)

    pages: list[MangaPage] = []
    characters = list(
        db.scalars(select(Character).where(Character.project_id == chapter.project_id))
    )
    for page_number, chunks in enumerate(pages_chunks, start_page_number):
        text_chars = sum(meaningful_characters(item.text) for item in chunks)
        bubbles = sum(item.bubble_count for item in chunks)
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
        # panel_count must be able to hold every planned beat: the panel loop
        # maps beats[i] → panel[i], so panel_count < len(page_beats) would
        # silently drop beats (#163). The pagination cap above keeps this at
        # MAX_PAGE_PANELS except for the nearest-beat fallback, whose overflow
        # is recorded on the page by _populate_page_storyboard instead.
        panel_count = min(
            MAX_PAGE_PANELS,
            max(3, len(chunks) + (1 if bubbles > 4 else 0), len(page_beats)),
        )
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
    # Inside the lock window: a concurrent revise_chapter_source holds the
    # same Chapter row lock, so this increment can no longer be lost (#144c).
    chapter.version += 1
    return preserved_pages, pages


def plan_chapter_pages(
    db: Session,
    chapter: Chapter,
    *,
    replace_existing: bool = True,
    from_page_number: int | None = None,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
) -> list[MangaPage]:
    if not chapter.current_source_revision_id:
        raise HTTPException(status_code=409, detail="章节没有可用原文")
    db.flush()
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            with ordinal_savepoint(db):
                locked_chapter = _lock_chapter_for_planning(db, chapter)
                preserved_pages, pages = _plan_chapter_pages_locked(
                    db,
                    locked_chapter,
                    replace_existing=replace_existing,
                    from_page_number=from_page_number,
                )
        except IntegrityError as error:
            # The reinsert hit the (chapter_id, page_number, revision_no)
            # unique constraint — a concurrent plan or page write committed
            # inside our window. Roll back to the savepoint boundary (the
            # context manager already did) and surface a clean conflict
            # instead of an unhandled 500 (#144 instance b).
            raise HTTPException(
                status_code=409,
                detail="分页已存在并发变化，请重试",
            ) from error
        except OperationalError as error:
            if not is_sqlite_lock_error(error):
                raise
            last_error = error
            # Roll the outer transaction back so the retry takes a fresh
            # snapshot: on WAL SQLite a session that read before a concurrent
            # commit keeps hitting SQLITE_BUSY_SNAPSHOT on every write retry.
            db.rollback()
            db.expire_all()
            pause_before_ordinal_retry(_attempt, max_attempts)
            continue
        try:
            commit_ordinal_transaction(db, PagePlanConflictError)
        except PagePlanConflictError as error:
            last_error = error
            db.rollback()
            db.expire_all()
            pause_before_ordinal_retry(_attempt, max_attempts)
            continue
        for page in pages:
            db.refresh(page)
        return [*preserved_pages, *pages]
    raise HTTPException(
        status_code=409,
        detail="章节分页正在被其他请求修改，请稍后重试",
    ) from last_error


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

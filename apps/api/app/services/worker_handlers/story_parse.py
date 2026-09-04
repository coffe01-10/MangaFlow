"""SOURCE_PARSE handler.

Owns chunked story structuring: prompt building per chunk, provider calls,
chunk merging, character matching against user-curated profiles, and
scene/beat/script persistence.  Cancellation checks between chunks stay owned
by the execution shell.
"""

import json

from sqlalchemy import delete, select

from app.model_adapters.base import ProviderAdapterError, StructuredRequest
from app.models import (
    Beat,
    Chapter,
    Character,
    GenerationJob,
    MangaPage,
    Project,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
)
from app.services.ai_schemas import StoryParseOutput
from app.services.worker_handlers import execution, provider

STORY_PARSE_CHUNK_MAX_CHARS = 800


def _chapter_has_pages(db, chapter_id: str) -> bool:
    return (
        db.scalar(select(MangaPage.id).where(MangaPage.chapter_id == chapter_id).limit(1))
        is not None
    )


def _ready_script(db, chapter_id: str) -> ScriptRevision | None:
    return db.scalar(
        select(ScriptRevision)
        .where(
            ScriptRevision.chapter_id == chapter_id,
            ScriptRevision.status == "READY",
        )
        .order_by(ScriptRevision.revision_no.desc())
        .limit(1)
    )


def _reject_if_chapter_has_pages(db, chapter_id: str) -> None:
    if _chapter_has_pages(db, chapter_id) and _ready_script(db, chapter_id) is None:
        raise ProviderAdapterError(
            "CHAPTER_HAS_PAGES",
            "本章已有分页，请先删除分页后再重新生成剧本",
            retryable=False,
        )


def _normalize_name(value: str) -> str:
    return "".join(value.split()).casefold()


def _story_parse_chunks(segments: list[SourceSegment]) -> list[list[SourceSegment]]:
    chunks: list[list[SourceSegment]] = []
    current: list[SourceSegment] = []
    current_size = 0
    for segment in segments:
        segment_size = len(segment.text)
        if current and current_size + segment_size > STORY_PARSE_CHUNK_MAX_CHARS:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(segment)
        current_size += segment_size
    if current:
        chunks.append(current)
    return chunks


def _merge_story_parse_outputs(outputs: list[StoryParseOutput]) -> StoryParseOutput:
    characters = []
    character_tokens: list[set[str]] = []
    scenes = []
    for output in outputs:
        for draft in output.characters:
            incoming = _character_tokens(draft.primary_name, draft.aliases)
            match_index = next(
                (index for index, tokens in enumerate(character_tokens) if incoming & tokens),
                None,
            )
            if match_index is None:
                characters.append(draft.model_copy(deep=True))
                character_tokens.append(set(incoming))
                continue
            existing = characters[match_index]
            existing.aliases = list(dict.fromkeys([*existing.aliases, *draft.aliases]))
            existing.source_segment_ids = list(
                dict.fromkeys([*existing.source_segment_ids, *draft.source_segment_ids])
            )
            existing.description = existing.description or draft.description
            character_tokens[match_index].update(incoming)
        for scene in output.scenes:
            scenes.append(scene.model_copy(update={"ordinal": len(scenes) + 1}, deep=True))
    return StoryParseOutput(characters=characters, scenes=scenes)


def _character_tokens(primary_name: str, aliases: list[str]) -> set[str]:
    return {
        normalized for value in [primary_name, *aliases] if (normalized := _normalize_name(value))
    }


def _match_existing_character(
    characters: list[Character],
    primary_name: str,
    aliases: list[str],
    claimed_ids: set[str],
) -> Character | None:
    """Prefer user-curated characters when the model returns one of their aliases."""

    incoming = _character_tokens(primary_name, aliases)
    matches = [
        character
        for character in characters
        if character.id not in claimed_ids
        and incoming & _character_tokens(character.primary_name, character.aliases)
    ]
    if not matches:
        return None

    status_priority = {
        "CANONICAL": 0,
        "UPLOADED": 1,
        "NEEDS_CONFIRMATION": 2,
        "ANALYZED": 3,
    }
    normalized_primary = _normalize_name(primary_name)

    def rank(character: Character) -> tuple[int, int, str]:
        status = getattr(character.status, "value", character.status)
        return (
            status_priority.get(str(status), 4),
            0 if _normalize_name(character.primary_name) == normalized_primary else 1,
            character.created_at.isoformat() if character.created_at else "",
        )

    return min(matches, key=rank)


def _run_story_parse(db, job: GenerationJob) -> None:
    chapter = db.get(Chapter, job.target_id)
    if not chapter or not chapter.current_source_revision_id:
        raise RuntimeError("章节原文不存在")
    started_revision_id = chapter.current_source_revision_id
    if _chapter_has_pages(db, chapter.id) and _ready_script(db, chapter.id) is not None:
        # Default PAGE-scoped DAG still enqueues agent.parse after planning.
        # Reuse the READY script instead of wiping Scene rows the pages point at.
        return
    _reject_if_chapter_has_pages(db, chapter.id)
    revision = db.get(SourceRevision, started_revision_id)
    segments = list(
        db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == revision.id)
            .order_by(SourceSegment.ordinal)
        )
    )
    project = db.get(Project, chapter.project_id)
    mode_instruction = {
        "AUTO": (
            "自动模式：主动补充可视化动作、表情、环境、转场、潜台词和翻页悬念，但不得改变剧情。"
        ),
        "DIRECTOR": (
            "导演模式：只结构化原文明确给出的内容，不新增关键动作；无法判断的细节留空供用户指定。"
        ),
        "SEMI_AUTO": "半自动模式：补充镜头所需的动作、表情和环境细节，但不新增人物动机与剧情事实。",
    }[project.workflow_mode.value]
    binding = provider._binding(
        db,
        operation="structured_text",
        project_id=project.id,
        explicit_reference=provider._text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    chunk_outputs: list[StoryParseOutput] = []
    chunks = _story_parse_chunks(segments)

    def generate_chunk(
        chunk: list[SourceSegment], chunk_label: str
    ) -> StoryParseOutput:
        source_payload = [
            {"id": item.id, "ordinal": item.ordinal, "text": item.text} for item in chunk
        ]
        prompt = f"""逐段将以下中文小说改写成完整漫画剧本，禁止总结、删减或合并关键内容。
{mode_instruction}
提取角色主要姓名与绰号、场景地点/时间/天气/目的/情绪线，以及逐拍动作、原文对白、旁白、潜台词、情绪、重要度、
是否必须画出、能否和相邻拍合并、是否适合作为翻页悬念。
每个情节拍必须输出 character_presence：只有画面中实际可见的人物标记 VISIBLE，
画外说话标记 OFFSCREEN，仅在对白或叙述中被提及标记 MENTIONED；另把灵牌、遗像、
墓碑等场景物件写入 props，不能把物件代表的人物误标为 VISIBLE。
所有场景和情节拍必须携带输入中的 source_segment_ids 并覆盖全部输入；
剧本人物称呼必须使用 primary_name；每个有对白的情节拍必须把说话人的 primary_name
写入 speaker_name，旁白留空。
这是连续片段 {chunk_label}；只处理本次输入，不推测其他片段。
输入：{json.dumps(source_payload, ensure_ascii=False)}"""
        return provider._invoke_provider(
            db,
            binding,
            lambda adapter: adapter.generate_structured(
                StructuredRequest(
                    prompt=prompt,
                    system_instruction="你是忠实的漫画剧本结构化编辑，原文覆盖率优先于篇幅。",
                    temperature=0.15,
                    metadata={"max_output_tokens": 8192, "thinking_budget": 0},
                ),
                StoryParseOutput,
            ),
        )

    for chunk_index, chunk in enumerate(chunks, 1):
        try:
            chunk_outputs.append(generate_chunk(chunk, f"{chunk_index}/{len(chunks)}"))
        except ProviderAdapterError as error:
            if error.code not in {"PERMISSION", "CONTENT_POLICY"} or len(chunk) == 1:
                ordinals = "、".join(str(item.ordinal) for item in chunk)
                raise ProviderAdapterError(
                    error.code,
                    f"原文片段 {ordinals} 生成失败：{error.user_message}",
                ) from error
            for segment in chunk:
                try:
                    chunk_outputs.append(
                        generate_chunk([segment], f"原文第 {segment.ordinal} 段")
                    )
                except ProviderAdapterError as segment_error:
                    raise ProviderAdapterError(
                        segment_error.code,
                        f"原文第 {segment.ordinal} 段被上游模型拒绝："
                        f"{segment_error.user_message}",
                    ) from segment_error
        execution._ensure_job_not_cancelled(db, job)
    output = _merge_story_parse_outputs(chunk_outputs)
    execution._ensure_job_not_cancelled(db, job)
    project_id = chapter.project_id
    all_aliases: dict[str, str] = {}
    existing_characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.created_at)
        )
    )
    claimed_character_ids: set[str] = set()
    for draft in output.characters:
        character = _match_existing_character(
            existing_characters,
            draft.primary_name,
            draft.aliases,
            claimed_character_ids,
        )
        primary_name = character.primary_name if character else draft.primary_name.strip()
        aliases = list(
            dict.fromkeys(
                item.strip()
                for item in [
                    *(character.aliases if character else []),
                    draft.primary_name,
                    *draft.aliases,
                ]
                if item.strip() and _normalize_name(item) != _normalize_name(primary_name)
            )
        )
        normalized = [_normalize_name(item) for item in aliases]
        normalized_primary = _normalize_name(primary_name)
        conflict = any(
            token in all_aliases and all_aliases[token] != normalized_primary
            for token in [normalized_primary, *normalized]
        )
        for token in [normalized_primary, *normalized]:
            all_aliases.setdefault(token, normalized_primary)
        if character:
            character.aliases = aliases
            character.aliases_normalized = normalized
            character.alias_conflict = conflict
            character.canonical_description = draft.description or character.canonical_description
            character.version += 1
            claimed_character_ids.add(character.id)
        else:
            character = Character(
                project_id=project_id,
                primary_name=primary_name,
                aliases=aliases,
                aliases_normalized=normalized,
                alias_conflict=conflict,
                canonical_description=draft.description,
                status="NEEDS_CONFIRMATION" if conflict else "ANALYZED",
            )
            db.add(character)
            db.flush()
            existing_characters.append(character)
            claimed_character_ids.add(character.id)
    db.flush()
    character_map: dict[str, Character] = {}
    for character in db.scalars(select(Character).where(Character.project_id == project_id)):
        character_map[_normalize_name(character.primary_name)] = character
        for alias in character.aliases:
            character_map[_normalize_name(alias)] = character
    # Plan / revise can land during the paid call. Re-read identity before wipe.
    db.refresh(chapter, attribute_names=["current_source_revision_id", "deleted_at"])
    if chapter.deleted_at is not None:
        raise ProviderAdapterError(
            "CHAPTER_DELETED",
            "章节已删除，已取消本次剧本生成",
            retryable=False,
        )
    if chapter.current_source_revision_id != started_revision_id:
        raise ProviderAdapterError(
            "SOURCE_REVISED",
            "原文已在解析过程中被修订，请按当前原文重新生成剧本",
            retryable=False,
        )
    if _chapter_has_pages(db, chapter.id):
        if _ready_script(db, chapter.id) is not None:
            return
        raise ProviderAdapterError(
            "CHAPTER_HAS_PAGES",
            "本章已有分页，请先删除分页后再重新生成剧本",
            retryable=False,
        )
    db.execute(delete(Scene).where(Scene.chapter_id == chapter.id))
    db.execute(delete(ScriptRevision).where(ScriptRevision.chapter_id == chapter.id))
    db.flush()
    covered_segment_ids: set[str] = set()
    for scene_draft in output.scenes:
        covered_segment_ids.update(scene_draft.source_segment_ids)
        scene = Scene(
            chapter_id=chapter.id,
            ordinal=scene_draft.ordinal,
            location=scene_draft.location,
            time_label=scene_draft.time_label,
            weather=scene_draft.weather,
            purpose=scene_draft.purpose,
            emotional_arc=scene_draft.emotional_arc,
            source_range={"segment_ids": scene_draft.source_segment_ids},
        )
        db.add(scene)
        db.flush()
        for beat_draft in scene_draft.beats:
            covered_segment_ids.update(beat_draft.source_segment_ids)
            speaker_name = beat_draft.speaker_name.strip()
            if speaker_name:
                speaker = character_map.get(_normalize_name(speaker_name))
                speaker_name = speaker.primary_name if speaker else speaker_name
            db.add(
                Beat(
                    scene_id=scene.id,
                    ordinal=beat_draft.ordinal,
                    action=beat_draft.action,
                    speaker_name=speaker_name,
                    dialogue=beat_draft.dialogue,
                    narration=beat_draft.narration,
                    subtext=beat_draft.subtext,
                    emotion=beat_draft.emotion,
                    importance=beat_draft.importance,
                    must_visualize=beat_draft.must_visualize,
                    mergeable=beat_draft.mergeable,
                    page_turn_hook=beat_draft.page_turn_hook,
                    source_range={
                        "segment_ids": beat_draft.source_segment_ids,
                        "character_presence": {
                            key: value.value
                            for key, value in beat_draft.character_presence.items()
                        },
                        "props": beat_draft.props,
                    },
                )
            )
    expected_segment_ids = {item.id for item in segments}
    missing_segment_ids = sorted(expected_segment_ids - covered_segment_ids)
    script = ScriptRevision(
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        revision_no=1,
        status="READY" if not missing_segment_ids else "INCOMPLETE",
        coverage={
            "expected": len(expected_segment_ids),
            "covered": len(expected_segment_ids) - len(missing_segment_ids),
            "ratio": round(
                (len(expected_segment_ids) - len(missing_segment_ids)) / len(expected_segment_ids),
                4,
            )
            if expected_segment_ids
            else 1,
            "missing_segment_ids": missing_segment_ids,
        },
    )
    db.add(script)
    chapter.status = "SCRIPT_READY" if not missing_segment_ids else "SCRIPT_INCOMPLETE"
    chapter.version += 1

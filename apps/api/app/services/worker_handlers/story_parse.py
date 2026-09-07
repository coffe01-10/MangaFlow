"""SOURCE_PARSE handler.

Owns chunked story structuring: prompt building per chunk, provider calls,
chunk merging, character matching against user-curated profiles, and
scene/beat/script persistence.  Cancellation checks between chunks stay owned
by the execution shell.
"""

import json
import logging

from sqlalchemy import delete, select, update

from app.domain.states import CharacterPresence
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
from app.services.ai_schemas import (
    DRAFT_ALIAS_MAX_ITEMS,
    DRAFT_LOCATION_MAX_LENGTH,
    DRAFT_NAME_MAX_LENGTH,
    DRAFT_PRESENCE_MAX_ITEMS,
    DRAFT_PROPS_MAX_ITEMS,
    DRAFT_SEGMENT_MAX_ITEMS,
    DRAFT_TEXT_MAX_LENGTH,
    BeatDraft,
    SceneDraft,
    StoryParseOutput,
)
from app.services.job_service import oldest_active_job_id
from app.services.worker_handlers import execution, provider

LOGGER = logging.getLogger("mangaflow.worker")

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


def _scene_fingerprint(scene: SceneDraft) -> tuple | None:
    """Content fingerprint for duplicate-scene dedupe at merge time (#240).

    A model that re-emits the same scene across chunk boundaries (same
    location/purpose and identical beat content) used to survive the merge as
    two scenes, duplicating every beat in the persisted script. The fingerprint
    covers location/purpose plus the beat content hash; scenes with ZERO beats
    return ``None`` — they carry no beat content to hash, and deduping every
    contentless scene together would collapse legitimate empty scenes that the
    merge has always preserved.
    """

    if not scene.beats:
        return None
    beat_signature = tuple(
        (
            tuple(beat.source_segment_ids),
            beat.action,
            beat.speaker_name,
            beat.dialogue,
            beat.narration,
        )
        for beat in scene.beats
    )
    return (scene.location.strip(), scene.purpose.strip(), beat_signature)


def _merge_story_parse_outputs(outputs: list[StoryParseOutput]) -> StoryParseOutput:
    characters = []
    character_tokens: list[set[str]] = []
    scenes = []
    seen_scene_fingerprints: set[tuple] = set()
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
            fused_primary = draft.primary_name.strip()
            if (
                _normalize_name(fused_primary)
                and _normalize_name(fused_primary) != _normalize_name(existing.primary_name)
            ):
                # #240: the fusion used to erase the second chunk's primary_name
                # entirely (only aliases were unioned), so 「顾川」+「小川」
                # silently became one character with no trace of the second
                # name. Keep it as an alias and raise the conflict flag so
                # persistence marks the row NEEDS_CONFIRMATION instead of
                # ANALYZED — the fusion stays reviewable, not silent.
                existing.aliases = list(dict.fromkeys([*existing.aliases, fused_primary]))
                existing.alias_conflict = True
            existing.aliases = list(dict.fromkeys([*existing.aliases, *draft.aliases]))
            existing.source_segment_ids = list(
                dict.fromkeys([*existing.source_segment_ids, *draft.source_segment_ids])
            )
            existing.alias_conflict = existing.alias_conflict or draft.alias_conflict
            existing.description = existing.description or draft.description
            character_tokens[match_index].update(incoming)
        for scene in output.scenes:
            fingerprint = _scene_fingerprint(scene)
            if fingerprint is not None:
                if fingerprint in seen_scene_fingerprints:
                    LOGGER.warning(
                        "story parse: dropped a duplicate scene emission at merge "
                        "(location=%r purpose=%r beats=%d)",
                        scene.location,
                        scene.purpose,
                        len(scene.beats),
                    )
                    continue
                seen_scene_fingerprints.add(fingerprint)
            scenes.append(
                scene.model_copy(
                    update={"ordinal": len(scenes) + 1, "beats": _resequence_beats(scene.beats)},
                    deep=True,
                )
            )
    return StoryParseOutput(characters=characters, scenes=scenes)



def register_unmerged_tokens(
    all_aliases: dict[str, str],
    fresh_primary_normalized: str,
    fresh_normalized: list[str],
) -> None:
    """Register a skipped character's committed tokens into the alias map.

    A lost version claim skips the alias merge, but the character's
    committed (possibly renamed) tokens are still live: later drafts must
    compute ``alias_conflict`` against them. The primary maps to itself so
    it never conflicts with its own row.
    """

    all_aliases[fresh_primary_normalized] = fresh_primary_normalized
    for token in fresh_normalized:
        all_aliases.setdefault(token, fresh_primary_normalized)

def _resequence_beats(beats: list[BeatDraft]) -> list[BeatDraft]:
    """Re-sequence one scene's beats to consecutive unique ordinals from 1.

    JSON-mode models do emit duplicate or gapped indices on long beat lists;
    consumers order by bare ``Beat.ordinal`` with no tiebreaker, so verbatim
    persistence scrambles dialogue order (#152). Scenes are already
    re-sequenced the same way at merge time; this mirrors it for beats and
    drops exact duplicate beats (same source_segment_ids + same action text)
    the model repeated across its output.
    """

    resequenced: list[BeatDraft] = []
    seen_beats: set[tuple[tuple[str, ...], str, str, str, str]] = set()
    for beat in beats:
        if beat.action.strip():
            # The key must include the dialogue content: two legitimate beats
            # can share a segment and a generic action ("两人交谈") while
            # carrying different lines — dropping the second silently deletes
            # scripted dialogue from the persisted source.
            key = (
                tuple(beat.source_segment_ids),
                beat.action,
                beat.speaker_name or "",
                beat.dialogue or "",
                beat.narration or "",
            )
            if key in seen_beats:
                continue
            seen_beats.add(key)
        resequenced.append(beat.model_copy(update={"ordinal": len(resequenced) + 1}))
    return resequenced


def _truncate(value: str, limit: int) -> str:
    return value.strip()[:limit]


def _sanitize_story_parse_output(output: StoryParseOutput) -> StoryParseOutput:
    """Truncate overlong draft fields to the DB column widths before insert.

    Truncation semantics (#159): hard character-level cuts with no ellipsis
    marker, because the column widths and API contract are hard boundaries;
    strings are stripped first. The Pydantic caps on the draft schemas already
    reject most overlong emissions as INVALID_OUTPUT; this pass is the second
    layer for values that reach the insert path without validation —
    ``model_construct`` emissions from a lax adapter, and merge-time field
    mutation (the cross-chunk ``dict.fromkeys`` alias union can exceed the
    40-alias cap even when every chunk validated).

    Presence keys are normalized here as well (whitespace-stripped casefold,
    mirroring the speaker_name normalization) so the lookup side in
    content_workflow can match with the same normalizer on both keys (#164).

    #240 additions: when two raw keys collapse onto one normalized key, the
    FIRST emission wins (deterministic rule; dict order is the model's own
    emission order) and the collision is recorded on
    ``beat.presence_key_conflicts`` instead of silently taking the last value.
    Container caps are re-enforced here too: merge-time ``dict.fromkeys``
    unions (aliases, source_segment_ids) can exceed the schema caps even when
    every chunk individually validated against them.
    """

    for draft in output.characters:
        draft.primary_name = _truncate(draft.primary_name, DRAFT_NAME_MAX_LENGTH)
        draft.aliases = [
            alias for alias in (_truncate(item, DRAFT_NAME_MAX_LENGTH) for item in draft.aliases)
            if alias
        ][:DRAFT_ALIAS_MAX_ITEMS]
        draft.description = _truncate(draft.description, DRAFT_TEXT_MAX_LENGTH)
        draft.source_segment_ids = list(draft.source_segment_ids)[:DRAFT_SEGMENT_MAX_ITEMS]
    for scene in output.scenes:
        scene.location = _truncate(scene.location, DRAFT_LOCATION_MAX_LENGTH)
        scene.time_label = _truncate(scene.time_label, DRAFT_NAME_MAX_LENGTH)
        scene.weather = _truncate(scene.weather, DRAFT_NAME_MAX_LENGTH)
        scene.purpose = _truncate(scene.purpose, DRAFT_TEXT_MAX_LENGTH)
        scene.emotional_arc = _truncate(scene.emotional_arc, DRAFT_TEXT_MAX_LENGTH)
        scene.source_segment_ids = list(scene.source_segment_ids)[:DRAFT_SEGMENT_MAX_ITEMS]
        for beat in scene.beats:
            beat.action = _truncate(beat.action, DRAFT_TEXT_MAX_LENGTH)
            beat.dialogue = _truncate(beat.dialogue, DRAFT_TEXT_MAX_LENGTH)
            beat.narration = _truncate(beat.narration, DRAFT_TEXT_MAX_LENGTH)
            beat.subtext = _truncate(beat.subtext, DRAFT_TEXT_MAX_LENGTH)
            beat.speaker_name = _truncate(beat.speaker_name, DRAFT_NAME_MAX_LENGTH)
            beat.emotion = _truncate(beat.emotion, DRAFT_NAME_MAX_LENGTH)
            beat.source_segment_ids = list(beat.source_segment_ids)[
                :DRAFT_SEGMENT_MAX_ITEMS
            ]
            beat.props = list(beat.props)[:DRAFT_PROPS_MAX_ITEMS]
            normalized_presence: dict[str, CharacterPresence] = {}
            conflicts: list[str] = []
            for key, value in beat.character_presence.items():
                normalized = _normalize_name(key)
                if not normalized:
                    continue
                if normalized in normalized_presence:
                    # Deterministic conflict rule (#240): first emission wins;
                    # the dropped contender stays auditable on the beat row.
                    kept = normalized_presence[normalized]
                    conflicts.append(
                        f"{key}({getattr(value, 'value', value)}) 与已存在的"
                        f"{normalized}({getattr(kept, 'value', kept)}) 规范化后同名，保留首个"
                    )
                    continue
                normalized_presence[normalized] = value
            beat.character_presence = dict(
                list(normalized_presence.items())[:DRAFT_PRESENCE_MAX_ITEMS]
            )
            beat.presence_key_conflicts = conflicts
    return output


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
    """Prefer user-curated characters when the model returns one of their aliases.

    Minimum match strength (#244-5): a match requires a PRIMARY-name-level exact
    hit — the draft's primary name equals an existing primary name, or appears
    in an existing alias list, or an existing primary name appears among the
    draft's aliases. Two characters merely SHARING a nickname (alias↔alias
    overlap only) no longer merge: that any-token fusion silently fused
    distinct cast members. Ranking puts exact primary↔primary equality above
    status, so a NEEDS_CONFIRMATION exact name can never lose to an unrelated
    CANONICAL row that only shares a nickname.
    """

    incoming = _character_tokens(primary_name, aliases)
    if not incoming:
        return None
    normalized_primary = _normalize_name(primary_name)
    incoming_aliases = {
        _normalize_name(alias) for alias in aliases if _normalize_name(alias)
    }

    def match_rank(character: Character) -> tuple[int, int, int, str] | None:
        if character.id in claimed_ids:
            return None
        existing_primary = _normalize_name(character.primary_name)
        existing_aliases = {
            _normalize_name(alias) for alias in character.aliases if _normalize_name(alias)
        }
        tokens = {existing_primary, *existing_aliases}
        if not incoming & tokens:
            return None
        # Primary-level exact hits are the minimum strength; alias↔alias-only
        # overlap is below the bar and returns no rank at all.
        primary_to_primary = bool(
            normalized_primary and normalized_primary == existing_primary
        )
        primary_to_alias = bool(
            normalized_primary and normalized_primary in existing_aliases
        ) or bool(existing_primary and existing_primary in incoming_aliases)
        if not (primary_to_primary or primary_to_alias):
            return None
        status = getattr(character.status, "value", character.status)
        status_priority = {
            "CANONICAL": 0,
            "UPLOADED": 1,
            "NEEDS_CONFIRMATION": 2,
            "ANALYZED": 3,
        }
        return (
            0 if primary_to_primary else 1,
            status_priority.get(str(status), 4),
            character.created_at.isoformat() if character.created_at else "",
            character.id,
        )

    ranked = [
        (rank, character)
        for character in characters
        if (rank := match_rank(character)) is not None
    ]
    if not ranked:
        return None
    return min(ranked, key=lambda pair: pair[0])[1]


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
    # Defense in depth behind the route/planning-side guards (#124): jobs
    # created through disjoint idempotency-key namespaces (route parse vs
    # workflow agent.parse) can both be queued before either runs. The loser
    # must fail HERE — before any paid chunk call — instead of double-paying
    # and destructively rewriting the winner's committed script. Two claimants
    # taken in the same window each see the other ACTIVE, so a symmetric
    # "any active sibling blocks me" check killed both and left the chapter
    # with zero parses; the loser is therefore decided deterministically:
    # only the OLDEST active parse (created_at, tie-break id) proceeds, and
    # every younger claimant fails terminally before any paid call.
    oldest_id = oldest_active_job_id(
        db,
        job_type="SOURCE_PARSE",
        target_id=chapter.id,
        target_type="CHAPTER",
    )
    if oldest_id is not None and oldest_id != job.id:
        # Distinct from the retryable CONCURRENCY_LIMIT slot-wait marker: this
        # is a terminal same-chapter conflict, failed before any paid call.
        raise ProviderAdapterError(
            "SOURCE_PARSE_CONFLICT",
            "该章节已有进行中的剧本解析任务，本次重复解析已在调用模型前取消",
            retryable=False,
        )
    revision = db.get(SourceRevision, started_revision_id)
    segments = list(
        db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_revision_id == revision.id)
            .order_by(SourceSegment.ordinal)
        )
    )
    db.refresh(chapter, attribute_names=["deleted_at"])
    if chapter.deleted_at is not None:
        # Entry fence: a chapter deleted while this parse sat queued must not
        # pay for a single chunk (the wipe-time check below used to be the
        # only guard, after the entire chunked loop had already run).
        raise ProviderAdapterError(
            "CHAPTER_DELETED",
            "章节已删除，已取消本次剧本生成",
            retryable=False,
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
                    retryable=error.retryable,
                    retry_after_seconds=error.retry_after_seconds,
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
                        retryable=segment_error.retryable,
                        retry_after_seconds=segment_error.retry_after_seconds,
                    ) from segment_error
        db.refresh(chapter, attribute_names=["deleted_at"])
        if chapter.deleted_at is not None:
            # Mid-loop fence: a chapter deleted while earlier chunks were
            # already paid must not pay for the remaining ones.
            raise ProviderAdapterError(
                "CHAPTER_DELETED",
                "章节已删除，已取消本次剧本生成",
                retryable=False,
            )
        execution._ensure_job_not_cancelled(db, job)
    output = _merge_story_parse_outputs(chunk_outputs)
    output = _sanitize_story_parse_output(output)
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
        )[:DRAFT_ALIAS_MAX_ITEMS]
        normalized = [_normalize_name(item) for item in aliases]
        normalized_primary = _normalize_name(primary_name)
        conflict = any(
            token in all_aliases and all_aliases[token] != normalized_primary
            for token in [normalized_primary, *normalized]
        )
        for token in [normalized_primary, *normalized]:
            all_aliases.setdefault(token, normalized_primary)
        if character:
            # Per-character conditional claim (the same discipline as PATCH
            # /characters): the merge re-reads fresh state and retries a
            # bounded number of times, so a concurrent character PATCH that
            # committed after our snapshot is merged onto instead of clobbered.
            # On final loss we log and skip this character's alias merge — the
            # billed ScriptRevision still lands, and a re-parse can recover it.
            merged = False
            for _attempt in range(3):
                db.refresh(character)
                fresh_primary = character.primary_name.strip()
                fresh_aliases = list(
                    dict.fromkeys(
                        item.strip()
                        for item in [
                            *character.aliases,
                            draft.primary_name,
                            *draft.aliases,
                        ]
                        if item.strip()
                        and _normalize_name(item) != _normalize_name(fresh_primary)
                    )
                )[:DRAFT_ALIAS_MAX_ITEMS]
                fresh_normalized = [_normalize_name(item) for item in fresh_aliases]
                fresh_primary_normalized = _normalize_name(fresh_primary)
                fresh_conflict = any(
                    token in all_aliases and all_aliases[token] != fresh_primary_normalized
                    for token in [fresh_primary_normalized, *fresh_normalized]
                )
                claimed = db.execute(
                    update(Character)
                    .where(
                        Character.id == character.id,
                        Character.version == character.version,
                    )
                    .values(version=Character.version + 1)
                    .execution_options(synchronize_session=False)
                )
                if claimed.rowcount == 1:
                    character.aliases = fresh_aliases
                    character.aliases_normalized = fresh_normalized
                    character.alias_conflict = fresh_conflict or draft.alias_conflict
                    fresh_status = str(
                        getattr(character.status, "value", character.status) or ""
                    )
                    if (
                        fresh_status == "CANONICAL"
                        and (character.canonical_description or "").strip()
                    ):
                        # #240: a re-parse must not overwrite the user-curated
                        # canonical description of a confirmed character. The
                        # model text is parked in the log (there is no
                        # description-parking column on Character); the
                        # curated value stays authoritative.
                        if draft.description.strip():
                            LOGGER.info(
                                "story parse: kept curated canonical description of "
                                "character %s (model text skipped: %s)",
                                character.id,
                                draft.description[:200],
                            )
                    else:
                        character.canonical_description = (
                            draft.description or character.canonical_description
                        )
                    for token in [fresh_primary_normalized, *fresh_normalized]:
                        all_aliases[token] = fresh_primary_normalized
                    merged = True
                    break
            claimed_character_ids.add(character.id)
            if not merged:
                # Register the character's committed tokens even though the
                # merge was skipped: the map keeps only the stale snapshot
                # otherwise, and later drafts under-report alias conflicts
                # against the renamed character for the rest of this parse.
                register_unmerged_tokens(
                    all_aliases, fresh_primary_normalized, fresh_normalized
                )
                LOGGER.warning(
                    "story parse: character %s changed concurrently; "
                    "skipped its alias merge (script kept, re-parse to recover)",
                    character.id,
                )
        else:
            character = Character(
                project_id=project_id,
                primary_name=primary_name,
                aliases=aliases,
                aliases_normalized=normalized,
                alias_conflict=conflict or draft.alias_conflict,
                canonical_description=draft.description,
                status=(
                    "NEEDS_CONFIRMATION"
                    if (conflict or draft.alias_conflict)
                    else "ANALYZED"
                ),
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
    expected_segment_ids = {item.id for item in segments}
    covered_segment_ids: set[str] = set()
    for scene_draft in output.scenes:
        # #240: claimed segment ids are validated against the chunk inputs
        # before they can mark anything covered or persist into source_range —
        # a hallucinated id is neither coverage nor a traceable reference.
        scene_segment_ids = [
            segment_id
            for segment_id in scene_draft.source_segment_ids
            if segment_id in expected_segment_ids
        ]
        dropped_scene_ids = [
            segment_id
            for segment_id in scene_draft.source_segment_ids
            if segment_id not in expected_segment_ids
        ]
        if dropped_scene_ids:
            LOGGER.warning(
                "story parse: scene claimed %d segment ids outside the chapter "
                "input; dropped from source_range and coverage",
                len(dropped_scene_ids),
            )
        scene = Scene(
            chapter_id=chapter.id,
            ordinal=scene_draft.ordinal,
            location=scene_draft.location,
            time_label=scene_draft.time_label,
            weather=scene_draft.weather,
            purpose=scene_draft.purpose,
            emotional_arc=scene_draft.emotional_arc,
            source_range={"segment_ids": scene_segment_ids},
        )
        db.add(scene)
        db.flush()
        for beat_draft in scene_draft.beats:
            # #240: coverage is beat-level only. A scene claiming a segment
            # while emitting zero beats for it must NOT count as covered —
            # scene-level claims alone used to lie SCRIPT_READY/100%.
            beat_segment_ids = [
                segment_id
                for segment_id in beat_draft.source_segment_ids
                if segment_id in expected_segment_ids
            ]
            covered_segment_ids.update(beat_segment_ids)
            speaker_name = beat_draft.speaker_name.strip()
            if speaker_name:
                speaker = character_map.get(_normalize_name(speaker_name))
                speaker_name = speaker.primary_name if speaker else speaker_name
            beat_source_range = {
                "segment_ids": beat_segment_ids,
                "character_presence": {
                    key: value.value
                    for key, value in beat_draft.character_presence.items()
                },
                "props": beat_draft.props,
            }
            if beat_draft.presence_key_conflicts:
                # #240: normalized-key collisions are first-wins; the dropped
                # contenders ride along on the persisted row for audit.
                beat_source_range["presence_key_conflicts"] = list(
                    beat_draft.presence_key_conflicts
                )
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
                    source_range=beat_source_range,
                )
            )
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
    # #240: the blind ORM ``chapter.version += 1`` is a read-modify-write that
    # loses the bump to a concurrent writer. Claim the snapshot version with a
    # conditional UPDATE; on a lost claim the increment still lands (single
    # SQL expression), and the conflict becomes observable instead of silent.
    claimed_chapter = db.execute(
        update(Chapter)
        .where(Chapter.id == chapter.id, Chapter.version == chapter.version)
        .values(version=Chapter.version + 1)
        .execution_options(synchronize_session=False)
    )
    if claimed_chapter.rowcount != 1:
        LOGGER.warning(
            "story parse: chapter %s version changed concurrently during the parse; "
            "forcing the script-ready bump (conditional claim lost)",
            chapter.id,
        )
        db.execute(
            update(Chapter)
            .where(Chapter.id == chapter.id)
            .values(version=Chapter.version + 1)
            .execution_options(synchronize_session=False)
        )
    db.expire(chapter, ["version"])

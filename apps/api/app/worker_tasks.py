import hashlib
import json
import os
import socket
from datetime import timedelta
from threading import Event, Lock, Thread
from uuid import uuid4

from PIL import Image
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import SessionLocal
from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import (
    ImageRequest,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)
from app.models import (
    Asset,
    AssetCandidate,
    Beat,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    MangaPage,
    Outfit,
    PageCandidate,
    Project,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
    StyleProfile,
    WorkflowNodeRun,
    utcnow,
)
from app.services.ai_schemas import PageInspectionOutput, StoryParseOutput, StyleAnalysisOutput
from app.services.media import create_thumbnails, remove_thumbnails
from app.services.model_router import model_supports_resolution
from app.services.page_completion import (
    PASSING_QUALITY_OUTCOMES,
    REQUIRED_QUALITY_CATEGORIES,
    latest_inspections_by_category,
)
from app.services.prompt_compiler import compile_page_prompt
from app.services.worker_handlers import provider
from app.services.worker_handlers.execution import (
    JobCancelledError,
    JobLeaseLostError,
    StaleStoryboardVersionError,
    _commit_owned_progress,
    _ensure_job_not_cancelled,
    _lease_is_expired,
)
from app.services.worker_handlers.page_generate import _run_page_generate
from app.services.worker_handlers.provider import (
    _asset_path,
    _binding,
    _invoke_provider,
    _lease_reference_assets,
    _text_model_reference,
    _validate_reference_capacity,
)

ACTIVE_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}
CLAIMABLE_STATUSES = {JobStatus.WAITING, JobStatus.QUEUED}
EXECUTION_RESERVATION_LOCK = Lock()
STORY_PARSE_CHUNK_MAX_CHARS = 800


def _adapter(_alias: str):
    """Legacy test seam retained while production calls use catalog bindings."""

    return None


# Handlers bind models through ``provider._binding``; bridge this module's
# ``_adapter`` seam at call time so existing monkeypatches keep steering it.
provider.install_legacy_adapter_lookup(lambda alias: _adapter(alias))


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex}"


def _lease_duration() -> timedelta:
    return timedelta(seconds=get_settings().job_lease_seconds)


class _LeaseHeartbeat:
    """Refresh a job lease without sharing the worker's SQLAlchemy session."""

    def __init__(self, job_id: str, owner: str):
        self.job_id = job_id
        self.owner = owner
        self.duration = _lease_duration()
        self.interval = max(5.0, min(30.0, self.duration.total_seconds() / 3))
        self.stop = Event()
        self.lost = False
        self.thread: Thread | None = None

    def __enter__(self):
        self.thread = Thread(
            target=self._run,
            name=f"mangaflow-lease-{self.job_id[:8]}",
            daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=max(1.0, self.interval))

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                now = utcnow()
                with SessionLocal() as db:
                    updated = db.execute(
                        update(GenerationJob)
                        .where(
                            GenerationJob.id == self.job_id,
                            GenerationJob.lease_owner == self.owner,
                            GenerationJob.lease_expires_at.is_not(None),
                            GenerationJob.lease_expires_at > now,
                            GenerationJob.status.in_(ACTIVE_STATUSES),
                            GenerationJob.status.not_in(
                                {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
                            ),
                        )
                        .values(lease_expires_at=now + self.duration)
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
                    if updated.rowcount != 1:
                        self.lost = True
                        return
            except Exception:
                # A transient heartbeat failure should not turn a healthy
                # provider call into a second paid request.  The lease itself
                # remains the source of truth and will be reclaimed if it
                # eventually expires.
                if self.stop.wait(1.0):
                    return


def _claim_job(db, job_id: str, owner: str) -> GenerationJob | None:
    """Atomically claim one queued/retryable job for this worker."""

    job = db.get(GenerationJob, job_id)
    if not job or job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
        return None
    now = utcnow()
    expired = job.status in ACTIVE_STATUSES and _lease_is_expired(job.lease_expires_at)
    if job.status not in CLAIMABLE_STATUSES and not expired:
        return None
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    is_postgres = dialect_name == "postgresql"

    if is_postgres:
        project = db.scalar(
            select(Project)
            .where(Project.id == job.project_id)
            .with_for_update()
        )
    else:
        project = db.get(Project, job.project_id)

    if not project or project.deleted_at is not None:
        return None

    expected_status = job.status
    active_subquery = (
        select(func.count(GenerationJob.id))
        .where(
            GenerationJob.project_id == project.id,
            GenerationJob.id != job.id,
            GenerationJob.status.in_(ACTIVE_STATUSES),
            or_(
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at > now,
            ),
        )
        .scalar_subquery()
    )

    claim_filter = [
        GenerationJob.id == job.id,
        GenerationJob.attempt_count < GenerationJob.max_attempts,
        active_subquery < project.default_concurrency,
    ]
    if expired:
        claim_filter.append(GenerationJob.status == expected_status)
        if job.lease_owner is not None:
            claim_filter.append(GenerationJob.lease_owner == job.lease_owner)
        else:
            claim_filter.append(GenerationJob.lease_owner.is_(None))
        if job.lease_expires_at is not None:
            claim_filter.append(GenerationJob.lease_expires_at <= now)
        else:
            claim_filter.append(GenerationJob.lease_expires_at.is_(None))
    else:
        claim_filter.append(GenerationJob.status == expected_status)
    updated = db.execute(
        update(GenerationJob)
        .where(*claim_filter)
        .values(
            status=JobStatus.PREPARING,
            progress=5,
            attempt_count=GenerationJob.attempt_count + 1,
            started_at=func.coalesce(GenerationJob.started_at, now),
            error_code=None,
            error_message=None,
            lease_owner=owner,
            lease_expires_at=now + _lease_duration(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        # 在仍持有锁的事务中通过严格条件更新标记等待状态，绝不释放锁后无条件覆盖新租约
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                GenerationJob.status == expected_status,
                GenerationJob.lease_owner.is_(None),
                GenerationJob.lease_expires_at.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="CONCURRENCY_LIMIT",
                error_message="等待项目并发名额",
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return None
    db.commit()
    db.expire_all()
    return db.get(GenerationJob, job_id)


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
    revision = db.get(SourceRevision, chapter.current_source_revision_id)
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
    binding = _binding(
        db,
        operation="structured_text",
        project_id=project.id,
        explicit_reference=_text_model_reference(job, project),
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
        return _invoke_provider(
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
                        f"原文第 {segment.ordinal} 段被 Vertex 拒绝："
                        f"{segment_error.user_message}",
                    ) from segment_error
        _ensure_job_not_cancelled(db, job)
    output = _merge_story_parse_outputs(chunk_outputs)
    _ensure_job_not_cancelled(db, job)
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


def _save_asset_candidate(db, candidate: AssetCandidate, project_id: str, data: bytes) -> Asset:
    settings = get_settings()
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.sha256 == digest,
        )
    )
    if existing:
        return existing
    destination = (
        settings.storage_root
        / "generated"
        / project_id
        / candidate.batch_id
        / f"{candidate.id}.png"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        with Image.open(destination) as image:
            width, height = image.size
            mime_type = Image.MIME.get(image.format or "PNG", "image/png")
    except OSError:
        width = height = None
        mime_type = "image/png"
    batch = db.get(GenerationBatch, candidate.batch_id)
    try:
        with db.begin_nested():
            asset = Asset(
                project_id=project_id,
                kind=batch.generation_kind.lower(),
                original_name=(
                    f"{batch.generation_kind.lower()}-{candidate.variant.lower()}-"
                    f"{candidate.ordinal}.png"
                ),
                storage_key=destination.relative_to(settings.storage_root).as_posix(),
                mime_type=mime_type,
                byte_size=len(data),
                sha256=digest,
                width=width,
                height=height,
                source="AI_GENERATED",
                status="GENERATED",
            )
            db.add(asset)
            db.flush()
            thumbnails = create_thumbnails(destination, settings.storage_root, asset.id)
            asset.thumbnail_320_key = thumbnails[320]
            asset.thumbnail_640_key = thumbnails[640]
        return asset
    except IntegrityError:
        destination.unlink(missing_ok=True)
        existing = db.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.sha256 == digest,
            )
        )
        if existing:
            return existing
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        if "asset" in locals() and asset.id:
            remove_thumbnails(settings.storage_root, asset.id)
        raise


def _run_asset_generate(db, job: GenerationJob) -> None:
    candidate = db.get(AssetCandidate, job.target_id)
    if not candidate:
        raise RuntimeError("资产候选不存在")
    batch = db.get(GenerationBatch, candidate.batch_id)
    if not batch:
        raise RuntimeError("资产生成批次不存在")
    references: list[Asset] = []
    if batch.target_type == "CHARACTER":
        character = db.get(Character, batch.target_id)
        if not character:
            raise RuntimeError("角色档案不存在")
        references = list(
            db.scalars(
                select(Asset)
                .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                .where(
                    CharacterReference.character_id == character.id,
                    Asset.deleted_at.is_(None),
                    Asset.project_id == batch.project_id,
                    Asset.kind == "CHARACTER_REFERENCE",
                )
                .order_by(CharacterReference.is_canonical.desc())
            )
        )
        subject = {
            "primary_name": character.primary_name,
            "aliases": character.aliases,
            "description": character.canonical_description,
            "locked_features": character.locked_features,
        }
    elif batch.target_type == "OUTFIT":
        outfit = db.get(Outfit, batch.target_id)
        if not outfit:
            raise RuntimeError("服装档案不存在")
        character = db.get(Character, outfit.character_id)
        if not character:
            raise RuntimeError("服装所属角色不存在")
        character_references = list(
            db.scalars(
                select(Asset)
                .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                .where(
                    CharacterReference.character_id == character.id,
                    Asset.deleted_at.is_(None),
                    Asset.project_id == batch.project_id,
                    Asset.kind == "CHARACTER_REFERENCE",
                )
            )
        )
        outfit_references = (
            list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(outfit.reference_asset_ids),
                        Asset.project_id == batch.project_id,
                        Asset.deleted_at.is_(None),
                        Asset.kind == "OUTFIT_REFERENCE",
                    )
                )
            )
            if outfit.reference_asset_ids
            else []
        )
        references = [*character_references, *outfit_references]
        subject = {
            "character": character.primary_name,
            "outfit": outfit.name,
            "components": outfit.components,
            "state_rules": outfit.state_rules,
            "locked_fields": outfit.locked_fields,
        }
    elif batch.target_type == "STYLE":
        style = db.get(StyleProfile, batch.target_id)
        if not style:
            raise RuntimeError("风格档案不存在")
        reference_ids = style.profile.get("reference_asset_ids", [])
        references = (
            list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(reference_ids),
                        Asset.project_id == batch.project_id,
                        Asset.deleted_at.is_(None),
                        Asset.kind == "STYLE_REFERENCE",
                    )
                )
            )
            if reference_ids
            else []
        )
        subject = {
            "name": style.name,
            "color_mode": style.color_mode,
            "profile": style.profile,
            "locked_fields": style.locked_fields,
        }
    else:
        raise RuntimeError("资产生成目标类型无效")
    if batch.target_type == "OUTFIT":
        if not character_references:
            raise RuntimeError("服装所属角色的参考图已失效，请重新绑定后再生成")
        if not outfit_references:
            raise RuntimeError("服装参考图已失效，请重新绑定后再生成")
    if batch.target_type == "STYLE" and not references:
        raise RuntimeError("漫画风格参考图已失效，请重新绑定后再生成")
    prompt_payload = {
        "target_type": batch.target_type,
        "variant": candidate.variant,
        "instruction": candidate.instruction,
        "subject": subject,
    }
    asset_color_mode = subject.get("color_mode", "reference")
    task_instruction = (
        "在同一张角色设定页中同时展示正面、侧面、背面、代表性表情和关键局部细节；"
        "所有视图必须是同一个角色，版面清楚但不生成任何文字标签。"
        if candidate.variant == "SHEET"
        else (
            "在同一张服装角色设定页中展示角色穿着指定服装的正面、侧面、背面、"
            "代表性表情和服装关键局部；同时服从人物与服装参考，不得改变角色身份。"
            if candidate.variant == "OUTFIT_SHEET"
            else {
                "CHARACTER": (
                    "按 variant 生成同一角色的单一标准视图；"
                    "必须保持脸、发型、体型和标志特征一致。"
                ),
                "OUTFIT": (
                    "生成该角色穿着指定服装的完整全身造型图；同时服从人物参考和服装参考，"
                    "准确还原服装剪裁、层次、配饰与状态，不改变角色身份。"
                ),
                "STYLE": (
            "生成不含现有作品角色的原创风格测试页，用简单人物与背景验证线稿、"
            + (
                "网点、黑白对比和分格语言，"
                if asset_color_mode == "monochrome"
                else "色板、上色方式、光影层次和分格语言，"
            )
            + "不复制参考漫画的文字与剧情。"
                ),
            }[batch.target_type]
        )
    )
    base_instruction = (
        "生成黑白日式漫画规范资产图。"
        if batch.target_type == "STYLE" and asset_color_mode == "monochrome"
        else "生成彩色日式漫画规范资产图。"
        if batch.target_type == "STYLE"
        else "生成漫画制作规范资产图，色彩与明暗服从参考素材。"
    )
    prompt = (
        base_instruction
        + task_instruction
        + "不要加入文字水印；背景使用便于比对的简洁浅色。输入："
        + json.dumps(prompt_payload, ensure_ascii=False)
    )
    checksum = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    candidate.prompt_snapshot = {
        "template": "asset-v1.1.0",
        "checksum": checksum,
        "input": prompt_payload,
        "prompt_preview": prompt,
    }
    candidate.status = "GENERATING"
    _commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=45)
    reference_ids = [asset.id for asset in references]
    _lease_reference_assets(db, job, reference_ids)
    for asset in references:
        if not _asset_path(asset).is_file():
            raise RuntimeError(f"参考图文件不存在：{asset.original_name}")
    reference_bytes = [_asset_path(asset).read_bytes() for asset in references]
    reference_types = [asset.mime_type for asset in references]
    binding = _binding(
        db,
        operation="image_edit" if reference_bytes else "image_generate",
        project_id=batch.project_id,
        explicit_reference=(
            candidate.catalog_model_id or job.catalog_model_id or candidate.model_alias
        ),
        task_kind=job.job_type,
    )
    candidate.catalog_model_id = binding.resolved.model.id
    job.catalog_model_id = binding.resolved.model.id
    if not model_supports_resolution(binding.resolved.model, candidate.resolution.value):
        raise ProviderAdapterError(
            "UNSUPPORTED_CAPABILITY", "所选模型不支持当前输出清晰度"
        )
    _validate_reference_capacity(binding, len(reference_bytes))
    db.commit()
    response = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.generate_asset(
            ImageRequest(
                prompt=prompt,
                resolution=candidate.resolution.value,
                aspect_ratio=(
                    "4:3"
                    if candidate.variant in {"SHEET", "OUTFIT_SHEET"}
                    else "3:4"
                ),
                reference_images=tuple(reference_bytes),
                reference_mime_types=tuple(reference_types),
            )
        )
    )
    _ensure_job_not_cancelled(db, job)
    asset = _save_asset_candidate(db, candidate, batch.project_id, response.images[0])
    record = GenerationRecord(
        job_id=job.id,
        provider=(binding.resolved.provider.preset_key or binding.resolved.provider.name)[:32],
        model_id=response.model_id,
        catalog_model_id=binding.resolved.model.id,
        location=str(
            binding.resolved.connection.nonsecret_config.get("region", "global")
        )[:64],
        parameters={
            "resolution": candidate.resolution.value,
            "variant": candidate.variant,
            "protocol": binding.resolved.connection.protocol,
            "route_reason": binding.resolved.route_reason,
            "route_score": binding.resolved.route_score,
        },
        prompt_template="asset-v1.1.0",
        prompt_version="asset-v1.1.0",
        prompt_checksum=checksum,
        input_versions={"batch": batch.version},
        reference_asset_ids=[asset.id for asset in references],
        provider_request_id=response.request_id,
        finished_at=utcnow(),
        usage=response.usage,
        output_asset_ids=[asset.id],
        status="COMPLETED",
    )
    db.add(record)
    db.flush()
    candidate.asset_id = asset.id
    candidate.generation_record_id = record.id
    candidate.status = "READY"
    if batch.target_type == "STYLE" and candidate.variant == "STYLE_TEST":
        style = db.get(StyleProfile, batch.target_id)
        if style:
            style.status = "TEST_GENERATED"
            style.version += 1


def _build_style_prompt_summary(analyzed: dict, color_mode: str) -> str:
    """Compile visual language without leaking subjects from the reference page."""

    prefix = "彩色日式漫画" if color_mode == "color" else "黑白日式漫画"
    visual_parts = [
        analyzed.get("line_art", ""),
        analyzed.get("screentone", ""),
        analyzed.get("contrast", ""),
        analyzed.get("panel_language", ""),
        analyzed.get("lighting", ""),
    ]
    return "；".join([prefix, *(part for part in visual_parts if part)])


def _build_color_palette(analyzed: dict) -> dict[str, str]:
    """Recover an editable palette when the model omits the optional palette object."""

    palette = analyzed.get("palette")
    if isinstance(palette, dict) and palette:
        return {str(key): str(value) for key, value in palette.items() if str(value).strip()}

    color_rules = [str(rule) for rule in analyzed.get("color_rules", []) if str(rule).strip()]
    return {
        "主色": color_rules[0] if color_rules else "低饱和冷灰蓝，保持克制与潮湿感",
        "辅助色": "低明度卡其灰与雾紫，只用于小面积识别和层次",
        "肤色": "偏冷的自然肤色，保留血色但避免过度红润",
        "发色": "深黑与低明度识别色，保留发丝层次和角色辨识度",
        "环境色": "潮湿京都的蓝灰、纸门米灰与深木色",
        "光影色": analyzed.get("lighting") or "柔和冷色散射光，阴影不使用纯黑硬切",
    }


def _run_style_analyze(db, job: GenerationJob) -> None:
    style = db.get(StyleProfile, job.target_id)
    if not style:
        raise RuntimeError("风格档案不存在")
    reference_ids = style.profile.get("reference_asset_ids", [])
    references = list(
        db.scalars(
            select(Asset).where(
                Asset.id.in_(reference_ids),
                Asset.deleted_at.is_(None),
                Asset.kind == "STYLE_REFERENCE",
            )
        )
    )
    if not references:
        raise RuntimeError("风格档案没有可用漫画参考图")
    _commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=35)
    visual_dimensions = (
        "线稿、网点、黑白对比、留白、人物画法、背景画法、光影"
        if style.color_mode == "monochrome"
        else "线稿、主辅色板、肤色与发色、上色方式、色彩光影、人物画法、背景画法"
    )
    atmosphere = job.request_parameters.get("palette_atmosphere", "")
    prompt = f"""分析这些漫画参考页的视觉风格，只总结可复用的画面语言，不识别作者姓名或作品名。
目标输出类型是{'黑白漫画' if style.color_mode == 'monochrome' else '彩色漫画'}。
输出{visual_dimensions}、日式分格语言、构图规则、禁止项，
以及一段可直接用于生图的中文 prompt_summary。彩色模式必须额外输出 palette，包含
主色、辅助色、肤色、发色、环境色和光影色，并输出 color_rules。
章节氛围补充：{atmosphere or '葬礼后的克制、潮湿京都与低饱和情绪'}。
不要复制参考页中的文字或剧情。"""
    _lease_reference_assets(db, job, [asset.id for asset in references[:8]])
    project = db.get(Project, style.project_id)
    binding = _binding(
        db,
        operation="multimodal_analysis",
        project_id=style.project_id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=tuple(_asset_path(asset).read_bytes() for asset in references[:8]),
                mime_types=tuple(asset.mime_type for asset in references[:8]),
            ),
            StyleAnalysisOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    analyzed = output.model_dump()
    analyzed["prompt_summary"] = _build_style_prompt_summary(analyzed, style.color_mode)
    analyzed["reference_asset_ids"] = reference_ids
    analyzed["palette_draft"] = (
        _build_color_palette(analyzed) if style.color_mode == "color" else {}
    )
    analyzed.pop("palette", None)
    analyzed["palette_confirmed"] = False
    analyzed["test_image_approved"] = False
    style.profile = analyzed
    if style.color_mode == "color":
        style.locked_fields = [
            "细腻线稿" if field == "黑白墨线" else field
            for field in style.locked_fields
            if field != "禁止彩色"
        ]
        if "低饱和色板" not in style.locked_fields:
            style.locked_fields = [*style.locked_fields, "低饱和色板"]
    style.status = "DRAFT"
    style.version += 1
    job.progress = 90


def _run_inspection(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate or not candidate.asset_id:
        raise RuntimeError("候选图片尚未生成")
    page = db.get(MangaPage, candidate.page_id)
    asset = db.get(Asset, candidate.asset_id)
    project = db.get(Project, db.get(Chapter, page.chapter_id).project_id)
    inspection_storyboard_version = page.storyboard_version
    _, snapshot = compile_page_prompt(db, page, project)
    categories = job.request_parameters.get(
        "categories",
        ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"],
    )
    prompt = f"""你是漫画成片质检员。对照结构化目标检查这张生成漫画页。
只检查这些类别：{json.dumps(categories, ensure_ascii=False)}。
目标剧本、格位、说话人、角色、服装与风格上下文：
{json.dumps(snapshot["input"], ensure_ascii=False, separators=(",", ":"))}
SPEAKER 检查气泡归属；
CHARACTER 检查脸、发型、体型和标志特征；OUTFIT 检查场景指定服装；
PROP 检查关键道具；CONTINUITY 检查与页面结构、场景状态和前后逻辑的一致性。
每个请求类别至少输出一项，字段为 category、outcome、score、severity、details、regions；
outcome 只能用 PASS、ACCEPTABLE、MISMATCH、MISSING、EXTRA；
details 必须写清 expected、observed 和 differences。
regions 使用 0 到 1 的归一化 x/y/width/height。"""
    _commit_owned_progress(
        db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=45
    )
    _lease_reference_assets(db, job, [asset.id])
    binding = _binding(
        db,
        operation="multimodal_analysis",
        project_id=project.id,
        explicit_reference=_text_model_reference(job, project),
        task_kind=job.job_type,
    )
    job.catalog_model_id = binding.resolved.model.id
    output = _invoke_provider(
        db,
        binding,
        lambda adapter: adapter.analyze_multimodal(
            MultimodalRequest(
                prompt=prompt,
                images=(_asset_path(asset).read_bytes(),),
                mime_types=(asset.mime_type,),
            ),
            PageInspectionOutput,
        ),
    )
    _ensure_job_not_cancelled(db, job)
    valid_outcomes = {
        "MATCH",
        "PASS",
        "ACCEPTABLE",
        "MISMATCH",
        "MISSING",
        "EXTRA",
    }
    passing_outcomes = {"MATCH", "PASS", "ACCEPTABLE"}
    requested = [str(item) for item in categories]
    seen: dict[str, object] = {}
    needs_review = False
    for item in output.items:
        category = str(item.category)
        if category not in requested:
            continue
        if item.outcome not in valid_outcomes:
            raise RuntimeError("质检结果包含非法 outcome")
        seen[category] = item
        if item.outcome not in passing_outcomes:
            needs_review = True
        db.add(
            InspectionResult(
                generation_record_id=candidate.generation_record_id,
                candidate_id=candidate.id,
                storyboard_version=inspection_storyboard_version,
                category=item.category,
                outcome=item.outcome,
                score=item.score,
                details=item.details.model_dump(),
                regions=item.regions,
                severity=item.severity,
            )
        )
    db.flush()
    db.refresh(page)
    if page.storyboard_version != inspection_storyboard_version:
        # Preserve the audit result, but never pass a newer storyboard with an old response.
        _commit_owned_progress(db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=85)
        return
    latest = latest_inspections_by_category(db, candidate.id, inspection_storyboard_version)
    complete = (
        bool(seen)
        and set(requested) <= set(seen)
        and set(REQUIRED_QUALITY_CATEGORIES) <= set(latest)
    )
    needs_review = needs_review or any(
        latest[category].outcome not in PASSING_QUALITY_OUTCOMES
        for category in REQUIRED_QUALITY_CATEGORIES
        if category in latest
    )
    if not complete:
        candidate.status = "READY"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            page.continuity_status = "NOT_CHECKED"
            page.version += 1
    elif needs_review:
        candidate.status = "NEEDS_REVIEW"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            page.continuity_status = "NEEDS_REVIEW"
            page.status = PageStatus.NEEDS_REPAIR
            page.version += 1
    else:
        candidate.status = "INSPECTED"
        if page.selected_candidate_id == candidate.id and candidate.is_selected:
            page.continuity_status = "PASSED"
            page.status = PageStatus.FINAL_READY
            page.version += 1
    _commit_owned_progress(
        db, job, status=JobStatus.CONSISTENCY_CHECKING, progress=85
    )


def _mark_worker_failure(
    db,
    job_id: str,
    owner: str,
    error_code: str,
    error_message: str,
    *,
    candidate_status: str = "FAILED",
    retryable: bool = False,
) -> tuple[bool, str | None, bool]:
    """Persist failure output or reset for retry while worker holds valid lease.

    Returns (updated, workflow_run_id, is_final_failure).
    """

    now = utcnow()
    job = db.get(GenerationJob, job_id)
    if not job:
        return False, None, False

    is_retryable = bool(retryable and (job.attempt_count < job.max_attempts))
    target_status = JobStatus.WAITING if is_retryable else JobStatus.FAILED

    updated = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.lease_owner == owner,
            GenerationJob.lease_expires_at.is_not(None),
            GenerationJob.lease_expires_at > now,
            GenerationJob.status.in_(ACTIVE_STATUSES),
            GenerationJob.status != JobStatus.CANCELLED,
        )
        .values(
            status=target_status,
            error_code=error_code,
            error_message=error_message[:500],
            finished_at=None if is_retryable else now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        return False, None, False

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id

    if not is_retryable:
        page_candidate = db.get(PageCandidate, job.target_id)
        if page_candidate:
            page_candidate.status = candidate_status
        asset_candidate = db.get(AssetCandidate, job.target_id)
        if asset_candidate:
            asset_candidate.status = "FAILED"
        style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
        if style:
            style.status = "DRAFT"

        if node_run and node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            node_run.status = "FAILED"
            node_run.error_code = error_code
            node_run.error_message = error_message[:500]
            node_run.finished_at = now

    db.commit()
    db.expire_all()
    return True, workflow_run_id, not is_retryable


def _defer_concurrency_wait(job_id: str) -> None:
    """Keep a slot-wait job schedulable instead of silently succeeding out of RQ."""

    from rq import Queue, get_current_job

    from app.services.job_service import rq_retry_policy

    current = get_current_job()
    if current is None:
        # LOCAL/AUTO's local executor already owns a bounded backoff loop.
        return
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if (
            job is None
            or job.status != JobStatus.WAITING
            or job.error_code != "CONCURRENCY_LIMIT"
        ):
            return
        retry = rq_retry_policy(job)
    # Use the running worker's queue/connection. A child-local thread cannot survive RQ exit.
    # Let a scheduling failure reach RQ's retry/error handling instead of hiding it.
    Queue(current.origin, connection=current.connection).enqueue_in(
        timedelta(seconds=3),
        "app.worker_tasks.execute_job",
        job_id,
        job_id=f"{job_id}-slot-{uuid4().hex}",
        job_timeout=settings.job_timeout_seconds,
        retry=retry,
    )


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    owner = _worker_id()
    db.info["job_lease_owner"] = owner
    try:
        job = db.get(GenerationJob, job_id)
        if not job or job.status == JobStatus.CANCELLED:
            return
        project = db.get(Project, job.project_id)
        if not project or project.deleted_at is not None:
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
            return
        db.info["job_id"] = job_id
        with EXECUTION_RESERVATION_LOCK:
            job = _claim_job(db, job_id, owner)
        if not job:
            _defer_concurrency_wait(job_id)
            return
        with _LeaseHeartbeat(job.id, owner) as heartbeat:
            if job.job_type in {"PAGE_GENERATE", "PAGE_REPAIR", "PAGE_UPSCALE"}:
                _run_page_generate(db, job)
            elif job.job_type == "ASSET_GENERATE":
                _run_asset_generate(db, job)
            elif job.job_type == "SOURCE_PARSE":
                _run_story_parse(db, job)
            elif job.job_type == "STYLE_ANALYZE":
                _run_style_analyze(db, job)
            elif job.job_type == "PAGE_INSPECT":
                _run_inspection(db, job)
            elif job.job_type == "WORKFLOW_NODE":
                from app.services.workflow_engine import execute_workflow_node

                execute_workflow_node(db, job)
            else:
                raise RuntimeError(f"未知任务类型：{job.job_type}")
            _ensure_job_not_cancelled(db, job)
            if heartbeat.lost:
                raise JobLeaseLostError("任务租约已被其他执行器接管")
            workflow_run_id = job.request_parameters.get("workflow_run_id")
            db.expire(
                job,
                attribute_names=[
                    "status",
                    "progress",
                    "finished_at",
                    "error_code",
                    "error_message",
                    "lease_owner",
                    "lease_expires_at",
                ],
            )
            with db.no_autoflush:
                completed = db.execute(
                    update(GenerationJob)
                    .where(
                        GenerationJob.id == job.id,
                        GenerationJob.lease_owner == owner,
                        GenerationJob.status != JobStatus.CANCELLED,
                    )
                    .values(
                        status=JobStatus.COMPLETED,
                        progress=100,
                        finished_at=utcnow(),
                        error_code=None,
                        error_message=None,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            if completed.rowcount != 1:
                current = db.get(GenerationJob, job.id)
                if current and current.status == JobStatus.CANCELLED:
                    raise JobCancelledError("任务已取消，完成状态不再写入")
                raise JobLeaseLostError("任务租约已被其他执行器接管")
            db.commit()
        if workflow_run_id:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
    except JobLeaseLostError:
        db.rollback()
        return
    except JobCancelledError:
        db.rollback()
        db.expire_all()
        job = db.get(GenerationJob, job_id)
        if job and job.status != JobStatus.COMPLETED and (
            job.status == JobStatus.CANCELLED or job.lease_owner == owner
        ):
            from app.services.job_service import mark_job_cancelled

            mark_job_cancelled(db, job)
            db.commit()
        return
    except StaleStoryboardVersionError as error:
        db.rollback()
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            "STALE_STORYBOARD_VERSION",
            str(error),
            candidate_status="STALE",
            retryable=False,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    except ProviderAdapterError as error:
        db.rollback()
        is_retryable = getattr(error, "retryable", True)
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            error.code,
            error.user_message,
            retryable=is_retryable,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    except Exception as error:
        db.rollback()
        marked, workflow_run_id, is_final = _mark_worker_failure(
            db,
            job_id,
            owner,
            "WORKER_ERROR",
            str(error),
            retryable=True,
        )
        if not marked:
            return
        if workflow_run_id and is_final:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        raise
    finally:
        db.close()

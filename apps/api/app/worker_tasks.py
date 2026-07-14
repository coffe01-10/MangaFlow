import hashlib
import json
from pathlib import Path

from PIL import Image
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.database import SessionLocal
from app.domain.states import JobStatus, PageStatus
from app.model_adapters.base import ImageRequest, MultimodalRequest, StructuredRequest
from app.model_adapters.vertex import VertexAdapterError, VertexImageAdapter, VertexTextAdapter
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
    utcnow,
)
from app.services.ai_schemas import PageInspectionOutput, StoryParseOutput
from app.services.model_registry import build_registry
from app.services.prompt_compiler import PAGE_TEMPLATE_VERSION, compile_page_prompt

ACTIVE_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}


def _normalize_name(value: str) -> str:
    return "".join(value.split()).casefold()


def _asset_path(asset: Asset) -> Path:
    settings = get_settings()
    root = settings.upload_root if asset.source == "USER_UPLOAD" else settings.storage_root
    path = (root / asset.storage_key).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("素材路径越界")
    return path


def _adapter(alias: str):
    settings = get_settings()
    capability = build_registry(settings).get(alias)
    if not capability:
        raise VertexAdapterError("UNSUPPORTED_CAPABILITY", "未识别的模型选项")
    if alias.startswith("image."):
        return VertexImageAdapter(settings, capability)
    return VertexTextAdapter(settings, capability)


def _load_reference_assets(db, page: MangaPage, project: Project) -> list[Asset]:
    references = list(
        db.scalars(
            select(Asset)
            .join(CharacterReference, CharacterReference.asset_id == Asset.id)
            .join(Character, Character.id == CharacterReference.character_id)
            .where(
                Character.project_id == project.id,
                Asset.deleted_at.is_(None),
            )
            .order_by(CharacterReference.is_canonical.desc(), CharacterReference.created_at)
            .limit(10)
        )
    )
    previous = db.scalar(
        select(MangaPage).where(
            MangaPage.chapter_id == page.chapter_id,
            MangaPage.page_number == page.page_number - 1,
        )
    )
    if previous and previous.selected_candidate_id:
        candidate = db.get(PageCandidate, previous.selected_candidate_id)
        if candidate and candidate.asset_id:
            previous_asset = db.get(Asset, candidate.asset_id)
            if previous_asset:
                references.append(previous_asset)
    return references[:14]


def _save_generated_asset(db, candidate: PageCandidate, data: bytes) -> Asset:
    settings = get_settings()
    page = db.get(MangaPage, candidate.page_id)
    chapter = db.get(Chapter, page.chapter_id)
    digest = hashlib.sha256(data).hexdigest()
    destination = (
        settings.storage_root
        / "generated"
        / chapter.project_id
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
    asset = Asset(
        project_id=chapter.project_id,
        kind="page_candidate",
        original_name=f"page-{page.page_number}-candidate-{candidate.ordinal}.png",
        storage_key=destination.relative_to(settings.storage_root).as_posix(),
        mime_type=mime_type,
        byte_size=len(data),
        sha256=digest,
        width=width,
        height=height,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add(asset)
    db.flush()
    return asset


def _save_asset_candidate(db, candidate: AssetCandidate, project_id: str, data: bytes) -> Asset:
    settings = get_settings()
    digest = hashlib.sha256(data).hexdigest()
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
    asset = Asset(
        project_id=project_id,
        kind=batch.generation_kind.lower(),
        original_name=f"{batch.generation_kind.lower()}-{candidate.variant.lower()}-{candidate.ordinal}.png",
        storage_key=destination.relative_to(settings.storage_root).as_posix(),
        mime_type=mime_type,
        byte_size=len(data),
        sha256=digest,
        width=width,
        height=height,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add(asset)
    db.flush()
    return asset


def _run_page_generate(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate:
        raise RuntimeError("候选记录不存在")
    page = db.get(MangaPage, candidate.page_id)
    chapter = db.get(Chapter, page.chapter_id)
    project = db.get(Project, chapter.project_id)
    if not page.scene_ids or not page.beat_ids:
        raise RuntimeError("页面缺少剧本与分镜来源，禁止生成")
    if not page.source_coverage.get("complete"):
        raise RuntimeError("页面原文覆盖不完整，禁止生成")

    prompt, snapshot = compile_page_prompt(db, page, project)
    candidate.prompt_snapshot = snapshot
    candidate.status = "GENERATING"
    page.status = PageStatus.DRAFT_GENERATING
    job.status = JobStatus.UPLOADING_REFERENCES
    job.progress = 20
    db.commit()

    reference_assets = _load_reference_assets(db, page, project)
    reference_bytes: list[bytes] = []
    reference_types: list[str] = []
    for asset in reference_assets:
        path = _asset_path(asset)
        if path.is_file():
            reference_bytes.append(path.read_bytes())
            reference_types.append(asset.mime_type)

    if job.job_type == "PAGE_REPAIR":
        original = db.get(PageCandidate, job.request_parameters.get("original_candidate_id"))
        if not original or not original.asset_id:
            raise RuntimeError("修复任务缺少原始候选图")
        original_asset = db.get(Asset, original.asset_id)
        reference_bytes.insert(0, _asset_path(original_asset).read_bytes())
        reference_types.insert(0, original_asset.mime_type)
        prompt += "\n仅修复指定区域，保持其他人物、背景、格线、文字和构图不变。"

    job.status = JobStatus.GENERATING
    job.progress = 45
    db.commit()
    response = _adapter(candidate.model_alias).generate_page(
        ImageRequest(
            prompt=prompt,
            resolution=candidate.resolution.value,
            aspect_ratio="3:4",
            reference_images=tuple(reference_bytes[:14]),
            reference_mime_types=tuple(reference_types[:14]),
        )
    )
    asset = _save_generated_asset(db, candidate, response.images[0])
    record = GenerationRecord(
        job_id=job.id,
        model_id=response.model_id,
        location=get_settings().google_cloud_location,
        parameters={"resolution": candidate.resolution.value, "aspect_ratio": "3:4"},
        prompt_template=PAGE_TEMPLATE_VERSION,
        prompt_version=PAGE_TEMPLATE_VERSION,
        prompt_checksum=snapshot["checksum"],
        input_versions={"page": page.version, "page_revision": page.revision_no},
        reference_asset_ids=[asset.id for asset in reference_assets],
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
    page.status = PageStatus.DRAFT_READY
    page.version += 1


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
    source_payload = [
        {"id": item.id, "ordinal": item.ordinal, "text": item.text} for item in segments
    ]
    prompt = """逐段解析以下中文小说，禁止总结、删除或合并关键内容。
提取角色、主要姓名、绰号、场景和情节拍。
所有情节拍必须携带输入中的 source_segment_ids；剧本对白中的人物名称必须使用 primary_name。
输入：""" + json.dumps(source_payload, ensure_ascii=False)
    output = _adapter("text.fast").generate_structured(
        StructuredRequest(
            prompt=prompt,
            system_instruction="你是忠实的漫画剧本结构化编辑，原文覆盖率优先于篇幅。",
            temperature=0.15,
        ),
        StoryParseOutput,
    )
    project_id = chapter.project_id
    all_aliases: dict[str, str] = {}
    for draft in output.characters:
        normalized_primary = _normalize_name(draft.primary_name)
        character = db.scalar(
            select(Character).where(
                Character.project_id == project_id,
                Character.primary_name == draft.primary_name,
            )
        )
        aliases = list(dict.fromkeys(item.strip() for item in draft.aliases if item.strip()))
        normalized = [_normalize_name(item) for item in aliases]
        conflict = any(
            alias in all_aliases and all_aliases[alias] != normalized_primary
            for alias in normalized
        )
        for alias in normalized:
            all_aliases.setdefault(alias, normalized_primary)
        if character:
            character.aliases = aliases
            character.aliases_normalized = normalized
            character.alias_conflict = conflict
            character.canonical_description = draft.description
            character.version += 1
        else:
            db.add(
                Character(
                    project_id=project_id,
                    primary_name=draft.primary_name,
                    aliases=aliases,
                    aliases_normalized=normalized,
                    alias_conflict=conflict,
                    canonical_description=draft.description,
                    status="NEEDS_CONFIRMATION" if conflict else "ANALYZED",
                )
            )
    db.flush()
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
            purpose=scene_draft.purpose,
            emotional_arc=scene_draft.emotional_arc,
            source_range={"segment_ids": scene_draft.source_segment_ids},
        )
        db.add(scene)
        db.flush()
        for beat_draft in scene_draft.beats:
            covered_segment_ids.update(beat_draft.source_segment_ids)
            db.add(
                Beat(
                    scene_id=scene.id,
                    ordinal=beat_draft.ordinal,
                    action=beat_draft.action,
                    dialogue=beat_draft.dialogue,
                    narration=beat_draft.narration,
                    emotion=beat_draft.emotion,
                    source_range={"segment_ids": beat_draft.source_segment_ids},
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


def _run_asset_generate(db, job: GenerationJob) -> None:
    candidate = db.get(AssetCandidate, job.target_id)
    if not candidate:
        raise RuntimeError("资产候选不存在")
    batch = db.get(GenerationBatch, candidate.batch_id)
    references: list[Asset] = []
    if batch.target_type == "CHARACTER":
        character = db.get(Character, batch.target_id)
        references = list(
            db.scalars(
                select(Asset)
                .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                .where(CharacterReference.character_id == character.id)
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
        character = db.get(Character, outfit.character_id)
        references = list(
            db.scalars(
                select(Asset)
                .join(CharacterReference, CharacterReference.asset_id == Asset.id)
                .where(CharacterReference.character_id == character.id)
            )
        )
        subject = {
            "character": character.primary_name,
            "outfit": outfit.name,
            "components": outfit.components,
            "state_rules": outfit.state_rules,
            "locked_fields": outfit.locked_fields,
        }
    elif batch.target_type == "STYLE":
        style = db.get(StyleProfile, batch.target_id)
        reference_ids = style.profile.get("reference_asset_ids", [])
        references = (
            list(db.scalars(select(Asset).where(Asset.id.in_(reference_ids))))
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
    prompt_payload = {
        "target_type": batch.target_type,
        "variant": candidate.variant,
        "instruction": candidate.instruction,
        "subject": subject,
    }
    prompt = (
        "生成黑白日式漫画规范资产图。严格保持参考图中的身份、脸部与锁定特征。"
        "不要加入文字水印；背景使用便于比对的简洁浅色。输入："
        + json.dumps(prompt_payload, ensure_ascii=False)
    )
    checksum = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    candidate.prompt_snapshot = {
        "template": "asset-v1.0.0",
        "checksum": checksum,
        "input": prompt_payload,
    }
    candidate.status = "GENERATING"
    job.status = JobStatus.GENERATING
    job.progress = 45
    db.commit()
    reference_bytes = [_asset_path(asset).read_bytes() for asset in references[:14]]
    reference_types = [asset.mime_type for asset in references[:14]]
    response = _adapter(candidate.model_alias).generate_asset(
        ImageRequest(
            prompt=prompt,
            resolution=candidate.resolution.value,
            aspect_ratio="3:4",
            reference_images=tuple(reference_bytes),
            reference_mime_types=tuple(reference_types),
        )
    )
    asset = _save_asset_candidate(db, candidate, batch.project_id, response.images[0])
    record = GenerationRecord(
        job_id=job.id,
        model_id=response.model_id,
        location=get_settings().google_cloud_location,
        parameters={"resolution": candidate.resolution.value, "variant": candidate.variant},
        prompt_template="asset-v1.0.0",
        prompt_version="asset-v1.0.0",
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


def _run_inspection(db, job: GenerationJob) -> None:
    candidate = db.get(PageCandidate, job.target_id)
    if not candidate or not candidate.asset_id:
        raise RuntimeError("候选图片尚未生成")
    page = db.get(MangaPage, candidate.page_id)
    asset = db.get(Asset, candidate.asset_id)
    expected = "\n".join(item.get("text", "") for item in page.source_coverage.get("ranges", []))
    prompt = f"""检查这张漫画页。目标文字如下：\n{expected}\n
检查错字、漏字、文字顺序、说话人、角色身份、服装、标志特征、道具和连续性。
每项输出 category、outcome、score、severity、details、regions。"""
    job.status = JobStatus.OCR_CHECKING
    job.progress = 45
    db.commit()
    output = _adapter("text.fast").analyze_multimodal(
        MultimodalRequest(
            prompt=prompt,
            images=(_asset_path(asset).read_bytes(),),
            mime_types=(asset.mime_type,),
        ),
        PageInspectionOutput,
    )
    needs_review = False
    for item in output.items:
        if item.outcome not in {"MATCH", "PASS", "ACCEPTABLE"}:
            needs_review = True
        db.add(
            InspectionResult(
                generation_record_id=candidate.generation_record_id,
                candidate_id=candidate.id,
                category=item.category,
                outcome=item.outcome,
                score=item.score,
                details=item.details,
                regions=item.regions,
                severity=item.severity,
            )
        )
    candidate.status = "NEEDS_REVIEW" if needs_review else "INSPECTED"
    page.continuity_status = "NEEDS_REVIEW" if needs_review else "PASSED"
    job.status = JobStatus.CONSISTENCY_CHECKING
    job.progress = 85


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    job = db.get(GenerationJob, job_id)
    if not job or job.status == JobStatus.CANCELLED:
        db.close()
        return
    try:
        project = db.get(Project, job.project_id)
        active = (
            db.scalar(
                select(func.count(GenerationJob.id)).where(
                    GenerationJob.project_id == job.project_id,
                    GenerationJob.status.in_(ACTIVE_STATUSES),
                    GenerationJob.id != job.id,
                )
            )
            or 0
        )
        if project and active >= project.default_concurrency:
            job.status = JobStatus.WAITING
            job.error_code = "CONCURRENCY_LIMIT"
            job.error_message = "等待项目并发名额"
            db.commit()
            return
        job.status = JobStatus.PREPARING
        job.progress = 5
        job.attempt_count += 1
        job.started_at = job.started_at or utcnow()
        db.commit()
        if job.job_type in {"PAGE_GENERATE", "PAGE_REPAIR"}:
            _run_page_generate(db, job)
        elif job.job_type == "ASSET_GENERATE":
            _run_asset_generate(db, job)
        elif job.job_type == "SOURCE_PARSE":
            _run_story_parse(db, job)
        elif job.job_type == "PAGE_INSPECT":
            _run_inspection(db, job)
        else:
            raise RuntimeError(f"未知任务类型：{job.job_type}")
        job.status = JobStatus.COMPLETED
        job.progress = 100
        job.finished_at = utcnow()
        job.error_code = None
        job.error_message = None
        db.commit()
    except VertexAdapterError as error:
        db.rollback()
        job = db.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.error_code = error.code
        job.error_message = error.user_message
        job.finished_at = utcnow()
        candidate = db.get(PageCandidate, job.target_id)
        if candidate:
            candidate.status = "FAILED"
        asset_candidate = db.get(AssetCandidate, job.target_id)
        if asset_candidate:
            asset_candidate.status = "FAILED"
        db.commit()
        raise
    except Exception as error:
        db.rollback()
        job = db.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.error_code = "WORKER_ERROR"
        job.error_message = str(error)[:500]
        job.finished_at = utcnow()
        candidate = db.get(PageCandidate, job.target_id)
        if candidate:
            candidate.status = "FAILED"
        asset_candidate = db.get(AssetCandidate, job.target_id)
        if asset_candidate:
            asset_candidate.status = "FAILED"
        db.commit()
        raise
    finally:
        db.close()

"""Red-team issue-sweep regressions for the worker handler family.

One file per the issue-sweep plan covering defects #223-4, #231, #240,
#244-1, #244-5, #205 (metadata), #210-5, #237-2 (handler half) and #236-2
(handler half): pre-call fences, completion guards, beat-level coverage,
container caps, prompt-injection bounding, terminal missing-blob handling,
stale-inspect page restore and the style-reference fence.
"""

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.config import get_settings
from app.domain.states import CharacterPresence, JobStatus, PageStatus, Resolution
from app.model_adapters.base import ModelResponse, ProviderAdapterError
from app.models import (
    Asset,
    AssetStatus,
    Beat,
    Chapter,
    Character,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
    RepairPlan,
    Scene,
    ScriptRevision,
    SourceRevision,
    SourceSegment,
    StyleProfile,
    StyleStatus,
    utcnow,
)
from app.services.ai_schemas import (
    DRAFT_PRESENCE_MAX_ITEMS,
    DRAFT_PROPS_MAX_ITEMS,
    DRAFT_SEGMENT_MAX_ITEMS,
    INSPECTION_ITEMS_MAX,
    INSPECTION_REGIONS_MAX_ITEMS,
    INSPECTION_TEXT_MAX_LENGTH,
    BeatDraft,
    CharacterDraft,
    InspectionDetails,
    InspectionItem,
    PageInspectionOutput,
    SceneDraft,
    StoryParseOutput,
)
from app.services.prompt_compiler import PROMPT_CHAR_BUDGET, STRUCTURED_BLOCK_MAX_CHARS
from app.services.worker_handlers import provider
from app.services.worker_handlers.execution import StaleStoryboardVersionError
from app.services.worker_handlers.inspection import _run_inspection
from app.services.worker_handlers.page_generate import (
    _asset_blob_bytes as page_asset_blob_bytes,
)
from app.services.worker_handlers.page_generate import _load_reference_assets, _run_page_generate
from app.services.worker_handlers.story_parse import (
    _match_existing_character,
    _merge_story_parse_outputs,
    _run_story_parse,
)
from app.services.worker_handlers.style_analyze import _run_style_analyze
from app.worker_tasks import _mark_worker_failure

INSPECT_CATEGORIES = [
    "SPEAKER",
    "CHARACTER",
    "OUTFIT",
    "PROP",
    "CONTINUITY",
    "PRESENCE",
]


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _own_lease(db, job, owner="sweep-owner") -> str:
    job.attempt_count = max(job.attempt_count or 0, 1)
    job.lease_owner = owner
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    db.info["job_id"] = job.id
    db.info["job_lease_owner"] = owner
    db.commit()
    return owner


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------


def _seed_page_candidate(db, *, name="sweep", based_on=1, page_storyboard_version=1):
    """Project + chapter + page + READY selected-less candidate with asset."""

    project = Project(name=name)
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=page_storyboard_version,
        status=PageStatus.STORYBOARDED,
        source_coverage={"complete": True},
        scene_ids=["s1"],
        beat_ids=["b1"],
    )
    db.add(page)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="ready.png",
        storage_key="generated/ready.png",
        mime_type="image/png",
        byte_size=10,
        sha256=("sweep-" + name).encode().hex().ljust(64, "0")[:64],
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add_all([batch, asset])
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=asset.id,
        based_on_storyboard_version=based_on,
    )
    db.add(candidate)
    db.commit()
    return project, chapter, page, candidate, asset


def _seed_chapter_with_segments(db, *, name="sweep-parse", texts=("段落一。", "段落二。")):
    project = Project(name=name)
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    revision = SourceRevision(
        chapter_id=chapter.id,
        revision=1,
        source_type="TEXT",
        original_text="".join(texts),
        sha256="0" * 64,
        character_count=sum(len(text) for text in texts),
    )
    db.add(revision)
    db.flush()
    segments = []
    for index, text in enumerate(texts, 1):
        segment = SourceSegment(
            source_revision_id=revision.id,
            ordinal=index,
            text=text,
            start_offset=0,
            end_offset=len(text),
            sha256=f"{index:064d}",
        )
        db.add(segment)
        segments.append(segment)
    chapter.current_source_revision_id = revision.id
    db.commit()
    return project, chapter, segments


def _parse_job(db, chapter, status=JobStatus.PREPARING):
    job = GenerationJob(
        project_id=chapter.project_id,
        target_type="CHAPTER",
        target_id=chapter.id,
        job_type="SOURCE_PARSE",
        status=status,
        model_alias="text.fast",
    )
    db.add(job)
    db.commit()
    return job


def _fake_inspection_output(detected=None):
    return PageInspectionOutput(
        items=[
            InspectionItem(
                category=category,
                outcome="PASS",
                score=0.99,
                severity="INFO",
                details={
                    "expected": "结构化目标",
                    "observed": "符合目标",
                    "detected_characters": detected or [],
                },
            )
            for category in INSPECT_CATEGORIES
        ]
    )


# ---------------------------------------------------------------------------
# A (#223-4) + F (#205): inspection pre-call fence and request metadata
# ---------------------------------------------------------------------------


def test_inspection_pre_call_stale_fence_blocks_paid_call(db_session, monkeypatch):
    """A candidate whose based_on_storyboard_version is already stale at claim
    time must fail StaleStoryboardVersionError BEFORE the paid call (#223-4),
    mirroring the page_generate guard; nothing is persisted."""

    project, chapter, page, candidate, asset = _seed_page_candidate(
        db_session, name="质检预检栅栏", based_on=2, page_storyboard_version=1
    )
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": INSPECT_CATEGORIES},
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)

    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )

    def forbid_invoke(*_args, **_kwargs):
        raise AssertionError("过期分镜候选不得发起付费质检调用")

    monkeypatch.setattr(provider, "_invoke_provider", forbid_invoke)

    with pytest.raises(StaleStoryboardVersionError) as excinfo:
        _run_inspection(db_session, job)
    assert "分镜版本已变化" in str(excinfo.value)
    assert (
        db_session.scalar(select(func.count(InspectionResult.id))) == 0
    ), "预检失败不得持久化任何质检行"


def test_inspection_multimodal_request_carries_token_budget(db_session, monkeypatch, tmp_path):
    """#205: the MultimodalRequest must carry max_output_tokens metadata —
    without it the provider default (2048) truncates the verdict list."""

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    project, chapter, page, candidate, asset = _seed_page_candidate(db_session)
    blob = tmp_path / "generated" / "ready.png"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(_png_bytes())
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": INSPECT_CATEGORIES},
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)

    captured: dict[str, object] = {}

    class CaptureAdapter:
        def analyze_multimodal(self, request, output_schema):
            captured["request"] = request
            return _fake_inspection_output()

    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    monkeypatch.setattr(
        provider, "_invoke_provider", lambda db, binding, callback: callback(CaptureAdapter())
    )

    _run_inspection(db_session, job)
    db_session.commit()

    request = captured["request"]
    assert request.metadata == {"max_output_tokens": 8192, "thinking_budget": 0}
    assert request.images == (blob.read_bytes(),)


# ---------------------------------------------------------------------------
# D1 (#244-1): one verdict row per category, capped item count
# ---------------------------------------------------------------------------


def test_inspection_persists_one_verdict_row_per_category(db_session, monkeypatch, tmp_path):
    """Model verdicts repeating a category must dedupe to the FIRST emission:
    one InspectionResult row per category, and the deterministic PRESENCE
    cross-check reads the same (first) verdict that was persisted."""

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    project, chapter, page, candidate, asset = _seed_page_candidate(db_session, name="质检类别去重")
    blob = tmp_path / "generated" / "ready.png"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(_png_bytes())
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": INSPECT_CATEGORIES},
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)

    def verdict(category, outcome, detected=None):
        return InspectionItem(
            category=category,
            outcome=outcome,
            score=0.9,
            severity="INFO",
            details={
                "expected": "目标",
                "observed": "结果",
                "detected_characters": detected or [],
            },
        )

    output = PageInspectionOutput(
        items=[
            verdict("PRESENCE", "PASS", detected=["苏清白"]),
            verdict("PRESENCE", "MISMATCH", detected=["无关路人"]),
            verdict("SPEAKER", "PASS"),
            verdict("SPEAKER", "MISSING"),
            verdict("CHARACTER", "PASS"),
            verdict("OUTFIT", "PASS"),
            verdict("PROP", "PASS"),
            verdict("CONTINUITY", "PASS"),
        ]
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    monkeypatch.setattr(
        provider,
        "_invoke_provider",
        lambda db, binding, callback: callback(
            SimpleNamespace(analyze_multimodal=lambda request, schema: output)
        ),
    )

    _run_inspection(db_session, job)
    db_session.commit()

    rows = list(
        db_session.scalars(
            select(InspectionResult).where(InspectionResult.candidate_id == candidate.id)
        )
    )
    assert sorted(row.category for row in rows) == sorted(set(INSPECT_CATEGORIES))
    by_category = {row.category: row for row in rows}
    # First emission wins for both repeated categories.
    assert by_category["SPEAKER"].outcome == "PASS"
    assert by_category["PRESENCE"].outcome == "PASS"
    assert by_category["PRESENCE"].details["detected_characters"] == ["苏清白"]
    # The duplicate MISMATCH never reached persistence, so the run converges
    # to a pass for this empty-snapshot page.
    db_session.refresh(candidate)
    assert candidate.status == "INSPECTED"


def test_inspection_handler_caps_persisted_item_rows(db_session, monkeypatch, tmp_path):
    """Handler-side backstop (#244-1): an unvalidated emission with more
    distinct categories than INSPECTION_ITEMS_MAX persists at most the cap,
    dropping extras deterministically."""

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    project, chapter, page, candidate, asset = _seed_page_candidate(db_session, name="质检行数封顶")
    blob = tmp_path / "generated" / "ready.png"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(_png_bytes())
    flood = [f"FLOOD{index:02d}" for index in range(INSPECTION_ITEMS_MAX + 8)]
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": [*flood, "PRESENCE"]},
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)

    template = InspectionItem(
        category="PRESENCE",
        outcome="PASS",
        details={"expected": "目标", "observed": "结果"},
    )
    items = [
        InspectionItem.model_construct(
            **{
                **{name: getattr(template, name) for name in InspectionItem.model_fields},
                "category": category,
            }
        )
        for category in flood
    ]
    items.insert(0, template)
    output = PageInspectionOutput.model_construct(items=items)

    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    monkeypatch.setattr(
        provider,
        "_invoke_provider",
        lambda db, binding, callback: callback(
            SimpleNamespace(analyze_multimodal=lambda request, schema: output)
        ),
    )

    _run_inspection(db_session, job)
    db_session.commit()

    persisted = db_session.scalar(
        select(func.count(InspectionResult.id)).where(
            InspectionResult.candidate_id == candidate.id
        )
    )
    assert persisted == INSPECTION_ITEMS_MAX


# ---------------------------------------------------------------------------
# G (#210-5): missing blobs fail terminally
# ---------------------------------------------------------------------------


def test_inspection_missing_blob_fails_terminal_before_model(db_session, monkeypatch, tmp_path):
    """A vanished candidate blob must raise non-retryable INVALID_INPUT — the
    raw FileNotFoundError used to classify as retryable WORKER_ERROR and burn
    paid re-runs (#210-5)."""

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    project, chapter, page, candidate, asset = _seed_page_candidate(db_session, name="质检缺文件")
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": INSPECT_CATEGORIES},
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)

    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    # The blob read happens while building the request inside the provider
    # callback: argument evaluation precedes the adapter call, so a missing
    # file terminates before any dispatch.
    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError("缺文件不得分发给适配器")

    monkeypatch.setattr(
        provider,
        "_invoke_provider",
        lambda db, binding, callback: callback(
            SimpleNamespace(analyze_multimodal=fail_dispatch)
        ),
    )

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_inspection(db_session, job)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.retryable is False
    assert db_session.scalar(select(func.count(InspectionResult.id))) == 0


def test_style_analyze_missing_reference_blob_fails_terminal(
    db_session, monkeypatch, tmp_path
):
    """#210-5 on style_analyze: a missing STYLE_REFERENCE file terminates the
    job non-retryably instead of retrying as WORKER_ERROR."""

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    project = Project(name="风格缺文件")
    db_session.add(project)
    db_session.flush()
    reference = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style-missing.png",
        mime_type="image/png",
        byte_size=10,
        sha256="1" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db_session.add(reference)
    db_session.flush()
    style = StyleProfile(
        project_id=project.id,
        name="缺文件风格",
        color_mode="monochrome",
        status=StyleStatus.ANALYZING,
        profile={"reference_asset_ids": [reference.id]},
    )
    db_session.add(style)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        status=JobStatus.GENERATING,
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)

    monkeypatch.setattr(
        "app.services.worker_handlers.style_analyze.provider._binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError("缺文件不得分发给适配器")

    monkeypatch.setattr(
        "app.services.worker_handlers.style_analyze.provider._invoke_provider",
        lambda db, binding, callback: callback(
            SimpleNamespace(analyze_multimodal=fail_dispatch)
        ),
    )

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_style_analyze(db_session, job)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.retryable is False


def test_page_generate_blob_preflight_is_terminal(db_session, monkeypatch, tmp_path):
    """#210-5 on page_generate: the blob helper fails terminally for a missing
    file and returns the bytes for a live one."""

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    project = Project(name="页面缺文件")
    db_session.add(project)
    db_session.flush()
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="ghost.png",
        storage_key="generated/ghost.png",
        mime_type="image/png",
        byte_size=10,
        sha256="2" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
    db_session.commit()

    with pytest.raises(ProviderAdapterError) as excinfo:
        page_asset_blob_bytes(asset)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.retryable is False

    blob = tmp_path / "generated" / "ghost.png"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"png-bytes")
    assert page_asset_blob_bytes(asset) == b"png-bytes"


# ---------------------------------------------------------------------------
# B (#231): style analyze completion guard + failure-path DRAFT reset guard
# ---------------------------------------------------------------------------


def test_style_analyze_late_completion_does_not_clobber_confirmed_style(
    db_session, monkeypatch, tmp_path
):
    """A confirmation/activation landing while the paid multimodal call is in
    flight must survive the late completion: no DRAFT demotion, no
    palette_confirmed/test_image_approved reset, no profile overwrite, no
    version bump — the analyzed payload is discarded with a logged note and
    the job still completes (#231, the STYLE_TEST guard pattern)."""

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    project = Project(name="风格晚到完成")
    db_session.add(project)
    db_session.flush()
    reference = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style-late.png",
        mime_type="image/png",
        byte_size=10,
        sha256="3" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db_session.add(reference)
    db_session.flush()
    reference_file = settings.upload_root / reference.storage_key
    reference_file.parent.mkdir(parents=True, exist_ok=True)
    reference_file.write_bytes(_png_bytes())
    confirmed_profile = {
        "reference_asset_ids": [reference.id],
        "prompt_summary": "用户已确认的档案",
        "palette_confirmed": True,
        "test_image_approved": True,
    }
    style = StyleProfile(
        project_id=project.id,
        name="晚到完成风格",
        color_mode="color",
        status=StyleStatus.ANALYZING,
        profile=dict(confirmed_profile),
    )
    db_session.add(style)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        status=JobStatus.GENERATING,
    )
    db_session.add(job)
    db_session.commit()
    _own_lease(db_session, job)
    version_before = style.version

    def confirming_invoke(db, binding, callback):
        # The user confirms and activates the style while the paid call runs.
        row = db.get(StyleProfile, style.id)
        row.status = StyleStatus.CONFIRMED
        row.version += 1
        db.commit()
        return callback(
            SimpleNamespace(
                analyze_multimodal=lambda request, schema: SimpleNamespace(
                    model_dump=lambda: {
                        "line_art": "模型线稿",
                        "prompt_summary": "模型覆写档案",
                    }
                )
            )
        )

    monkeypatch.setattr(
        "app.services.worker_handlers.style_analyze.provider._binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None))
        ),
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.style_analyze.provider._invoke_provider",
        confirming_invoke,
    )

    _run_style_analyze(db_session, job)
    db_session.commit()
    db_session.expire_all()

    row = db_session.get(StyleProfile, style.id)
    assert row.status == StyleStatus.CONFIRMED
    assert row.version == version_before + 1  # only the mid-flight confirmation bump
    assert row.profile["palette_confirmed"] is True
    assert row.profile["test_image_approved"] is True
    assert row.profile["prompt_summary"] == "用户已确认的档案"
    assert "line_art" not in row.profile, "模型分析结果不得写入已确认档案"
    refreshed_job = db_session.get(GenerationJob, job.id)
    assert refreshed_job.progress == 90


@pytest.mark.parametrize("protected", [StyleStatus.CONFIRMED, StyleStatus.ACTIVE])
def test_terminal_style_failure_keeps_confirmed_or_active_style(db_session, protected):
    """#231 failure half: the DRAFT reset in _mark_worker_failure must exclude
    CONFIRMED/ACTIVE styles (sibling exclusion alone did not cover the
    mid-flight confirmation/activation transition)."""

    project = Project(name="风格失败不降级")
    db_session.add(project)
    db_session.flush()
    style = StyleProfile(
        project_id=project.id,
        name="不降级风格",
        color_mode="monochrome",
        status=protected,
    )
    db_session.add(style)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        status=JobStatus.GENERATING,
    )
    db_session.add(job)
    db_session.commit()
    owner = _own_lease(db_session, job)

    marked, _, is_final = _mark_worker_failure(
        db_session, job.id, owner, "WORKER_ERROR", "风格分析失败", retryable=False
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED
    assert db_session.get(StyleProfile, style.id).status == protected


# ---------------------------------------------------------------------------
# H (#237-2): stale PAGE_INSPECT terminal failure must not flag a healthy page
# ---------------------------------------------------------------------------


def _final_checking_page(db, *, continuity="NOT_CHECKED"):
    project = Project(name="过期质检恢复")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        status=PageStatus.FINAL_CHECKING,
        continuity_status=continuity,
    )
    db.add(page)
    db.flush()
    generate_job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="pending",
        job_type="PAGE_GENERATE",
        status=JobStatus.COMPLETED,
    )
    db.add(generate_job)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="ready.png",
        storage_key="generated/ready.png",
        mime_type="image/png",
        byte_size=10,
        sha256="4" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add_all([batch, asset])
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=asset.id,
        job_id=generate_job.id,
        is_selected=True,
    )
    db.add(candidate)
    db.flush()
    generate_job.target_id = candidate.id
    page.selected_candidate_id = candidate.id
    page.version += 1
    db.commit()
    return project, page, candidate


def test_stale_inspect_failure_leaves_unflagged_page_parked(db_session):
    """#237-2: a terminal STALE_STORYBOARD_VERSION inspect failure must NOT
    flip the page to NEEDS_REPAIR/NEEDS_REVIEW — the inspect was stale, the
    page is not broken. It stays parked in FINAL_CHECKING awaiting a fresh
    inspect (the inspect route enqueues from any page status)."""

    project, page, candidate = _final_checking_page(db_session)
    version_before = page.version
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
    )
    db_session.add(job)
    db_session.commit()
    owner = _own_lease(db_session, job)

    marked, _, is_final = _mark_worker_failure(
        db_session,
        job.id,
        owner,
        "STALE_STORYBOARD_VERSION",
        "检查期间分镜已变化",
        candidate_status="STALE",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()

    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED
    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.FINAL_CHECKING
    assert restored.continuity_status == "NOT_CHECKED"
    assert restored.version == version_before
    kept = db_session.get(PageCandidate, candidate.id)
    assert kept.status == "READY"
    assert kept.is_selected is True


def test_genuine_inspect_failure_still_flips_page_to_repair(db_session):
    """Control for #237-2: a genuine terminal failure keeps the full restore —
    NEEDS_REPAIR + continuity NEEDS_REVIEW + version bump — so the page never
    strands in FINAL_CHECKING."""

    project, page, candidate = _final_checking_page(db_session)
    version_before = page.version
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
    )
    db_session.add(job)
    db_session.commit()
    owner = _own_lease(db_session, job)

    marked, _, _ = _mark_worker_failure(
        db_session, job.id, owner, "WORKER_ERROR", "质检失败", retryable=False
    )
    assert marked is True
    db_session.expire_all()

    restored = db_session.get(MangaPage, page.id)
    assert restored.status == PageStatus.NEEDS_REPAIR
    assert restored.continuity_status == "NEEDS_REVIEW"
    assert restored.version == version_before + 1


# ---------------------------------------------------------------------------
# I (#236-2 handler half): style reference fence in _load_reference_assets
# ---------------------------------------------------------------------------


def test_missing_style_reference_row_stops_reference_load(db_session):
    """A soft-deleted/missing style reference row must stop the load with the
    same pre-call error class as scene references — the deleted_at filter used
    to silently shrink the list and the paid call lost the style image."""

    project = Project(name="风格参考栅栏")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, scene_ids=[], beat_ids=[])
    db_session.add(page)
    db_session.flush()
    live = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="live.png",
        storage_key="live.png",
        mime_type="image/png",
        byte_size=10,
        sha256="5" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    deleted = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="deleted.png",
        storage_key="deleted.png",
        mime_type="image/png",
        byte_size=10,
        sha256="6" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
        deleted_at=datetime.now(UTC),
    )
    db_session.add_all([live, deleted])
    db_session.flush()
    style = StyleProfile(
        project_id=project.id,
        name="栅栏风格",
        color_mode="monochrome",
        profile={"reference_asset_ids": [live.id, deleted.id]},
    )
    db_session.add(style)
    db_session.flush()
    page.style_id = style.id
    db_session.commit()

    with pytest.raises(RuntimeError, match="风格参考图已删除或失效"):
        _load_reference_assets(db_session, page, project)

    # Control: once the style only lists the live row, the load succeeds.
    style.profile = {"reference_asset_ids": [live.id]}
    db_session.commit()
    references = _load_reference_assets(db_session, page, project)
    assert [item.id for item in references] == [live.id]


# ---------------------------------------------------------------------------
# C (#240): coverage, merge dedupe/fusion, curated description, presence keys
# ---------------------------------------------------------------------------


def test_scene_claim_without_beats_never_counts_covered(db_session, monkeypatch):
    """#240-1: a scene claiming segment ids while emitting ZERO beats must not
    mark them covered — no SCRIPT_READY/100% lie without beat-level content."""

    project, chapter, segments = _seed_chapter_with_segments(db_session, name="零拍覆盖")
    job = _parse_job(db_session, chapter)

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="祠堂",
                        purpose="空场景",
                        source_segment_ids=[segment.id for segment in segments],
                        beats=[],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    _run_story_parse(db_session, job)
    db_session.commit()
    db_session.expire(chapter, ["status", "version"])

    assert chapter.status == "SCRIPT_INCOMPLETE"
    script = db_session.scalar(
        select(ScriptRevision).where(ScriptRevision.chapter_id == chapter.id)
    )
    assert script.status == "INCOMPLETE"
    assert sorted(script.coverage["missing_segment_ids"]) == sorted(
        segment.id for segment in segments
    )
    assert script.coverage["ratio"] == 0


def test_beat_coverage_ignores_hallucinated_segment_ids(db_session, monkeypatch):
    """#240-1: claimed ids are validated against the chunk input — a
    hallucinated id neither marks coverage nor persists into source_range."""

    project, chapter, segments = _seed_chapter_with_segments(db_session, name="幻觉段落")
    job = _parse_job(db_session, chapter)

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="渡廊",
                        purpose="对话",
                        source_segment_ids=[segments[0].id, "hallucinated-id"],
                        beats=[
                            BeatDraft(
                                ordinal=1,
                                action=segments[0].text,
                                source_segment_ids=[segments[0].id, "hallucinated-id"],
                            ),
                            BeatDraft(
                                ordinal=2,
                                action=segments[1].text,
                                source_segment_ids=[segments[1].id],
                            ),
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    _run_story_parse(db_session, job)
    db_session.commit()
    db_session.expire(chapter, ["status"])

    # The real ids are beat-covered; the hallucinated one never counts.
    assert chapter.status == "SCRIPT_READY"
    scene = db_session.scalar(select(Scene).where(Scene.chapter_id == chapter.id))
    assert scene.source_range["segment_ids"] == [segments[0].id]
    beats = list(db_session.scalars(select(Beat).where(Beat.scene_id == scene.id)))
    assert [beat.source_range["segment_ids"] for beat in beats] == [
        [segments[0].id],
        [segments[1].id],
    ]


def test_merge_dedupes_duplicate_scene_emissions():
    """#240-2: a scene re-emitted across chunks with identical location/purpose
    and identical beat content collapses to one scene; distinct scenes and
    contentless (zero-beat) scenes survive."""

    def scene_with_beats(ordinal, action="同一动作", dialogue="同一句"):
        return SceneDraft(
            ordinal=ordinal,
            location="渡廊",
            purpose="对峙",
            source_segment_ids=["seg-1"],
            beats=[
                BeatDraft(ordinal=1, action=action, dialogue=dialogue, source_segment_ids=["seg-1"])
            ],
        )

    outputs = [
        StoryParseOutput(characters=[], scenes=[scene_with_beats(3)]),
        StoryParseOutput(characters=[], scenes=[scene_with_beats(9)]),
        StoryParseOutput(characters=[], scenes=[SceneDraft(ordinal=2, beats=[])]),
        StoryParseOutput(characters=[], scenes=[SceneDraft(ordinal=5, beats=[])]),
    ]
    merged = _merge_story_parse_outputs(outputs)

    assert len(merged.scenes) == 3
    assert [scene.ordinal for scene in merged.scenes] == [1, 2, 3]
    assert merged.scenes[0].beats[0].dialogue == "同一句"


def test_cross_chunk_fusion_keeps_second_primary_and_flags_conflict():
    """#240-3: fusing 「顾川/[小川]」 with 「小川/[]」 must keep 小川 as an alias
    and mark the merged draft alias_conflict instead of silently erasing the
    second primary name."""

    outputs = [
        StoryParseOutput(
            characters=[CharacterDraft(primary_name="顾川", aliases=["小川"], description="主角")],
            scenes=[],
        ),
        StoryParseOutput(characters=[CharacterDraft(primary_name="小川")], scenes=[]),
    ]
    merged = _merge_story_parse_outputs(outputs)

    assert len(merged.characters) == 1
    fused = merged.characters[0]
    assert fused.primary_name == "顾川"
    assert "小川" in fused.aliases
    assert fused.alias_conflict is True


def test_parsed_fused_character_lands_as_needs_confirmation(db_session, monkeypatch):
    """The merge-time fusion flag must reach the persisted Character row:
    alias_conflict=True and status NEEDS_CONFIRMATION (not ANALYZED)."""

    project, chapter, segments = _seed_chapter_with_segments(db_session, name="融合落库")
    job = _parse_job(db_session, chapter)

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[
                    CharacterDraft(primary_name="顾川", aliases=["小川"]),
                    CharacterDraft(primary_name="小川"),
                ],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="祠堂",
                        purpose="对话",
                        source_segment_ids=[segment.id for segment in segments],
                        beats=[
                            BeatDraft(
                                ordinal=1,
                                action=segment.text,
                                source_segment_ids=[segment.id],
                            )
                            for segment in segments
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    _run_story_parse(db_session, job)
    db_session.commit()

    character = db_session.scalar(
        select(Character).where(Character.project_id == project.id)
    )
    assert character is not None
    assert "小川" in character.aliases
    assert character.alias_conflict is True
    assert str(getattr(character.status, "value", character.status)) == "NEEDS_CONFIRMATION"


def test_reparse_does_not_overwrite_canonical_description(db_session, monkeypatch):
    """#240-4: a CANONICAL character with a non-empty curated description must
    keep it; the model's re-parse text is parked in the log, never written."""

    project, chapter, segments = _seed_chapter_with_segments(db_session, name="定稿描述")
    curated = Character(
        project_id=project.id,
        primary_name="顾川",
        aliases=[],
        canonical_description="用户定稿的人物描述",
        status=AssetStatus.CANONICAL,
    )
    db_session.add(curated)
    db_session.commit()
    job = _parse_job(db_session, chapter)

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[
                    CharacterDraft(primary_name="顾川", description="模型重析的描述")
                ],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="祠堂",
                        purpose="对话",
                        source_segment_ids=[segment.id for segment in segments],
                        beats=[
                            BeatDraft(
                                ordinal=1,
                                action=segment.text,
                                source_segment_ids=[segment.id],
                            )
                            for segment in segments
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    _run_story_parse(db_session, job)
    db_session.commit()

    db_session.expire(curated, ["canonical_description", "alias_conflict"])
    assert curated.canonical_description == "用户定稿的人物描述"
    assert curated.alias_conflict is False


def test_presence_key_conflict_first_wins_and_is_flagged(db_session, monkeypatch):
    """#240-6: two raw presence keys normalizing onto one key resolve
    first-wins deterministically, and the collision is recorded on the
    persisted beat instead of silently taking the last value."""

    project, chapter, segments = _seed_chapter_with_segments(db_session, name="在场键冲突")
    job = _parse_job(db_session, chapter)

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="灵堂",
                        purpose="祭拜",
                        source_segment_ids=[segment.id for segment in segments],
                        beats=[
                            BeatDraft(
                                ordinal=1,
                                action=segments[0].text,
                                source_segment_ids=[segments[0].id],
                                character_presence={
                                    "爸 爸": CharacterPresence.VISIBLE,
                                    "爸爸": CharacterPresence.OFFSCREEN,
                                    "": CharacterPresence.MENTIONED,
                                },
                            ),
                            BeatDraft(
                                ordinal=2,
                                action=segments[1].text,
                                source_segment_ids=[segments[1].id],
                            ),
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    _run_story_parse(db_session, job)
    db_session.commit()

    beats = list(
        db_session.scalars(
            select(Beat).join(Scene).where(Scene.chapter_id == chapter.id).order_by(Beat.ordinal)
        )
    )
    first = beats[0]
    assert first.source_range["character_presence"] == {"爸爸": "VISIBLE"}
    conflicts = first.source_range["presence_key_conflicts"]
    assert len(conflicts) == 1
    assert "OFFSCREEN" in conflicts[0]
    assert "presence_key_conflicts" not in beats[1].source_range


def test_chapter_version_bump_survives_concurrent_write(db_session, monkeypatch):
    """#240-7: the script-ready version bump is a conditional UPDATE — a
    concurrent chapter write landing mid-parse keeps its own increment instead
    of being lost to a read-modify-write."""

    from sqlalchemy.orm import sessionmaker

    project, chapter, segments = _seed_chapter_with_segments(db_session, name="版本条件更新")
    job = _parse_job(db_session, chapter)
    version_before = chapter.version
    bumped = {"done": False}

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            if not bumped["done"]:
                bumped["done"] = True
                writer = sessionmaker(
                    bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
                )()
                try:
                    writer.execute(
                        update(Chapter)
                        .where(Chapter.id == chapter.id)
                        .values(version=Chapter.version + 1)
                        .execution_options(synchronize_session=False)
                    )
                    writer.commit()
                finally:
                    writer.close()
            return StoryParseOutput(
                characters=[],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        location="渡廊",
                        purpose="对话",
                        source_segment_ids=[segment.id for segment in segments],
                        beats=[
                            BeatDraft(
                                ordinal=1,
                                action=segment.text,
                                source_segment_ids=[segment.id],
                            )
                            for segment in segments
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    _run_story_parse(db_session, job)
    db_session.commit()

    final_version = db_session.scalar(
        select(Chapter.version).where(Chapter.id == chapter.id)
    )
    assert final_version == version_before + 2
    db_session.expire(chapter, ["status"])
    assert chapter.status == "SCRIPT_READY"


# ---------------------------------------------------------------------------
# E (#244-5): alias matcher strength and ranking
# ---------------------------------------------------------------------------


def _character_row(db, project_id, primary_name, aliases, status):
    row = Character(
        project_id=project_id,
        primary_name=primary_name,
        aliases=aliases,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def test_match_existing_character_ignores_alias_only_overlap(db_session):
    """Two characters merely SHARING a nickname (alias↔alias overlap with no
    primary-level hit) must not merge (#244-5)."""

    project = Project(name="匹配强度")
    db_session.add(project)
    db_session.flush()
    shared = _character_row(db_session, project.id, "张三", ["老张"], AssetStatus.UPLOADED)
    assert (
        _match_existing_character(
            [shared], "李四", ["老张"], set()
        )
        is None
    )


def test_match_existing_character_exact_name_outranks_status(db_session):
    """An exact primary-name equality must outrank a better-status candidate
    that only matches through an alias (#244-5)."""

    project = Project(name="匹配排序")
    db_session.add(project)
    db_session.flush()
    alias_holder = _character_row(
        db_session, project.id, "顾川", [], AssetStatus.CANONICAL
    )
    exact = _character_row(
        db_session, project.id, "阿川", [], AssetStatus.NEEDS_CONFIRMATION
    )
    matched = _match_existing_character(
        [alias_holder, exact], "阿川", ["顾川"], set()
    )
    assert matched is exact


# ---------------------------------------------------------------------------
# C5 / #244-1: schema container caps
# ---------------------------------------------------------------------------


def test_schema_container_caps_reject_oversized_emissions():
    with pytest.raises(ValidationError):
        CharacterDraft(
            primary_name="甲", source_segment_ids=[str(i) for i in range(DRAFT_SEGMENT_MAX_ITEMS + 1)]
        )
    with pytest.raises(ValidationError):
        SceneDraft(
            ordinal=1,
            source_segment_ids=[str(i) for i in range(DRAFT_SEGMENT_MAX_ITEMS + 1)],
            beats=[],
        )
    with pytest.raises(ValidationError):
        BeatDraft(ordinal=1, props=[f"p{i}" for i in range(DRAFT_PROPS_MAX_ITEMS + 1)])
    with pytest.raises(ValidationError):
        BeatDraft(
            ordinal=1,
            character_presence={
                f"角色{i}": CharacterPresence.VISIBLE
                for i in range(DRAFT_PRESENCE_MAX_ITEMS + 1)
            },
        )
    with pytest.raises(ValidationError):
        InspectionDetails(
            expected="E" * (INSPECTION_TEXT_MAX_LENGTH + 1), observed="o"
        )
    with pytest.raises(ValidationError):
        InspectionItem(
            category="X",
            outcome="PASS",
            details={"expected": "e", "observed": "o"},
            regions=[{"x": 0} for _ in range(INSPECTION_REGIONS_MAX_ITEMS + 1)],
        )
    with pytest.raises(ValidationError):
        PageInspectionOutput(
            items=[
                InspectionItem(
                    category=f"C{i}",
                    outcome="PASS",
                    details={"expected": "e", "observed": "o"},
                )
                for i in range(INSPECTION_ITEMS_MAX + 1)
            ]
        )


# ---------------------------------------------------------------------------
# D2 (#244-1): repair/region context injection is bounded
# ---------------------------------------------------------------------------


def test_repair_prompt_bounds_inspection_details_injection(
    db_session, monkeypatch, tmp_path
):
    """The PAGE_REPAIR branch must bound the appended inspection.details +
    repair.target_regions JSON to the compiler's per-block budget — the raw
    json.dumps used to append unbounded after the compiled prompt."""

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    project, chapter, page, parent, parent_asset = _seed_page_candidate(
        db_session, name="修复注入封顶"
    )
    parent_blob = tmp_path / "generated" / "ready.png"
    parent_blob.parent.mkdir(parents=True, exist_ok=True)
    parent_blob.write_bytes(_png_bytes())

    giant_details = {
        "expected": "期" * 9000,
        "observed": "观" * 9000,
        "differences": ["差" * 3000 for _ in range(20)],
    }
    inspection = InspectionResult(
        candidate_id=parent.id,
        storyboard_version=page.storyboard_version,
        category="CHARACTER",
        outcome="MISMATCH",
        severity="ERROR",
        details=giant_details,
        regions=[],
    )
    db_session.add(inspection)
    db_session.flush()
    repair = RepairPlan(
        inspection_result_id=inspection.id,
        repair_type="BUBBLE_REGION",
        target_regions=[{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2} for _ in range(500)],
        target_fields=[],
        lock_conflicts=[],
    )
    db_session.add(repair)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=2,
        generation_kind="PAGE",
    )
    db_session.add(batch)
    db_session.flush()
    child = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=2,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    db_session.add(child)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child.id,
        job_type="PAGE_REPAIR",
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
        request_parameters={
            "original_candidate_id": parent.id,
            "repair_plan_id": repair.id,
        },
    )
    db_session.add(job)
    db_session.flush()
    child.job_id = job.id
    db_session.commit()
    _own_lease(db_session, job)

    captured: dict[str, object] = {}

    class CaptureImageAdapter:
        def generate_page(self, request):
            captured["prompt"] = request.prompt
            return ModelResponse(
                model_id="fake-image",
                request_id="fake-request",
                usage={"fake": True},
                images=(_png_bytes(),),
            )

    binding = SimpleNamespace(
        resolved=SimpleNamespace(
            # id=None keeps the catalog_model_id FK writes null (no ai_models
            # row is seeded for this offline dispatch).
            model=SimpleNamespace(id=None, capabilities={}),
            provider=SimpleNamespace(preset_key=None, name="离线测试供应商"),
            connection=SimpleNamespace(id=None, nonsecret_config={}, protocol="HTTP_API"),
            route_reason="EXPLICIT",
            route_score=None,
        ),
        adapter=CaptureImageAdapter(),
        selected_key=None,
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.page_generate.provider._binding",
        lambda *args, **kwargs: binding,
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.page_generate.provider._invoke_provider",
        lambda db, invoke_binding, callback: callback(binding.adapter),
    )

    _run_page_generate(db_session, job)
    db_session.commit()

    prompt = captured["prompt"]
    assert "这是局部修复任务" in prompt
    # The oversized original blobs never reach the paid prompt...
    assert "期" * 2500 not in prompt
    assert "差" * 2500 not in prompt
    assert len(prompt) < PROMPT_CHAR_BUDGET
    # ...and the embedded context block itself fits the per-block budget.
    embedded = prompt.split("这是局部修复任务。严格根据以下检查结果修复指定范围：", 1)[1]
    context_json = embedded.split("。", 1)[0]
    assert len(context_json) <= STRUCTURED_BLOCK_MAX_CHARS
    assert json.loads(context_json)["category"] == "CHARACTER"

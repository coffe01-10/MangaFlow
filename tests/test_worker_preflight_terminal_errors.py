"""Terminal classification of deterministic worker preflight failures (#643).

Three pre-paid-call checks in asset_generate / page_generate used to raise
bare RuntimeError, which the worker shell classifies as retryable
WORKER_ERROR — a deterministic failure (missing reference file, derived job
without its original candidate, repair job without its plan/inspection)
then idled through max_attempts of lease retries. They must raise
``ProviderAdapterError("INVALID_INPUT", ..., retryable=False)`` like the
#210-5 missing-blob preflights in inspection/style_analyze/page_generate.

Real provider calls stay NOT RUN: the paid dispatch is monkeypatched to
fail loudly, and the execute_job-level case runs on the isolated
file-backed SQLite worker database.
"""

from sqlalchemy import select

import pytest

from app import worker_tasks
from app.config import get_settings
from app.domain.states import JobStatus, PageStatus, Resolution
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    Asset,
    AssetCandidate,
    Chapter,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    MangaPage,
    PageCandidate,
    Project,
)
from app.services.worker_handlers.page_generate import _run_page_generate
from test_deleted_candidate_worker import _seed_page_job, _worker_database


def _forbid_paid_call(monkeypatch) -> list[str]:
    """Any provider dispatch in these tests is a defect; record and fail."""

    calls: list[str] = []

    def forbid_invoke(_db, _binding, _callback):
        calls.append("invoke")
        raise AssertionError("前置校验失败不得发起付费调用")

    def forbid_binding(*_args, **_kwargs):
        calls.append("binding")
        raise AssertionError("前置校验失败不得解析模型绑定")

    from app.services.worker_handlers import provider

    monkeypatch.setattr(provider, "_invoke_provider", forbid_invoke)
    monkeypatch.setattr(provider, "_binding", forbid_binding)
    return calls


# --- #643-1: asset_generate missing reference file ----------------------------


def test_asset_missing_reference_file_fails_terminal_invalid_input(monkeypatch):
    """A STYLE job whose leased reference blob vanished must end FAILED with
    error_code INVALID_INPUT on the first attempt — no paid call, no retry."""

    with _worker_database(monkeypatch) as factory:
        from test_deleted_candidate_worker import _seed_asset_job

        job_id, candidate_id = _seed_asset_job(factory)
        # Vanish the seeded reference blob after enqueueing.
        with factory() as db:
            from app.models import Asset as AssetModel

            reference = db.scalar(select(AssetModel).where(AssetModel.kind == "STYLE_REFERENCE"))
            blob = get_settings().upload_root / reference.storage_key
            assert blob.is_file()
            blob.unlink()

        calls = _forbid_paid_call(monkeypatch)
        with pytest.raises(ProviderAdapterError) as excinfo:
            worker_tasks.execute_job(job_id)
        assert excinfo.value.code == "INVALID_INPUT"
        assert excinfo.value.retryable is False

        with factory() as db:
            job = db.get(GenerationJob, job_id)
            assert job.status == JobStatus.FAILED
            assert job.error_code == "INVALID_INPUT"
            # Terminal on the first attempt: no WAITING retry loop.
            assert job.attempt_count == 1
            candidate = db.get(AssetCandidate, candidate_id)
            assert candidate.status == "FAILED"
            assert db.scalar(select(GenerationRecord)) is None
        assert calls == []


def test_asset_generate_helper_classifies_missing_file_terminal(
    db_session, monkeypatch, tmp_path
):
    """The shared helper mirrors the page/inspection preflight: missing file
    raises INVALID_INPUT (non-retryable), a live blob returns its bytes."""

    from app.services.worker_handlers.asset_generate import _asset_blob_bytes

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    project = Project(name="预检助手")
    db_session.add(project)
    db_session.flush()
    asset = Asset(
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="missing.png",
        storage_key="preflight/missing.png",
        mime_type="image/png",
        byte_size=4,
        sha256="9" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db_session.add(asset)
    db_session.commit()

    with pytest.raises(ProviderAdapterError) as excinfo:
        _asset_blob_bytes(asset)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.retryable is False
    assert "missing.png" in excinfo.value.user_message

    blob = tmp_path / "uploads" / "preflight" / "missing.png"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"live")
    assert _asset_blob_bytes(asset) == b"live"


# --- #643-2: derived page job without its original candidate ------------------


def test_derived_job_without_original_candidate_fails_terminal(db_session, monkeypatch):
    """A PAGE_REPAIR whose request lost the original_candidate_id must fail
    INVALID_INPUT before any dispatch, not retry as WORKER_ERROR."""

    project = Project(name="派生缺原图")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        status=PageStatus.STORYBOARDED,
        source_coverage={"complete": True},
        scene_ids=["s1"],
        beat_ids=["b1"],
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_REPAIR",
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
        request_parameters={},
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    db_session.commit()

    calls = _forbid_paid_call(monkeypatch)
    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_page_generate(db_session, job)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.retryable is False
    assert "原始候选图" in excinfo.value.user_message
    assert calls == []


def test_derived_job_without_original_fails_terminal_via_execute_job(monkeypatch):
    """The same guard through the full worker shell: the job lands FAILED
    with INVALID_INPUT on attempt one, candidate FAILED, page restored."""

    with _worker_database(monkeypatch) as factory:
        job_id, candidate_id = _seed_page_job(factory)
        with factory() as db:
            job = db.get(GenerationJob, job_id)
            job.job_type = "PAGE_REPAIR"
            job.request_parameters = {}
            db.commit()

        calls = _forbid_paid_call(monkeypatch)
        with pytest.raises(ProviderAdapterError) as excinfo:
            worker_tasks.execute_job(job_id)
        assert excinfo.value.code == "INVALID_INPUT"
        assert calls == []

        with factory() as db:
            job = db.get(GenerationJob, job_id)
            assert job.status == JobStatus.FAILED
            assert job.error_code == "INVALID_INPUT"
            assert job.attempt_count == 1
            assert db.get(PageCandidate, candidate_id).status == "FAILED"
            assert db.scalar(select(GenerationRecord)) is None


# --- #643-3: repair job without RepairPlan / InspectionResult ------------------


def test_repair_job_without_plan_fails_terminal(db_session, monkeypatch, tmp_path):
    """A PAGE_REPAIR whose repair_plan_id no longer resolves must fail
    INVALID_INPUT before any dispatch — the plan will not reappear on retry."""

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")

    project = Project(name="修复缺计划")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        status=PageStatus.STORYBOARDED,
        source_coverage={"complete": True},
        scene_ids=["s1"],
        beat_ids=["b1"],
    )
    db_session.add(page)
    db_session.flush()
    original_batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add(original_batch)
    db_session.flush()
    original_asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="original.png",
        storage_key="generated/original.png",
        mime_type="image/png",
        byte_size=8,
        sha256="8" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add(original_asset)
    db_session.flush()
    blob = settings.storage_root / original_asset.storage_key
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"original")
    original = PageCandidate(
        batch_id=original_batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=original_asset.id,
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={},
    )
    db_session.add(original)
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
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=2,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    db_session.add(candidate)
    db_session.flush()
    # original_candidate_id resolves, but repair_plan_id does not.
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_REPAIR",
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
        request_parameters={"original_candidate_id": original.id},
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    db_session.commit()

    calls = _forbid_paid_call(monkeypatch)
    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_page_generate(db_session, job)
    assert excinfo.value.code == "INVALID_INPUT"
    assert excinfo.value.retryable is False
    assert "修复计划" in excinfo.value.user_message
    assert calls == []

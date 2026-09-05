"""Worker-side deleted-candidate and stale-reference guards for paid jobs.

Regression coverage for the worker half of delete-during-generation: a job
whose target candidate was soft-deleted must never attach asset + READY to
the deleted row (before or after a full paid call), and asset generation
must re-query its leased references before dispatching the paid request.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.config import get_settings
from app.database import Base
from app.domain.states import JobStatus, PageStatus, Resolution
from app.model_adapters.base import ModelResponse
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
    StyleProfile,
)
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers import provider


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@contextmanager
def _worker_database(monkeypatch):
    """Isolated worker runtime: file SQLite + offline storage roots."""

    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = get_settings()
        monkeypatch.setattr(settings, "storage_root", root / "storage")
        monkeypatch.setattr(settings, "upload_root", root / "uploads")
        engine = create_engine(
            f"sqlite:///{root / 'worker.db'}",
            connect_args={"check_same_thread": False},
        )
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(engine)
        monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
        monkeypatch.setattr(database, "SessionLocal", factory)
        try:
            yield factory
        finally:
            engine.dispose()


def _seed_page_job(factory, *, candidate_deleted: bool = False) -> tuple[str, str]:
    """Seed a QUEUED PAGE_GENERATE job; returns (job_id, candidate_id)."""

    with factory() as db:
        ensure_provider_presets(db, get_settings(), auto_commit=False)
        project = Project(name="候选删除守卫", default_concurrency=1)
        db.add(project)
        db.flush()
        chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
        db.add(chapter)
        db.flush()
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            storyboard_version=1,
            status=PageStatus.STORYBOARDED,
            source_coverage={"complete": True},
            scene_ids=["scene-1"],
            beat_ids=["beat-1"],
        )
        db.add(page)
        db.flush()
        batch = GenerationBatch(
            project_id=project.id,
            chapter_id=chapter.id,
            page_id=page.id,
            ordinal=1,
            generation_kind="PAGE",
        )
        db.add(batch)
        db.flush()
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
        db.add(candidate)
        db.flush()
        if candidate_deleted:
            candidate.deleted_at = datetime.now(UTC)
        job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id=candidate.id,
            job_type="PAGE_GENERATE",
            status=JobStatus.QUEUED,
            model_alias="image.nano_banana_2",
        )
        db.add(job)
        db.flush()
        candidate.job_id = job.id
        db.commit()
        return job.id, candidate.id


def _seed_asset_job(factory, *, candidate_deleted: bool = False) -> tuple[str, str]:
    """Seed a QUEUED ASSET_GENERATE (STYLE) job; returns (job_id, candidate_id)."""

    with factory() as db:
        ensure_provider_presets(db, get_settings(), auto_commit=False)
        project = Project(name="资产候选删除守卫", default_concurrency=1)
        db.add(project)
        db.flush()
        reference = Asset(
            project_id=project.id,
            kind="STYLE_REFERENCE",
            original_name="style.png",
            storage_key="style-reference.png",
            mime_type="image/png",
            byte_size=10,
            sha256="a" * 64,
            source="USER_UPLOAD",
            status="UPLOADED",
        )
        db.add(reference)
        db.flush()
        reference_file = get_settings().upload_root / reference.storage_key
        reference_file.parent.mkdir(parents=True, exist_ok=True)
        reference_file.write_bytes(_png_bytes())
        style = StyleProfile(
            project_id=project.id,
            name="删除守卫风格",
            color_mode="color",
            profile={"reference_asset_ids": [reference.id]},
            status="DRAFT",
        )
        db.add(style)
        db.flush()
        batch = GenerationBatch(
            project_id=project.id,
            target_type="STYLE",
            target_id=style.id,
            generation_kind="STYLE_TEST",
            ordinal=1,
        )
        db.add(batch)
        db.flush()
        candidate = AssetCandidate(
            batch_id=batch.id,
            ordinal=1,
            model_alias="image.nano_banana_2",
            resolution=Resolution.DRAFT_1K,
            variant="STYLE_TEST",
            status="QUEUED",
        )
        db.add(candidate)
        db.flush()
        if candidate_deleted:
            candidate.deleted_at = datetime.now(UTC)
        job = GenerationJob(
            project_id=project.id,
            target_type="ASSET_CANDIDATE",
            target_id=candidate.id,
            job_type="ASSET_GENERATE",
            status=JobStatus.QUEUED,
            model_alias="image.nano_banana_2",
        )
        db.add(job)
        db.flush()
        candidate.job_id = job.id
        db.commit()
        return job.id, candidate.id


@pytest.mark.parametrize("kind", ["page", "asset"])
def test_candidate_deleted_before_claim_cancels_job_without_paid_call(
    monkeypatch, kind
):
    """A soft-deleted target candidate ends the job CANCELLED before the
    provider is invoked; the deleted row stays deleted, never READY."""

    with _worker_database(monkeypatch) as factory:
        if kind == "page":
            job_id, candidate_id = _seed_page_job(factory, candidate_deleted=True)
        else:
            job_id, candidate_id = _seed_asset_job(factory, candidate_deleted=True)
        provider_calls: list[object] = []
        adapter_lookups: list[str] = []

        class ForbiddenAdapter:
            def generate_page(self, request):
                provider_calls.append(request)
                raise AssertionError("已删除候选不得调用模型")

            def generate_asset(self, request):
                provider_calls.append(request)
                raise AssertionError("已删除候选不得调用模型")

        def fake_adapter(alias):
            adapter_lookups.append(alias)
            return ForbiddenAdapter()

        monkeypatch.setattr(worker_tasks, "_adapter", fake_adapter)
        worker_tasks.execute_job(job_id)

        with factory() as db:
            job = db.get(GenerationJob, job_id)
            assert job.status == JobStatus.CANCELLED
            assert job.cancelled_at is not None
            assert provider_calls == []
            assert adapter_lookups == []
            if kind == "page":
                candidate = db.get(PageCandidate, candidate_id)
            else:
                candidate = db.get(AssetCandidate, candidate_id)
            assert candidate.deleted_at is not None
            assert candidate.status != "READY"
            assert candidate.asset_id is None
            assert db.scalar(select(GenerationRecord)) is None


@pytest.mark.parametrize("kind", ["page", "asset"])
def test_candidate_deleted_mid_flight_discards_output_and_cancels_job(
    monkeypatch, kind
):
    """A delete landing between the paid call and persistence attaches
    nothing: the produced output is dropped, the job ends CANCELLED and no
    exception escapes ``execute_job``."""

    with _worker_database(monkeypatch) as factory:
        if kind == "page":
            job_id, candidate_id = _seed_page_job(factory)
        else:
            job_id, candidate_id = _seed_asset_job(factory)
        paid_calls: list[int] = []

        def deleting_provider(db, binding, callback):
            paid_calls.append(1)
            model = PageCandidate if kind == "page" else AssetCandidate
            row = db.get(model, candidate_id)
            row.deleted_at = datetime.now(UTC)
            row.version += 1
            db.commit()
            return ModelResponse(
                model_id="fake-image",
                request_id="fake-request",
                usage={"fake": True},
                images=(_png_bytes(),),
            )

        monkeypatch.setattr(
            provider, "_invoke_provider", deleting_provider
        )
        worker_tasks.execute_job(job_id)

        with factory() as db:
            job = db.get(GenerationJob, job_id)
            assert job.status == JobStatus.CANCELLED
            assert job.cancelled_at is not None
            assert paid_calls == [1]
            if kind == "page":
                candidate = db.get(PageCandidate, candidate_id)
            else:
                candidate = db.get(AssetCandidate, candidate_id)
            assert candidate.deleted_at is not None
            assert candidate.status != "READY"
            assert candidate.asset_id is None
            assert db.scalar(select(GenerationRecord)) is None
            assert db.scalar(select(Asset).where(Asset.source == "AI_GENERATED")) is None


def test_asset_generation_requeries_references_after_lease(db_session, tmp_path, monkeypatch):
    """Mirror page_generate's post-lease guard: a reference soft-deleted
    between the lease commit and provider dispatch must stop the paid call
    with the same fail-closed RuntimeError."""

    from app.domain.states import JobStatus as JobStatusState
    from app.worker_tasks import _run_asset_generate

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    project = Project(name="租后失效")
    db_session.add(project)
    db_session.flush()
    reference = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style-lease.png",
        mime_type="image/png",
        byte_size=10,
        sha256="b" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db_session.add(reference)
    db_session.flush()
    reference_file = settings.upload_root / reference.storage_key
    reference_file.parent.mkdir(parents=True, exist_ok=True)
    reference_file.write_bytes(_png_bytes())
    style = StyleProfile(
        project_id=project.id,
        name="租后失效风格",
        color_mode="color",
        profile={"reference_asset_ids": [reference.id]},
        status="DRAFT",
    )
    db_session.add(style)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        generation_kind="STYLE_TEST",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="STYLE_TEST",
        status="QUEUED",
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        status=JobStatusState.PREPARING,
        model_alias="image.nano_banana_2",
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    db_session.info["job_id"] = job.id
    db_session.commit()
    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()

    provider_calls: list[object] = []

    class ForbiddenAdapter:
        def generate_asset(self, request):
            provider_calls.append(request)
            raise AssertionError("参考图租后失效不得调用模型")

    monkeypatch.setattr(worker_tasks, "_adapter", lambda _alias: ForbiddenAdapter())
    real_lease = provider._lease_reference_assets

    def lease_then_delete(db, lease_job, asset_ids):
        real_lease(db, lease_job, asset_ids)
        # The reference is soft-deleted after the lease committed but before
        # the paid request goes out.
        db.execute(
            update(Asset)
            .where(Asset.id.in_(asset_ids))
            .values(deleted_at=datetime.now(UTC))
        )
        db.commit()

    monkeypatch.setattr(provider, "_lease_reference_assets", lease_then_delete)

    with pytest.raises(RuntimeError, match="参考图在生成前发生变化"):
        _run_asset_generate(db_session, job)
    assert provider_calls == []


def test_delete_asset_rechecks_selected_guard_after_page_lock(db_session, client, monkeypatch):
    """delete_asset must re-read the selected guard after taking the page
    lock (the agreed convention with select_candidate): a select that commits
    between the pre-lock read and the guard must win, returning 409 instead
    of silently deleting the now-adopted candidate's asset."""

    from sqlalchemy.orm import sessionmaker as make_session

    from app.api.routes import uploads

    project = Project(name="删除锁守卫")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        status=PageStatus.STORYBOARDED,
        source_coverage={"complete": True},
        scene_ids=[],
        beat_ids=[],
    )
    db_session.add(page)
    db_session.flush()
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="candidate.png",
        storage_key="candidate.png",
        mime_type="image/png",
        byte_size=10,
        sha256="c" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add(asset)
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
        status="READY",
        asset_id=asset.id,
        is_selected=False,
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={},
    )
    db_session.add(candidate)
    db_session.commit()

    real_lock = getattr(uploads, "lock_entity", None)

    def lock_after_concurrent_select(db, model_cls, entity_id):
        if model_cls is MangaPage:
            # Deterministic in-process interleaving: the select-candidate
            # session reads the candidate as not-selected, adopts it and
            # commits right before delete_asset's post-lock re-read.
            other = make_session(bind=db.get_bind(), expire_on_commit=False)()
            try:
                winner = other.get(PageCandidate, candidate.id)
                winner.is_selected = True
                winner.version += 1
                page_row = other.get(MangaPage, page.id)
                page_row.selected_candidate_id = candidate.id
                other.commit()
            finally:
                other.close()
        return real_lock(db, model_cls, entity_id)

    monkeypatch.setattr(
        uploads, "lock_entity", lock_after_concurrent_select, raising=False
    )

    response = client.delete(f"/api/v1/assets/{asset.id}")

    assert response.status_code == 409
    assert "当前采用的分页成图不能删除" in response.json()["detail"]
    db_session.expire_all()
    survivor = db_session.get(PageCandidate, candidate.id)
    assert survivor.deleted_at is None
    assert survivor.is_selected is True
    assert db_session.get(Asset, asset.id).deleted_at is None
    if real_lock is None:
        pytest.fail("delete_asset never took the page lock (fix not applied)")

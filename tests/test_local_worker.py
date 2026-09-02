import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.config import Settings
from app.database import Base
from app.domain.states import JobStatus, PageStatus, Resolution
from app.services.worker_handlers import execution, provider
from app.models import (
    AppSetting,
    Asset,
    AssetCandidate,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Project,
    StyleProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.services import job_service
from app.services.workflow_engine import default_graph


def test_local_worker_executes_eight_jobs_with_project_concurrency(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'jobs.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="本地并发", default_concurrency=2)
            db.add(project)
            db.flush()
            jobs = [
                GenerationJob(
                    project_id=project.id,
                    target_type="CHAPTER",
                    target_id=f"target-{index}",
                    job_type="SOURCE_PARSE",
                    status=JobStatus.QUEUED,
                )
                for index in range(8)
            ]
            db.add_all(jobs)
            db.commit()
            job_ids = [job.id for job in jobs]

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(database, "SessionLocal", testing_session)
        active = 0
        peak = 0
        lock = Lock()

        def fake_run(_db, _job):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.04)
            with lock:
                active -= 1

        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(job_service._execute_locally, job_id)
                for job_id in job_ids
            ]
            for future in futures:
                future.result(timeout=10)

        with testing_session() as db:
            completed = list(db.query(GenerationJob).all())
            assert all(job.status == JobStatus.COMPLETED for job in completed)
            assert all(job.attempt_count == 1 for job in completed)
        assert peak == 2
        engine.dispose()


def test_duplicate_worker_claim_executes_a_job_only_once(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'duplicate.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="重复执行", default_concurrency=2)
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="duplicate-target",
                job_type="SOURCE_PARSE",
                status=JobStatus.QUEUED,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        calls = 0
        lock = Lock()

        def fake_run(_db, _job):
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.08)

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker_tasks.execute_job, job_id) for _ in range(2)]
            for future in futures:
                future.result(timeout=10)

        with testing_session() as db:
            persisted = db.get(GenerationJob, job_id)
            assert persisted.status == JobStatus.COMPLETED
            assert persisted.attempt_count == 1
        assert calls == 1
        engine.dispose()


def test_active_job_cancellation_is_not_overwritten(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'cancel.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="取消竞态")
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="cancel-target",
                job_type="SOURCE_PARSE",
                status=JobStatus.QUEUED,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        started = Event()
        release = Event()

        def fake_run(_db, _job):
            started.set()
            assert release.wait(5)

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker_tasks.execute_job, job_id)
            assert started.wait(5)
            with testing_session() as db:
                job_service.cancel_job(db, db.get(GenerationJob, job_id))
            release.set()
            future.result(timeout=5)

        with testing_session() as db:
            cancelled = db.get(GenerationJob, job_id)
            assert cancelled.status == JobStatus.CANCELLED
            assert cancelled.cancelled_at is not None
        engine.dispose()


def test_completed_job_persists_full_progress(monkeypatch):
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'progress.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)
        with testing_session() as db:
            project = Project(name="完成进度")
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="progress-target",
                job_type="SOURCE_PARSE",
                status=JobStatus.QUEUED,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        def fake_run(_db, active_job):
            active_job.progress = 85

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        worker_tasks.execute_job(job_id)

        with testing_session() as db:
            completed = db.get(GenerationJob, job_id)
            assert completed.status == JobStatus.COMPLETED
            assert completed.progress == 100
        engine.dispose()


def test_asset_generation_revalidates_deleted_style_reference(db_session, monkeypatch):
    project = Project(name="引用失效")
    db_session.add(project)
    db_session.flush()
    reference = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style.png",
        mime_type="image/png",
        byte_size=10,
        sha256="f" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
        deleted_at=datetime.now(UTC),
    )
    db_session.add(reference)
    db_session.flush()
    style = StyleProfile(
        project_id=project.id,
        name="失效风格",
        color_mode="color",
        profile={"reference_asset_ids": [reference.id], "palette_confirmed": True},
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
        model_alias="image.nano_banana_2",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.commit()
    provider_calls: list[object] = []

    class FakeAdapter:
        def generate_asset(self, request):
            provider_calls.append(request)
            raise AssertionError("provider must not be called")

    monkeypatch.setattr(worker_tasks, "_adapter", lambda _alias: FakeAdapter())

    with pytest.raises(RuntimeError, match="风格参考图已失效"):
        worker_tasks._run_asset_generate(db_session, job)
    assert provider_calls == []


def test_cancelling_generation_resets_candidate_and_page(db_session):
    project = Project(name="取消页面生成")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        status=PageStatus.DRAFT_GENERATING,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(project_id=project.id, page_id=page.id, ordinal=1)
    db_session.add(batch)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="pending",
        job_type="PAGE_GENERATE",
        status=JobStatus.GENERATING,
    )
    db_session.add(job)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="GENERATING",
        job_id=job.id,
    )
    db_session.add(candidate)
    db_session.commit()

    job_service.cancel_job(db_session, job)

    assert candidate.status == "CANCELLED"
    assert page.status == PageStatus.STORYBOARDED


def test_create_job_reuses_the_winner_of_an_idempotency_race(db_session, monkeypatch):
    project = Project(name="幂等竞态")
    db_session.add(project)
    db_session.flush()
    winner = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="winner",
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
        idempotency_key="race-key",
    )
    db_session.add(winner)
    db_session.commit()

    original_scalar = db_session.scalar
    scalar_calls = 0

    def hide_the_first_lookup(statement, *args, **kwargs):
        nonlocal scalar_calls
        scalar_calls += 1
        if scalar_calls == 1:
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", hide_the_first_lookup)
    result = job_service.create_job(
        db_session,
        project_id=project.id,
        target_type="CHAPTER",
        target_id="loser",
        job_type="SOURCE_PARSE",
        idempotency_key="race-key",
    )

    assert result.id == winner.id
    assert scalar_calls >= 2
    db_session.rollback()


def _waiting_job(db_session, name: str = "队列模式") -> GenerationJob:
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=f"target-{name}",
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
    )
    db_session.add(job)
    db_session.commit()
    return job


def _set_queue_mode(db_session, mode: str) -> None:
    db_session.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db_session.commit()


def test_local_mode_submits_without_touching_redis(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    job = _waiting_job(db_session, "local")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )

    result = job_service.enqueue_job(db_session, job)

    assert result.status == JobStatus.QUEUED
    assert result.error_code == "LOCAL_WORKER"
    assert submitted == [job.id]


def test_redis_mode_keeps_job_waiting_when_redis_is_unavailable(
    db_session, monkeypatch
):
    _set_queue_mode(db_session, "REDIS")
    job = _waiting_job(db_session, "redis")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )
    monkeypatch.setattr(
        "redis.Redis.from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )

    result = job_service.enqueue_job(db_session, job)

    assert result.status == JobStatus.WAITING
    assert result.error_code == "QUEUE_UNAVAILABLE"
    assert submitted == []


def test_auto_mode_falls_back_to_local_in_development(db_session, monkeypatch):
    _set_queue_mode(db_session, "AUTO")
    job = _waiting_job(db_session, "auto")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )
    monkeypatch.setattr(
        "redis.Redis.from_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError()),
    )

    result = job_service.enqueue_job(db_session, job)

    assert result.status == JobStatus.QUEUED
    assert result.error_code == "LOCAL_WORKER"
    assert submitted == [job.id]


def test_startup_recovery_requeues_waiting_jobs_in_local_mode(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    job = _waiting_job(db_session, "recover")
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 1
    assert submitted == [job.id]
    db_session.refresh(job)
    assert job.status == JobStatus.QUEUED


def test_startup_recovery_reclaims_an_expired_worker_lease(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="租约恢复")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="lease-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(job)
    db_session.commit()
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 1
    assert submitted == [job.id]
    db_session.refresh(job)
    assert job.status == JobStatus.QUEUED
    assert job.lease_owner is None
    assert job.lease_expires_at is None


def test_startup_recovery_reclaims_legacy_active_job_without_lease(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="旧租约恢复")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="legacy-lease-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        lease_owner="legacy-worker",
        lease_expires_at=None,
    )
    db_session.add(job)
    db_session.commit()
    submitted: list[str] = []
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(
        job_service, "_submit_local", lambda job_id: submitted.append(job_id)
    )

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 1
    assert submitted == [job.id]
    db_session.refresh(job)
    assert job.status == JobStatus.QUEUED
    assert job.lease_owner is None


def test_startup_recovery_marks_exhausted_target_and_workflow_failed(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="耗尽任务清理")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        status=PageStatus.DRAFT_GENERATING,
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="GENERATING",
    )
    db_session.add(candidate)
    db_session.flush()
    workflow = WorkflowDefinition(
        project_id=project.id,
        name="耗尽任务工作流",
        draft_graph=default_graph(),
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=default_graph(),
        graph_checksum="w" * 64,
        validation_report={"valid": True},
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status="RUNNING",
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="generate",
        node_type="generator.page",
        status="RUNNING",
    )
    db_session.add(node_run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.GENERATING,
        attempt_count=3,
        max_attempts=3,
        lease_owner="dead-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        request_parameters={"workflow_run_id": run.id},
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    node_run.job_id = job.id
    db_session.commit()

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 0
    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED
    assert db_session.get(PageCandidate, candidate.id).status == "FAILED"
    assert db_session.get(WorkflowNodeRun, node_run.id).status == "FAILED"
    assert db_session.get(WorkflowRun, run.id).status == "FAILED"


def test_worker_id_generates_unique_token_per_invocation():
    tokens = [worker_tasks._worker_id() for _ in range(100)]
    assert len(set(tokens)) == 100


def test_expired_lease_in_same_process_cannot_be_mutated_by_old_worker(
    db_session, monkeypatch
):
    project = Project(name="租约交接", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="target-handover",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.commit()

    owner_a = worker_tasks._worker_id()
    claimed_a = worker_tasks._claim_job(db_session, job.id, owner_a)
    assert claimed_a is not None
    assert claimed_a.lease_owner == owner_a

    # 模拟 Worker A 超时导致租约过期
    claimed_a.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    # 同一进程内的 Worker B 抢占任务
    owner_b = worker_tasks._worker_id()
    assert owner_b != owner_a
    claimed_b = worker_tasks._claim_job(db_session, job.id, owner_b)
    assert claimed_b is not None
    assert claimed_b.lease_owner == owner_b
    assert claimed_b.attempt_count == 2

    # 1. 验证 Worker A 的心跳无法续租。interval=0 避免 wait(30s)；
    # SessionLocal 必须指向测试库，否则会打到空的开发 sqlite 并空转。
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    heartbeat_a = worker_tasks._LeaseHeartbeat(job.id, owner_a)
    heartbeat_a.interval = 0
    heartbeat_a._run()
    assert heartbeat_a.lost is True

    # 2. 验证 Worker A 检查状态时被租约丢失拦截
    db_session.info["job_lease_owner"] = owner_a
    with pytest.raises(worker_tasks.JobLeaseLostError):
        worker_tasks._ensure_job_not_cancelled(db_session, claimed_b)

    # 3. 验证 Worker A 无法写回失败状态
    marked, *_ = worker_tasks._mark_worker_failure(
        db_session,
        job.id,
        owner_a,
        "WORKER_ERROR",
        "旧 worker 晚返回",
    )
    assert marked is False
    db_session.expire_all()
    reloaded = db_session.get(GenerationJob, job.id)
    assert reloaded.status == JobStatus.PREPARING
    assert reloaded.lease_owner == owner_b


def test_startup_recovery_interleaved_with_worker_claim_does_not_overwrite_new_lease(
    db_session, monkeypatch
):
    _set_queue_mode(db_session, "LOCAL")
    project = Project(name="恢复并发交错", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="interleaved-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        lease_owner="old-dead-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(job)
    db_session.commit()

    # 在 recovery 执行前，Worker B 抢占了该任务
    owner_b = worker_tasks._worker_id()
    claimed = worker_tasks._claim_job(db_session, job.id, owner_b)
    assert claimed is not None
    assert claimed.lease_owner == owner_b

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    # 执行 recovery 扫描
    recovered = job_service.recover_pending_jobs(db_session)
    assert recovered == 0

    db_session.expire_all()
    reloaded = db_session.get(GenerationJob, job.id)
    # 验证新 Worker 的租约与状态没有被 recovery 覆盖
    assert reloaded.status == JobStatus.PREPARING
    expiry = (
        reloaded.lease_expires_at
        if reloaded.lease_expires_at.tzinfo
        else reloaded.lease_expires_at.replace(tzinfo=UTC)
    )
    assert expiry > datetime.now(UTC)


def test_worker_failure_interleaved_with_reclaimed_lease_does_not_mark_failed(db_session):
    project = Project(name="失败写回并发交错", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, status=PageStatus.DRAFT_GENERATING)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1)
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="GENERATING",
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        max_attempts=3,
        lease_owner="old-worker-a",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(job)
    db_session.commit()

    # 新 Worker B 抢占成功
    owner_b = worker_tasks._worker_id()
    claimed = worker_tasks._claim_job(db_session, job.id, owner_b)
    assert claimed is not None

    # 旧 Worker A 尝试写入失败
    marked, *_ = worker_tasks._mark_worker_failure(
        db_session,
        job.id,
        "old-worker-a",
        "TIMEOUT",
        "旧调用超时",
    )
    assert marked is False

    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.PREPARING
    assert db_session.get(PageCandidate, candidate.id).status == "GENERATING"


def test_multi_worker_claim_respects_project_concurrency_without_process_lock():
    with TemporaryDirectory() as directory:
        engine = create_engine(
            f"sqlite:///{Path(directory) / 'concurrency.db'}",
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )
        Base.metadata.create_all(engine)

        with testing_session() as db:
            project = Project(name="多会话并发", default_concurrency=2)
            db.add(project)
            db.flush()
            jobs = [
                GenerationJob(
                    project_id=project.id,
                    target_type="CHAPTER",
                    target_id=f"target-{i}",
                    job_type="SOURCE_PARSE",
                    status=JobStatus.QUEUED,
                )
                for i in range(6)
            ]
            db.add_all(jobs)
            db.commit()
            job_ids = [j.id for j in jobs]

        results = []
        lock = Lock()

        def try_claim(job_id: str):
            with testing_session() as db:
                owner = worker_tasks._worker_id()
                # 显式不使用 EXECUTION_RESERVATION_LOCK，验证数据库级子查询谓词
                claimed = worker_tasks._claim_job(db, job_id, owner)
                with lock:
                    if claimed is not None:
                        results.append(claimed.id)

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(try_claim, jid) for jid in job_ids]
            for f in futures:
                f.result(timeout=10)

        assert len(results) == 2
        with testing_session() as db:
            active_count = db.scalar(
                select(func.count(GenerationJob.id)).where(
                    GenerationJob.project_id == project.id,
                    GenerationJob.status.in_(worker_tasks.ACTIVE_STATUSES),
                )
            )
            assert active_count == 2

        engine.dispose()


def test_claim_job_uses_for_update_when_postgresql_dialect(db_session, monkeypatch):
    from unittest.mock import MagicMock

    project = Project(name="PostgreSQL行锁测试", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="target-pg-lock",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.commit()

    captured_statements = []
    original_scalar = db_session.scalar

    def mock_scalar(statement, *args, **kwargs):
        captured_statements.append(statement)
        if getattr(statement, "_for_update_arg", None) is not None:
            stmt_clean = select(Project).where(Project.id == job.project_id)
            return original_scalar(stmt_clean, *args, **kwargs)
        return original_scalar(statement, *args, **kwargs)

    real_bind = db_session.get_bind()
    mock_dialect = MagicMock()
    mock_dialect.name = "postgresql"

    class ProxyBind:
        def __getattr__(self, name):
            if name == "dialect":
                return mock_dialect
            return getattr(real_bind, name)

    monkeypatch.setattr(db_session, "get_bind", lambda *args, **kwargs: ProxyBind())
    monkeypatch.setattr(db_session, "scalar", mock_scalar)

    owner = worker_tasks._worker_id()
    claimed = worker_tasks._claim_job(db_session, job.id, owner)
    assert claimed is not None

    # 验证在 postgresql 下对 Project 执行了 with_for_update
    project_queries = [
        stmt for stmt in captured_statements
        if getattr(stmt, "_for_update_arg", None) is not None
    ]
    assert len(project_queries) == 1


def test_claim_failure_does_not_overwrite_reclaimed_lease_with_concurrency_limit(
    db_session,
):
    project = Project(name="失败抢占防覆盖测试", default_concurrency=1)
    db_session.add(project)
    db_session.flush()

    # 模拟项目当前已有一个活跃任务占满了并发名额 (concurrency = 1)
    active_job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="active-job-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        lease_owner="active-worker-x",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(active_job)
    db_session.flush()

    # 待抢占的目标任务
    target_job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="queued-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add(target_job)
    db_session.commit()

    # Worker A 尝试 claim target_job，但因 active_count >= 1 失败
    owner_a = worker_tasks._worker_id()
    claimed_a = worker_tasks._claim_job(db_session, target_job.id, owner_a)
    assert claimed_a is None

    db_session.expire_all()
    reloaded = db_session.get(GenerationJob, target_job.id)
    assert reloaded.status == JobStatus.WAITING
    assert reloaded.error_code == "CONCURRENCY_LIMIT"

    # 现在模拟活动任务完成释放了槽位，Worker B 成功抢占了 target_job
    active_job.status = JobStatus.COMPLETED
    active_job.lease_owner = None
    active_job.lease_expires_at = None
    db_session.commit()

    owner_b = worker_tasks._worker_id()
    claimed_b = worker_tasks._claim_job(db_session, target_job.id, owner_b)
    assert claimed_b is not None
    assert claimed_b.lease_owner == owner_b
    assert claimed_b.status == JobStatus.PREPARING

    # 模拟并发超额场景再次出现，另一个 Worker C 试图抢占 target_job
    # 但 target_job 此时已经被 Worker B 接管 (status=PREPARING, owner=owner_b)
    owner_c = worker_tasks._worker_id()
    claimed_c = worker_tasks._claim_job(db_session, target_job.id, owner_c)
    assert claimed_c is None

    db_session.expire_all()
    final_job = db_session.get(GenerationJob, target_job.id)
    # 验证 Worker B 的有效租约和 PREPARING 状态完好无损，没有被 Worker C 覆盖为 WAITING
    assert final_job.status == JobStatus.PREPARING
    assert final_job.lease_owner == owner_b
    expiry = (
        final_job.lease_expires_at
        if final_job.lease_expires_at.tzinfo
        else final_job.lease_expires_at.replace(tzinfo=UTC)
    )
    assert expiry > datetime.now(UTC)


def test_retryable_failure_resets_job_to_waiting_for_rq_retry(db_session):
    project = Project(name="RQ重试测试", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, status=PageStatus.DRAFT_GENERATING)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1)
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="GENERATING",
    )
    db_session.add(candidate)
    db_session.flush()
    workflow = WorkflowDefinition(project_id=project.id, name="工作流", draft_graph=default_graph())
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=default_graph(),
        graph_checksum="v" * 64,
        validation_report={"valid": True},
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status="RUNNING",
    )
    db_session.add(run)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="generate",
        node_type="generator.page",
        status="RUNNING",
    )
    db_session.add(node_run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status=JobStatus.QUEUED,
        max_attempts=3,
        request_parameters={"workflow_run_id": run.id},
    )
    db_session.add(job)
    db_session.flush()
    candidate.job_id = job.id
    node_run.job_id = job.id
    db_session.commit()

    # 1. 第一次尝试 claim (attempt 1)
    owner_1 = worker_tasks._worker_id()
    claimed = worker_tasks._claim_job(db_session, job.id, owner_1)
    assert claimed is not None
    assert claimed.attempt_count == 1
    assert claimed.status == JobStatus.PREPARING

    # 2. 模拟发生可重试的 Provider 异常
    marked, workflow_run_id, is_final = worker_tasks._mark_worker_failure(
        db_session,
        job.id,
        owner_1,
        "UPSTREAM_TIMEOUT",
        "504 Gateway Timeout",
        retryable=True,
    )
    assert marked is True
    assert is_final is False

    db_session.expire_all()
    job_after_fail = db_session.get(GenerationJob, job.id)
    # 验证任务被重置为 WAITING，等待 RQ 再次执行，而不是 FAILED
    assert job_after_fail.status == JobStatus.WAITING
    assert job_after_fail.error_code == "UPSTREAM_TIMEOUT"
    assert job_after_fail.lease_owner is None
    assert job_after_fail.lease_expires_at is None
    # 验证关联候选资源和工作流节点保持 RUNNING/GENERATING，未被误标记为 FAILED
    assert db_session.get(PageCandidate, candidate.id).status == "GENERATING"
    assert db_session.get(WorkflowNodeRun, node_run.id).status == "RUNNING"

    # 3. 模拟 RQ 重试执行（第二次 claim）
    owner_2 = worker_tasks._worker_id()
    claimed_2 = worker_tasks._claim_job(db_session, job.id, owner_2)
    assert claimed_2 is not None
    assert claimed_2.attempt_count == 2
    assert claimed_2.status == JobStatus.PREPARING
    assert claimed_2.lease_owner == owner_2


def test_terminal_failure_when_max_attempts_reached(db_session):
    project = Project(name="重试耗尽测试", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="target-exhaust",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
        max_attempts=2,
    )
    db_session.add(job)
    db_session.commit()

    # 第一次 claim
    owner_1 = worker_tasks._worker_id()
    worker_tasks._claim_job(db_session, job.id, owner_1)
    # 第一次失败 (retryable=True) -> 回到 WAITING
    worker_tasks._mark_worker_failure(db_session, job.id, owner_1, "ERR", "msg", retryable=True)

    # 第二次 claim (attempt_count = 2 == max_attempts)
    owner_2 = worker_tasks._worker_id()
    claimed_2 = worker_tasks._claim_job(db_session, job.id, owner_2)
    assert claimed_2.attempt_count == 2

    # 第二次失败 (retryable=True) -> 耗尽进入最终 FAILED
    marked, _, is_final = worker_tasks._mark_worker_failure(
        db_session,
        job.id,
        owner_2,
        "ERR",
        "msg",
        retryable=True,
    )
    assert marked is True
    assert is_final is True

    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED


def test_non_retryable_error_immediately_marks_terminal_failure(db_session):
    project = Project(name="不可重试错误测试", default_concurrency=2)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="target-non-retryable",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
        max_attempts=3,
    )
    db_session.add(job)
    db_session.commit()

    owner = worker_tasks._worker_id()
    worker_tasks._claim_job(db_session, job.id, owner)

    # 遇到明确不可重试的错误 (retryable=False)
    marked, _, is_final = worker_tasks._mark_worker_failure(
        db_session,
        job.id,
        owner,
        "INVALID_CONFIG",
        "配置错误无法重试",
        retryable=False,
    )
    assert marked is True
    assert is_final is True

    db_session.expire_all()
    # 即使 attempt_count 仅为 1，也直接进入最终 FAILED
    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED


def test_cancelled_job_does_not_move_to_generating_or_call_provider(db_session):
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker

    project = Project(name="取消后禁调用", default_concurrency=1)
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="cancel-dispatch",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.commit()

    owner = worker_tasks._worker_id()
    db_session.info["job_lease_owner"] = owner
    db_session.info["job_id"] = job.id
    claimed = worker_tasks._claim_job(db_session, job.id, owner)
    assert claimed is not None

    other_factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    with other_factory() as other:
        row = other.get(GenerationJob, job.id)
        job_service.mark_job_cancelled(other, row)
        other.commit()

    with pytest.raises(worker_tasks.JobCancelledError):
        execution._commit_owned_progress(
            db_session, claimed, status=JobStatus.GENERATING, progress=45
        )
    db_session.expire_all()
    cancelled = db_session.get(GenerationJob, job.id)
    assert cancelled.status == JobStatus.CANCELLED
    assert cancelled.cancelled_at is not None

    calls: list[int] = []
    with pytest.raises(worker_tasks.JobCancelledError):
        provider._invoke_provider(
            db_session,
            SimpleNamespace(adapter=object(), selected_key=None),
            lambda adapter: calls.append(1) or adapter,
        )
    assert calls == []


def test_redis_enqueue_does_not_overwrite_completed_job(monkeypatch):
    directory = TemporaryDirectory(ignore_cleanup_errors=True)
    engine = create_engine(
        f"sqlite:///{Path(directory.name) / 'enqueue.db'}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    try:
        with testing_session() as db:
            project = Project(name="入队不回写", default_concurrency=1)
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="enqueue-complete",
                job_type="SOURCE_PARSE",
                status=JobStatus.WAITING,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        class FakeRedis:
            def ping(self):
                return True

            def close(self):
                return None

            @classmethod
            def from_url(cls, *args, **kwargs):
                return cls()

        class FakeQueue:
            def __init__(self, *args, **kwargs):
                pass

            def enqueue(self, *args, **kwargs):
                with testing_session() as other:
                    row = other.get(GenerationJob, job_id)
                    row.status = JobStatus.COMPLETED
                    row.finished_at = datetime.now(UTC)
                    row.lease_owner = "worker-b"
                    other.commit()

        class FakeRetry:
            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr("redis.Redis", FakeRedis)
        monkeypatch.setattr("rq.Queue", FakeQueue)
        monkeypatch.setattr("rq.Retry", FakeRetry)
        with testing_session() as db:
            _set_queue_mode(db, "REDIS")
            loaded = db.get(GenerationJob, job_id)
            result = job_service.enqueue_job(db, loaded)
            assert result.status == JobStatus.COMPLETED
            assert result.lease_owner == "worker-b"
    finally:
        engine.dispose()
        directory.cleanup()



def test_enqueue_does_not_overwrite_generating_job(db_session, monkeypatch):
    """P1-9: enqueue must not revert a worker-advanced GENERATING row."""
    _set_queue_mode(db_session, "REDIS")
    job = _waiting_job(db_session, "clobber")
    job.status = JobStatus.GENERATING
    job.lease_owner = "active-worker"
    job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()
    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    enqueued = []

    class FakeRedis:
        def ping(self):
            return True

        def close(self):
            return None

        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            enqueued.append(args)

    monkeypatch.setattr("redis.Redis", FakeRedis)
    monkeypatch.setattr("rq.Queue", FakeQueue)
    result = job_service.enqueue_job(db_session, job)
    assert result.status == JobStatus.GENERATING
    assert result.lease_owner == "active-worker"
    assert enqueued == []


def test_local_retryable_failure_continues_without_restart(monkeypatch):
    from app.model_adapters.base import ProviderAdapterError

    directory = TemporaryDirectory(ignore_cleanup_errors=True)
    engine = create_engine(
        f"sqlite:///{Path(directory.name) / 'retry.db'}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    try:
        with testing_session() as db:
            project = Project(name="本地重试", default_concurrency=1)
            db.add(project)
            db.flush()
            job = GenerationJob(
                project_id=project.id,
                target_type="CHAPTER",
                target_id="local-retry",
                job_type="SOURCE_PARSE",
                status=JobStatus.WAITING,
                max_attempts=3,
            )
            db.add(job)
            db.commit()
            job_id = job.id

        monkeypatch.setattr(worker_tasks, "SessionLocal", testing_session)
        monkeypatch.setattr(database, "SessionLocal", testing_session)
        attempts = {"n": 0}

        def fake_run(_db, _job):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ProviderAdapterError("RATE_LIMIT", "稍后重试", retryable=True)

        monkeypatch.setattr(worker_tasks, "_run_story_parse", fake_run)
        with testing_session() as db:
            _set_queue_mode(db, "LOCAL")
            job_service.enqueue_job(db, db.get(GenerationJob, job_id))

        deadline = time.time() + 8
        status = None
        while time.time() < deadline:
            with testing_session() as db:
                status = db.get(GenerationJob, job_id).status
                if status == JobStatus.COMPLETED:
                    break
            time.sleep(0.05)
        assert attempts["n"] >= 2
        assert status == JobStatus.COMPLETED
    finally:
        engine.dispose()
        directory.cleanup()


def test_execute_job_defers_when_concurrency_slot_is_busy(db_session, monkeypatch):
    project = Project(name="槽位等待", default_concurrency=1)
    db_session.add(project)
    db_session.flush()
    busy = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="busy",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        lease_owner="holder",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        attempt_count=1,
    )
    waiting = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="waiting",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add_all([busy, waiting])
    db_session.commit()

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    deferred: list[str] = []
    monkeypatch.setattr(
        worker_tasks, "_defer_concurrency_wait", lambda job_id: deferred.append(job_id)
    )
    worker_tasks.execute_job(waiting.id)
    assert deferred == [waiting.id]
    db_session.expire_all()
    held = db_session.get(GenerationJob, waiting.id)
    assert held.status == JobStatus.WAITING
    assert held.error_code == "CONCURRENCY_LIMIT"
    assert held.attempt_count == 0

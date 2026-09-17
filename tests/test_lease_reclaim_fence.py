"""Regression (issue #130): lease reclaim needs executor-confirmed silence,
and a lease-lost completion must surface the discarded paid output.

Fence: ``recover_pending_jobs`` used to reclaim purely on
``lease_expires_at <= now``. An expired lease only proves "no renewal in one
full lease period" — the executor's paid provider call may legitimately still
be running (job_timeout 900s vs lease 120s). Reclaiming at first observed
expiry re-ran the job under the live executor, whose completion CAS then
failed on lease_owner and silently rolled back already-paid output (double
spend, zero observability). The janitor now reclaims only after the expiry
has been cold beyond a grace window sized from the heartbeat cadence.
"""

import logging
from datetime import UTC, datetime, timedelta

from app import database, worker_tasks
from app.config import Settings, get_settings
from app.domain.states import JobStatus
from app.models import AppSetting, GenerationJob, Project
from app.services import job_service
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker


def _session_factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)


def _set_queue_mode(db, mode: str) -> None:
    db.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db.commit()


def _seed_leased_job(db, name: str, *, expired_seconds_ago: float, **overrides) -> GenerationJob:
    project = Project(name=name)
    db.add(project)
    db.flush()
    fields = dict(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=f"target-{name}",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        max_attempts=3,
        lease_owner="starved-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=expired_seconds_ago),
    )
    fields.update(overrides)
    job = GenerationJob(**fields)
    db.add(job)
    db.commit()
    return job


def test_janitor_skips_lease_expired_within_grace(db_session, monkeypatch):
    """Expiry alone is not silence: a lease cold for less than the grace
    window (60s at default settings) is left alone, and the still-alive
    executor's lease-fenced completion CAS can still win — its paid output is
    not forfeited to a premature requeue."""

    _set_queue_mode(db_session, "LOCAL")
    monkeypatch.setattr(get_settings(), "queue_enabled", True)
    job = _seed_leased_job(
        db_session, "围栏内过期", expired_seconds_ago=30, attempt_count=3, max_attempts=3
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)
    monkeypatch.setattr(job_service, "enqueue_job", lambda db, job: job)

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 0
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.GENERATING
    assert row.lease_owner == "starved-worker"
    assert row.error_code is None

    # The executor that was merely slow returns inside the fence and its
    # completion CAS still owns the row (issue #130's zombie outcome prevented).
    completed = db_session.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.lease_owner == "starved-worker",
        )
        .values(
            status=JobStatus.COMPLETED,
            progress=100,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    assert completed.rowcount == 1


def test_janitor_reclaims_lease_cold_beyond_grace(db_session, monkeypatch):
    _set_queue_mode(db_session, "LOCAL")
    monkeypatch.setattr(get_settings(), "queue_enabled", True)
    job = _seed_leased_job(db_session, "围栏外过期", expired_seconds_ago=120)
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)
    monkeypatch.setattr(job_service, "enqueue_job", lambda db, job: job)

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 1
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == JobStatus.WAITING
    assert row.error_code == "LEASE_EXPIRED"
    assert row.lease_owner is None
    assert row.lease_expires_at is None


def test_grace_setting_overrides_derived_fence(db_session, monkeypatch):
    """The explicit setting drives the fence: a large value holds a long-cold
    lease, a zero value reclaims immediately (fence disabled)."""

    _set_queue_mode(db_session, "LOCAL")
    settings = get_settings()
    monkeypatch.setattr(settings, "queue_enabled", True)
    job = _seed_leased_job(
        db_session, "围栏配置覆盖", expired_seconds_ago=45, attempt_count=3, max_attempts=3
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    monkeypatch.setattr(settings, "job_lease_reclaim_grace_seconds", 3600)
    job_service.recover_pending_jobs(db_session)
    db_session.expire_all()
    held = db_session.get(GenerationJob, job.id)
    assert held.status == JobStatus.GENERATING
    assert held.lease_owner == "starved-worker"

    monkeypatch.setattr(settings, "job_lease_reclaim_grace_seconds", 0)
    job_service.recover_pending_jobs(db_session)
    db_session.expire_all()
    reclaimed = db_session.get(GenerationJob, job.id)
    assert reclaimed.status == JobStatus.FAILED
    assert reclaimed.error_code == "LEASE_EXPIRED"
    assert reclaimed.finished_at is not None


def test_derived_grace_matches_heartbeat_geometry():
    """grace = max(2 * heartbeat_interval, lease / 3); 60s at default lease
    (heartbeat 30s), proportional for long leases, explicit override wins."""

    assert job_service._lease_reclaim_grace_seconds(Settings(environment="dev")) == 60.0
    # Lease geometry requires lease <= timeout, so a max-length lease pairs
    # with a max-length timeout; the grace derivation only reads the lease.
    assert (
        job_service._lease_reclaim_grace_seconds(
            Settings(job_lease_seconds=3600, job_timeout_seconds=3600)
        )
        == 1200.0
    )
    # Minimum lease 30s: heartbeat = max(5, min(30, 10)) = 10s -> max(20, 10).
    assert job_service._lease_reclaim_grace_seconds(Settings(job_lease_seconds=30)) == 20.0
    assert (
        job_service._lease_reclaim_grace_seconds(
            Settings(job_lease_seconds=120, job_lease_reclaim_grace_seconds=0)
        )
        == 0.0
    )


def test_completion_lease_lost_logs_double_spend_warning(db_session, monkeypatch, caplog):
    """The completion-side CAS failure (worker_tasks) raises JobLeaseLostError;
    its rollback discards the handler's uncommitted paid output. That discard
    must be loud: job id, job_type, attempt, loss reason and the double-spend
    risk (#130 observability half)."""

    project = Project(name="完成侧租约丢失")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="target-lost-completion",
        job_type="SOURCE_PARSE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(worker_tasks, "SessionLocal", _session_factory(db_session))

    def _steal_lease_then_succeed(db, stolen):
        # The handler "finishes its paid call"; meanwhile the janitor
        # reclaimed the row and a second executor owns the lease.
        db.execute(
            update(GenerationJob)
            .where(GenerationJob.id == stolen.id)
            .values(
                status=JobStatus.GENERATING,
                lease_owner="reclaimer",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
            )
        )
        db.commit()

    monkeypatch.setattr(worker_tasks, "_run_story_parse", _steal_lease_then_succeed)
    # Steal AFTER the pre-completion guard so the completion CAS is the branch
    # that fires JobLeaseLostError (the issue-cited silent rollback site).
    monkeypatch.setattr(worker_tasks, "_ensure_job_not_cancelled", lambda db, job: None)

    with caplog.at_level(logging.WARNING, logger="mangaflow.worker"):
        worker_tasks.execute_job(job.id)

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "double-spend" in record.getMessage()
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert job.id in message
    assert "SOURCE_PARSE" in message
    assert "attempt 1" in message
    assert "任务租约已被其他执行器接管" in message


def test_heartbeat_renews_an_expired_but_unreclaimed_lease(db_session, monkeypatch):
    """WE1 (round-10): the heartbeat's renewal CAS used to fence on
    ``lease_expires_at > now`` — a DB outage that froze the heartbeat thread
    made the FIRST successful renewal after reconnect observe the lapsed
    lease, mark itself lost, and every later guard discarded the
    already-billed output even though nobody reclaimed the row (#130's exact
    starved-but-alive scenario). Renewal is now ownership-fenced: an
    expired-but-own lease revives; a reclaimed (owner-flipped) row loses."""

    job = _seed_leased_job(db_session, "围栏内自续约", expired_seconds_ago=30)
    monkeypatch.setattr(worker_tasks, "SessionLocal", _session_factory(db_session))

    heartbeat = worker_tasks._LeaseHeartbeat(job.id, "starved-worker")
    assert heartbeat._renew_once() is True
    assert heartbeat.lost is False

    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.lease_owner == "starved-worker"
    refreshed = row.lease_expires_at
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=UTC)
    assert refreshed > datetime.now(UTC)

    # The janitor reclaimed mid-flight: the owner flipped, renewal fails.
    db_session.execute(
        update(GenerationJob)
        .where(GenerationJob.id == job.id)
        .values(lease_owner=None, status=JobStatus.WAITING)
    )
    db_session.commit()

    assert heartbeat._renew_once() is False
    assert heartbeat.lost is True


def test_transient_heartbeat_error_keeps_renewing(db_session, monkeypatch):
    """R2A-01: 一次瞬态 DB 异常（锁超时/连接池耗尽）不得永久终止心跳线程——
    _renew_once 的异常分支曾把「等待 1 秒后继续」回归成「返回 stop.wait(1.0)」
    （未停止时恒 False，_run 随即退出）。心跳死亡 → 租约过期 → janitor 收回
    重派 → 第二个执行器重跑付费调用，正是 #130 fence 要防的双花。"""

    job = _seed_leased_job(
        db_session, "瞬态异常续租", expired_seconds_ago=0, lease_owner="flaky-worker"
    )
    real_factory = _session_factory(db_session)
    broken = {"on": True}

    def flaky_factory():
        if broken["on"]:
            broken["on"] = False
            raise RuntimeError("database is locked")
        return real_factory()

    monkeypatch.setattr(worker_tasks, "SessionLocal", flaky_factory)

    heartbeat = worker_tasks._LeaseHeartbeat(job.id, "flaky-worker")
    assert heartbeat._renew_once() is True, "瞬态 DB 异常后心跳必须继续续租"
    assert heartbeat.lost is False

    monkeypatch.setattr(worker_tasks, "SessionLocal", real_factory)
    assert heartbeat._renew_once() is True, "数据库恢复后续租照常成功"
    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.lease_owner == "flaky-worker"


def test_cli_cancel_probe_honors_reclaim_grace(db_session):
    """R2A-03: CLI 取消探针不得在租约刚过期时杀掉付费子进程——#130 fence 的
    契约是「过期但未被收回的租约仍属本执行器」。只有显式取消、owner 易主
    （被 janitor 收回）、或过期冷过整个回收宽限窗（墙钟超时路径）才停。"""

    from app.services.job_service import cli_cancel_probe_should_stop

    settings = Settings(environment="dev")   # 派生宽限 = max(2×30s, 120/3) = 60s
    job = _seed_leased_job(db_session, "CLI 探针宽限", expired_seconds_ago=30)

    assert cli_cancel_probe_should_stop(job, job.lease_owner, settings) is False, (
        "刚过期且 owner 未变：付费子进程必须继续跑（完成 CAS 会仲裁归属）"
    )
    assert cli_cancel_probe_should_stop(job, "successor-owner", settings) is True, (
        "owner 易主（已被收回）：立即停止"
    )

    job.status = JobStatus.CANCELLED
    assert cli_cancel_probe_should_stop(job, job.lease_owner, settings) is True

    cold = _seed_leased_job(db_session, "CLI 冷冻宽限", expired_seconds_ago=600)
    assert cli_cancel_probe_should_stop(cold, cold.lease_owner, settings) is True, (
        "过期冷过整个宽限窗（墙钟超时路径）：停止付费子进程"
    )


def test_cli_adapters_delegate_the_cancel_probe_to_the_shared_predicate():
    """R2A-03（源契约钉）：三个 CLI 适配器的 _cancel_requested 必须委托共享谓词
    （cli_cancel_probe_should_stop），而不是各自内联「过期即杀」的旧逻辑——
    旧内联正是被收编掉的 #130 契约违背点。"""

    import pathlib

    adapters_root = (
        pathlib.Path(__file__).resolve().parents[1]
        / "apps" / "api" / "app" / "model_adapters"
    )
    for name in ("antigravity_cli.py", "grok_build_cli.py", "codex_cli.py"):
        source = (adapters_root / name).read_text(encoding="utf-8")
        probe = source[source.index("def _cancel_requested"):]
        probe = probe[: probe.index("\n    def \n") if "\n    def \n" in probe else len(probe)]
        assert "cli_cancel_probe_should_stop" in probe, (
            f"{name} 的取消探针必须委托共享谓词（#130 宽限契约）"
        )
        assert "expires_at <=" not in probe, (
            f"{name} 的取消探针不得内联「租约刚过期即杀」的旧判定"
        )


def test_handler_guards_treat_own_expired_lease_as_still_owned(db_session):
    """WE1, handler-side seams: _ensure_job_not_cancelled and the owned
    progress/checkpoint CASes used to treat a merely-expired OWN lease as
    lost — the post-paid-call guard raised JobLeaseLostError and the
    completion rolled back billed output that the owner-matched completion
    CAS (the #130 arbiter) would have committed. Ownership loss — owner
    flipped or lease reset — is what must raise."""

    from app.services.worker_handlers.execution import (
        JobLeaseLostError,
        _commit_owned_progress,
        _ensure_job_not_cancelled,
    )

    job = _seed_leased_job(db_session, "围栏内完成", expired_seconds_ago=30)
    job.status = JobStatus.GENERATING
    db_session.commit()
    db_session.info["job_lease_owner"] = "starved-worker"

    # Merely expired, still ours: no raise, and the owned write lands.
    _ensure_job_not_cancelled(db_session, job)
    _commit_owned_progress(db_session, job, status=JobStatus.GENERATING, progress=55)
    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).progress == 55

    # A successor executor holds the row: the guard raises (the write above
    # would have been the successor's to make).
    db_session.execute(
        update(GenerationJob)
        .where(GenerationJob.id == job.id)
        .values(lease_owner="successor-worker")
    )
    db_session.commit()
    try:
        _ensure_job_not_cancelled(db_session, job)
        raise AssertionError("owner flip must raise JobLeaseLostError")
    except JobLeaseLostError:
        pass


def test_execute_locally_logs_lease_lost_discard(db_session, monkeypatch, caplog):
    """The local executor's silent except-return (job_service) gets the same
    warning with row context (defense in depth for lease-lost raises from
    seams outside execute_job's own handler)."""

    project = Project(name="本地执行器租约丢失")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="target-lost-local",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=2,
        max_attempts=3,
        lease_owner="another-owner",
    )
    db_session.add(job)
    db_session.commit()

    factory = _session_factory(db_session)
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    monkeypatch.setattr(database, "SessionLocal", factory)

    def _raise_lease_lost(_job_id):
        raise worker_tasks.JobLeaseLostError("任务租约已被其他执行器接管")

    monkeypatch.setattr(worker_tasks, "execute_job", _raise_lease_lost)

    with caplog.at_level(logging.WARNING, logger="mangaflow.jobs"):
        job_service._execute_locally(job.id)

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "double-spend" in record.getMessage()
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert job.id in message
    assert "SOURCE_PARSE" in message
    assert "attempt 2" in message

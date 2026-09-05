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

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app import database, worker_tasks
from app.config import Settings, get_settings
from app.domain.states import JobStatus
from app.models import AppSetting, GenerationJob, Project
from app.services import job_service


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
    assert job_service._lease_reclaim_grace_seconds(Settings(job_lease_seconds=3600)) == 1200.0
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

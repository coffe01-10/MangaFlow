"""Regression tests for the Windows spawn-worker horse and timeout recovery.

The spawn worker's horse is a plain ``python -c`` child (see
``app.rq_windows``), so its import surface and its failure modes differ from
the POSIX fork worker. These tests are platform-neutral: they exercise the
same environment construction and parent-side recovery code paths that run on
Windows without requiring a Windows host.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

import app.rq_windows as rq_windows
from app import database
from app.config import Settings
from app.domain.states import JobStatus
from app.models import AppSetting, GenerationJob, ModelCallAttempt, Project
from app.rq_windows import horse_environment
from app.services import job_service

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = str(Path(rq_windows.__file__).resolve().parents[1])
TIMEOUT_MESSAGE = "生成超时，已由执行器强制终止"


def _resolve(entry: str) -> Path:
    return Path(entry).resolve()


def test_horse_environment_prepends_api_root_and_keeps_existing_pythonpath():
    env = horse_environment({"PYTHONPATH": os.pathsep.join(["/custom-a", "/custom-b"])})

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert _resolve(entries[0]) == Path(API_ROOT).resolve()
    assert entries[1:] == ["/custom-a", "/custom-b"]

    fresh = horse_environment({"PYTHONPATH": ""})
    assert _resolve(fresh["PYTHONPATH"]) == Path(API_ROOT).resolve()

    absent = horse_environment({})
    assert _resolve(absent["PYTHONPATH"]) == Path(API_ROOT).resolve()


def test_horse_subprocess_imports_app_from_repo_root():
    """The spawn horse must import ``app`` with only its child environment.

    Reproduces the shipped dev path: the horse is spawned as ``python -c``
    from the repo root, where ``sys.path[0]`` is the cwd and rq's ``--path``
    never reaches the child. The fixed environment must let the horse reach
    ``app.worker_tasks`` (where ``execute_job`` lives) or every RQ job burns
    its retry budget before running a single statement.
    """

    proc = subprocess.run(
        [sys.executable, "-c", "import app, app.worker_tasks; print('ok')"],
        cwd=str(REPO_ROOT),
        env=horse_environment(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_horse_import_fails_without_the_pythonpath_fix():
    """Negative control: the same spawn without the API root cannot import app.

    Skipped instead of failing when some ambient mechanism (a .pth file, a
    preset PYTHONPATH) already provides the ``app`` package from the repo
    root, which would make the control meaningless on that host.
    """

    env = horse_environment()
    stripped = os.pathsep.join(
        entry
        for entry in env.get("PYTHONPATH", "").split(os.pathsep)
        if entry and _resolve(entry) != Path(API_ROOT).resolve()
    )
    if stripped:
        env["PYTHONPATH"] = stripped
    else:
        env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [sys.executable, "-c", "import app; print('ok')"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode == 0:
        pytest.skip("ambient path already provides app")
    assert "ok" not in proc.stdout


def _session_factory(db_session):
    return sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )


def _set_queue_mode(db_session, mode: str) -> None:
    db_session.add(AppSetting(key="runtime", value={"queue_mode": mode}, version=1))
    db_session.commit()


def _seed_job(db_session, name: str, **overrides) -> GenerationJob:
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    fields = dict(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=f"target-{name}",
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
        attempt_count=1,
        max_attempts=3,
        lease_owner="spawn-worker",
    )
    fields.update(overrides)
    job = GenerationJob(**fields)
    db_session.add(job)
    db_session.flush()
    return job


def _seed_attempt(db_session, job: GenerationJob) -> ModelCallAttempt:
    """A dispatch attempt the killed horse never finalized (outcome NULL)."""
    attempt = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="fake-provider",
        model_id="fake-model",
    )
    db_session.add(attempt)
    db_session.flush()
    return attempt


def test_persist_timeout_marker_stamps_only_active_leased_jobs(
    db_session, monkeypatch
):
    """The monitor's pre-kill marker must land on the leased row and nowhere else."""

    monkeypatch.setattr(database, "SessionLocal", _session_factory(db_session))
    leased = _seed_job(
        db_session,
        "timeout-marker",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    terminal = _seed_job(
        db_session,
        "already-failed",
        status=JobStatus.FAILED,
        lease_owner=None,
        lease_expires_at=None,
        error_code="PROVIDER_ERROR",
        error_message="earlier failure",
    )
    db_session.commit()

    rq_windows._persist_timeout_marker(leased.id)
    # A missing or non-leased job id must be a silent no-op: a DB hiccup or a
    # raced row may never break the kill path this call precedes.
    rq_windows._persist_timeout_marker("no-such-job")

    db_session.expire_all()
    stamped = db_session.get(GenerationJob, leased.id)
    assert stamped.status == JobStatus.GENERATING
    assert stamped.error_code == "JOB_TIMEOUT"
    assert stamped.error_message == TIMEOUT_MESSAGE

    untouched = db_session.get(GenerationJob, terminal.id)
    assert untouched.error_code == "PROVIDER_ERROR"
    assert untouched.error_message == "earlier failure"


def test_recovery_terminalizes_timed_out_jobs_preserving_timeout_cause(
    db_session, monkeypatch
):
    """Exhausted timeout jobs must surface JOB_TIMEOUT, not LEASE_EXPIRED.

    The WindowsSpawnWorker monitor kills the horse without any in-horse
    cleanup; the RQ retry no-ops inside the still-valid lease, so the API-side
    recovery pass is what terminalizes the row. Its FAILED branch must keep
    the pre-kill JOB_TIMEOUT marker and finalize the attempts the horse left
    with a NULL outcome.
    """

    _set_queue_mode(db_session, "LOCAL")
    timed_out = _seed_job(
        db_session,
        "timeout-exhausted",
        attempt_count=3,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
        error_code="JOB_TIMEOUT",
        error_message=TIMEOUT_MESSAGE,
    )
    stale_attempt = _seed_attempt(db_session, timed_out)
    control = _seed_job(
        db_session,
        "lease-expired-control",
        attempt_count=3,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    control_attempt = _seed_attempt(db_session, control)
    db_session.commit()

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 0
    db_session.expire_all()
    final = db_session.get(GenerationJob, timed_out.id)
    assert final.status == JobStatus.FAILED
    assert final.error_code == "JOB_TIMEOUT"
    assert final.error_message == TIMEOUT_MESSAGE

    swept = db_session.get(ModelCallAttempt, stale_attempt.id)
    assert swept.outcome == "FAILED"
    assert swept.error_code == "JOB_TIMEOUT"
    assert swept.finished_at is not None

    # Control: a plain expired lease keeps the legacy behavior unchanged.
    control_final = db_session.get(GenerationJob, control.id)
    assert control_final.status == JobStatus.FAILED
    assert control_final.error_code == "LEASE_EXPIRED"
    control_row = db_session.get(ModelCallAttempt, control_attempt.id)
    assert control_row.outcome == "FAILED"
    assert control_row.error_code == "LEASE_EXPIRED"
    assert control_row.finished_at is not None


def test_recovery_requeue_preserves_timeout_cause_and_live_attempts(
    db_session, monkeypatch
):
    """Below max attempts, a timed-out job requeues keeping its timeout cause.

    The sweep of NULL-outcome attempts belongs to the terminal branch only:
    live (unexpired) jobs and requeued jobs keep their attempts untouched.
    """

    _set_queue_mode(db_session, "LOCAL")
    live = _seed_job(
        db_session,
        "live-lease",
        attempt_count=1,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    live_attempt = _seed_attempt(db_session, live)
    requeued = _seed_job(
        db_session,
        "requeue-timeout",
        attempt_count=1,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
        error_code="JOB_TIMEOUT",
        error_message=TIMEOUT_MESSAGE,
    )
    requeue_attempt = _seed_attempt(db_session, requeued)
    db_session.commit()

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "enqueue_job", lambda db, job: job)
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    recovered = job_service.recover_pending_jobs(db_session)

    assert recovered == 1
    db_session.expire_all()
    live_row = db_session.get(GenerationJob, live.id)
    assert live_row.status == JobStatus.GENERATING
    live_attempt_row = db_session.get(ModelCallAttempt, live_attempt.id)
    assert live_attempt_row.outcome is None
    assert live_attempt_row.error_code is None
    assert live_attempt_row.finished_at is None

    requeue_row = db_session.get(GenerationJob, requeued.id)
    assert requeue_row.status == JobStatus.WAITING
    assert requeue_row.error_code == "JOB_TIMEOUT"
    assert requeue_row.error_message == TIMEOUT_MESSAGE
    requeue_attempt_row = db_session.get(ModelCallAttempt, requeue_attempt.id)
    assert requeue_attempt_row.outcome is None
    assert requeue_attempt_row.finished_at is None

"""LOCAL wall-clock cap regressions (R-8 sub-fix A).

The lease heartbeat carries a wall-clock deadline; past the job budget it
stamps LOCAL_TIMEOUT once and stops renewing, recovery preserves the cause
through reclaim/requeue (like RQ's JOB_TIMEOUT), and every genai.Client bounds
a single HTTP request at the same budget (the heartbeat cannot interrupt a
thread blocked inside one call).

Seeding follows tests/test_local_worker.py's harness: a per-test engine via
the db_session fixture and monkeypatched SessionLocal factories.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from app import worker_tasks
from app.config import Settings, get_settings
from app.domain.states import JobStatus
from app.model_adapters import google as google_adapter
from app.models import AppSetting, GenerationJob, ModelCallAttempt, Project
from app.services import job_service, vertex_credentials

LOCAL_TIMEOUT_WAITING_MESSAGE = "本地执行超过墙钟上限，等待租约过期回收"
LOCAL_TIMEOUT_REQUEUE_MESSAGE = "本地执行超过墙钟上限，任务等待重新执行"
LOCAL_TIMEOUT_TERMINAL_MESSAGE = "本地执行超过墙钟上限，且已达到最大尝试次数"


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
        lease_owner="pinned-worker",
    )
    fields.update(overrides)
    job = GenerationJob(**fields)
    db_session.add(job)
    db_session.flush()
    return job


class _BoundedWaits:
    """Duck-typed Event that converts an endless poller into a test failure.

    A pre-fix heartbeat (renewing forever) would hang the suite instead of
    failing it; after ``max_calls`` waits this guard raises AssertionError so
    the regression surfaces honestly as a failure. Post-fix the loop exits by
    itself before the cap is hit.
    """

    def __init__(self, max_calls: int, message: str):
        self.max_calls = max_calls
        self.message = message
        self.calls = 0

    def wait(self, _timeout=None):
        self.calls += 1
        if self.calls > self.max_calls:
            raise AssertionError(self.message)
        return False


class _StopAfterFirstRenewal:
    """Duck-typed Event: enter the loop body once, then signal stop."""

    def __init__(self):
        self.calls = 0

    def wait(self, _timeout=None):
        self.calls += 1
        return self.calls > 1


def test_heartbeat_past_deadline_marks_local_timeout_once_without_renewal(
    db_session, monkeypatch, caplog
):
    """Expired deadline: no renewal, one LOCAL_TIMEOUT write, second pass no-op.

    The deadline is injected directly (nullable design) so the heartbeat's
    ``_run`` is testable without ``__enter__``.
    """

    job = _seed_job(
        db_session,
        "wallclock-heartbeat",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    db_session.commit()

    monkeypatch.setattr(worker_tasks, "SessionLocal", _session_factory(db_session))
    heartbeat = worker_tasks._LeaseHeartbeat(job.id, "pinned-worker")
    heartbeat.interval = 0
    heartbeat.deadline = time.monotonic() - 1  # budget already spent
    stop_guard = _BoundedWaits(
        3, "heartbeat kept renewing past the wall-clock deadline"
    )
    heartbeat.stop = stop_guard

    db_session.expire_all()
    before_lease = db_session.get(GenerationJob, job.id).lease_expires_at

    with caplog.at_level(logging.WARNING, logger="mangaflow.worker"):
        heartbeat._run()

        db_session.expire_all()
        marked = db_session.get(GenerationJob, job.id)
        assert marked.error_code == "LOCAL_TIMEOUT"
        assert marked.error_message == LOCAL_TIMEOUT_WAITING_MESSAGE
        # The row keeps its live lease and ACTIVE status: the wedged thread
        # still owns it, recovery reclaims it only once the lease expires.
        assert marked.status == JobStatus.GENERATING
        assert marked.lease_owner == "pinned-worker"
        assert marked.lease_expires_at == before_lease  # NOT renewed
        assert heartbeat.timed_out is True
        assert heartbeat.lost is False
        assert len(caplog.records) == 1

        first_pass_updated_at = marked.updated_at
        first_marker = (marked.error_code, marked.error_message)

        heartbeat._run()  # second pass: one-shot marker must be a no-op

        db_session.expire_all()
        remarked = db_session.get(GenerationJob, job.id)
        assert (remarked.error_code, remarked.error_message) == first_marker
        assert remarked.updated_at == first_pass_updated_at  # no second UPDATE
        assert remarked.lease_expires_at == before_lease
        assert len(caplog.records) == 1  # still logged exactly once

    assert stop_guard.calls <= 2  # each pass exited by itself; guard never tripped


def test_heartbeat_within_deadline_keeps_renewing_lease(db_session, monkeypatch):
    """Preservation: a healthy heartbeat inside the budget still renews."""

    job = _seed_job(
        db_session,
        "healthy-heartbeat",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    db_session.commit()

    monkeypatch.setattr(worker_tasks, "SessionLocal", _session_factory(db_session))
    heartbeat = worker_tasks._LeaseHeartbeat(job.id, "pinned-worker")
    heartbeat.interval = 0
    heartbeat.deadline = time.monotonic() + 3600  # comfortably inside the budget
    heartbeat.stop = _StopAfterFirstRenewal()

    db_session.expire_all()
    before_lease = db_session.get(GenerationJob, job.id).lease_expires_at

    heartbeat._run()

    db_session.expire_all()
    renewed = db_session.get(GenerationJob, job.id)
    assert renewed.lease_expires_at > before_lease
    assert renewed.status == JobStatus.GENERATING
    assert renewed.error_code is None
    assert heartbeat.lost is False
    assert heartbeat.timed_out is False


def test_recovery_terminalizes_local_timeout_preserving_cause(db_session, monkeypatch):
    """Exhausted LOCAL_TIMEOUT must surface its cause, not LEASE_EXPIRED."""

    _set_queue_mode(db_session, "LOCAL")
    local_timeout = _seed_job(
        db_session,
        "local-timeout-exhausted",
        attempt_count=3,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
        error_code="LOCAL_TIMEOUT",
        error_message=LOCAL_TIMEOUT_WAITING_MESSAGE,
    )
    stale_attempt = ModelCallAttempt(
        job_id=local_timeout.id,
        project_id=local_timeout.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="fake-provider",
        model_id="fake-model",
    )
    db_session.add(stale_attempt)
    # Control: a plain expired lease keeps the legacy terminal behavior.
    control = _seed_job(
        db_session,
        "lease-expired-control",
        attempt_count=3,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    db_session.commit()

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    recovered = job_service.recover_pending_jobs(db_session)
    assert recovered == 0

    db_session.expire_all()
    final = db_session.get(GenerationJob, local_timeout.id)
    assert final.status == JobStatus.FAILED
    assert final.error_code == "LOCAL_TIMEOUT"
    assert final.error_message == LOCAL_TIMEOUT_TERMINAL_MESSAGE

    swept = db_session.get(ModelCallAttempt, stale_attempt.id)
    assert swept.outcome == "FAILED"
    assert swept.error_code == "LOCAL_TIMEOUT"
    assert swept.finished_at is not None

    control_final = db_session.get(GenerationJob, control.id)
    assert control_final.status == JobStatus.FAILED
    assert control_final.error_code == "LEASE_EXPIRED"


def test_recovery_requeue_preserves_local_timeout_cause(db_session, monkeypatch):
    """Below max attempts, a LOCAL_TIMEOUT row requeues keeping its cause."""

    _set_queue_mode(db_session, "LOCAL")
    requeued = _seed_job(
        db_session,
        "local-timeout-requeue",
        attempt_count=1,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
        error_code="LOCAL_TIMEOUT",
        error_message=LOCAL_TIMEOUT_WAITING_MESSAGE,
    )
    attempt = ModelCallAttempt(
        job_id=requeued.id,
        project_id=requeued.project_id,
        job_attempt=1,
        dispatch_no=1,
        provider="fake-provider",
        model_id="fake-model",
    )
    db_session.add(attempt)
    db_session.commit()

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "enqueue_job", lambda db, job: job)
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)

    recovered = job_service.recover_pending_jobs(db_session)
    assert recovered == 1

    db_session.expire_all()
    row = db_session.get(GenerationJob, requeued.id)
    assert row.status == JobStatus.WAITING
    assert row.error_code == "LOCAL_TIMEOUT"
    assert row.error_message == LOCAL_TIMEOUT_REQUEUE_MESSAGE
    # The NULL-outcome sweep belongs to the terminal branch only.
    assert db_session.get(ModelCallAttempt, attempt.id).outcome is None


def test_genai_http_options_bounded_by_job_budget(monkeypatch):
    """Both genai seams expose the same job-budget milliseconds timeout."""

    expected = {"timeout": get_settings().job_timeout_seconds * 1000}
    assert google_adapter.genai_http_options() == expected
    assert vertex_credentials.genai_http_options() == expected

    monkeypatch.setattr(
        google_adapter, "get_settings", lambda: SimpleNamespace(job_timeout_seconds=45)
    )
    monkeypatch.setattr(
        vertex_credentials,
        "get_settings",
        lambda: SimpleNamespace(job_timeout_seconds=45),
    )
    assert google_adapter.genai_http_options() == {"timeout": 45000}
    assert vertex_credentials.genai_http_options() == {"timeout": 45000}


def test_google_text_client_carries_timeout_into_real_genai_client():
    """Wire path: adapter._client() builds a genai.Client with the timeout set.

    Offline construction only (no request is made); google-genai 1.75.0's
    types.HttpOptions.timeout is milliseconds and lands on
    client._api_client._http_options.timeout.
    """

    adapter = google_adapter.GoogleTextAdapter(
        google_adapter.GoogleRuntime(
            api_key="offline-test-key", model_id="gemini-test", display_name="Gemini"
        )
    )
    client = adapter._client()
    try:
        assert client._api_client._http_options.timeout == (
            get_settings().job_timeout_seconds * 1000
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

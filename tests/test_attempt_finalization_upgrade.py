"""Late-success finalize over a sweep-closed attempt row.

Regression cover for the LOCAL wall-clock hole: the recovery sweep's NULL-
attempt closeout (job_service.recover_pending_jobs terminal branch) stamps a
genuinely-SUCCEEDED paid call as FAILED — outcome=FAILED with a sweep terminal
error code and no usage — because the LOCAL worker thread stays alive past its
expired lease while wedged inside the provider call. The late finalize then
used to hard-raise ("模型调用审计行已由其他终态完成"), surfacing as non-retryable
AUDIT_PERSISTENCE_FAILED: the paid image was discarded and the ledger row kept
outcome=FAILED with NULL usage (real spend recorded as failed/zero).

finalize_model_call_attempt now upgrades a SUCCEEDED finalize over a
sweep-closed row; these tests pin the upgrade, its guard rails (genuine
failures and rows that already carry usage must still refuse), the idempotent
same-outcome replay, and the full sweep-then-finalize chain.

Seeding follows tests/test_model_call_audit.py (audit session factory) and
tests/test_local_wall_clock.py (recovery sweep driving).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

import app.services.worker_handlers.model_call_audit as audit
from app.config import Settings
from app.models import (
    AppSetting,
    GenerationJob,
    ModelCallAttempt,
    Project,
)
from app.services import job_service
from app.services.worker_handlers.model_call_audit import (
    ModelCallAttemptMeta,
    begin_model_call_attempt,
    finalize_model_call_attempt,
)

LOCAL_TIMEOUT_TERMINAL_MESSAGE = "本地执行超过墙钟上限，且已达到最大尝试次数"
LOCAL_TIMEOUT_WAITING_MESSAGE = "本地执行超过墙钟上限，等待租约过期回收"


def _seed_job(db_session, name: str, **overrides) -> GenerationJob:
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    fields = dict(
        project_id=project.id,
        target_type="CHAPTER",
        target_id=f"target-{name}",
        job_type="SOURCE_PARSE",
        status="GENERATING",
        attempt_count=1,
        max_attempts=3,
    )
    fields.update(overrides)
    job = GenerationJob(**fields)
    db_session.add(job)
    db_session.flush()
    return job


def _meta(job: GenerationJob, **overrides) -> ModelCallAttemptMeta:
    values = {
        "job_id": job.id,
        "project_id": job.project_id,
        "job_attempt": 1,
        "provider": "preset-provider",
        "model_id": "model-xyz",
        "route_reason": "EXPLICIT",
    }
    values.update(overrides)
    return ModelCallAttemptMeta(**values)


def _sweep_close_job_attempts(job_id: str, error_code: str, message: str) -> None:
    """Replay the recovery sweep's NULL-outcome closeout verbatim.

    The sweep's terminal branch writes only outcome/error_code/error_message/
    finished_at — never usage columns, because the sweep cannot know them.
    """

    with audit.SessionLocal() as db:
        db.execute(
            update(ModelCallAttempt)
            .where(
                ModelCallAttempt.job_id == job_id,
                ModelCallAttempt.outcome.is_(None),
            )
            .values(
                outcome="FAILED",
                error_code=error_code,
                error_message=message,
                finished_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()


def test_sweep_terminal_error_codes_are_the_exact_discriminator_set():
    """The upgrade discriminator is exactly the sweep's terminal code set.

    Genuine worker-failure finalizes write adapter error codes (UPSTREAM,
    RATE_LIMIT, AUTHENTICATION, ...) which never collide with these four
    sweep/recovery markers; usage_status alone is NOT a discriminator
    (genuine failures also write UNKNOWN).
    """

    from app.services.worker_handlers.model_call_audit import (
        SWEEP_TERMINAL_ERROR_CODES,
    )

    assert SWEEP_TERMINAL_ERROR_CODES == frozenset(
        {"JOB_TIMEOUT", "LOCAL_TIMEOUT", "LEASE_EXPIRED", "WORKER_LOST"}
    )


def test_late_succeeded_finalize_upgrades_sweep_closed_attempt(db_session):
    """T1: SUCCEEDED finalize over a sweep-closed row upgrades it, with usage.

    Pre-fix this finalize raised RuntimeError (and surfaced downstream as
    non-retryable AUDIT_PERSISTENCE_FAILED), discarding the paid result and
    leaving the row FAILED with NULL usage.
    """

    job = _seed_job(db_session, "sweep-upgrade")
    db_session.commit()
    attempt_id = begin_model_call_attempt(_meta(job))
    _sweep_close_job_attempts(job.id, "LOCAL_TIMEOUT", LOCAL_TIMEOUT_TERMINAL_MESSAGE)

    finalize_model_call_attempt(
        attempt_id,
        outcome="SUCCEEDED",
        model_id="model-reported-by-adapter",
        request_id="req-late-success",
        usage={"input_tokens": 11, "output_tokens": 7},
        output_image_count=1,
    )

    with audit.SessionLocal() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "SUCCEEDED"
    assert row.error_code is None
    assert row.error_message is None
    assert row.usage == {"input_tokens": 11, "output_tokens": 7}
    assert row.request_id == "req-late-success"
    assert row.model_id == "model-reported-by-adapter"
    assert row.usage_status == "COMPLETE"
    assert row.usage_source == "PROVIDER_REPORTED"
    assert row.unit_kind == "MIXED"
    assert row.finished_at is not None
    assert row.duration_ms is not None and row.duration_ms >= 0


def test_succeeded_finalize_over_genuine_failure_still_raises(db_session):
    """T2 preservation pin: a FAILED row carrying an adapter error code is a
    genuine failure, not a sweep guess — the late SUCCEEDED finalize must keep
    refusing (pre- and post-fix both raise)."""

    job = _seed_job(db_session, "genuine-failure-guard")
    db_session.commit()
    attempt_id = begin_model_call_attempt(_meta(job))
    finalize_model_call_attempt(
        attempt_id,
        outcome="FAILED",
        error_code="UPSTREAM",
        error_message="上游返回错误",
    )

    with pytest.raises(RuntimeError, match="模型调用审计行已由其他终态完成"):
        finalize_model_call_attempt(
            attempt_id,
            outcome="SUCCEEDED",
            request_id="req-must-not-land",
            usage={"input_tokens": 5},
        )

    with audit.SessionLocal() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "FAILED"
    assert row.error_code == "UPSTREAM"
    assert row.usage is None
    assert row.request_id is None


def test_succeeded_finalize_over_sweep_row_with_usage_still_raises(db_session):
    """T3 preservation pin: the upgrade requires usage IS NULL. A sweep-marker
    row that somehow already carries usage (weird legacy shape) must not have
    someone else's usage numbers overwritten."""

    job = _seed_job(db_session, "legacy-usage-guard")
    db_session.commit()
    attempt_id = begin_model_call_attempt(_meta(job))
    _sweep_close_job_attempts(job.id, "LOCAL_TIMEOUT", LOCAL_TIMEOUT_TERMINAL_MESSAGE)
    with audit.SessionLocal() as db:
        db.execute(
            update(ModelCallAttempt)
            .where(ModelCallAttempt.id == attempt_id)
            .values(usage={"input_tokens": 1}, usage_status="COMPLETE")
        )
        db.commit()

    with pytest.raises(RuntimeError, match="模型调用审计行已由其他终态完成"):
        finalize_model_call_attempt(
            attempt_id,
            outcome="SUCCEEDED",
            usage={"input_tokens": 9},
        )

    with audit.SessionLocal() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "FAILED"
    assert row.error_code == "LOCAL_TIMEOUT"
    assert row.usage == {"input_tokens": 1}


def test_failed_over_failed_replay_stays_idempotent(db_session):
    """T4 preservation pin: same-outcome replay stays tolerated (existing
    behavior) and writes nothing — finished_at keeps the first finalize's
    value."""

    job = _seed_job(db_session, "failed-replay")
    db_session.commit()
    attempt_id = begin_model_call_attempt(_meta(job))
    finalize_model_call_attempt(
        attempt_id,
        outcome="FAILED",
        error_code="RATE_LIMIT",
        error_message="限流",
    )
    with audit.SessionLocal() as db:
        first_finished = db.get(ModelCallAttempt, attempt_id).finished_at
    assert first_finished is not None

    finalize_model_call_attempt(
        attempt_id,
        outcome="FAILED",
        error_code="RATE_LIMIT",
        error_message="限流",
    )

    with audit.SessionLocal() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "FAILED"
    assert row.error_code == "RATE_LIMIT"
    assert row.finished_at == first_finished


def test_recovery_sweep_then_late_success_finalize_full_chain(db_session, monkeypatch):
    """T5, the real sequence end to end: a LOCAL thread wedged past its lease
    is terminalized by recover_pending_jobs, which sweep-closes the NULL
    attempt as FAILED/LOCAL_TIMEOUT; the wedged call then returns and the late
    SUCCEEDED finalize upgrades the row instead of raising."""

    db_session.add(AppSetting(key="runtime", value={"queue_mode": "LOCAL"}, version=1))
    job = _seed_job(
        db_session,
        "full-chain",
        attempt_count=3,
        max_attempts=3,
        lease_owner="pinned-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=120),
        error_code="LOCAL_TIMEOUT",
        error_message=LOCAL_TIMEOUT_WAITING_MESSAGE,
    )
    db_session.commit()
    attempt_id = begin_model_call_attempt(_meta(job))

    monkeypatch.setattr(
        job_service, "get_settings", lambda: Settings(environment="development")
    )
    monkeypatch.setattr(job_service, "_submit_local", lambda _job_id: None)
    assert job_service.recover_pending_jobs(db_session) == 0

    db_session.expire_all()
    swept_job = db_session.get(GenerationJob, job.id)
    assert swept_job.status == "FAILED"
    assert swept_job.error_code == "LOCAL_TIMEOUT"
    with audit.SessionLocal() as db:
        swept = db.get(ModelCallAttempt, attempt_id)
        assert swept.outcome == "FAILED"
        assert swept.error_code == "LOCAL_TIMEOUT"
        assert swept.usage is None
        assert swept.usage_status is None

    finalize_model_call_attempt(
        attempt_id,
        outcome="SUCCEEDED",
        model_id="model-reported-by-adapter",
        request_id="req-full-chain",
        usage={"output_images": 2},
        output_image_count=2,
    )

    with audit.SessionLocal() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "SUCCEEDED"
    assert row.error_code is None
    assert row.usage == {"output_images": 2}
    assert row.usage_status == "COMPLETE"
    assert row.output_images == 2
    assert row.request_id == "req-full-chain"
    assert row.finished_at is not None


def test_late_succeeded_finalize_upgrades_worker_lost_attempt(db_session):
    """origin/master's WORKER_LOST convergence joins the upgrade marker set.

    The periodic sweep closes an attempt older than timeout + lease + margin
    as FAILED(WORKER_LOST) while a wedged LOCAL thread can still be inside
    the call; a successful return must upgrade the row instead of raising.
    """

    job = _seed_job(db_session, "worker-lost-upgrade")
    db_session.commit()
    attempt_id = begin_model_call_attempt(_meta(job))
    _sweep_close_job_attempts(job.id, "WORKER_LOST", "执行器在调用期间丢失，审计行按失败收敛")

    finalize_model_call_attempt(
        attempt_id,
        outcome="SUCCEEDED",
        model_id="model-reported-by-adapter",
        request_id="req-late-success",
        usage={"input_tokens": 11, "output_tokens": 7},
        output_image_count=1,
    )

    with audit.SessionLocal() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "SUCCEEDED"
    assert row.error_code is None
    assert row.usage == {"input_tokens": 11, "output_tokens": 7}
    assert row.request_id == "req-late-success"

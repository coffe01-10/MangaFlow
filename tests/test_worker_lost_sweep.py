"""Regression: unfinalized audit rows converge to FAILED after deadlines.

A worker killed between ``begin_model_call_attempt`` and finalize left the
row with ``outcome IS NULL`` forever: cost views excluded it (billed money
invisible), the usage summary counted it as pending eternally, and nothing
distinguished "in flight" from "lost in a crash three months ago". The
periodic recovery pass now sweeps rows older than the job's hard timeout
plus lease plus margin to a terminal FAILED(WORKER_LOST) state; fresh rows
and successful finalizes are untouched, and the finalize CAS still wins if
a straggler ever resurfaces.
"""

from datetime import UTC, datetime, timedelta


from app.config import get_settings
from app.models import ModelCallAttempt
from app.services.job_service import sweep_lost_model_call_attempts


def _attempt(db, *, age_seconds: int, outcome: str | None) -> ModelCallAttempt:
    started = datetime.now(UTC) - timedelta(seconds=age_seconds)
    attempt = ModelCallAttempt(
        job_attempt=1,
        dispatch_no=1,
        outcome=outcome,
        provider="sweep-provider",
        model_id="m",
        channel="HTTP_API",
        started_at=started,
        usage_status="UNKNOWN",
    )
    db.add(attempt)
    db.commit()
    return attempt


def _max_age_seconds() -> int:
    settings = get_settings()
    return (
        settings.job_timeout_seconds
        + settings.job_lease_seconds
        + 300
    )


def test_sweep_converges_lost_attempts(db_session):
    lost = _attempt(db_session, age_seconds=_max_age_seconds() + 60, outcome=None)
    fresh = _attempt(db_session, age_seconds=10, outcome=None)
    succeeded = _attempt(
        db_session, age_seconds=_max_age_seconds() + 60, outcome="SUCCEEDED"
    )

    swept = sweep_lost_model_call_attempts(db_session)

    assert swept == 1
    db_session.expire_all()
    row = db_session.get(ModelCallAttempt, lost.id)
    assert row.outcome == "FAILED"
    assert row.error_code == "WORKER_LOST"
    assert row.finished_at is not None
    assert db_session.get(ModelCallAttempt, fresh.id).outcome is None
    assert (
        db_session.get(ModelCallAttempt, succeeded.id).outcome == "SUCCEEDED"
    )


def test_sweep_is_idempotent(db_session):
    _attempt(db_session, age_seconds=_max_age_seconds() + 60, outcome=None)

    assert sweep_lost_model_call_attempts(db_session) == 1
    assert sweep_lost_model_call_attempts(db_session) == 0


def test_late_finalize_loses_to_sweep(db_session):
    """The finalize CAS refuses to overwrite the converged terminal state."""

    from app.services.worker_handlers.model_call_audit import (
        finalize_model_call_attempt,
    )

    attempt = _attempt(
        db_session, age_seconds=_max_age_seconds() + 60, outcome=None
    )
    sweep_lost_model_call_attempts(db_session)

    import pytest

    with pytest.raises(RuntimeError, match="终态"):
        finalize_model_call_attempt(
            attempt.id,
            outcome="SUCCEEDED",
            usage={"input_tokens": 3},
        )
    db_session.expire_all()
    row = db_session.get(ModelCallAttempt, attempt.id)
    assert row.outcome == "FAILED"


def test_sweep_age_tracks_settings(db_session, monkeypatch):
    settings = get_settings()
    boundary = _attempt(db_session, age_seconds=_max_age_seconds() - 30, outcome=None)
    sweep_lost_model_call_attempts(db_session)
    db_session.expire_all()
    assert db_session.get(ModelCallAttempt, boundary.id).outcome is None

    # Shrinking the deadlines moves the same row into the swept window.
    monkeypatch.setattr(settings, "job_timeout_seconds", 30)
    monkeypatch.setattr(settings, "job_lease_seconds", 30)
    swept = sweep_lost_model_call_attempts(db_session)
    assert swept == 1
    db_session.expire_all()
    assert db_session.get(ModelCallAttempt, boundary.id).outcome == "FAILED"

"""Durable per-attempt model call audit trail.

``begin`` and ``finalize`` run on an independent ``SessionLocal`` so audit rows
survive caller-owned transaction rollbacks: failures must not erase audit
history and a successful paid call must be recorded even if later output
writes fail. Callers snapshot scalar metadata before invoking; ORM instances
from the caller's session never cross into the audit session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import ModelCallAttempt, utcnow


@dataclass(frozen=True)
class ModelCallAttemptMeta:
    """Scalar binding metadata snapped from the caller's session objects."""

    job_id: str
    project_id: str
    job_attempt: int
    provider: str
    model_id: str
    catalog_model_id: str | None = None
    connection_id: str | None = None
    selected_key_id: str | None = None
    route_reason: str | None = None
    route_score: float | None = None
    route_switched: bool = False


def begin_model_call_attempt(meta: ModelCallAttemptMeta) -> str:
    """Persist an in-flight attempt row and return its id.

    The row commits autonomously: if the process dies mid-call the audit shows
    an unfinalized (``outcome IS NULL``) attempt. ``dispatch_no`` is the next
    monotonically increasing number within ``(job_id, job_attempt)``; the
    unique constraint on that triple is the hard race guard.
    """

    with SessionLocal() as db:
        dispatch_no = (
            db.scalar(
                select(func.max(ModelCallAttempt.dispatch_no)).where(
                    ModelCallAttempt.job_id == meta.job_id,
                    ModelCallAttempt.job_attempt == meta.job_attempt,
                )
            )
            or 0
        ) + 1
        attempt = ModelCallAttempt(
            job_id=meta.job_id,
            project_id=meta.project_id,
            job_attempt=meta.job_attempt,
            dispatch_no=dispatch_no,
            route_switched=meta.route_switched,
            provider=meta.provider,
            model_id=meta.model_id,
            catalog_model_id=meta.catalog_model_id,
            connection_id=meta.connection_id,
            selected_key_id=meta.selected_key_id,
            route_reason=meta.route_reason,
            route_score=meta.route_score,
            started_at=utcnow(),
        )
        db.add(attempt)
        db.commit()
        return attempt.id


def finalize_model_call_attempt(
    attempt_id: str,
    *,
    outcome: str,
    model_id: str | None = None,
    request_id: str | None = None,
    usage: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Finalize an attempt row in its own committed transaction.

    ``usage``/``request_id`` are only written when provided: adapters that do
    not expose them (text/multimodal parsing) leave the columns ``NULL``.
    """

    with SessionLocal() as db:
        attempt = db.get(ModelCallAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError(f"审计行不存在：{attempt_id}")
        finished_at = datetime.now(UTC)
        started = attempt.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        attempt.outcome = outcome
        attempt.finished_at = finished_at
        attempt.duration_ms = max(
            0, int((finished_at - started).total_seconds() * 1000)
        )
        if model_id is not None:
            attempt.model_id = model_id
        if request_id is not None:
            attempt.request_id = request_id
        if usage is not None:
            attempt.usage = usage
        attempt.error_code = error_code
        attempt.error_message = error_message[:500] if error_message else None
        db.commit()

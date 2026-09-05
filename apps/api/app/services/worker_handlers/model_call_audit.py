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

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import ModelCallAttempt, utcnow
from app.services.usage_ledger import (
    attach_attempt_outputs as _attach_attempt_outputs,
)
from app.services.usage_ledger import (
    normalize_usage,
)
from app.services.usage_ledger import (
    record_output_attachment_failure as _record_output_attachment_failure,
)

# Terminal error codes the recovery sweep (job_service.recover_pending_jobs)
# writes when it closes NULL-outcome attempts on an expired lease. Duplicated
# here as stable literals on purpose: model_call_audit must not import
# job_service (the worker-handler layer sits below job orchestration), and a
# genuine worker-failure finalize only ever writes adapter error codes, which
# never collide with these three.
SWEEP_TERMINAL_ERROR_CODES = frozenset({"JOB_TIMEOUT", "LOCAL_TIMEOUT", "LEASE_EXPIRED"})


@dataclass(frozen=True)
class ModelCallAttemptMeta:
    """Scalar binding metadata snapped from the caller's session objects."""

    job_id: str | None
    project_id: str | None
    job_attempt: int
    provider: str
    model_id: str
    catalog_model_id: str | None = None
    connection_id: str | None = None
    selected_key_id: str | None = None
    route_reason: str | None = None
    route_score: float | None = None
    route_switched: bool = False
    dispatch_request_id: str | None = None
    channel: str = "HTTP_API"
    probe_id: str | None = None
    chapter_id: str | None = None
    page_id: str | None = None
    panel_id: str | None = None
    candidate_id: str | None = None


def begin_model_call_attempt(meta: ModelCallAttemptMeta) -> str:
    """Persist an in-flight attempt row and return its id.

    The row commits autonomously: if the process dies mid-call the audit shows
    an unfinalized (``outcome IS NULL``) attempt. ``dispatch_no`` is the next
    monotonically increasing number within ``(job_id, job_attempt)``; the
    unique constraint on that triple is the hard race guard.
    """

    with SessionLocal() as db:
        for _ in range(5):
            if meta.dispatch_request_id:
                replay = db.scalar(
                    select(ModelCallAttempt).where(
                        ModelCallAttempt.dispatch_request_id
                        == meta.dispatch_request_id
                    )
                )
                if replay is not None:
                    return replay.id
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
                dispatch_request_id=meta.dispatch_request_id,
                route_switched=meta.route_switched,
                channel=meta.channel,
                provider=meta.provider,
                model_id=meta.model_id,
                catalog_model_id=meta.catalog_model_id,
                connection_id=meta.connection_id,
                selected_key_id=meta.selected_key_id,
                probe_id=meta.probe_id,
                chapter_id=meta.chapter_id,
                page_id=meta.page_id,
                panel_id=meta.panel_id,
                candidate_id=meta.candidate_id,
                route_reason=meta.route_reason,
                route_score=meta.route_score,
                started_at=utcnow(),
            )
            db.add(attempt)
            try:
                db.commit()
                return attempt.id
            except IntegrityError:
                db.rollback()
                if meta.dispatch_request_id:
                    replay = db.scalar(
                        select(ModelCallAttempt).where(
                            ModelCallAttempt.dispatch_request_id
                            == meta.dispatch_request_id
                        )
                    )
                    if replay is not None:
                        return replay.id
        raise RuntimeError("无法在并发冲突后分配模型调用派发序号")


def finalize_model_call_attempt(
    attempt_id: str,
    *,
    outcome: str,
    model_id: str | None = None,
    request_id: str | None = None,
    usage: dict | None = None,
    output_image_count: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Finalize an attempt row in its own committed transaction.

    ``usage``/``request_id`` are only written when provided: adapters that do
    not expose them (text/multimodal parsing) leave the columns ``NULL``.
    """

    with SessionLocal() as db:
        started = db.scalar(
            select(ModelCallAttempt.started_at).where(
                ModelCallAttempt.id == attempt_id
            )
        )
        if started is None:
            raise RuntimeError(f"审计行不存在：{attempt_id}")
        finished_at = datetime.now(UTC)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        values: dict[str, object | None] = {
            "outcome": outcome,
            "finished_at": finished_at,
            "duration_ms": max(
                0, int((finished_at - started).total_seconds() * 1000)
            ),
            "error_code": error_code,
            "error_message": error_message[:500] if error_message else None,
        }
        if model_id is not None:
            values["model_id"] = model_id
        if request_id is not None:
            values["request_id"] = request_id
        if usage is not None:
            values["usage"] = usage
        normalized = normalize_usage(
            usage,
            output_image_count=output_image_count,
        )
        values.update(
            {
                "usage_status": normalized.usage_status,
                "usage_source": normalized.usage_source,
                "unit_kind": normalized.unit_kind,
                "input_tokens": normalized.input_tokens,
                "output_tokens": normalized.output_tokens,
                "cached_input_tokens": normalized.cached_input_tokens,
                "cache_hit": normalized.cache_hit,
                "output_images": normalized.output_images,
            }
        )
        finalized = db.execute(
            update(ModelCallAttempt)
            .where(
                ModelCallAttempt.id == attempt_id,
                ModelCallAttempt.outcome.is_(None),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if finalized.rowcount != 1:
            existing_outcome = db.scalar(
                select(ModelCallAttempt.outcome).where(
                    ModelCallAttempt.id == attempt_id
                )
            )
            if existing_outcome == outcome:
                db.rollback()
                return
            if outcome == "SUCCEEDED" and existing_outcome == "FAILED":
                # The recovery sweep's closeout is a best-effort guess: for a
                # LOCAL job the worker thread stays alive past the expired
                # lease, so the sweep may have stamped an attempt FAILED (with
                # a sweep terminal code and NULL usage) whose provider call is
                # still wedged — and that call then returns successfully and
                # lands here. Upgrade the guess to the real outcome and usage
                # (same ``values`` as the normal finalize, so no drift) instead
                # of hard-raising AUDIT_PERSISTENCE_FAILED and discarding the
                # paid call. The conditional WHERE re-checks the full sweep
                # profile atomically: a genuine failure finalize never carries
                # a sweep terminal code, and a row that already has usage (or
                # any other concurrent transition) still refuses.
                upgraded = db.execute(
                    update(ModelCallAttempt)
                    .where(
                        ModelCallAttempt.id == attempt_id,
                        ModelCallAttempt.outcome == "FAILED",
                        ModelCallAttempt.error_code.in_(SWEEP_TERMINAL_ERROR_CODES),
                        ModelCallAttempt.usage.is_(None),
                        ModelCallAttempt.usage_status.is_(None),
                    )
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                if upgraded.rowcount == 1:
                    db.commit()
                    return
                db.rollback()
            raise RuntimeError("模型调用审计行已由其他终态完成")
        db.commit()


def attach_attempt_outputs(
    attempt_id: str,
    *,
    asset_ids: list[str],
    dimensions: list[dict[str, object | None]],
) -> None:
    _attach_attempt_outputs(
        SessionLocal,
        attempt_id,
        asset_ids=asset_ids,
        dimensions=dimensions,
    )


def record_output_attachment_failure(attempt_id: str) -> None:
    _record_output_attachment_failure(SessionLocal, attempt_id)


def attach_attempt_probe(attempt_ids: list[str], probe_id: str) -> None:
    """Link paid smoke attempts only after the probe row has committed."""

    if not attempt_ids:
        return
    with SessionLocal() as db:
        attempts = list(
            db.scalars(
                select(ModelCallAttempt).where(ModelCallAttempt.id.in_(attempt_ids))
            )
        )
        if len(attempts) != len(set(attempt_ids)):
            raise RuntimeError("部分模型调用审计行不存在，无法挂接探测记录")
        for attempt in attempts:
            if attempt.probe_id not in {None, probe_id}:
                raise RuntimeError("模型调用审计行已挂接其他探测记录")
            attempt.probe_id = probe_id
        db.commit()

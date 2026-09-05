from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.helpers import ensure_project_scope
from app.database import get_db
from app.models import ModelCallAttempt, ProviderUsageReconciliation
from app.schemas import ModelCallAttemptRead
from app.services.usage_ledger import (
    create_reconciliation,
    summarize_usage,
    usage_attempt_query,
)
from app.usage_schemas import (
    ProviderUsageReconciliationCreate,
    ProviderUsageReconciliationRead,
    UsageAttemptPage,
    UsageSummaryRead,
)

router = APIRouter(prefix="/usage")


def _as_utc(value: datetime | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    if value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{field} 必须包含明确时区")
    return value.astimezone(UTC)


def _encode_cursor(attempt: ModelCallAttempt) -> str:
    started = attempt.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    payload = json.dumps(
        {"started_at": started.astimezone(UTC).isoformat(), "id": attempt.id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        started = datetime.fromisoformat(payload["started_at"])
        attempt_id = str(payload["id"])
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(status_code=422, detail="用量分页游标无效") from error
    if started.utcoffset() is None or not attempt_id:
        raise HTTPException(status_code=422, detail="用量分页游标无效")
    return started.astimezone(UTC), attempt_id


@router.get("/attempts", response_model=UsageAttemptPage)
def list_usage_attempts(
    project_id: str | None = None,
    job_id: str | None = None,
    channel: str | None = Query(default=None, pattern="^(HTTP_API|CLI)$"),
    provider: str | None = None,
    model_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> UsageAttemptPage:
    since = _as_utc(since, field="since")
    until = _as_utc(until, field="until")
    if since and until and until <= since:
        raise HTTPException(status_code=422, detail="until 必须晚于 since")
    query = usage_attempt_query(
        project_id=project_id,
        job_id=job_id,
        channel=channel,
        provider=provider,
        model_id=model_id,
        since=since,
        until=until,
    )
    if cursor:
        cursor_started, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                ModelCallAttempt.started_at < cursor_started,
                and_(
                    ModelCallAttempt.started_at == cursor_started,
                    ModelCallAttempt.id < cursor_id,
                ),
            )
        )
    rows = list(
        db.scalars(
            query.order_by(
                ModelCallAttempt.started_at.desc(),
                ModelCallAttempt.id.desc(),
            ).limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    return UsageAttemptPage(
        items=items,
        next_cursor=_encode_cursor(items[-1]) if has_more and items else None,
    )


@router.get("/attempts/{attempt_id}", response_model=ModelCallAttemptRead)
def get_usage_attempt(
    attempt_id: str,
    project_id: str | None = None,
    db: Session = Depends(get_db),
) -> ModelCallAttempt:
    attempt = db.get(ModelCallAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="模型调用记录不存在")
    # The ledger is cross-project by design (global usage view); an explicit
    # project_id narrows the read to that project's attempts the same way the
    # list endpoint's optional filter does.
    ensure_project_scope(db, attempt, project_id, label="模型调用记录")
    return attempt


@router.get("/summary", response_model=UsageSummaryRead)
def get_usage_summary(
    project_id: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    db: Session = Depends(get_db),
) -> UsageSummaryRead:
    since = _as_utc(from_, field="from")
    until = _as_utc(to, field="to")
    if since and until and until <= since:
        raise HTTPException(status_code=422, detail="to 必须晚于 from")
    return summarize_usage(
        db,
        project_id=project_id,
        provider=provider,
        model_id=model_id,
        since=since,
        until=until,
    )


@router.post(
    "/reconciliations",
    response_model=ProviderUsageReconciliationRead,
    status_code=status.HTTP_201_CREATED,
)
def post_usage_reconciliation(
    payload: ProviderUsageReconciliationCreate,
    db: Session = Depends(get_db),
) -> ProviderUsageReconciliation:
    return create_reconciliation(db, payload)


@router.get(
    "/reconciliations",
    response_model=list[ProviderUsageReconciliationRead],
)
def list_usage_reconciliations(
    provider: str | None = None,
    model_id: str | None = None,
    channel: str | None = Query(default=None, pattern="^(HTTP_API|CLI)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    db: Session = Depends(get_db),
) -> list[ProviderUsageReconciliation]:
    since = _as_utc(since, field="since")
    until = _as_utc(until, field="until")
    if since and until and until <= since:
        raise HTTPException(status_code=422, detail="until 必须晚于 since")
    query = select(ProviderUsageReconciliation)
    if provider:
        query = query.where(ProviderUsageReconciliation.provider == provider)
    if model_id:
        query = query.where(ProviderUsageReconciliation.model_id == model_id)
    if channel:
        query = query.where(ProviderUsageReconciliation.channel == channel)
    if since:
        query = query.where(ProviderUsageReconciliation.period_start >= since)
    if until:
        query = query.where(ProviderUsageReconciliation.period_end <= until)
    return list(
        db.scalars(
            query.order_by(
                ProviderUsageReconciliation.period_start.desc(),
                ProviderUsageReconciliation.id.desc(),
            )
        )
    )

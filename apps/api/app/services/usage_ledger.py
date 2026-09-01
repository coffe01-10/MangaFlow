"""Structured model-call usage ledger and operator billing reconciliation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AssetCandidate,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    ModelPricingVersion,
    PageCandidate,
    Panel,
    ProviderUsageReconciliation,
)
from app.usage_schemas import (
    CurrencyAmount,
    ProviderUsageReconciliationCreate,
    UsageSummaryGroup,
    UsageSummaryRead,
)

_INPUT_ALIASES = ("input_tokens", "prompt_tokens", "prompt_token_count")
_OUTPUT_ALIASES = (
    "output_tokens",
    "completion_tokens",
    "candidates_token_count",
)
_CACHED_PATHS = (
    ("cached_input_tokens",),
    ("cached_content_token_count",),
    ("cache_read_input_tokens",),
    ("prompt_tokens_details", "cached_tokens"),
)
_IMAGE_ALIASES = ("output_images",)
_IGNORED_USAGE_KEYS = {
    "total_tokens",
    "total_token_count",
    "prompt_tokens_details",
    "cache_creation_input_tokens",
    "estimated_cost",
    "cost_source",
    "cleanup_warning",
}
_KNOWN_TOP_LEVEL_KEYS = (
    set(_INPUT_ALIASES)
    | set(_OUTPUT_ALIASES)
    | set(_IMAGE_ALIASES)
    | {path[0] for path in _CACHED_PATHS}
    | _IGNORED_USAGE_KEYS
)


@dataclass(frozen=True)
class NormalizedUsage:
    usage_status: str
    usage_source: str | None
    unit_kind: str
    input_tokens: Decimal | None
    output_tokens: Decimal | None
    cached_input_tokens: Decimal | None
    cache_hit: bool | None
    output_images: Decimal | None


@dataclass(frozen=True)
class UsageDimensions:
    chapter_id: str | None = None
    page_id: str | None = None
    panel_id: str | None = None
    candidate_id: str | None = None


def _nonnegative_integer(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result < 0 or result != result.to_integral_value():
        return None
    return result


def _is_positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return result.is_finite() and result > 0


def _path_value(payload: dict[str, Any], path: tuple[str, ...]) -> object:
    value: object = payload
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _first_value(payload: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Decimal | None:
    for path in paths:
        value = _nonnegative_integer(_path_value(payload, path))
        if value is not None:
            return value
    return None


def normalize_usage(
    usage: dict | None,
    *,
    output_image_count: int | None = None,
) -> NormalizedUsage:
    """Normalize known provider families without manufacturing missing zeros."""

    payload = usage if isinstance(usage, dict) else {}
    input_tokens = _first_value(payload, tuple((key,) for key in _INPUT_ALIASES))
    output_tokens = _first_value(payload, tuple((key,) for key in _OUTPUT_ALIASES))
    cached_input_tokens = _first_value(payload, _CACHED_PATHS)
    output_images = _first_value(payload, tuple((key,) for key in _IMAGE_ALIASES))
    has_unmapped_positive = any(
        key not in _KNOWN_TOP_LEVEL_KEYS and _is_positive_number(value)
        for key, value in payload.items()
    )
    provider_reported = has_unmapped_positive or any(
        item is not None
        for item in (input_tokens, output_tokens, cached_input_tokens, output_images)
    )
    if output_images is None and output_image_count is not None:
        output_images = _nonnegative_integer(output_image_count)

    has_tokens = any(
        item is not None for item in (input_tokens, output_tokens, cached_input_tokens)
    )
    has_images = output_images is not None
    if has_tokens and has_images:
        unit_kind = "MIXED"
    elif has_tokens:
        unit_kind = "TEXT_TOKENS"
    elif has_images:
        unit_kind = "IMAGES"
    else:
        unit_kind = "UNKNOWN"

    if not has_tokens and not has_images and has_unmapped_positive:
        status = "PARTIAL"
    elif not has_tokens and not has_images:
        status = "UNKNOWN"
    elif has_tokens and (input_tokens is None or output_tokens is None):
        status = "PARTIAL"
    else:
        status = "COMPLETE"

    if has_unmapped_positive and status == "COMPLETE":
        status = "PARTIAL"

    source = None
    if provider_reported:
        source = "PROVIDER_REPORTED"
    elif has_images:
        source = "ADAPTER_ESTIMATED"
    return NormalizedUsage(
        usage_status=status,
        usage_source=source,
        unit_kind=unit_kind,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_hit=None if cached_input_tokens is None else cached_input_tokens > 0,
        output_images=output_images,
    )


def resolve_usage_dimensions(db: Session, job: GenerationJob) -> UsageDimensions:
    """Snapshot stable creator dimensions once, without later job joins."""

    if job.target_type == "CHAPTER":
        chapter = db.get(Chapter, job.target_id)
        return UsageDimensions(chapter_id=chapter.id if chapter else None)
    if job.target_type in {"PAGE", "MANGA_PAGE"}:
        page = db.get(MangaPage, job.target_id)
        return UsageDimensions(
            chapter_id=page.chapter_id if page else None,
            page_id=page.id if page else None,
        )
    if job.target_type == "PANEL":
        panel = db.get(Panel, job.target_id)
        page = db.get(MangaPage, panel.page_id) if panel else None
        return UsageDimensions(
            chapter_id=page.chapter_id if page else None,
            page_id=page.id if page else None,
            panel_id=panel.id if panel else None,
        )
    if job.target_type == "PAGE_CANDIDATE":
        candidate = db.get(PageCandidate, job.target_id)
        page = db.get(MangaPage, candidate.page_id) if candidate else None
        return UsageDimensions(
            chapter_id=page.chapter_id if page else None,
            page_id=page.id if page else None,
            candidate_id=candidate.id if candidate else None,
        )
    if job.target_type == "ASSET_CANDIDATE":
        candidate = db.get(AssetCandidate, job.target_id)
        batch = db.get(GenerationBatch, candidate.batch_id) if candidate else None
        return UsageDimensions(
            chapter_id=batch.chapter_id if batch else None,
            page_id=batch.page_id if batch else None,
            candidate_id=candidate.id if candidate else None,
        )
    return UsageDimensions()


def attach_attempt_outputs(
    session_factory,
    attempt_id: str,
    *,
    asset_ids: list[str],
    dimensions: list[dict[str, object | None]],
) -> None:
    """Idempotently attach persisted output assets in an autonomous transaction."""

    unique_ids = list(dict.fromkeys(asset_ids))
    if not unique_ids or len(unique_ids) > 100 or any(not item for item in unique_ids):
        raise ValueError("输出资产列表必须包含 1 到 100 个有效 ID")
    safe_dimensions: list[dict[str, object | None]] = []
    by_id = {str(item.get("asset_id")): item for item in dimensions}
    for asset_id in unique_ids:
        item = by_id.get(asset_id, {})
        safe_dimensions.append(
            {
                "asset_id": asset_id,
                "width": item.get("width") if isinstance(item.get("width"), int) else None,
                "height": item.get("height") if isinstance(item.get("height"), int) else None,
                "quality": (
                    str(item["quality"])[:64] if item.get("quality") is not None else None
                ),
            }
        )
    with session_factory() as db:
        attempt = db.get(ModelCallAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError(f"审计行不存在：{attempt_id}")
        if attempt.output_asset_ids is not None:
            if (
                attempt.output_asset_ids == unique_ids
                and attempt.output_image_dims == safe_dimensions
            ):
                return
            raise RuntimeError("审计行已挂接不同的输出资产")
        attempt.output_asset_ids = unique_ids
        attempt.output_image_dims = safe_dimensions
        if attempt.output_images is None:
            attempt.output_images = Decimal(len(unique_ids))
        normalized = normalize_usage(
            attempt.usage,
            output_image_count=int(attempt.output_images),
        )
        attempt.usage_status = normalized.usage_status
        attempt.usage_source = normalized.usage_source
        attempt.unit_kind = normalized.unit_kind
        attempt.input_tokens = normalized.input_tokens
        attempt.output_tokens = normalized.output_tokens
        attempt.cached_input_tokens = normalized.cached_input_tokens
        attempt.cache_hit = normalized.cache_hit
        db.commit()


def record_output_attachment_failure(session_factory, attempt_id: str) -> None:
    """Persist a redacted marker when post-commit output linking cannot finish."""

    with session_factory() as db:
        attempt = db.get(ModelCallAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError(f"审计行不存在：{attempt_id}")
        if attempt.output_asset_ids is not None:
            return
        attempt.error_code = "OUTPUT_ATTACHMENT_FAILED"
        attempt.error_message = "生成资产已提交，但用量账本未能挂接输出"
        db.commit()


def _reconciliation_matches(
    existing: ProviderUsageReconciliation,
    payload: ProviderUsageReconciliationCreate,
) -> bool:
    expected = payload.model_dump()
    actual = {}
    for key in expected:
        value = getattr(existing, key)
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        actual[key] = value
    return actual == expected


def create_reconciliation(
    db: Session, payload: ProviderUsageReconciliationCreate
) -> ProviderUsageReconciliation:
    if db.get_bind().dialect.name == "postgresql":
        lock_key = "|".join(
            (
                payload.billing_account_id,
                payload.provider,
                payload.model_id,
                payload.channel,
                payload.connection_id or "",
            )
        )
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )
    identity = (
        payload.billing_account_id,
        payload.import_batch_id,
        payload.idempotency_key,
    )
    existing = db.scalar(
        select(ProviderUsageReconciliation).where(
            ProviderUsageReconciliation.billing_account_id == identity[0],
            ProviderUsageReconciliation.import_batch_id == identity[1],
            ProviderUsageReconciliation.idempotency_key == identity[2],
        )
    )
    if existing is not None:
        if _reconciliation_matches(existing, payload):
            return existing
        raise HTTPException(status_code=409, detail="对账幂等键已用于不同内容")

    same_connection = (
        ProviderUsageReconciliation.connection_id.is_(None)
        if payload.connection_id is None
        else ProviderUsageReconciliation.connection_id == payload.connection_id
    )
    overlap = db.scalar(
        select(ProviderUsageReconciliation.id)
        .where(
            ProviderUsageReconciliation.billing_account_id
            == payload.billing_account_id,
            ProviderUsageReconciliation.provider == payload.provider,
            ProviderUsageReconciliation.model_id == payload.model_id,
            ProviderUsageReconciliation.channel == payload.channel,
            same_connection,
            ProviderUsageReconciliation.period_start < payload.period_end,
            ProviderUsageReconciliation.period_end > payload.period_start,
        )
        .limit(1)
    )
    if overlap:
        raise HTTPException(status_code=409, detail="同一账单维度的对账周期不能重叠")
    row = ProviderUsageReconciliation(**payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        replay = db.scalar(
            select(ProviderUsageReconciliation).where(
                ProviderUsageReconciliation.billing_account_id == identity[0],
                ProviderUsageReconciliation.import_batch_id == identity[1],
                ProviderUsageReconciliation.idempotency_key == identity[2],
            )
        )
        if replay is not None and _reconciliation_matches(replay, payload):
            return replay
        if replay is not None:
            raise HTTPException(
                status_code=409,
                detail="对账幂等键已用于不同内容",
            ) from error
        raise HTTPException(status_code=409, detail="对账记录发生并发冲突") from error
    db.refresh(row)
    return row


def usage_attempt_query(
    *,
    project_id: str | None,
    job_id: str | None,
    channel: str | None,
    provider: str | None,
    since: datetime | None,
    until: datetime | None,
):
    query = select(ModelCallAttempt)
    if project_id:
        query = query.where(ModelCallAttempt.project_id == project_id)
    if job_id:
        query = query.where(ModelCallAttempt.job_id == job_id)
    if channel:
        query = query.where(ModelCallAttempt.channel == channel)
    if provider:
        query = query.where(ModelCallAttempt.provider == provider)
    if since:
        query = query.where(ModelCallAttempt.started_at >= since)
    if until:
        query = query.where(ModelCallAttempt.started_at < until)
    return query


def _total_or_none(items: list[ModelCallAttempt], attribute: str) -> int | None:
    values = [getattr(item, attribute) for item in items]
    present = [Decimal(value) for value in values if value is not None]
    return int(sum(present, Decimal(0))) if present else None


def summarize_usage(
    db: Session,
    *,
    project_id: str | None = None,
    provider: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> UsageSummaryRead:
    from app.services.model_costs import (
        _active_price,
        _estimate_attempt,
        _usage_for_attempt,
    )

    attempts = list(
        db.scalars(
            usage_attempt_query(
                project_id=project_id,
                job_id=None,
                channel=None,
                provider=provider,
                since=since,
                until=until,
            ).order_by(ModelCallAttempt.started_at, ModelCallAttempt.id)
        )
    )
    pairs = {(item.provider, item.model_id) for item in attempts}
    prices = (
        list(
            db.scalars(
                select(ModelPricingVersion).where(
                    or_(
                        *[
                            and_(
                                ModelPricingVersion.provider == pair[0],
                                ModelPricingVersion.model_id == pair[1],
                            )
                            for pair in pairs
                        ]
                    )
                )
            )
        )
        if pairs
        else []
    )
    prices_by_pair: dict[tuple[str, str], list[ModelPricingVersion]] = defaultdict(list)
    for price in prices:
        prices_by_pair[(price.provider, price.model_id)].append(price)

    buckets: dict[tuple, list[ModelCallAttempt]] = defaultdict(list)
    for attempt in attempts:
        started = attempt.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        key = (
            started.astimezone(UTC).date(),
            attempt.provider,
            attempt.model_id,
            attempt.channel,
        )
        buckets[key].append(attempt)

    groups: list[UsageSummaryGroup] = []
    for (day, item_provider, model_id, channel), items in sorted(buckets.items()):
        status_counts = {"UNKNOWN": 0, "PARTIAL": 0, "COMPLETE": 0}
        estimated: dict[str, Decimal] = defaultdict(Decimal)
        for item in items:
            status_counts[item.usage_status or "UNKNOWN"] += 1
            if item.outcome is None:
                continue
            price = _active_price(
                prices_by_pair.get((item.provider, item.model_id), []),
                item.started_at,
            )
            if price is None:
                continue
            amount, _complete = _estimate_attempt(_usage_for_attempt(item), price)
            if amount is not None:
                estimated[price.currency] += amount

        groups.append(
            UsageSummaryGroup(
                day=day,
                provider=item_provider,
                model_id=model_id,
                channel=channel,
                attempt_count=len(items),
                succeeded_count=sum(item.outcome == "SUCCEEDED" for item in items),
                failed_count=sum(item.outcome == "FAILED" for item in items),
                pending_count=sum(item.outcome is None for item in items),
                input_tokens=_total_or_none(items, "input_tokens"),
                output_tokens=_total_or_none(items, "output_tokens"),
                cached_input_tokens=_total_or_none(items, "cached_input_tokens"),
                output_images=_total_or_none(items, "output_images"),
                usage_status_counts=status_counts,
                estimated_costs=[
                    CurrencyAmount(currency=currency, amount=amount)
                    for currency, amount in sorted(estimated.items())
                ],
            )
        )

    if project_id:
        return UsageSummaryRead(groups=groups, billed=[])

    billed_query = select(ProviderUsageReconciliation)
    if provider:
        billed_query = billed_query.where(
            ProviderUsageReconciliation.provider == provider
        )
    if since:
        billed_query = billed_query.where(
            ProviderUsageReconciliation.period_start >= since
        )
    if until:
        billed_query = billed_query.where(
            ProviderUsageReconciliation.period_end <= until
        )
    billed = list(
        db.scalars(
            billed_query.order_by(
                ProviderUsageReconciliation.period_start,
                ProviderUsageReconciliation.id,
            )
        )
    )
    return UsageSummaryRead(groups=groups, billed=billed)

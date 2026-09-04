"""Versioned, explainable model-call cost estimates.

Pricing rows are immutable and estimates are derived from the price active at
each recorded dispatch time. They are estimates, never provider invoices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from fastapi import HTTPException
from sqlalchemy import or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ModelCallAttempt, ModelPricingVersion
from app.provider_schemas import ModelPricingVersionCreate
from app.services.usage_ledger import USAGE_VALUE_CAP

_MILLION = Decimal(1_000_000)
_DISPLAY_QUANTUM = Decimal("0.000001")
_USAGE_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens", "prompt_token_count"),
    "output_tokens": (
        "output_tokens",
        "completion_tokens",
        "candidates_token_count",
    ),
    "output_images": ("output_images",),
}
_KNOWN_USAGE_KEYS = {
    alias for aliases in _USAGE_ALIASES.values() for alias in aliases
}
_AGGREGATE_USAGE_KEYS = {"total_tokens", "total_token_count"}
_CACHED_USAGE_KEYS = {
    "cached_input_tokens",
    "cached_content_token_count",
    "cache_read_input_tokens",
    "prompt_tokens_details",
}


@dataclass(frozen=True)
class JobCostEstimate:
    value: Decimal | None
    currency: str | None
    status: str
    pricing_versions: tuple[str, ...]
    note: str


def create_pricing_version(
    db: Session, payload: ModelPricingVersionCreate
) -> ModelPricingVersion:
    overlap = db.scalar(
        select(ModelPricingVersion.id)
        .where(
            ModelPricingVersion.provider == payload.provider,
            ModelPricingVersion.model_id == payload.model_id,
            or_(
                ModelPricingVersion.effective_to.is_(None),
                ModelPricingVersion.effective_to > payload.effective_from,
            ),
            True
            if payload.effective_to is None
            else ModelPricingVersion.effective_from < payload.effective_to,
        )
        .limit(1)
    )
    if overlap:
        raise HTTPException(
            status_code=409,
            detail="同一供应商与模型的价格生效区间不能重叠",
        )
    row = ModelPricingVersion(**payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="价格版本已存在") from error
    db.refresh(row)
    return row


def list_pricing_versions(
    db: Session, *, provider: str | None = None, model_id: str | None = None
) -> list[ModelPricingVersion]:
    query = select(ModelPricingVersion)
    if provider:
        query = query.where(ModelPricingVersion.provider == provider)
    if model_id:
        query = query.where(ModelPricingVersion.model_id == model_id)
    return list(
        db.scalars(
            query.order_by(
                ModelPricingVersion.provider,
                ModelPricingVersion.model_id,
                ModelPricingVersion.effective_from.desc(),
            )
        )
    )


def estimate_jobs(db: Session, job_ids: list[str]) -> dict[str, JobCostEstimate]:
    if not job_ids:
        return {}
    attempts = list(
        db.scalars(
            select(ModelCallAttempt)
            .where(ModelCallAttempt.job_id.in_(job_ids))
            .order_by(
                ModelCallAttempt.job_id,
                ModelCallAttempt.job_attempt,
                ModelCallAttempt.dispatch_no,
            )
        )
    )
    pairs = {(attempt.provider, attempt.model_id) for attempt in attempts}
    prices = (
        list(
            db.scalars(
                select(ModelPricingVersion).where(
                    tuple_(
                        ModelPricingVersion.provider,
                        ModelPricingVersion.model_id,
                    ).in_(pairs)
                )
            )
        )
        if pairs
        else []
    )
    prices_by_pair: dict[tuple[str, str], list[ModelPricingVersion]] = {}
    for price in prices:
        prices_by_pair.setdefault((price.provider, price.model_id), []).append(price)
    attempts_by_job: dict[str, list[ModelCallAttempt]] = {}
    billable_attempts = [attempt for attempt in attempts if attempt.outcome is not None]
    for attempt in billable_attempts:
        attempts_by_job.setdefault(attempt.job_id, []).append(attempt)
    return {
        job_id: _estimate_attempts(
            attempts_by_job.get(job_id, []), prices_by_pair=prices_by_pair
        )
        for job_id in job_ids
    }


def _estimate_attempts(
    attempts: list[ModelCallAttempt],
    *,
    prices_by_pair: dict[tuple[str, str], list[ModelPricingVersion]],
) -> JobCostEstimate:
    if not attempts:
        return JobCostEstimate(
            value=None,
            currency=None,
            status="UNAVAILABLE",
            pricing_versions=(),
            note="尚无模型调用记录，无法估算；估算值不等于供应商账单",
        )
    total = Decimal(0)
    currencies: set[str] = set()
    version_labels: set[str] = set()
    complete_attempts = 0
    priced_attempts = 0
    for attempt in attempts:
        price = _active_price(
            prices_by_pair.get((attempt.provider, attempt.model_id), []),
            attempt.started_at,
        )
        if price is None:
            continue
        amount, complete = _estimate_attempt(_usage_for_attempt(attempt), price)
        currencies.add(price.currency)
        version_labels.add(
            f"{price.provider}/{price.model_id}:{price.pricing_version}"
        )
        if amount is not None:
            total += amount
            priced_attempts += 1
        if complete:
            complete_attempts += 1
    versions = tuple(sorted(version_labels))
    if len(currencies) > 1:
        return JobCostEstimate(
            value=None,
            currency=None,
            status="UNAVAILABLE",
            pricing_versions=versions,
            note="调用涉及多种币种且未配置汇率，无法合并估算；估算值不等于供应商账单",
        )
    currency = next(iter(currencies), None)
    if priced_attempts == 0:
        return JobCostEstimate(
            value=None,
            currency=currency,
            status="UNAVAILABLE",
            pricing_versions=versions,
            note="缺少调用 usage 或对应价格版本，费用不可估算；估算值不等于供应商账单",
        )
    try:
        with localcontext() as context:
            context.prec = 60
            value = total.quantize(_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return JobCostEstimate(
            value=None,
            currency=currency,
            status="UNAVAILABLE",
            pricing_versions=versions,
            note="调用用量超出可估算范围，费用不可估算；估算值不等于供应商账单",
        )
    if complete_attempts == len(attempts):
        return JobCostEstimate(
            value=value,
            currency=currency,
            status="AVAILABLE",
            pricing_versions=versions,
            note=(
                f"基于 {len(attempts)} 次实际调用和生效价格版本估算，"
                "不等于供应商账单"
            ),
        )
    return JobCostEstimate(
        value=value,
        currency=currency,
        status="PARTIAL",
        pricing_versions=versions,
        note=(
            f"仅完整估算 {complete_attempts}/{len(attempts)} 次调用；"
            "其余调用缺少 usage 或价格，估算值不等于供应商账单"
        ),
    )


def _active_price(
    prices: list[ModelPricingVersion], started_at: datetime
) -> ModelPricingVersion | None:
    started = _as_utc(started_at)
    active = [
        price
        for price in prices
        if _as_utc(price.effective_from) <= started
        and (price.effective_to is None or started < _as_utc(price.effective_to))
    ]
    return max(active, key=lambda price: _as_utc(price.effective_from), default=None)


def _estimate_attempt(
    usage: dict | None, price: ModelPricingVersion
) -> tuple[Decimal | None, bool]:
    quantities, has_unmapped_usage = _normalized_usage(usage)
    amount = Decimal(0)
    has_amount = False
    complete = not has_unmapped_usage
    if price.request_each is not None:
        amount += Decimal(price.request_each)
        has_amount = True
    input_tokens = quantities.get("input_tokens")
    cached_tokens = quantities.get("cached_input_tokens")
    if cached_tokens is not None and input_tokens is None:
        complete = False
    if input_tokens is not None and cached_tokens is not None:
        if cached_tokens > input_tokens:
            complete = False
        elif price.input_tokens_per_million is None:
            if input_tokens > 0:
                complete = False
        else:
            cached_rate = price.cached_input_tokens_per_million
            if cached_rate is None:
                amount += input_tokens * Decimal(price.input_tokens_per_million) / _MILLION
                has_amount = True
                complete = False
            else:
                amount += (
                    (input_tokens - cached_tokens)
                    * Decimal(price.input_tokens_per_million)
                    / _MILLION
                )
                amount += cached_tokens * Decimal(cached_rate) / _MILLION
                has_amount = True
    elif input_tokens is not None and price.input_tokens_per_million is not None:
        amount += input_tokens * Decimal(price.input_tokens_per_million) / _MILLION
        has_amount = True
    elif (
        input_tokens is None and price.input_tokens_per_million is not None
    ) or (input_tokens is not None and input_tokens > 0):
        complete = False

    components = (
        ("output_tokens", price.output_tokens_per_million, _MILLION),
        ("output_images", price.output_image_each, Decimal(1)),
    )
    for unit, rate, divisor in components:
        quantity = quantities.get(unit)
        if rate is not None:
            if quantity is None:
                complete = False
            else:
                amount += quantity * Decimal(rate) / divisor
                has_amount = True
        elif quantity is not None and quantity > 0:
            complete = False
    return (amount if has_amount else None), complete and has_amount


def _normalized_usage(usage: dict | None) -> tuple[dict[str, Decimal], bool]:
    if not isinstance(usage, dict):
        return {}, False
    normalized: dict[str, Decimal] = {}
    for unit, aliases in _USAGE_ALIASES.items():
        for alias in aliases:
            if alias not in usage:
                continue
            value = _nonnegative_decimal(usage[alias])
            if value is not None:
                normalized[unit] = value
                break
    cached = None
    for alias in (
        "cached_input_tokens",
        "cached_content_token_count",
        "cache_read_input_tokens",
    ):
        if alias in usage:
            cached = _nonnegative_decimal(usage[alias])
            if cached is not None:
                break
    details = usage.get("prompt_tokens_details")
    if cached is None and isinstance(details, dict):
        cached = _nonnegative_decimal(details.get("cached_tokens"))
    if cached is not None:
        normalized["cached_input_tokens"] = cached
    has_unmapped_usage = any(
        key not in _KNOWN_USAGE_KEYS
        and key not in _CACHED_USAGE_KEYS
        and key not in _AGGREGATE_USAGE_KEYS
        and (_nonnegative_decimal(value) or Decimal(0)) > 0
        for key, value in usage.items()
    )
    return normalized, has_unmapped_usage


def _usage_for_attempt(attempt: ModelCallAttempt) -> dict | None:
    usage = dict(attempt.usage) if isinstance(attempt.usage, dict) else {}
    structured = {
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "cached_input_tokens": attempt.cached_input_tokens,
        "output_images": attempt.output_images,
    }
    for key, value in structured.items():
        if value is not None and not any(
            alias in usage for alias in _USAGE_ALIASES.get(key, (key,))
        ):
            usage[key] = value
    return usage or None


def _nonnegative_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0 or result > USAGE_VALUE_CAP:
        return None
    return result


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

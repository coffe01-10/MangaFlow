from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import ModelCallAttemptRead


class UsageAttemptPage(BaseModel):
    items: list[ModelCallAttemptRead]
    next_cursor: str | None = None


class CurrencyAmount(BaseModel):
    currency: str
    amount: Decimal


class UsageSummaryGroup(BaseModel):
    day: date
    provider: str
    model_id: str
    channel: Literal["HTTP_API", "CLI"]
    attempt_count: int
    succeeded_count: int
    failed_count: int
    pending_count: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    output_images: int | None
    usage_status_counts: dict[str, int]
    estimated_costs: list[CurrencyAmount]


class UsageSummaryRead(BaseModel):
    groups: list[UsageSummaryGroup]
    billed: list[ProviderUsageReconciliationRead]


class ProviderUsageReconciliationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=128)
    channel: Literal["HTTP_API", "CLI"]
    connection_id: str | None = Field(default=None, max_length=36)
    billing_account_id: str = Field(min_length=1, max_length=160)
    import_batch_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    period_start: datetime
    period_end: datetime
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    billed_amount: Decimal = Field(ge=0, max_digits=20, decimal_places=8)
    source_note: str = Field(default="", max_length=500)
    entered_by: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_start.utcoffset() is None or self.period_end.utcoffset() is None:
            raise ValueError("对账周期必须包含明确时区")
        self.period_start = self.period_start.astimezone(UTC)
        self.period_end = self.period_end.astimezone(UTC)
        if self.period_end <= self.period_start:
            raise ValueError("对账周期结束时间必须晚于开始时间")
        return self


class ProviderUsageReconciliationRead(ProviderUsageReconciliationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime

    @field_validator("period_start", "period_end", "created_at", mode="before")
    @classmethod
    def restore_utc_for_naive_database_values(cls, value):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

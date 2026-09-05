"""Regression: absurd provider usage magnitudes must not kill paid calls.

A malformed provider payload such as ``{"prompt_tokens": 1e20}`` used to
overflow the numeric(20,0) ledger columns on PostgreSQL during finalize;
the resulting data error was wrapped as AUDIT_PERSISTENCE_FAILED
(non-retryable), so a billed, successful generation was permanently
failed and its audit row left unfinalized. Usage values above the ledger
cap now read as absent (status UNKNOWN, raw payload preserved), and the
estimate aggregation quantizes inside a widened decimal context so
aggregate magnitudes cannot raise InvalidOperation and 500 the jobs list.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.models import ModelCallAttempt, ModelPricingVersion
from app.services.model_costs import _estimate_attempts
from app.services.usage_ledger import USAGE_VALUE_CAP, normalize_usage
from app.services.worker_handlers.model_call_audit import (
    ModelCallAttemptMeta,
    begin_model_call_attempt,
    finalize_model_call_attempt,
)


def test_normalize_usage_reads_absurd_magnitudes_as_absent():
    normalized = normalize_usage({"prompt_tokens": 10**20, "completion_tokens": 7})
    assert normalized.input_tokens is None
    assert normalized.output_tokens == 7
    assert normalized.usage_status == "PARTIAL"

    only_garbage = normalize_usage({"prompt_token_count": 10**30})
    assert only_garbage.input_tokens is None
    assert only_garbage.usage_status == "UNKNOWN"
    assert only_garbage.usage_source is None


def test_normalize_usage_keeps_values_at_the_cap():
    normalized = normalize_usage({"prompt_tokens": USAGE_VALUE_CAP})
    assert normalized.input_tokens == USAGE_VALUE_CAP
    assert normalized.usage_status == "PARTIAL"


def test_finalize_succeeds_with_garbage_usage_magnitude(db_session):
    meta = ModelCallAttemptMeta(
        job_id=None,
        project_id=None,
        job_attempt=1,
        provider="test-provider",
        model_id="test-model",
    )
    attempt_id = begin_model_call_attempt(meta)
    finalize_model_call_attempt(
        attempt_id,
        outcome="SUCCEEDED",
        usage={"prompt_tokens": 10**20, "completion_tokens": 5},
        output_image_count=1,
    )

    row = db_session.get(ModelCallAttempt, attempt_id)
    assert row is not None
    assert row.outcome == "SUCCEEDED"
    assert row.input_tokens is None
    assert row.output_tokens == 5
    assert row.usage_status == "PARTIAL"
    assert row.usage["prompt_tokens"] == 10**20


def _attempt(db_session, provider: str, usage: dict) -> ModelCallAttempt:
    return ModelCallAttempt(
        job_attempt=1,
        dispatch_no=1,
        outcome="SUCCEEDED",
        provider=provider,
        model_id="m",
        channel="HTTP_API",
        started_at=datetime.now(UTC),
        usage=usage,
        usage_status="COMPLETE",
    )


def test_estimate_aggregate_survives_huge_totals(db_session):
    price = ModelPricingVersion(
        provider="bulk",
        model_id="m",
        pricing_version="v1",
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        currency="USD",
        input_tokens_per_million=Decimal("999999999999.99999999"),
    )
    db_session.add(price)
    for index in range(1500):
        attempt = _attempt(db_session, "bulk", {"input_tokens": int(USAGE_VALUE_CAP)})
        attempt.dispatch_no = index + 1
        db_session.add(attempt)
    db_session.commit()

    attempts = (
        db_session.query(ModelCallAttempt)
        .filter(ModelCallAttempt.provider == "bulk")
        .all()
    )
    estimate = _estimate_attempts(attempts, prices_by_pair={("bulk", "m"): [price]})
    assert estimate.status in {"AVAILABLE", "PARTIAL"}
    assert estimate.value is not None

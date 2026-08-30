"""Offline behavior tests for versioned model-call cost estimates."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import GenerationJob, ModelCallAttempt, ModelPricingVersion, Project
from app.services.model_costs import estimate_jobs


def _seed_job(db, *, name: str = "费用估算项目") -> GenerationJob:
    project = Project(name=name)
    db.add(project)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id="candidate-cost",
        job_type="PAGE_GENERATE",
        status="COMPLETED",
        attempt_count=2,
        max_attempts=3,
    )
    db.add(job)
    db.commit()
    return job


def _price(
    db,
    *,
    provider: str = "provider-a",
    model_id: str = "model-a",
    version: str = "2026-01",
    currency: str = "USD",
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    input_rate: str | None = None,
    output_rate: str | None = None,
    image_rate: str | None = None,
    request_rate: str | None = None,
) -> ModelPricingVersion:
    row = ModelPricingVersion(
        provider=provider,
        model_id=model_id,
        pricing_version=version,
        currency=currency,
        effective_from=effective_from or datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=effective_to,
        input_tokens_per_million=(Decimal(input_rate) if input_rate else None),
        output_tokens_per_million=(Decimal(output_rate) if output_rate else None),
        output_image_each=(Decimal(image_rate) if image_rate else None),
        request_each=(Decimal(request_rate) if request_rate else None),
    )
    db.add(row)
    db.commit()
    return row


def _attempt(
    db,
    job: GenerationJob,
    *,
    dispatch_no: int,
    started_at: datetime,
    job_attempt: int = 1,
    provider: str = "provider-a",
    model_id: str = "model-a",
    usage: dict | None = None,
    outcome: str = "SUCCEEDED",
    route_switched: bool = False,
) -> ModelCallAttempt:
    row = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=job_attempt,
        dispatch_no=dispatch_no,
        route_switched=route_switched,
        outcome=outcome,
        provider=provider,
        model_id=model_id,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        duration_ms=1000,
        usage=usage,
    )
    db.add(row)
    db.commit()
    return row


def test_known_price_counts_failed_retry_and_route_switch_attempts(db_session):
    job = _seed_job(db_session)
    started = datetime(2026, 2, 1, tzinfo=UTC)
    _price(
        db_session,
        input_rate="2",
        image_rate="0.1",
        request_rate="0.01",
    )
    _attempt(
        db_session,
        job,
        dispatch_no=1,
        started_at=started,
        outcome="FAILED",
        usage={"prompt_tokens": 1_000_000, "output_images": 0},
    )
    _attempt(
        db_session,
        job,
        job_attempt=2,
        dispatch_no=1,
        started_at=started + timedelta(seconds=2),
        outcome="FAILED",
        usage={"input_tokens": 0, "output_images": 1},
    )
    _attempt(
        db_session,
        job,
        job_attempt=2,
        dispatch_no=2,
        started_at=started + timedelta(seconds=4),
        route_switched=True,
        usage={"input_tokens": 0, "output_images": 1},
    )

    estimate = estimate_jobs(db_session, [job.id])[job.id]

    assert estimate.status == "AVAILABLE"
    assert estimate.value == Decimal("2.230000")
    assert estimate.currency == "USD"
    assert estimate.pricing_versions == ("provider-a/model-a:2026-01",)
    assert "3 次实际调用" in estimate.note
    assert "不等于供应商账单" in estimate.note



def test_token_aliases_do_not_double_count_aggregate_total(db_session):
    job = _seed_job(db_session)
    _price(db_session, input_rate="2", output_rate="4")
    _attempt(
        db_session,
        job,
        dispatch_no=1,
        started_at=datetime(2026, 2, 1, tzinfo=UTC),
        usage={
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
            "total_tokens": 1_500_000,
        },
    )

    estimate = estimate_jobs(db_session, [job.id])[job.id]

    assert estimate.status == "AVAILABLE"
    assert estimate.value == Decimal("4.000000")

def test_missing_usage_is_partial_when_another_attempt_is_known(db_session):
    job = _seed_job(db_session)
    started = datetime(2026, 2, 1, tzinfo=UTC)
    _price(db_session, input_rate="1")
    _attempt(
        db_session,
        job,
        dispatch_no=1,
        started_at=started,
        usage={"input_tokens": 500_000},
    )
    _attempt(
        db_session,
        job,
        dispatch_no=2,
        started_at=started + timedelta(seconds=1),
        usage=None,
    )

    estimate = estimate_jobs(db_session, [job.id])[job.id]

    assert estimate.status == "PARTIAL"
    assert estimate.value == Decimal("0.500000")
    assert "1/2" in estimate.note


def test_missing_price_or_all_usage_never_becomes_zero(db_session):
    missing_price_job = _seed_job(db_session, name="缺价格")
    missing_usage_job = _seed_job(db_session, name="缺用量")
    started = datetime(2026, 2, 1, tzinfo=UTC)
    _attempt(
        db_session,
        missing_price_job,
        dispatch_no=1,
        started_at=started,
        provider="unknown-provider",
        model_id="unknown-model",
        usage={"input_tokens": 1},
    )
    _price(db_session, input_rate="1")
    _attempt(
        db_session,
        missing_usage_job,
        dispatch_no=1,
        started_at=started,
        usage=None,
    )

    estimates = estimate_jobs(db_session, [missing_price_job.id, missing_usage_job.id])

    for estimate in estimates.values():
        assert estimate.status == "UNAVAILABLE"
        assert estimate.value is None


def test_historical_attempt_uses_version_active_at_dispatch_time(db_session):
    job = _seed_job(db_session)
    boundary = datetime(2026, 6, 1, tzinfo=UTC)
    _price(
        db_session,
        version="old",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=boundary,
        request_rate="0.25",
    )
    _price(
        db_session,
        version="new",
        effective_from=boundary,
        request_rate="0.75",
    )
    _attempt(
        db_session,
        job,
        dispatch_no=1,
        started_at=datetime(2026, 5, 1, tzinfo=UTC),
        usage=None,
    )

    estimate = estimate_jobs(db_session, [job.id])[job.id]

    assert estimate.status == "AVAILABLE"
    assert estimate.value == Decimal("0.250000")
    assert estimate.pricing_versions == ("provider-a/model-a:old",)


def test_request_rate_does_not_hide_unmapped_billable_usage(db_session):
    job = _seed_job(db_session)
    _price(db_session, request_rate="0.10")
    _attempt(
        db_session,
        job,
        dispatch_no=1,
        started_at=datetime(2026, 2, 1, tzinfo=UTC),
        usage={"cached_content_token_count": 10},
    )

    estimate = estimate_jobs(db_session, [job.id])[job.id]

    assert estimate.status == "PARTIAL"
    assert estimate.value == Decimal("0.100000")
    assert "0/1" in estimate.note


def test_mixed_currencies_are_not_summed_without_exchange_rates(db_session):
    job = _seed_job(db_session)
    started = datetime(2026, 2, 1, tzinfo=UTC)
    _price(db_session, provider="provider-a", currency="USD", request_rate="1")
    _price(
        db_session,
        provider="provider-b",
        model_id="model-b",
        currency="CNY",
        request_rate="2",
    )
    _attempt(db_session, job, dispatch_no=1, started_at=started, usage=None)
    _attempt(
        db_session,
        job,
        dispatch_no=2,
        started_at=started + timedelta(seconds=1),
        provider="provider-b",
        model_id="model-b",
        usage=None,
        route_switched=True,
    )

    estimate = estimate_jobs(db_session, [job.id])[job.id]

    assert estimate.status == "UNAVAILABLE"
    assert estimate.value is None
    assert estimate.currency is None
    assert "多种币种" in estimate.note


def test_pricing_configuration_is_immutable_and_rejects_overlap(client):
    first = {
        "provider": "provider-a",
        "model_id": "model-a",
        "pricing_version": "v1",
        "currency": "USD",
        "effective_from": "2026-01-01T00:00:00Z",
        "effective_to": "2026-06-01T00:00:00Z",
        "input_tokens_per_million": "1.25000000",
    }
    naive = {**first, "effective_from": "2026-01-01T00:00:00"}
    assert client.post("/api/v1/providers/pricing-versions", json=naive).status_code == 422

    created = client.post("/api/v1/providers/pricing-versions", json=first)
    assert created.status_code == 201
    assert created.json()["pricing_version"] == "v1"

    overlap = {
        **first,
        "pricing_version": "v2-overlap",
        "effective_from": "2026-05-01T00:00:00Z",
        "effective_to": None,
    }
    rejected = client.post("/api/v1/providers/pricing-versions", json=overlap)
    assert rejected.status_code == 409
    assert "不能重叠" in rejected.json()["detail"]

    second = {
        **first,
        "pricing_version": "v2",
        "effective_from": "2026-06-01T00:00:00Z",
        "effective_to": None,
    }
    assert client.post("/api/v1/providers/pricing-versions", json=second).status_code == 201
    listed = client.get(
        "/api/v1/providers/pricing-versions",
        params={"provider": "provider-a", "model_id": "model-a"},
    )
    assert listed.status_code == 200
    assert [item["pricing_version"] for item in listed.json()] == ["v2", "v1"]


def test_job_endpoint_exposes_explicit_estimate_semantics(client, db_session):
    job = _seed_job(db_session)
    _price(db_session, request_rate="0.125", version="job-api-v1")
    _attempt(
        db_session,
        job,
        dispatch_no=1,
        started_at=datetime(2026, 2, 1, tzinfo=UTC),
        usage=None,
    )

    response = client.get(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["estimated_cost"] == 0.125
    assert body["estimated_cost_currency"] == "USD"
    assert body["estimated_cost_status"] == "AVAILABLE"
    assert body["estimated_cost_pricing_versions"] == [
        "provider-a/model-a:job-api-v1"
    ]
    assert "不等于供应商账单" in body["estimated_cost_note"]

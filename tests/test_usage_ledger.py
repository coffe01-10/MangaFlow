import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.models import (
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    ModelPricingVersion,
    PageCandidate,
    Project,
    ProviderUsageReconciliation,
)
from app.services.model_costs import estimate_jobs
from app.services.usage_ledger import normalize_usage, resolve_usage_dimensions
from app.services.worker_handlers import provider as provider_handler
from app.services.worker_handlers.model_call_audit import (
    ModelCallAttemptMeta,
    attach_attempt_outputs,
    begin_model_call_attempt,
    finalize_model_call_attempt,
)
from sqlalchemy import create_engine, inspect, select, text


def _seed_page_job(db_session) -> tuple[GenerationJob, PageCandidate]:
    project = Project(name="结构化用量项目")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.test",
        resolution="DRAFT_1K",
    )
    db_session.add(candidate)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_GENERATE",
        status="GENERATING",
        attempt_count=1,
    )
    db_session.add(job)
    db_session.commit()
    return job, candidate


def _meta(job: GenerationJob, **overrides) -> ModelCallAttemptMeta:
    values = {
        "job_id": job.id,
        "project_id": job.project_id,
        "job_attempt": 1,
        "provider": "provider-a",
        "model_id": "model-a",
        "dispatch_request_id": "dispatch-stable-1",
    }
    values.update(overrides)
    return ModelCallAttemptMeta(**values)


def test_normalize_usage_preserves_unknown_and_supports_nested_cache():
    missing = normalize_usage(None)
    assert missing.usage_status == "UNKNOWN"
    assert missing.input_tokens is None
    assert missing.output_images is None

    openai = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    )
    assert openai.usage_status == "COMPLETE"
    assert openai.usage_source == "PROVIDER_REPORTED"
    assert openai.unit_kind == "TEXT_TOKENS"
    assert openai.input_tokens == Decimal(100)
    assert openai.cached_input_tokens == Decimal(40)
    assert openai.cache_hit is True

    estimated_image = normalize_usage(None, output_image_count=1)
    assert estimated_image.usage_status == "COMPLETE"
    assert estimated_image.usage_source == "ADAPTER_ESTIMATED"
    assert estimated_image.output_images == Decimal(1)

    unmapped = normalize_usage(
        {"prompt_token_count": 10, "candidates_token_count": 2, "audio_seconds": 1}
    )
    assert unmapped.usage_status == "PARTIAL"
    unknown_unit = normalize_usage({"audio_seconds": 0.5})
    assert unknown_unit.usage_status == "PARTIAL"
    assert unknown_unit.usage_source == "PROVIDER_REPORTED"
    assert unknown_unit.unit_kind == "UNKNOWN"
    assert unknown_unit.input_tokens is None


def test_dispatch_replay_dimensions_finalize_and_output_attachment(db_session):
    job, candidate = _seed_page_job(db_session)
    dimensions = resolve_usage_dimensions(db_session, job)
    first = begin_model_call_attempt(
        _meta(
            job,
            chapter_id=dimensions.chapter_id,
            page_id=dimensions.page_id,
            candidate_id=dimensions.candidate_id,
        )
    )
    replay = begin_model_call_attempt(
        _meta(
            job,
            chapter_id=dimensions.chapter_id,
            page_id=dimensions.page_id,
            candidate_id=dimensions.candidate_id,
        )
    )
    assert replay == first

    finalize_model_call_attempt(
        first,
        outcome="SUCCEEDED",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 25},
        },
        output_image_count=1,
    )
    attach_attempt_outputs(
        first,
        asset_ids=["asset-1"],
        dimensions=[
            {
                "asset_id": "asset-1",
                "width": 1024,
                "height": 1536,
                "quality": "DRAFT_1K",
            }
        ],
    )
    attach_attempt_outputs(
        first,
        asset_ids=["asset-1"],
        dimensions=[
            {
                "asset_id": "asset-1",
                "width": 1024,
                "height": 1536,
                "quality": "DRAFT_1K",
            }
        ],
    )

    db_session.expire_all()
    row = db_session.get(ModelCallAttempt, first)
    page = db_session.get(MangaPage, candidate.page_id)
    assert row.chapter_id == page.chapter_id
    assert row.page_id == candidate.page_id
    assert row.candidate_id == candidate.id
    assert row.usage_status == "COMPLETE"
    assert row.unit_kind == "MIXED"
    assert row.input_tokens == Decimal(100)
    assert row.cached_input_tokens == Decimal(25)
    assert row.output_images == Decimal(1)
    assert row.output_asset_ids == ["asset-1"]
    assert row.output_image_dims[0]["width"] == 1024
    assert db_session.scalar(select(ModelCallAttempt).where(ModelCallAttempt.id != first)) is None


def test_output_attachment_failure_is_persisted_without_reversing_success(
    db_session, monkeypatch
):
    job, _candidate = _seed_page_job(db_session)
    attempt_id = begin_model_call_attempt(_meta(job))
    finalize_model_call_attempt(attempt_id, outcome="SUCCEEDED", output_image_count=1)
    db_session.info["pending_model_call_outputs"] = {
        attempt_id: {
            "asset_ids": ["asset-committed"],
            "dimensions": [{"asset_id": "asset-committed", "width": 1024}],
        }
    }

    def fail_attachment(*_args, **_kwargs):
        raise RuntimeError("sensitive database detail")

    monkeypatch.setattr(provider_handler, "attach_attempt_outputs", fail_attachment)
    provider_handler.flush_staged_attempt_outputs(db_session)

    db_session.expire_all()
    row = db_session.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "SUCCEEDED"
    assert row.output_asset_ids is None
    assert row.error_code == "OUTPUT_ATTACHMENT_FAILED"
    assert "sensitive" not in row.error_message


def test_cached_input_price_is_split_and_missing_cache_rate_is_partial(db_session):
    job, _candidate = _seed_page_job(db_session)
    started = datetime(2026, 9, 1, tzinfo=UTC)
    price = ModelPricingVersion(
        provider="provider-a",
        model_id="model-a",
        pricing_version="cached-v1",
        currency="USD",
        effective_from=started - timedelta(days=1),
        input_tokens_per_million=Decimal(2),
        cached_input_tokens_per_million=Decimal("0.5"),
    )
    attempt = ModelCallAttempt(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=1,
        dispatch_no=1,
        outcome="SUCCEEDED",
        provider="provider-a",
        model_id="model-a",
        started_at=started,
        usage={"input_tokens": 1_000_000, "cached_input_tokens": 400_000},
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
    )
    db_session.add_all([price, attempt])
    db_session.commit()

    estimate = estimate_jobs(db_session, [job.id])[job.id]
    assert estimate.status == "AVAILABLE"
    assert estimate.value == Decimal("1.400000")

    price.cached_input_tokens_per_million = None
    db_session.commit()
    estimate = estimate_jobs(db_session, [job.id])[job.id]
    assert estimate.status == "PARTIAL"
    assert estimate.value == Decimal("2.000000")


def test_usage_attempt_pagination_summary_and_unknown_semantics(
    db_session, client
):
    job, _candidate = _seed_page_job(db_session)
    started = datetime(2026, 9, 1, 10, tzinfo=UTC)
    db_session.add(
        ModelPricingVersion(
            provider="provider-a",
            model_id="model-a",
            pricing_version="v1",
            currency="USD",
            effective_from=started - timedelta(days=1),
            request_each=Decimal("0.1"),
        )
    )
    for index in range(3):
        db_session.add(
            ModelCallAttempt(
                job_id=job.id,
                project_id=job.project_id,
                job_attempt=1,
                dispatch_no=index + 1,
                route_switched=index > 0,
                outcome="SUCCEEDED" if index < 2 else None,
                channel="CLI" if index == 1 else "HTTP_API",
                provider="provider-a",
                model_id="model-a",
                started_at=started + timedelta(minutes=index),
                usage_status="COMPLETE" if index == 0 else "UNKNOWN",
                input_tokens=10 if index == 0 else None,
            )
        )
    db_session.commit()

    first = client.get(
        "/api/v1/usage/attempts",
        params={"project_id": job.project_id, "limit": 2},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 2
    assert first.json()["next_cursor"]
    second = client.get(
        "/api/v1/usage/attempts",
        params={"cursor": first.json()["next_cursor"], "limit": 2},
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["items"]) == 1
    malformed = client.get(
        "/api/v1/usage/attempts",
        params={"cursor": "_w"},
    )
    assert malformed.status_code == 422

    cli_only = client.get("/api/v1/usage/attempts", params={"channel": "CLI"})
    assert len(cli_only.json()["items"]) == 1

    summary = client.get(
        "/api/v1/usage/summary",
        params={"project_id": job.project_id},
    )
    assert summary.status_code == 200, summary.text
    groups = summary.json()["groups"]
    assert sum(item["attempt_count"] for item in groups) == 3
    http_group = next(item for item in groups if item["channel"] == "HTTP_API")
    assert http_group["input_tokens"] == 10
    assert http_group["output_tokens"] is None
    assert http_group["pending_count"] == 1
    assert http_group["estimated_costs"] == [
        {"currency": "USD", "amount": "0.10000000"}
    ]


def test_usage_read_filters_narrow_by_model_id(db_session, client):
    job, _candidate = _seed_page_job(db_session)
    started = datetime(2026, 9, 1, 10, tzinfo=UTC)
    for dispatch_no, model_id in enumerate(("model-a", "model-b"), start=1):
        db_session.add(
            ModelCallAttempt(
                job_id=job.id,
                project_id=job.project_id,
                job_attempt=1,
                dispatch_no=dispatch_no,
                outcome="SUCCEEDED",
                channel="HTTP_API",
                provider="provider-a",
                model_id=model_id,
                started_at=started,
                usage_status="COMPLETE",
                input_tokens=10,
            )
        )
    db_session.add(
        ProviderUsageReconciliation(
            provider="provider-a",
            model_id="model-a",
            channel="HTTP_API",
            billing_account_id="account-a",
            import_batch_id="batch-a",
            idempotency_key="line-1",
            period_start=started - timedelta(days=1),
            period_end=started + timedelta(days=1),
            currency="USD",
            billed_amount=Decimal("12.5"),
            entered_by="operator",
        )
    )
    db_session.commit()

    filtered = client.get(
        "/api/v1/usage/attempts",
        params={"project_id": job.project_id, "model_id": "model-b"},
    )
    assert filtered.status_code == 200, filtered.text
    items = filtered.json()["items"]
    assert len(items) == 1
    assert items[0]["model_id"] == "model-b"

    summary = client.get(
        "/api/v1/usage/summary",
        params={"project_id": job.project_id, "model_id": "model-b"},
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert [group["model_id"] for group in body["groups"]] == ["model-b"]
    assert body["billed"] == []

    billed_only = client.get("/api/v1/usage/summary", params={"model_id": "model-a"})
    assert billed_only.status_code == 200, billed_only.text
    assert len(billed_only.json()["billed"]) == 1

    # Without a model filter the reconciliation list must stay complete.
    unfiltered = client.get("/api/v1/usage/summary")
    assert unfiltered.status_code == 200, unfiltered.text
    assert len(unfiltered.json()["billed"]) == 1
    assert len(unfiltered.json()["groups"]) == 2


def test_reconciliation_is_idempotent_rejects_overlap_and_stays_separate(
    db_session, client
):
    payload = {
        "provider": "provider-a",
        "model_id": "model-a",
        "channel": "HTTP_API",
        "connection_id": "connection-a",
        "billing_account_id": "account-a",
        "import_batch_id": "batch-a",
        "idempotency_key": "line-1",
        "period_start": "2026-08-01T00:00:00Z",
        "period_end": "2026-09-01T00:00:00Z",
        "currency": "USD",
        "billed_amount": "12.50000000",
        "source_note": "运营者核对摘要",
        "entered_by": "operator",
    }
    created = client.post("/api/v1/usage/reconciliations", json=payload)
    assert created.status_code == 201, created.text
    replay = client.post("/api/v1/usage/reconciliations", json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created.json()["id"]
    changed_replay = client.post(
        "/api/v1/usage/reconciliations",
        json={**payload, "billed_amount": "99.00000000"},
    )
    assert changed_replay.status_code == 409
    assert "幂等键" in changed_replay.json()["detail"]

    overlap = {
        **payload,
        "import_batch_id": "batch-b",
        "idempotency_key": "line-2",
        "connection_id": "connection-b",
        "period_start": "2026-08-15T00:00:00Z",
        "period_end": "2026-09-15T00:00:00Z",
    }
    rejected = client.post("/api/v1/usage/reconciliations", json=overlap)
    assert rejected.status_code == 409
    assert "不能重叠" in rejected.json()["detail"]

    listed = client.get("/api/v1/usage/reconciliations")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    summary = client.get("/api/v1/usage/summary")
    assert summary.status_code == 200
    assert summary.json()["billed"][0]["billed_amount"] == "12.50000000"
    assert db_session.scalar(select(ProviderUsageReconciliation.id)) is not None


def test_usage_ledger_migration_backfills_and_roundtrips(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'usage-ledger.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "20260831_22")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (
                    id, name, language, reading_direction, page_ratio,
                    default_resolution, draft_resolution, workflow_mode,
                    default_concurrency, ocr_enabled,
                    consistency_check_enabled, deleted_at,
                    created_at, updated_at, version
                ) VALUES (
                    'project-ledger', '迁移账本', 'zh-CN', 'rtl', 'b5_portrait',
                    'STANDARD_2K', 'DRAFT_1K', 'SEMI_AUTO', 2, 0, 1, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO generation_jobs (
                    id, project_id, target_type, target_id, job_type, status,
                    priority, attempt_count, max_attempts, request_parameters,
                    progress, created_at, updated_at, version
                ) VALUES (
                    'job-ledger', 'project-ledger', 'PROJECT', 'project-ledger',
                    'WORKFLOW_NODE', 'COMPLETED', 50, 1, 3, '{}', 100,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO model_call_attempts (
                    id, job_id, project_id, job_attempt, dispatch_no,
                    route_switched, outcome, provider, model_id,
                    started_at, usage, created_at, updated_at, version
                ) VALUES (
                    'attempt-ledger', 'job-ledger', 'project-ledger', 1, 1,
                    0, 'SUCCEEDED', 'provider-a', 'model-a', CURRENT_TIMESTAMP,
                    :usage,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
                )
                """
            ),
            {
                "usage": json.dumps(
                    {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 25},
                        "audio_seconds": 1,
                    }
                )
            },
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    columns = {
        column["name"] for column in schema.get_columns("model_call_attempts")
    }
    assert {
        "dispatch_request_id",
        "channel",
        "usage_status",
        "cached_input_tokens",
        "output_asset_ids",
    } <= columns
    assert "provider_usage_reconciliations" in schema.get_table_names()
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT channel, usage_status, usage_source, input_tokens, "
                "output_tokens, cached_input_tokens "
                "FROM model_call_attempts WHERE id='attempt-ledger'"
            )
        ).one()
        assert tuple(row) == (
            "HTTP_API",
            "PARTIAL",
            "PROVIDER_REPORTED",
            100,
            20,
            25,
        )
    engine.dispose()

    command.downgrade(config, "20260831_22")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "provider_usage_reconciliations" not in schema.get_table_names()
    assert "usage_status" not in {
        column["name"] for column in schema.get_columns("model_call_attempts")
    }
    with engine.connect() as connection:
        usage = connection.execute(
            text("SELECT usage FROM model_call_attempts WHERE id='attempt-ledger'")
        ).scalar_one()
        assert "prompt_tokens" in usage
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()

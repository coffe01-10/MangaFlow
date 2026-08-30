"""Offline behavior tests for the model call audit service.

The audit service runs on an independent SessionLocal by design; these tests
swap the sessionmaker for a test factory bound to an in-memory database so
begin/finalize are exercised for real (real rows, real commits) while staying
deterministic and offline.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.worker_handlers.model_call_audit as audit
from app.database import Base
from app.models import (
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Project,
    Chapter,
)
from app.services.worker_handlers.model_call_audit import (
    ModelCallAttemptMeta,
    begin_model_call_attempt,
    finalize_model_call_attempt,
)


@pytest.fixture
def audit_sessions(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'audit.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    original = audit.SessionLocal
    audit.SessionLocal = factory
    try:
        yield factory
    finally:
        audit.SessionLocal = original
        engine.dispose()


def _seed_job(factory) -> GenerationJob:
    with factory() as db:
        project = Project(name="审计服务项目")
        db.add(project)
        db.flush()
        chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
        db.add(chapter)
        db.flush()
        page = MangaPage(
            chapter_id=chapter.id,
            page_number=1,
            storyboard_version=1,
            status="PLANNED",
            source_coverage={"complete": True},
            scene_ids=["scene-1"],
            beat_ids=["beat-1"],
        )
        db.add(page)
        db.flush()
        job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id=page.id,
            job_type="PAGE_GENERATE",
            status="GENERATING",
            attempt_count=1,
        )
        db.add(job)
        db.commit()
        return job


def _meta(job: GenerationJob, **overrides) -> ModelCallAttemptMeta:
    values = {
        "job_id": job.id,
        "project_id": job.project_id,
        "job_attempt": 1,
        "provider": "preset-provider",
        "model_id": "model-xyz",
        "route_reason": "EXPLICIT",
    }
    values.update(overrides)
    return ModelCallAttemptMeta(**values)


def test_begin_assigns_monotonic_dispatch_numbers_per_job_attempt(audit_sessions):
    job = _seed_job(audit_sessions)

    first = begin_model_call_attempt(_meta(job))
    second = begin_model_call_attempt(_meta(job))
    replacement = begin_model_call_attempt(_meta(job, route_switched=True))

    with audit_sessions() as db:
        rows = list(
            db.scalars(
                select(ModelCallAttempt)
                .where(ModelCallAttempt.job_id == job.id)
                .order_by(ModelCallAttempt.dispatch_no)
            )
        )
    assert [row.dispatch_no for row in rows] == [1, 2, 3]
    assert [row.outcome for row in rows] == [None, None, None]
    assert rows[2].route_switched is True
    assert first and second and replacement


def test_begin_starts_new_sequence_for_next_job_attempt(audit_sessions):
    job = _seed_job(audit_sessions)
    begin_model_call_attempt(_meta(job, job_attempt=1))
    begin_model_call_attempt(_meta(job, job_attempt=2))

    with audit_sessions() as db:
        rows = list(db.scalars(select(ModelCallAttempt).order_by(ModelCallAttempt.job_attempt, ModelCallAttempt.dispatch_no)))
    assert [(row.job_attempt, row.dispatch_no) for row in rows] == [(1, 1), (2, 1)]


def test_success_finalize_records_usage_request_and_duration(audit_sessions):
    job = _seed_job(audit_sessions)
    attempt_id = begin_model_call_attempt(_meta(job))

    finalize_model_call_attempt(
        attempt_id,
        outcome="SUCCEEDED",
        model_id="model-reported-by-adapter",
        request_id="req-123",
        usage={"input_tokens": 10, "output_tokens": 20},
    )

    with audit_sessions() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "SUCCEEDED"
    assert row.model_id == "model-reported-by-adapter"
    assert row.request_id == "req-123"
    assert row.usage == {"input_tokens": 10, "output_tokens": 20}
    assert row.error_code is None
    assert row.duration_ms is not None and row.duration_ms >= 0


def test_finalize_without_usage_keeps_columns_null(audit_sessions):
    job = _seed_job(audit_sessions)
    attempt_id = begin_model_call_attempt(_meta(job))

    finalize_model_call_attempt(attempt_id, outcome="SUCCEEDED")

    with audit_sessions() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.usage is None
    assert row.request_id is None
    assert row.outcome == "SUCCEEDED"


def test_failure_finalize_truncates_message_and_survives_caller_rollback(
    audit_sessions,
):
    job = _seed_job(audit_sessions)
    attempt_id = begin_model_call_attempt(_meta(job))

    finalize_model_call_attempt(
        attempt_id,
        outcome="FAILED",
        error_code="RATE_LIMIT",
        error_message="x" * 800,
    )
    # Caller-owned transaction rolls back afterwards; the audit row must stay.
    with audit_sessions() as db:
        db.rollback()

    with audit_sessions() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row.outcome == "FAILED"
    assert row.error_code == "RATE_LIMIT"
    assert row.error_message == "x" * 500


def test_failed_audit_survives_worker_rollback(audit_sessions, monkeypatch):
    """Correction-proof: a worker-style rollback after failure finalize must not
    erase the FAILED attempt row (independent session)."""

    job = _seed_job(audit_sessions)
    attempt_id = begin_model_call_attempt(_meta(job))
    finalize_model_call_attempt(
        attempt_id, outcome="FAILED", error_code="AUTHENTICATION", error_message="denied"
    )

    # Simulate the worker's rollback on its own session (not the audit session).
    with audit_sessions() as worker_db:
        worker_db.rollback()

    with audit_sessions() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    assert row is not None and row.outcome == "FAILED"


def test_unknown_attempt_finalize_raises(audit_sessions):
    with pytest.raises(RuntimeError, match="审计行不存在"):
        finalize_model_call_attempt("missing-attempt-id", outcome="FAILED")


def test_read_model_redacts_sensitive_material(audit_sessions):
    """The read schema must never expose secrets, credential paths, headers or
    request payloads — only redacted metadata and opaque row references."""

    import json as jsonlib

    from app.models import ProviderConnection, ProviderKey, ProviderProfile
    from app.schemas import ModelCallAttemptRead

    job = _seed_job(audit_sessions)
    with audit_sessions() as db:
        profile = ProviderProfile(preset_key="preset-provider", name="测试供应商")
        db.add(profile)
        db.flush()
        connection = ProviderConnection(
            provider_id=profile.id, name="默认连接", protocol="test", base_url="https://x"
        )
        db.add(connection)
        db.flush()
        key = ProviderKey(connection_id=connection.id, label="primary", encrypted_secret="enc")
        db.add(key)
        db.commit()
        connection_id = connection.id
        key_id = key.id
    attempt_id = begin_model_call_attempt(
        _meta(job, selected_key_id=key_id, connection_id=connection_id)
    )
    finalize_model_call_attempt(
        attempt_id,
        outcome="FAILED",
        error_code="AUTHENTICATION",
        error_message="凭据无效，请检查密钥配置",
        usage={"input_tokens": 3},
    )

    with audit_sessions() as db:
        row = db.get(ModelCallAttempt, attempt_id)
    payload = ModelCallAttemptRead.model_validate(row).model_dump(mode="json")
    blob = jsonlib.dumps(payload, ensure_ascii=False).lower()

    # Opaque references only: key/connection ids, never key material or hints.
    assert payload["selected_key_id"] == key_id
    assert payload["connection_id"] == connection_id
    for forbidden in (
        "secret",
        "encrypted",
        "api_key",
        "authorization",
        "extra_headers",
        "base_url",
        "prompt",
    ):
        assert forbidden not in blob, f"read model leaked sensitive material: {forbidden}"
    # The ledger schema itself carries no credential-path or payload columns.
    column_names = {column.name for column in ModelCallAttempt.__table__.columns}
    assert not {"credentials", "credential_path", "request_body", "headers"} & column_names

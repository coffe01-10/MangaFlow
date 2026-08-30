"""Provider-dispatch audit wiring tests using fake adapters.

Drives ``worker_handlers.provider._invoke_provider`` directly with fake
adapters and a file-backed SQLite database shared between the caller session
and the independent audit session, proving the audit lifecycle around real
paid-call boundaries: success, retryable failure, terminal failure, route
switch, fail-closed begin, finalize failure and multi-chunk numbering.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.worker_handlers.model_call_audit as audit
import app.services.worker_handlers.provider as provider
from app.database import Base
from app.model_adapters.base import ModelResponse, ProviderAdapterError
from app.models import (
    AIModel,
    GenerationJob,
    ModelCallAttempt,
    Project,
    ProviderConnection,
    ProviderKey,
    ProviderProfile,
)
from app.services.credential_crypto import SelectedProviderKey
from app.services.model_router import AdapterBinding, ResolvedModel


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'wiring.db').as_posix()}")
    Base.metadata.create_all(engine)
    caller_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    audit_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(audit, "SessionLocal", audit_factory)

    with caller_factory() as db:
        project = Project(name="接线测试项目")
        db.add(project)
        db.flush()
        profile = ProviderProfile(preset_key="preset-provider", name="测试供应商")
        db.add(profile)
        db.flush()
        connection = ProviderConnection(
            provider_id=profile.id, name="默认连接", protocol="test", base_url="https://x"
        )
        db.add(connection)
        db.flush()
        model = AIModel(
            connection_id=connection.id,
            provider_model_id="pm-primary",
            display_name="主模型",
            model_type="IMAGE",
            operations=["image_generate"],
        )
        db.add(model)
        db.flush()
        key = ProviderKey(connection_id=connection.id, label="primary", encrypted_secret="enc")
        db.add(key)
        db.flush()
        key2 = ProviderKey(connection_id=connection.id, label="backup", encrypted_secret="enc2")
        db.add(key2)
        db.flush()
        job = GenerationJob(
            project_id=project.id,
            target_type="PAGE_CANDIDATE",
            target_id="page-1",
            job_type="PAGE_GENERATE",
            status="GENERATING",
            attempt_count=2,
        )
        db.add(job)
        db.commit()
        rows = {
            "project_id": project.id,
            "profile": profile,
            "connection": connection,
            "model": model,
            "key": key,
            "key2": key2,
            "job": job,
        }
    return caller_factory, rows


def _binding(rows, adapter, key=None, replacement=False):
    key = key or rows["key"]
    return AdapterBinding(
        resolved=ResolvedModel(
            model=rows["model"],
            connection=rows["connection"],
            provider=rows["profile"],
            route_reason="EXPLICIT" if not replacement else "FAILOVER",
            route_score=1.0 if replacement else 0.0,
        ),
        adapter=adapter,
        selected_key=SelectedProviderKey(row=key, secret="secret-value"),
    )


def _rows_for_job(factory, job_id):
    with factory() as db:
        return list(
            db.scalars(
                select(ModelCallAttempt)
                .where(ModelCallAttempt.job_id == job_id)
                .order_by(ModelCallAttempt.dispatch_no)
            )
        )


class _FakeAdapter:
    def __init__(self):
        self.calls = 0

    def generate_page(self, request):
        self.calls += 1
        return ModelResponse(
            model_id="reported-model", request_id="req-1", usage={"tokens": 7}
        )


def test_success_records_attempt_with_usage_and_request_id(env):
    caller_factory, rows = env
    adapter = _FakeAdapter()
    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        result = provider._invoke_provider(
            db, _binding(rows, adapter), lambda a: a.generate_page(None)
        )
    assert result.request_id == "req-1"
    attempts = _rows_for_job(caller_factory, rows["job"].id)
    assert len(attempts) == 1
    assert attempts[0].outcome == "SUCCEEDED"
    assert attempts[0].job_attempt == 2
    assert attempts[0].dispatch_no == 1
    assert attempts[0].provider == "preset-provider"
    assert attempts[0].model_id == "reported-model"
    assert attempts[0].request_id == "req-1"
    assert attempts[0].usage == {"tokens": 7}
    assert attempts[0].route_switched is False
    assert attempts[0].route_reason == "EXPLICIT"


def test_retryable_failure_records_failed_attempt_and_survives_rollback(env):
    caller_factory, rows = env

    def failing(_request):
        raise ProviderAdapterError("RATE_LIMIT", "服务繁忙", retryable=True)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with pytest.raises(ProviderAdapterError) as exc_info:
            provider._invoke_provider(db, _binding(rows, failing), failing)
        assert exc_info.value.code == "RATE_LIMIT"
        # Caller-owned transaction rolls back afterwards; audit row must persist.
        db.rollback()

    attempts = _rows_for_job(caller_factory, rows["job"].id)
    assert len(attempts) == 1
    assert attempts[0].outcome == "FAILED"
    assert attempts[0].error_code == "RATE_LIMIT"
    assert attempts[0].error_message == "服务繁忙"
    assert attempts[0].finished_at is not None


def test_route_switch_records_two_attempts_and_key_marking_order(env, monkeypatch):
    caller_factory, rows = env
    key_failures: list[str] = []
    key_successes: list[str] = []

    monkeypatch.setattr(
        provider, "mark_key_failure", lambda db, key, code, retry_after_seconds=None: key_failures.append(key.id)
    )
    monkeypatch.setattr(
        provider, "mark_key_success", lambda db, key: key_successes.append(key.id)
    )

    class _SwitchingAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderAdapterError("AUTHENTICATION", "密钥无效")
            return ModelResponse(model_id="fallback-model", request_id="req-2", usage={"tokens": 3})

    adapter = _SwitchingAdapter()
    replacement_binding = _binding(
        rows, adapter, key=rows["key2"], replacement=True
    )
    monkeypatch.setattr(provider, "bind_adapter", lambda *a, **k: replacement_binding)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        result = provider._invoke_provider(
            db, _binding(rows, adapter), lambda a: a.generate_page(None)
        )
    assert result.request_id == "req-2"
    assert adapter.calls == 2
    assert key_failures == [rows["key"].id]
    assert key_successes == [rows["key2"].id]

    attempts = _rows_for_job(caller_factory, rows["job"].id)
    assert [(item.dispatch_no, item.outcome, item.route_switched) for item in attempts] == [
        (1, "FAILED", False),
        (2, "SUCCEEDED", True),
    ]
    assert attempts[0].error_code == "AUTHENTICATION"
    assert attempts[1].model_id == "fallback-model"
    assert attempts[1].selected_key_id == rows["key2"].id


def test_begin_failure_blocks_provider_call(env, monkeypatch):
    caller_factory, rows = env
    adapter = _FakeAdapter()

    def broken_begin(meta):
        raise RuntimeError("数据库不可用")

    monkeypatch.setattr(provider, "begin_model_call_attempt", broken_begin)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with pytest.raises(ProviderAdapterError) as exc_info:
            provider._invoke_provider(db, _binding(rows, adapter), lambda a: a.generate_page(None))
    assert exc_info.value.code == "AUDIT_PERSISTENCE_FAILED"
    assert exc_info.value.retryable is False
    assert adapter.calls == 0


def test_finalize_failure_never_repeats_paid_call(env, monkeypatch):
    caller_factory, rows = env

    class _FailingAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            raise ProviderAdapterError("RATE_LIMIT", "繁忙", retryable=True)

    adapter = _FailingAdapter()

    def broken_finalize(attempt_id, **kwargs):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(provider, "finalize_model_call_attempt", broken_finalize)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with pytest.raises(ProviderAdapterError) as exc_info:
            provider._invoke_provider(db, _binding(rows, adapter), lambda a: a.generate_page(None))
    assert exc_info.value.code == "AUDIT_PERSISTENCE_FAILED"
    assert exc_info.value.retryable is False
    assert adapter.calls == 1
    # In-flight audit row preserved for diagnosis.
    attempts = _rows_for_job(caller_factory, rows["job"].id)
    assert len(attempts) == 1
    assert attempts[0].outcome is None


def test_multi_chunk_calls_then_route_switch_numbering(env, monkeypatch):
    caller_factory, rows = env
    monkeypatch.setattr(provider, "mark_key_failure", lambda *a, **k: None)
    monkeypatch.setattr(provider, "mark_key_success", lambda *a, **k: None)

    class _ChunkAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 3:
                raise ProviderAdapterError("PERMISSION", "无权限")
            return ModelResponse(model_id=f"chunk-{self.calls}", request_id=None, usage=None)

    adapter = _ChunkAdapter()
    replacement_binding = _binding(rows, adapter, key=rows["key2"], replacement=True)
    monkeypatch.setattr(provider, "bind_adapter", lambda *a, **k: replacement_binding)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        for _ in range(2):
            provider._invoke_provider(db, _binding(rows, adapter), lambda a: a.generate_page(None))
        provider._invoke_provider(db, _binding(rows, adapter), lambda a: a.generate_page(None))

    attempts = _rows_for_job(caller_factory, rows["job"].id)
    assert [(item.dispatch_no, item.outcome, item.route_switched) for item in attempts] == [
        (1, "SUCCEEDED", False),
        (2, "SUCCEEDED", False),
        (3, "FAILED", False),
        (4, "SUCCEEDED", True),
    ]

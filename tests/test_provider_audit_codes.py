"""Audit-code and failure-diagnostics scoping tests for ``provider._invoke_provider``.

Covers the two R1 findings on the paid-call failure path:

1. An unclassified exception must converge its audit row with the SAME code
   the worker writes on the job (``WORKER_ERROR``), never ``INVALID_OUTPUT``
   (which pointed output-quality triage at adapter/worker crashes).
2. The key/connection failure diagnostics must survive the worker's rollback
   WITHOUT publishing unrelated pending changes from the caller's session
   (the old bare ``db.commit()`` committed everything pending).
3. ``_mark_key_outcome`` is best-effort on all three of its call sites: a
   diagnostics failure after a SUCCEEDED paid call (or before the adapter's
   re-raised classification on the retry-error path) must never replace the
   adapter's outcome — that reclassified finished jobs as retryable
   WORKER_ERROR and bought a second paid dispatch.
"""

import inspect
import logging

import pytest
from fastapi import HTTPException
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
    engine = create_engine(f"sqlite:///{(tmp_path / 'audit-codes.db').as_posix()}")
    Base.metadata.create_all(engine)
    caller_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    audit_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(audit, "SessionLocal", audit_factory)

    with caller_factory() as db:
        project = Project(name="审计编码测试项目")
        db.add(project)
        db.flush()
        profile = ProviderProfile(preset_key="preset-audit", name="审计供应商")
        db.add(profile)
        db.flush()
        connection = ProviderConnection(
            provider_id=profile.id, name="默认连接", protocol="test", base_url="https://x"
        )
        db.add(connection)
        db.flush()
        model = AIModel(
            connection_id=connection.id,
            provider_model_id="pm-audit",
            display_name="审计模型",
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
            "project": project,
            "profile": profile,
            "connection": connection,
            "model": model,
            "key": key,
            "key2": key2,
            "job": job,
        }
    return caller_factory, rows


def _binding(rows, adapter, key=None):
    key = key or rows["key"]
    return AdapterBinding(
        resolved=ResolvedModel(
            model=rows["model"],
            connection=rows["connection"],
            provider=rows["profile"],
            route_reason="EXPLICIT",
            route_score=1.0,
        ),
        adapter=adapter,
        selected_key=SelectedProviderKey(row=key, secret="secret-value"),
    )


def _attempts_for_job(factory, job_id):
    with factory() as db:
        return list(
            db.scalars(
                select(ModelCallAttempt)
                .where(ModelCallAttempt.job_id == job_id)
                .order_by(ModelCallAttempt.dispatch_no)
            )
        )


def test_unclassified_exception_audits_worker_error_not_invalid_output(env):
    """The audit code must match the job-level WORKER_ERROR the worker's
    generic path writes for the same exception (worker_tasks.execute_job),
    so reliability triage joins both rows to one failure class."""

    caller_factory, rows = env

    class _CrashingAdapter:
        def generate_page(self, request):
            raise RuntimeError("adapter bug: unexpected internal state")

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with pytest.raises(RuntimeError, match="adapter bug"):
            provider._invoke_provider(
                db, _binding(rows, _CrashingAdapter()), lambda a: a.generate_page(None)
            )

    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert len(attempts) == 1
    assert attempts[0].outcome == "FAILED"
    assert attempts[0].error_code == "WORKER_ERROR"
    assert attempts[0].error_code != "INVALID_OUTPUT"
    assert attempts[0].finished_at is not None

    # The audit code must stay joined to the worker's job-level literal:
    # execute_job's generic path stamps the same class on the job row.
    from app import worker_tasks

    generic_handler_source = inspect.getsource(worker_tasks.execute_job)
    assert '"WORKER_ERROR"' in generic_handler_source
    assert provider.UNCLASSIFIED_ERROR_CODE == "WORKER_ERROR"


def test_unclassified_exception_in_replacement_dispatch_audits_worker_error(
    env, monkeypatch
):
    caller_factory, rows = env

    class _SwitchingAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderAdapterError("AUTHENTICATION", "密钥无效")
            raise RuntimeError("replacement dispatch crashed")

    adapter = _SwitchingAdapter()
    replacement_binding = _binding(rows, adapter, key=rows["key2"])
    monkeypatch.setattr(provider, "bind_adapter", lambda *a, **k: replacement_binding)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with pytest.raises(RuntimeError, match="replacement dispatch crashed"):
            provider._invoke_provider(
                db, _binding(rows, adapter), lambda a: a.generate_page(None)
            )

    assert adapter.calls == 2
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [(item.dispatch_no, item.outcome, item.error_code) for item in attempts] == [
        (1, "FAILED", "AUTHENTICATION"),
        (2, "FAILED", "WORKER_ERROR"),
    ]


def test_failure_diagnostics_survive_without_publishing_pending_caller_changes(env):
    """A failed paid call must persist key/connection diagnostics durably
    (the worker rolls the caller session back right after) while an
    unrelated pending change in that same session must NOT be committed —
    the old failure-path ``db.commit()`` published both."""

    caller_factory, rows = env

    class _FailingAdapter:
        def generate_page(self, request):
            raise ProviderAdapterError("UPSTREAM", "上游错误", retryable=True)

    adapter = _FailingAdapter()

    with caller_factory() as db:
        job = db.get(GenerationJob, rows["job"].id)
        # Unrelated pending caller write: never flushed, never committed by
        # the handler before the dispatch (mirrors e.g. a catalog_model_id
        # set in inspection.py just before _invoke_provider).
        job.error_message = "pending-unrelated-change"
        db.info["job_id"] = rows["job"].id
        with pytest.raises(ProviderAdapterError) as exc_info:
            provider._invoke_provider(db, _binding(rows, adapter), adapter.generate_page)
        assert exc_info.value.code == "UPSTREAM"
        binding_key_row = rows["key"]

    # Audit row: durable with the provider code (unchanged behavior).
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [item.error_code for item in attempts] == ["UPSTREAM"]

    with caller_factory() as fresh:
        # Diagnostics DID persist on their own scoped transaction.
        persisted_key = fresh.get(ProviderKey, binding_key_row.id)
        assert persisted_key.last_error_code == "UPSTREAM"
        assert persisted_key.health_state == "DEGRADED"
        persisted_connection = fresh.get(ProviderConnection, rows["connection"].id)
        assert persisted_connection.error_code == "UPSTREAM"
        assert persisted_connection.message == "模型调用失败，已记录最近一次真实流量错误"
        # The unrelated pending caller change was NOT published.
        persisted_job = fresh.get(GenerationJob, rows["job"].id)
        assert persisted_job.error_message is None


def test_caller_session_sees_committed_diagnostics_after_failure(env):
    """The caller's key object is expired after the scoped write, so the
    replacement-key rebinding below the failure observes committed state
    (identity-map staleness would re-select the just-failed key)."""

    caller_factory, rows = env

    class _FailingAdapter:
        def generate_page(self, request):
            raise ProviderAdapterError("RATE_LIMIT", "服务繁忙", retryable=True)

    adapter = _FailingAdapter()

    with caller_factory() as db:
        key = db.get(ProviderKey, rows["key"].id)
        db.info["job_id"] = rows["job"].id
        binding = _binding(rows, adapter, key=key)
        with pytest.raises(ProviderAdapterError):
            provider._invoke_provider(db, binding, adapter.generate_page)
        # Expired attribute: re-read hits the DB and sees the diagnostics.
        assert key.health_state == "COOLDOWN"
        assert key.last_error_code == "RATE_LIMIT"
        assert key.cooldown_until is not None


def test_success_path_unchanged_for_audit_and_keys(env, monkeypatch):
    """Regression guard: the scoped-diagnostics change must not alter the
    success path (SUCCEEDED audit with usage; key marked healthy)."""

    caller_factory, rows = env
    successes: list[str] = []
    monkeypatch.setattr(
        provider, "mark_key_success", lambda db, key: successes.append(key.id)
    )

    class _OkAdapter:
        def generate_page(self, request):
            return ModelResponse(
                model_id="reported", request_id="req-ok", usage={"tokens": 5}
            )

    adapter = _OkAdapter()
    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        result = provider._invoke_provider(
            db, _binding(rows, adapter), lambda a: a.generate_page(None)
        )
    assert result.request_id == "req-ok"
    assert successes == [rows["key"].id]
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [(item.outcome, item.error_code) for item in attempts] == [("SUCCEEDED", None)]


def test_diagnostics_write_failure_preserves_original_error(env, monkeypatch, caplog):
    """A diagnostics-write failure must never replace the adapter's error.

    Before the best-effort guard, an exception inside the scoped diagnostics
    session (lock timeout, pool exhaustion) propagated out of the failure
    handler: the original ProviderAdapterError was never re-raised, the
    replacement-key retry was skipped, and a non-retryable AUTHENTICATION was
    reclassified by the worker as a retried generic WORKER_ERROR (a second
    paid dispatch).
    """

    caller_factory, rows = env

    class _DeniedAdapter:
        def generate_page(self, request):
            raise ProviderAdapterError("AUTHENTICATION", "密钥无效", retryable=False)

    adapter = _DeniedAdapter()

    def _locked(*args, **kwargs):
        raise RuntimeError("diagnostics backend locked")

    monkeypatch.setattr(provider, "mark_key_failure", _locked)

    def _no_replacement(*args, **kwargs):
        raise HTTPException(status_code=409, detail="没有可用替换密钥")

    monkeypatch.setattr(provider, "bind_adapter", _no_replacement)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with caplog.at_level(logging.WARNING, logger=provider.__name__):
            with pytest.raises(ProviderAdapterError) as exc_info:
                provider._invoke_provider(db, _binding(rows, adapter), adapter.generate_page)
        # The ORIGINAL error (with its classification) reaches the worker.
        assert exc_info.value.code == "AUTHENTICATION"
        assert exc_info.value.retryable is False
        assert exc_info.value.user_message == "密钥无效"

    # The diagnostics failure was logged with the key id, not raised.
    assert "diagnostics backend locked" in caplog.text
    assert rows["key"].id in caplog.text
    # Audit convergence is unaffected: the attempt keeps the adapter code.
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [(item.outcome, item.error_code) for item in attempts] == [
        ("FAILED", "AUTHENTICATION")
    ]


def test_replacement_failure_mark_persists_without_publishing_caller_changes(
    env, monkeypatch
):
    """The replacement key's failure mark must commit on the scoped second
    session: durable despite the caller's rollback, while a pending unrelated
    caller change stays uncommitted. The old ``mark_key_failure(db, ...)``
    ran on the CALLER's session and its internal commit published both."""

    caller_factory, rows = env

    class _FlakyAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderAdapterError("AUTHENTICATION", "密钥无效", retryable=False)
            raise ProviderAdapterError("RATE_LIMIT", "服务繁忙", retryable=True)

    adapter = _FlakyAdapter()

    with caller_factory() as db:
        # Caller-bound replacement key row, as the real bind_adapter would
        # produce: the helper must re-fetch by id, never rebind this object.
        backup_key = db.get(ProviderKey, rows["key2"].id)
        replacement_binding = _binding(rows, adapter, key=backup_key)
        monkeypatch.setattr(
            provider, "bind_adapter", lambda *a, **k: replacement_binding
        )
        job = db.get(GenerationJob, rows["job"].id)
        job.error_message = "pending-unrelated-change"
        db.info["job_id"] = rows["job"].id
        with pytest.raises(ProviderAdapterError) as exc_info:
            provider._invoke_provider(db, _binding(rows, adapter), adapter.generate_page)
        # The replacement attempt ran and ITS error is the one re-raised.
        assert adapter.calls == 2
        assert exc_info.value.code == "RATE_LIMIT"

    with caller_factory() as fresh:
        backup = fresh.get(ProviderKey, rows["key2"].id)
        assert backup.last_error_code == "RATE_LIMIT"
        assert backup.health_state == "COOLDOWN"
        assert backup.cooldown_until is not None
        # Primary-key diagnostics (scoped session) still durable.
        primary = fresh.get(ProviderKey, rows["key"].id)
        assert primary.health_state == "DENIED"
        assert primary.enabled is False
        # The pending unrelated caller change was NOT published by either mark.
        assert fresh.get(GenerationJob, rows["job"].id).error_message is None


def test_replacement_success_mark_persists_without_publishing_caller_changes(
    env, monkeypatch
):
    """Same contract for the replacement SUCCESS mark: the key becomes HEALTHY
    durably while the caller's pending change waits for the caller's own
    commit (mark_key_success commits internally and must not publish it)."""

    caller_factory, rows = env

    class _RecoveringAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderAdapterError("AUTHENTICATION", "密钥无效", retryable=False)
            return ModelResponse(
                model_id="reported", request_id="req-recovery", usage={"tokens": 1}
            )

    adapter = _RecoveringAdapter()

    # Pre-degrade the backup key so the success mark is observable.
    with caller_factory() as db:
        backup = db.get(ProviderKey, rows["key2"].id)
        backup.health_state = "DEGRADED"
        backup.last_error_code = "OLD"
        db.commit()

    replacement_binding = _binding(rows, adapter, key=rows["key2"])
    monkeypatch.setattr(provider, "bind_adapter", lambda *a, **k: replacement_binding)

    with caller_factory() as db:
        job = db.get(GenerationJob, rows["job"].id)
        job.error_message = "pending-unrelated-change"
        db.info["job_id"] = rows["job"].id
        result = provider._invoke_provider(
            db, _binding(rows, adapter), adapter.generate_page
        )
        assert result.request_id == "req-recovery"

    with caller_factory() as fresh:
        backup = fresh.get(ProviderKey, rows["key2"].id)
        assert backup.health_state == "HEALTHY"
        assert backup.last_error_code is None
        assert backup.cooldown_until is None
        assert fresh.get(GenerationJob, rows["job"].id).error_message is None


def test_primary_success_survives_key_mark_diagnostics_failure(
    env, monkeypatch, caplog
):
    """A diagnostics failure while marking the key after a SUCCESSFUL paid call
    must never replace that success.

    Before the best-effort guard inside ``_mark_key_outcome``, the raise
    propagated out of ``_invoke_provider`` after the audit row was already
    SUCCEEDED: the worker's generic handler then marked the finished job as a
    retryable WORKER_ERROR — a second paid dispatch plus audit/job divergence.
    """

    caller_factory, rows = env

    def _locked(db, key):
        raise RuntimeError("diagnostics pool exhausted")

    monkeypatch.setattr(provider, "mark_key_success", _locked)

    class _OkAdapter:
        def generate_page(self, request):
            return ModelResponse(
                model_id="reported", request_id="req-ok", usage={"tokens": 5}
            )

    adapter = _OkAdapter()
    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with caplog.at_level(logging.WARNING, logger=provider.__name__):
            result = provider._invoke_provider(
                db, _binding(rows, adapter), lambda a: a.generate_page(None)
            )
        # The paid call's result reaches the caller; nothing was raised.
        assert result.request_id == "req-ok"

    # The diagnostics failure was logged with the key id, not raised.
    assert "diagnostics pool exhausted" in caplog.text
    assert rows["key"].id in caplog.text
    # Audit convergence is unaffected: the attempt stays SUCCEEDED.
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [(item.outcome, item.error_code) for item in attempts] == [
        ("SUCCEEDED", None)
    ]
    # Job-level classification unchanged: no exception escapes, so the worker
    # never runs its generic WORKER_ERROR path for this dispatch.
    with caller_factory() as fresh:
        job = fresh.get(GenerationJob, rows["job"].id)
        assert job.status == "GENERATING"
        assert job.error_message is None


def test_replacement_success_survives_key_mark_diagnostics_failure(
    env, monkeypatch, caplog
):
    """Same contract for the replacement-key SUCCESS mark: the recovery result
    is returned even when the scoped second session cannot open at all (pool
    exhaustion), so a successful replacement dispatch is never reclassified.
    The primary failure's ``_record_key_and_connection_failure`` shares the
    broken session and must swallow its own diagnostics error the same way."""

    caller_factory, rows = env

    def _broken_sessionmaker(db):
        def _explode():
            raise RuntimeError("diagnostics pool exhausted")

        return _explode

    monkeypatch.setattr(provider, "_diagnostics_sessionmaker", _broken_sessionmaker)

    class _RecoveringAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderAdapterError("AUTHENTICATION", "密钥无效", retryable=False)
            return ModelResponse(
                model_id="reported", request_id="req-recovery", usage={"tokens": 1}
            )

    adapter = _RecoveringAdapter()
    replacement_binding = _binding(rows, adapter, key=rows["key2"])
    monkeypatch.setattr(provider, "bind_adapter", lambda *a, **k: replacement_binding)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with caplog.at_level(logging.WARNING, logger=provider.__name__):
            result = provider._invoke_provider(
                db, _binding(rows, adapter), adapter.generate_page
            )
        assert adapter.calls == 2
        # The replacement paid call's result reaches the caller; nothing raised.
        assert result.request_id == "req-recovery"

    # The replacement-mark diagnostics failure was logged with the key id.
    assert "diagnostics pool exhausted" in caplog.text
    assert rows["key2"].id in caplog.text
    # Both audit rows converge with the adapter outcomes (audit rows use the
    # independent SessionLocal, unaffected by the broken diagnostics session).
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [(item.outcome, item.error_code) for item in attempts] == [
        ("FAILED", "AUTHENTICATION"),
        ("SUCCEEDED", None),
    ]
    with caller_factory() as fresh:
        job = fresh.get(GenerationJob, rows["job"].id)
        assert job.status == "GENERATING"
        assert job.error_message is None


def test_replacement_retry_error_not_replaced_by_mark_diagnostics_failure(
    env, monkeypatch, caplog
):
    """On the retry-error path, a diagnostics failure inside
    ``_mark_key_outcome`` must not replace the re-raised ProviderAdapterError.

    Before the guard, the diagnostics RuntimeError propagated instead of the
    adapter's RATE_LIMIT, so the worker classified a bounded provider cooldown
    as a generic retryable WORKER_ERROR and re-dispatched the paid call.
    """

    caller_factory, rows = env

    class _FlakyAdapter:
        def __init__(self):
            self.calls = 0

        def generate_page(self, request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderAdapterError("AUTHENTICATION", "密钥无效", retryable=False)
            raise ProviderAdapterError("RATE_LIMIT", "服务繁忙", retryable=True)

    adapter = _FlakyAdapter()

    def _locked(db, key, *args, **kwargs):
        raise RuntimeError("diagnostics lock wait expired")

    monkeypatch.setattr(provider, "mark_key_failure", _locked)
    replacement_binding = _binding(rows, adapter, key=rows["key2"])
    monkeypatch.setattr(provider, "bind_adapter", lambda *a, **k: replacement_binding)

    with caller_factory() as db:
        db.info["job_id"] = rows["job"].id
        with caplog.at_level(logging.WARNING, logger=provider.__name__):
            with pytest.raises(ProviderAdapterError) as exc_info:
                provider._invoke_provider(db, _binding(rows, adapter), adapter.generate_page)
        assert adapter.calls == 2
        # The ADAPTER outcome — code, retryability, message — is re-raised,
        # not the diagnostics failure and not a reclassification.
        assert exc_info.value.code == "RATE_LIMIT"
        assert exc_info.value.retryable is True
        assert exc_info.value.user_message == "服务繁忙"

    # Both diagnostics failures (primary record + replacement mark) were
    # logged with their key ids, never raised.
    assert "diagnostics lock wait expired" in caplog.text
    assert rows["key2"].id in caplog.text
    # Audit keeps the adapter codes for both dispatches.
    attempts = _attempts_for_job(caller_factory, rows["job"].id)
    assert [(item.outcome, item.error_code) for item in attempts] == [
        ("FAILED", "AUTHENTICATION"),
        ("FAILED", "RATE_LIMIT"),
    ]

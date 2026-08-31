"""Model routing and paid-call invocation shared by the task handlers.

The legacy ``_adapter`` test seam stays owned by ``app.worker_tasks``; that
module installs the lookup below so patches of ``app.worker_tasks._adapter``
keep steering every handler's model binding.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.config import get_settings
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    AIModel,
    Asset,
    GenerationJob,
    JobAssetReference,
    Project,
    ProviderConnection,
    ProviderProfile,
)
from app.services.credential_crypto import mark_key_failure, mark_key_success
from app.services.model_router import (
    AdapterBinding,
    ResolvedModel,
    bind_adapter,
    get_catalog_model,
)
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers.execution import _ensure_job_not_cancelled
from app.services.worker_handlers.model_call_audit import (
    ModelCallAttemptMeta,
    begin_model_call_attempt,
    finalize_model_call_attempt,
)


def _uninstalled_legacy_adapter(_alias: str):
    """Default seam value before ``app.worker_tasks`` installs the live lookup."""

    return None


_legacy_adapter_lookup: Callable[[str], Any] = _uninstalled_legacy_adapter


def install_legacy_adapter_lookup(lookup: Callable[[str], Any]) -> None:
    """Bridge the module-global ``_adapter`` seam owned by ``app.worker_tasks``.

    The lookup is invoked per binding call and must resolve the attribute at
    call time so monkeypatching ``app.worker_tasks._adapter`` keeps working.
    """

    global _legacy_adapter_lookup
    _legacy_adapter_lookup = lookup


def _binding(
    db,
    *,
    operation: str,
    project_id: str,
    explicit_reference: str | None,
    task_kind: str,
) -> AdapterBinding:
    legacy_adapter = _legacy_adapter_lookup(explicit_reference or "auto")
    if legacy_adapter is not None:
        settings = get_settings()
        ensure_provider_presets(db, settings)
        model = get_catalog_model(db, explicit_reference or "")
        if model is None:
            model = db.scalar(
                select(AIModel).where(
                    AIModel.model_type == ("IMAGE" if operation.startswith("image_") else "TEXT")
                )
            )
        if model is None:
            raise ProviderAdapterError("MODEL_ROUTE_UNAVAILABLE", "测试模型目录不存在")
        connection = db.get(ProviderConnection, model.connection_id)
        provider = db.get(ProviderProfile, connection.provider_id) if connection else None
        if not connection or not provider:
            raise ProviderAdapterError("MODEL_ROUTE_UNAVAILABLE", "测试模型连接不存在")
        return AdapterBinding(
            resolved=ResolvedModel(model=model, connection=connection, provider=provider),
            adapter=legacy_adapter,
            selected_key=None,
        )
    try:
        return bind_adapter(
            db,
            get_settings(),
            operation=operation,
            explicit_reference=explicit_reference,
            project_id=project_id,
            task_kind=task_kind,
        )
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, str) else "模型路由配置无效"
        raise ProviderAdapterError("MODEL_ROUTE_UNAVAILABLE", detail) from error


def _audit_meta(db, binding: AdapterBinding) -> ModelCallAttemptMeta | None:
    """Scalar audit metadata for the current job, or None outside a job."""

    job_id = db.info.get("job_id")
    if not job_id:
        return None
    job = db.get(GenerationJob, job_id)
    if job is None:
        return None
    return ModelCallAttemptMeta(
        job_id=job.id,
        project_id=job.project_id,
        job_attempt=job.attempt_count,
        provider=(
            binding.resolved.provider.preset_key or binding.resolved.provider.name
        ),
        model_id=binding.resolved.model.provider_model_id,
        catalog_model_id=binding.resolved.model.id,
        connection_id=binding.resolved.connection.id,
        selected_key_id=(
            binding.selected_key.row.id if binding.selected_key else None
        ),
        route_reason=binding.resolved.route_reason,
        route_score=binding.resolved.route_score,
    )


def _begin_or_fail(meta: ModelCallAttemptMeta | None) -> str | None:
    """Begin an audit row, failing closed so the paid call never runs without one.

    The user-visible message is a fixed sanitized string: the raw driver error
    (which can embed SQL, local paths or connection details) is preserved only
    as the exception chain for logging, never as stored or returned text.
    """

    if meta is None:
        return None
    try:
        return begin_model_call_attempt(meta)
    except Exception as error:
        raise ProviderAdapterError(
            "AUDIT_PERSISTENCE_FAILED",
            "无法写入模型调用审计，已停止本次模型调用",
            retryable=False,
        ) from error


def _finalize_or_fail(attempt_id: str | None, **kwargs) -> None:
    """Finalize an audit row; persistence failure surfaces as non-retryable and
    never triggers another provider call. Same sanitized-message rule as
    ``_begin_or_fail``: the raw driver error stays in the exception chain only."""

    if attempt_id is None:
        return
    try:
        finalize_model_call_attempt(attempt_id, **kwargs)
    except Exception as error:
        raise ProviderAdapterError(
            "AUDIT_PERSISTENCE_FAILED",
            "无法更新模型调用审计，已保留诊断现场",
            retryable=False,
        ) from error


def _invoke_provider(db, binding: AdapterBinding, callback):
    job_id = db.info.get("job_id")
    if job_id:
        current = db.get(GenerationJob, job_id)
        if current is not None:
            _ensure_job_not_cancelled(db, current)
    attempt_id = _begin_or_fail(_audit_meta(db, binding))
    try:
        bind_context = getattr(binding.adapter, "bind_execution_context", None)
        if callable(bind_context):
            if not job_id or not attempt_id:
                raise ProviderAdapterError(
                    "AUDIT_PERSISTENCE_FAILED",
                    "CLI 图片调用必须绑定持久化任务与审计行",
                )
            bind_context(
                job_id=job_id,
                model_call_attempt_id=attempt_id,
                lease_owner=db.info.get("job_lease_owner"),
            )
        result = callback(binding.adapter)
    except ProviderAdapterError as error:
        _finalize_or_fail(
            attempt_id,
            outcome="FAILED",
            error_code=error.code,
            error_message=error.user_message,
        )
        if binding.selected_key:
            mark_key_failure(
                db,
                binding.selected_key.row,
                error.code,
                retry_after_seconds=error.retry_after_seconds,
            )
            if error.code in {"AUTHENTICATION", "PERMISSION", "RATE_LIMIT"}:
                try:
                    replacement = bind_adapter(
                        db,
                        get_settings(),
                        operation=binding.resolved.model.operations[0],
                        explicit_reference=binding.resolved.model.id,
                    )
                except HTTPException:
                    replacement = None
                if (
                    replacement
                    and replacement.selected_key
                    and replacement.selected_key.row.id != binding.selected_key.row.id
                ):
                    replacement_meta = _replacement_meta(db, binding, replacement)
                    replacement_id = _begin_or_fail(replacement_meta)
                    try:
                        result = callback(replacement.adapter)
                    except ProviderAdapterError as retry_error:
                        _finalize_or_fail(
                            replacement_id,
                            outcome="FAILED",
                            error_code=retry_error.code,
                            error_message=retry_error.user_message,
                        )
                        mark_key_failure(
                            db,
                            replacement.selected_key.row,
                            retry_error.code,
                            retry_after_seconds=retry_error.retry_after_seconds,
                        )
                        raise
                    _finalize_or_fail(
                        replacement_id,
                        outcome="SUCCEEDED",
                        model_id=getattr(result, "model_id", None),
                        request_id=getattr(result, "request_id", None),
                        usage=getattr(result, "usage", None),
                    )
                    mark_key_success(db, replacement.selected_key.row)
                    return result
        raise
    _finalize_or_fail(
        attempt_id,
        outcome="SUCCEEDED",
        model_id=getattr(result, "model_id", None),
        request_id=getattr(result, "request_id", None),
        usage=getattr(result, "usage", None),
    )
    if binding.selected_key:
        mark_key_success(db, binding.selected_key.row)
    return result


def _replacement_meta(
    db, binding: AdapterBinding, replacement: AdapterBinding
) -> ModelCallAttemptMeta | None:
    """Scalar metadata for a route-switch replacement dispatch (attempt 2+).

    Returns ``None`` outside a job context: the original pre-ledger route-switch
    behavior (two callback attempts, zero audit rows) must keep working there.
    """

    original = _audit_meta(db, binding)
    if original is None:
        return None
    return ModelCallAttemptMeta(
        job_id=original.job_id,
        project_id=original.project_id,
        job_attempt=original.job_attempt,
        provider=(
            replacement.resolved.provider.preset_key or replacement.resolved.provider.name
        ),
        model_id=replacement.resolved.model.provider_model_id,
        catalog_model_id=replacement.resolved.model.id,
        connection_id=replacement.resolved.connection.id,
        selected_key_id=(
            replacement.selected_key.row.id if replacement.selected_key else None
        ),
        route_reason=replacement.resolved.route_reason,
        route_score=replacement.resolved.route_score,
        route_switched=True,
    )


def _text_model_reference(job: GenerationJob, project: Project) -> str | None:
    if job.catalog_model_id:
        return job.catalog_model_id
    if job.model_alias:
        return job.model_alias
    return project.default_text_model_id or project.text_model_alias or "auto"


def _validate_reference_capacity(binding: AdapterBinding, count: int) -> None:
    configured = (binding.resolved.model.capabilities or {}).get("max_reference_images")
    if configured is not None and count > int(configured):
        raise ProviderAdapterError(
            "UNSUPPORTED_CAPABILITY",
            f"所选模型最多接收 {configured} 张参考图，本任务需要 {count} 张",
        )


def _lease_reference_assets(db, job: GenerationJob, asset_ids: list[str]) -> None:
    unique_ids = list(dict.fromkeys(asset_ids))
    active_ids = set(
        db.scalars(
            select(Asset.id).where(
                Asset.id.in_(unique_ids),
                Asset.project_id == job.project_id,
                Asset.deleted_at.is_(None),
            )
        )
    )
    if active_ids != set(unique_ids):
        raise RuntimeError("参考图已删除、失效或不属于当前项目，已停止模型调用")
    db.execute(delete(JobAssetReference).where(JobAssetReference.job_id == job.id))
    for asset_id in unique_ids:
        db.add(JobAssetReference(job_id=job.id, asset_id=asset_id))
    parameters = dict(job.request_parameters or {})
    parameters["reference_asset_ids"] = unique_ids
    job.request_parameters = parameters
    db.commit()


def _asset_path(asset: Asset) -> Path:
    settings = get_settings()
    root = settings.upload_root if asset.source == "USER_UPLOAD" else settings.storage_root
    path = (root / asset.storage_key).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError("素材路径越界")
    return path

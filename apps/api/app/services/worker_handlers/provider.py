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


def _invoke_provider(db, binding: AdapterBinding, callback):
    job_id = db.info.get("job_id")
    if job_id:
        current = db.get(GenerationJob, job_id)
        if current is not None:
            _ensure_job_not_cancelled(db, current)
    try:
        result = callback(binding.adapter)
    except ProviderAdapterError as error:
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
                    try:
                        result = callback(replacement.adapter)
                    except ProviderAdapterError as retry_error:
                        mark_key_failure(
                            db,
                            replacement.selected_key.row,
                            retry_error.code,
                            retry_after_seconds=retry_error.retry_after_seconds,
                        )
                        raise
                    mark_key_success(db, replacement.selected_key.row)
                    return result
        raise
    if binding.selected_key:
        mark_key_success(db, binding.selected_key.row)
    return result


def _text_model_reference(job: GenerationJob, project: Project) -> str | None:
    if job.catalog_model_id:
        return job.catalog_model_id
    if job.model_alias and job.model_alias != "text.fast":
        return job.model_alias
    return project.default_text_model_id or project.text_model_alias or job.model_alias


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

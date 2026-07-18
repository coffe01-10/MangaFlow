from __future__ import annotations

import io
from datetime import UTC, datetime
from statistics import median
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.states import JobStatus
from app.model_adapters.base import (
    ImageRequest,
    MultimodalRequest,
    ProviderAdapterError,
    StructuredRequest,
)
from app.models import (
    AIModel,
    GenerationJob,
    ModelProbe,
    ProviderConnection,
    ProviderProfile,
    RoutingPolicy,
)
from app.provider_schemas import (
    BalanceRead,
    ConnectionCreate,
    ConnectionTestRequest,
    ConnectionUpdate,
    ModelProbeRead,
    ProviderConnectionRead,
    ProviderCreate,
    ProviderKeyRead,
    ProviderKeyWrite,
    ProviderModelCreate,
    ProviderModelRead,
    ProviderModelUpdate,
    ProviderPresetRead,
    ProviderProfileRead,
    ProviderUpdate,
    RoutingPolicyRead,
    RoutingPolicyWrite,
)
from app.services.credential_crypto import mark_key_failure, mark_key_success
from app.services.model_router import bind_adapter
from app.services.provider_catalog import (
    add_connection,
    create_custom_provider,
    create_model,
    create_probe,
    delete_provider_key,
    discover_models,
    list_provider_views,
    read_balance,
    update_connection,
    update_model,
    update_provider,
    upsert_routing_policy,
    write_provider_key,
)
from app.services.provider_presets import ensure_provider_presets, preset_dicts

router = APIRouter(prefix="/providers")
routing_router = APIRouter(prefix="/routing-policies")


class _SmokeResult(BaseModel):
    ok: bool


def _profile_view(db: Session, provider_id: str) -> dict:
    view = next(
        (
            item
            for item in list_provider_views(db, get_settings())
            if item["id"] == provider_id
        ),
        None,
    )
    if view is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return view


@router.get("/presets", response_model=list[ProviderPresetRead])
def provider_presets() -> list[dict]:
    return preset_dicts()


@router.get("", response_model=list[ProviderProfileRead])
def providers(db: Session = Depends(get_db)) -> list[dict]:
    return list_provider_views(db, get_settings())


@router.post("", response_model=ProviderProfileRead, status_code=201)
def post_provider(payload: ProviderCreate, db: Session = Depends(get_db)) -> dict:
    profile = create_custom_provider(db, payload)
    return _profile_view(db, profile.id)


@router.patch("/{provider_id}", response_model=ProviderProfileRead)
def patch_provider(
    provider_id: str, payload: ProviderUpdate, db: Session = Depends(get_db)
) -> dict:
    profile = update_provider(db, provider_id, payload)
    return _profile_view(db, profile.id)


@router.delete("/{provider_id}", status_code=204)
def delete_provider(provider_id: str, db: Session = Depends(get_db)) -> Response:
    profile = db.get(ProviderProfile, provider_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if profile.built_in:
        raise HTTPException(status_code=409, detail="内置供应商只能停用，不能删除")
    referenced_job = db.scalar(
        select(GenerationJob.id)
        .join(AIModel, GenerationJob.catalog_model_id == AIModel.id)
        .join(ProviderConnection, AIModel.connection_id == ProviderConnection.id)
        .where(
            ProviderConnection.provider_id == provider_id,
            GenerationJob.status.not_in(
                [JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.NEEDS_REVIEW]
            ),
        )
        .limit(1)
    )
    if referenced_job:
        raise HTTPException(
            status_code=409,
            detail="供应商仍被执行中或可重试的生成任务引用，请先处理相关任务",
        )
    db.delete(profile)
    db.commit()
    return Response(status_code=204)


@router.post(
    "/{provider_id}/connections", response_model=ProviderConnectionRead, status_code=201
)
def post_connection(
    provider_id: str, payload: ConnectionCreate, db: Session = Depends(get_db)
) -> dict:
    connection = add_connection(db, provider_id, payload)
    profile = _profile_view(db, provider_id)
    return next(item for item in profile["connections"] if item["id"] == connection.id)


@router.patch("/connections/{connection_id}", response_model=ProviderConnectionRead)
def patch_connection(
    connection_id: str, payload: ConnectionUpdate, db: Session = Depends(get_db)
) -> dict:
    connection = update_connection(db, connection_id, payload)
    profile = _profile_view(db, connection.provider_id)
    return next(item for item in profile["connections"] if item["id"] == connection.id)


@router.put(
    "/connections/{connection_id}/keys", response_model=ProviderKeyRead, status_code=201
)
def put_key(
    connection_id: str, payload: ProviderKeyWrite, db: Session = Depends(get_db)
) -> ProviderKeyRead:
    return write_provider_key(db, get_settings(), connection_id, payload)


@router.delete("/connections/{connection_id}/keys/{key_id}", status_code=204)
def delete_key(
    connection_id: str, key_id: str, db: Session = Depends(get_db)
) -> Response:
    delete_provider_key(db, connection_id, key_id)
    return Response(status_code=204)


@router.post(
    "/connections/{connection_id}/discover", response_model=list[ProviderModelRead]
)
def post_discover(
    connection_id: str, db: Session = Depends(get_db)
) -> list[AIModel]:
    return discover_models(db, get_settings(), connection_id)


@router.get("/connections/{connection_id}/balance", response_model=BalanceRead)
def get_balance(connection_id: str, db: Session = Depends(get_db)) -> dict:
    return read_balance(db, get_settings(), connection_id)


@router.post(
    "/connections/{connection_id}/models",
    response_model=ProviderModelRead,
    status_code=201,
)
def post_model(
    connection_id: str, payload: ProviderModelCreate, db: Session = Depends(get_db)
) -> AIModel:
    return create_model(db, connection_id, payload)


@router.patch("/models/{model_id}", response_model=ProviderModelRead)
def patch_model(
    model_id: str, payload: ProviderModelUpdate, db: Session = Depends(get_db)
) -> AIModel:
    return update_model(db, model_id, payload)


@router.post(
    "/connections/{connection_id}/test", response_model=ModelProbeRead
)
def test_connection(
    connection_id: str,
    payload: ConnectionTestRequest,
    db: Session = Depends(get_db),
) -> ModelProbe:
    connection = db.get(ProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="供应商连接不存在")
    if payload.test_type == "CREDENTIALS":
        started = perf_counter()
        try:
            models = discover_models(db, get_settings(), connection_id)
        except HTTPException as error:
            db.refresh(connection)
            return create_probe(
                db,
                connection_id=connection_id,
                model_id=None,
                probe_type="CREDENTIALS",
                status="FAILED",
                latency_ms=round((perf_counter() - started) * 1000),
                error_code=connection.error_code or "CONNECTION_FAILED",
                message=str(error.detail),
            )
        return create_probe(
            db,
            connection_id=connection_id,
            model_id=None,
            probe_type="CREDENTIALS",
            status="PASSED",
            latency_ms=round((perf_counter() - started) * 1000),
            metrics={"discovered_models": len(models)},
            message="鉴权与模型列表测试通过",
        )
    model = db.get(AIModel, payload.model_id)
    if model is None or model.connection_id != connection_id:
        raise HTTPException(status_code=404, detail="测试模型不存在")
    image_probe_operation = (
        "image_generate" if "image_generate" in (model.operations or []) else "image_edit"
    )
    operation = {
        "TEXT": "structured_text",
        "VISION": "multimodal_analysis",
        "IMAGE": image_probe_operation,
        "BENCHMARK": image_probe_operation if model.model_type == "IMAGE" else "structured_text",
    }[payload.test_type]
    tested_operations = {operation}
    latencies: list[int] = []
    binding = None
    try:
        for _ in range(payload.runs):
            binding = bind_adapter(
                db,
                get_settings(),
                operation=operation,
                explicit_reference=model.id,
            )
            started = perf_counter()
            if operation == "structured_text":
                binding.adapter.generate_structured(
                    StructuredRequest(
                        prompt='只返回 {"ok": true}',
                        temperature=0,
                        metadata={"max_output_tokens": 64},
                    ),
                    _SmokeResult,
                )
            elif operation == "multimodal_analysis":
                image_buffer = io.BytesIO()
                Image.new("RGB", (2, 2), "white").save(image_buffer, format="PNG")
                binding.adapter.analyze_multimodal(
                    MultimodalRequest(
                        prompt='图片是白色，只返回 {"ok": true}',
                        images=(image_buffer.getvalue(),),
                        mime_types=("image/png",),
                        temperature=0,
                    ),
                    _SmokeResult,
                )
            else:
                image_buffer = io.BytesIO()
                Image.new("RGB", (2, 2), "white").save(image_buffer, format="PNG")
                if "image_generate" in (model.operations or []):
                    binding.adapter.generate_asset(
                        ImageRequest(
                            prompt="一个简单黑色圆点，白色背景，无文字",
                            resolution="1K",
                            aspect_ratio="1:1",
                        )
                    )
                    tested_operations.add("image_generate")
                if "image_edit" in (model.operations or []):
                    binding.adapter.edit_region(
                        ImageRequest(
                            prompt="保持白色背景，在中心添加一个黑色圆点，无文字",
                            resolution="1K",
                            aspect_ratio="1:1",
                            reference_images=(image_buffer.getvalue(),),
                            reference_mime_types=("image/png",),
                        )
                    )
                    tested_operations.add("image_edit")
            if binding.selected_key:
                mark_key_success(db, binding.selected_key.row)
            latencies.append(round((perf_counter() - started) * 1000))
        now = datetime.now(UTC)
        model.last_verified_at = now
        connection.last_success_at = now
        connection.last_checked_at = now
        model.median_latency_ms = round(median(latencies))
        model.success_rate = (
            1.0
            if model.success_rate is None
            else round(model.success_rate * 0.8 + 0.2, 4)
        )
        capabilities = dict(model.capabilities or {})
        verified_operations = set(capabilities.get("verified_operations") or [])
        verified_operations.update(tested_operations)
        capabilities["verified_operations"] = sorted(verified_operations)
        model.capabilities = capabilities
        model.confidence = (
            "VERIFIED"
            if set(model.operations or []).issubset(verified_operations)
            else "PARTIAL"
        )
        connection.health_state = "HEALTHY"
        connection.latency_ms = model.median_latency_ms
        connection.error_code = None
        connection.message = "模型能力测试通过"
        db.commit()
        return create_probe(
            db,
            connection_id=connection_id,
            model_id=model.id,
            probe_type=payload.test_type,
            status="PASSED",
            latency_ms=model.median_latency_ms,
            metrics={
                "min_ms": min(latencies),
                "median_ms": median(latencies),
                "max_ms": max(latencies),
            },
            message="模型测试通过",
        )
    except ProviderAdapterError as error:
        if binding and binding.selected_key:
            mark_key_failure(
                db,
                binding.selected_key.row,
                error.code,
                retry_after_seconds=error.retry_after_seconds,
            )
        model.success_rate = (
            0.0 if model.success_rate is None else round(model.success_rate * 0.8, 4)
        )
        connection.health_state = "DEGRADED"
        connection.error_code = error.code
        connection.message = error.user_message
        db.commit()
        return create_probe(
            db,
            connection_id=connection_id,
            model_id=model.id,
            probe_type=payload.test_type,
            status="FAILED",
            latency_ms=None,
            error_code=error.code,
            message=error.user_message,
        )


@router.get("/probes", response_model=list[ModelProbeRead])
def probes(
    connection_id: str | None = None,
    model_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[ModelProbe]:
    query = select(ModelProbe)
    if connection_id:
        query = query.where(ModelProbe.connection_id == connection_id)
    if model_id:
        query = query.where(ModelProbe.model_id == model_id)
    return list(db.scalars(query.order_by(ModelProbe.created_at.desc()).limit(100)))


@routing_router.get("", response_model=list[RoutingPolicyRead])
def routing_policies(db: Session = Depends(get_db)) -> list[RoutingPolicy]:
    ensure_provider_presets(db, get_settings())
    return list(db.scalars(select(RoutingPolicy).order_by(RoutingPolicy.task_kind)))


@routing_router.put("", response_model=RoutingPolicyRead)
def put_routing_policy(
    payload: RoutingPolicyWrite, db: Session = Depends(get_db)
) -> RoutingPolicy:
    return upsert_routing_policy(db, payload)

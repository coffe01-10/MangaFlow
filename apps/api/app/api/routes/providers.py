from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.states import JobStatus
from app.models import (
    AIModel,
    GenerationJob,
    ModelPricingVersion,
    ModelProbe,
    ProviderConnection,
    ProviderProfile,
    RoutingPolicy,
)
from app.provider_schemas import (
    BalanceRead,
    ConnectionCreate,
    ConnectionHealthRead,
    ConnectionTestRequest,
    ConnectionUpdate,
    ConnectionVerifyRequest,
    ConnectionVerifyResult,
    ModelPricingVersionCreate,
    ModelPricingVersionRead,
    ModelProbeRead,
    ModelVisibilityBatchResult,
    ModelVisibilityBatchUpdate,
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
from app.services.connection_verifier import (
    get_connection_health,
    verify_connection,
)
from app.services.model_costs import (
    create_pricing_version,
    list_pricing_versions,
)
from app.services.provider_catalog import (
    add_connection,
    create_custom_provider,
    create_model,
    delete_provider_key,
    discover_models,
    list_models_for_connection,
    list_provider_views,
    read_balance,
    set_model_visibility_bulk,
    update_connection,
    update_model,
    update_provider,
    upsert_routing_policy,
    write_provider_key,
)
from app.services.provider_presets import ensure_provider_presets, preset_dicts

router = APIRouter(prefix="/providers")
routing_router = APIRouter(prefix="/routing-policies")


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


@router.get(
    "/pricing-versions", response_model=list[ModelPricingVersionRead]
)
def get_pricing_versions(
    provider: str | None = Query(default=None, max_length=120),
    model_id: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
) -> list[ModelPricingVersion]:
    return list_pricing_versions(db, provider=provider, model_id=model_id)


@router.post(
    "/pricing-versions", response_model=ModelPricingVersionRead, status_code=201
)
def post_pricing_version(
    payload: ModelPricingVersionCreate,
    db: Session = Depends(get_db),
) -> ModelPricingVersion:
    return create_pricing_version(db, payload)


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


@router.get(
    "/connections/{connection_id}/health",
    response_model=ConnectionHealthRead,
)
def connection_health(
    connection_id: str, db: Session = Depends(get_db)
) -> dict:
    return get_connection_health(db, get_settings(), connection_id)


@router.post(
    "/connections/{connection_id}/verify",
    response_model=ConnectionVerifyResult,
)
def connection_verify(
    connection_id: str,
    payload: ConnectionVerifyRequest,
    db: Session = Depends(get_db),
) -> dict:
    health, probe = verify_connection(db, get_settings(), connection_id, payload)
    return {"health": health, "probe": probe}


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


@router.get(
    "/connections/{connection_id}/models",
    response_model=list[ProviderModelRead],
)
def get_connection_models(
    connection_id: str, db: Session = Depends(get_db)
) -> list[AIModel]:
    return list_models_for_connection(db, connection_id)


@router.patch(
    "/models/visibility",
    response_model=ModelVisibilityBatchResult,
)
def patch_model_visibility(
    payload: ModelVisibilityBatchUpdate, db: Session = Depends(get_db)
) -> dict[str, list[dict[str, object]]]:
    return set_model_visibility_bulk(db, payload)


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
    _, probe = verify_connection(
        db,
        get_settings(),
        connection_id,
        ConnectionVerifyRequest(
            level=(
                "CREDENTIALS"
                if payload.test_type == "CREDENTIALS"
                else "MODEL_SMOKE"
            ),
            catalog_model_id=payload.model_id,
            operation={
                "TEXT": "structured_text",
                "VISION": "multimodal_analysis",
            }.get(payload.test_type),
            acknowledge_cost=payload.acknowledge_cost,
            runs=payload.runs,
        ),
    )
    return probe


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
    ensure_provider_presets(db, get_settings(), auto_commit=True)
    return list(db.scalars(select(RoutingPolicy).order_by(RoutingPolicy.task_kind)))


@routing_router.put("", response_model=RoutingPolicyRead)
def put_routing_policy(
    payload: RoutingPolicyWrite, db: Session = Depends(get_db)
) -> RoutingPolicy:
    return upsert_routing_policy(db, payload)

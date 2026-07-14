from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.schemas import ModelCapabilityRead, VertexStatusRead
from app.services.model_registry import build_registry

router = APIRouter()


@router.get("", response_model=list[ModelCapabilityRead])
def list_models() -> list[dict]:
    registry = build_registry(get_settings())
    return [capability.to_dict() for capability in registry.values()]


@router.get("/vertex/status", response_model=VertexStatusRead)
def vertex_status() -> VertexStatusRead:
    settings = get_settings()
    present = bool(
        settings.google_application_credentials
        and settings.google_application_credentials.is_file()
    )
    return VertexStatusRead(
        configured=settings.vertex_configured,
        credential_file_present=present,
        location=settings.google_cloud_location,
        text_model=settings.vertex_text_model,
        image_models=[
            settings.vertex_image_model_nano_banana_2,
            settings.vertex_image_model_nano_banana_pro,
        ],
        verification="not_run",
        message="服务端凭据已配置，尚未执行联网验证"
        if settings.vertex_configured
        else "请在服务端配置 Vertex AI 服务账号",
    )


@router.post("/vertex/verify", response_model=VertexStatusRead)
def verify_vertex_credentials() -> VertexStatusRead:
    settings = get_settings()
    if not settings.vertex_configured:
        raise HTTPException(status_code=400, detail="Vertex AI 服务账号尚未配置")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            str(settings.google_application_credentials),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        credentials.refresh(Request())
    except Exception as error:
        raise HTTPException(status_code=502, detail="Vertex AI 凭据验证失败") from error

    return VertexStatusRead(
        configured=True,
        credential_file_present=True,
        location=settings.google_cloud_location,
        text_model=settings.vertex_text_model,
        image_models=[
            settings.vertex_image_model_nano_banana_2,
            settings.vertex_image_model_nano_banana_pro,
        ],
        verification="verified",
        message="Vertex AI 服务账号验证成功",
    )

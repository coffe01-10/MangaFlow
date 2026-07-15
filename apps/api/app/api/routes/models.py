from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas import ModelCapabilityRead
from app.services.model_registry import build_registry
from app.services.vertex_health import get_or_create_health, health_read, verify_vertex
from app.settings_schemas import VertexHealthRead, VertexVerifyRequest

router = APIRouter()


@router.get("", response_model=list[ModelCapabilityRead])
def list_models() -> list[dict]:
    registry = build_registry(get_settings())
    return [capability.to_dict() for capability in registry.values()]


# Compatibility aliases retained for one release. Both use the persisted status
# and never perform a model call on GET.
@router.get("/vertex/status", response_model=VertexHealthRead)
def vertex_status(db: Session = Depends(get_db)) -> VertexHealthRead:
    settings = get_settings()
    return health_read(get_or_create_health(db, settings), settings)


@router.post("/vertex/verify", response_model=VertexHealthRead)
def verify_vertex_credentials(
    payload: VertexVerifyRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> VertexHealthRead:
    return verify_vertex(db, get_settings(), payload or VertexVerifyRequest())

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AIModel, ProviderConnection, ProviderProfile
from app.schemas import ModelCapabilityRead
from app.services.model_availability import (
    catalog_model_is_available,
    connection_ids_with_usable_keys,
)
from app.services.provider_presets import ensure_provider_presets

router = APIRouter()


@router.get("", response_model=list[ModelCapabilityRead])
def list_models(db: Session = Depends(get_db)) -> list[dict]:
    settings = get_settings()
    ensure_provider_presets(db, settings, auto_commit=True)
    usable_key_connections = connection_ids_with_usable_keys(db)
    credentials_writable = settings.provider_credentials_writable
    rows = (
        db.query(AIModel, ProviderConnection, ProviderProfile)
        .join(ProviderConnection, AIModel.connection_id == ProviderConnection.id)
        .join(ProviderProfile, ProviderConnection.provider_id == ProviderProfile.id)
        .order_by(AIModel.model_type, AIModel.priority.desc(), AIModel.display_name)
        .all()
    )
    catalog = []
    for model, connection, profile in rows:
        available = catalog_model_is_available(
            model,
            connection,
            profile,
            credentials_writable=credentials_writable,
            has_usable_key=connection.id in usable_key_connections,
            environment_credentials_ready=settings.vertex_configured,
        )
        catalog.append(
            {
                "catalog_id": model.id,
                "connection_id": connection.id,
                "provider": profile.name,
                "protocol": connection.protocol,
                "model_id": model.provider_model_id,
                "logical_alias": model.legacy_alias or model.id,
                "display_name": model.display_name,
                "model_type": model.model_type,
                "input_modalities": model.input_modalities or [],
                "output_modalities": model.output_modalities or [],
                "operations": model.operations or [],
                "resolutions": (model.capabilities or {}).get("resolutions") or [],
                "preview_resolutions": (model.capabilities or {}).get("preview_resolutions")
                or [],
                "max_reference_images": int(
                    (model.capabilities or {}).get("max_reference_images") or 0
                ),
                "regions": (model.capabilities or {}).get("regions") or ["global"],
                "confidence": model.confidence,
                "enabled": available,
                "display_enabled": model.display_enabled,
                "auto_eligible": (
                    available
                    and model.confidence == "VERIFIED"
                    and connection.health_state == "HEALTHY"
                ),
                "priority": model.priority,
            }
        )
    return catalog

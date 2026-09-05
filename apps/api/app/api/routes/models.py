from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AIModel, ProviderConnection, ProviderProfile
from app.schemas import ModelCapabilityRead
from app.services.credential_source import environment_credentials_ready
from app.services.model_availability import (
    catalog_model_is_available,
    connection_ids_with_usable_keys,
)
from app.services.model_capabilities import (
    REGION_CAPABILITY_KEYS,
    capability_reference_limit,
    region_capability_enabled,
    region_capability_source,
)
from app.services.provider_presets import ensure_provider_presets

router = APIRouter()


def capability_string_list(
    capabilities: dict | None, key: str, *, default: list[str] | None = None
) -> list[str]:
    """Serialize one list-shaped capability without trusting its stored shape.

    A poisoned value (e.g. ``resolutions`` stored as a bare string) must not
    fail response validation for the whole catalog: a bare string reads as a
    single-element list, anything else non-list falls back to the default.
    """

    value = (capabilities or {}).get(key)
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return list(default or [])


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
            environment_credentials_ready=environment_credentials_ready(
                settings, connection.protocol
            ),
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
                "resolutions": capability_string_list(
                    model.capabilities, "resolutions"
                ),
                "preview_resolutions": capability_string_list(
                    model.capabilities, "preview_resolutions"
                ),
                "max_reference_images": capability_reference_limit(model.capabilities)
                or 0,
                "regions": capability_string_list(
                    model.capabilities, "regions", default=["global"]
                ),
                # V02-44B frozen region-edit bits (matrix §7.2): fail-closed,
                # absent/UNKNOWN never serializes as true; every bit carries
                # readable provenance.
                **{
                    key: region_capability_enabled(model.capabilities, key)
                    for key in REGION_CAPABILITY_KEYS
                },
                "region_capability_sources": {
                    key: region_capability_source(model.capabilities, key)
                    for key in REGION_CAPABILITY_KEYS
                },
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

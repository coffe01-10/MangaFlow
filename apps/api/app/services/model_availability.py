from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile
from app.services.credential_source import (
    ENV_SERVICE_ACCOUNT,
    connection_credential_source,
)


def catalog_model_is_available(
    model: AIModel,
    connection: ProviderConnection,
    profile: ProviderProfile,
    *,
    credentials_writable: bool,
    has_usable_key: bool,
    environment_credentials_ready: bool,
) -> bool:
    """Return whether a catalog model should be treated as enabled.

    Matches `/models` `enabled`: the model, connection, and provider must be
    enabled. Environment-account protocols require their runtime credentials
    to be ready. Connection-key protocols require writable encrypted storage
    and at least one enabled key that is not in cooldown.
    """

    if not (model.enabled and connection.enabled and profile.enabled):
        return False
    if connection_credential_source(connection) == ENV_SERVICE_ACCOUNT:
        return environment_credentials_ready
    return credentials_writable and has_usable_key


def connection_ids_with_usable_keys(db: Session) -> set[str]:
    """Return connections that currently have an enabled, non-cooled key."""

    now = datetime.now(UTC)
    return set(
        db.scalars(
            select(ProviderKey.connection_id).where(
                ProviderKey.enabled.is_(True),
                or_(
                    ProviderKey.cooldown_until.is_(None),
                    ProviderKey.cooldown_until <= now,
                ),
            )
        )
    )


def count_available_catalog_models(db: Session, settings: Settings) -> int:
    """Count catalog models using the same availability rule as `/models`."""

    usable_key_connections = connection_ids_with_usable_keys(db)
    credentials_writable = settings.provider_credentials_writable
    rows = db.execute(
        select(AIModel, ProviderConnection, ProviderProfile)
        .join(ProviderConnection, AIModel.connection_id == ProviderConnection.id)
        .join(ProviderProfile, ProviderConnection.provider_id == ProviderProfile.id)
    ).all()
    return sum(
        1
        for model, connection, profile in rows
        if catalog_model_is_available(
            model,
            connection,
            profile,
            credentials_writable=credentials_writable,
            has_usable_key=connection.id in usable_key_connections,
            environment_credentials_ready=settings.vertex_configured,
        )
    )

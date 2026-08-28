from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile

VERTEX_NATIVE_PROTOCOL = "VERTEX_NATIVE"


def catalog_model_is_available(
    model: AIModel,
    connection: ProviderConnection,
    profile: ProviderProfile,
    *,
    credentials_writable: bool,
    has_usable_key: bool,
) -> bool:
    """Return whether a catalog model should be treated as enabled.

    Matches `/models` `enabled`: the model, connection, and provider must be
    enabled. Compatible protocols also need writable credentials and at least
    one enabled key that is not in cooldown. Native Vertex keeps that branch
    and does not require keys here.
    """

    if not (model.enabled and connection.enabled and profile.enabled):
        return False
    if connection.protocol == VERTEX_NATIVE_PROTOCOL:
        return True
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
        )
    )

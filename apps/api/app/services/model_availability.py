from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIModel, ProviderConnection, ProviderKey, ProviderProfile
from app.services.credential_source import (
    CLI_SESSION,
    ENV_SERVICE_ACCOUNT,
    connection_credential_source,
    environment_credentials_ready,
)
from app.services.model_sunsets import (
    LIFECYCLE_ACTIVE,
    model_lifecycle_state,
)

# Availability phases (P0-2): "catalog" answers "is this row usable/managed"
# for catalog and readiness surfaces — DEPRECATED/EOL rows still count so the
# user sees "我有 N 个已退役模型" rather than a quietly shrinking number.
# "dispatch" answers "may this row accept a new-artifact dispatch" and refuses
# anything not ACTIVE. The AUTO router applies the same predicate at the
# query level (model_router.resolve_model).
AvailabilityPhase = Literal["catalog", "dispatch"]


def catalog_model_is_available(
    model: AIModel,
    connection: ProviderConnection,
    profile: ProviderProfile,
    *,
    credentials_writable: bool,
    has_usable_key: bool,
    environment_credentials_ready: bool,
    phase: AvailabilityPhase = "catalog",
) -> bool:
    """Return whether a catalog model should be treated as enabled.

    Matches `/models` `enabled`: the model, connection, and provider must be
    enabled. Environment-account protocols require their runtime credentials
    to be ready. Connection-key protocols require writable encrypted storage
    and at least one enabled key that is not in cooldown.

    ``phase="catalog"`` (default, used by GET /models and page readiness)
    ignores lifecycle: a retired row is still "available" in the sense of
    being readable and managed. ``phase="dispatch"`` additionally requires
    ``lifecycle == "ACTIVE"``, so DEPRECATED/EOL rows are excluded from new
    work without hiding them from the catalog.
    """

    if not (model.enabled and connection.enabled and profile.enabled):
        return False
    if phase == "dispatch" and model_lifecycle_state(model) != LIFECYCLE_ACTIVE:
        return False
    if connection_credential_source(connection) == ENV_SERVICE_ACCOUNT:
        return environment_credentials_ready
    if connection_credential_source(connection) == CLI_SESSION:
        return connection.health_state == "AVAILABLE"
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
            environment_credentials_ready=environment_credentials_ready(
                settings, connection.protocol
            ),
        )
    )

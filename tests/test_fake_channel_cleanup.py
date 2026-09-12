"""Regression tests for the fake channel's seeded-row cleanup (#443).

One opt-in ``--fake-channel`` run seeds a profile/connection/key/models
into the session's DATABASE_URL; without a removal path the rows persist
into later production sessions pointing at the intentionally unreachable
base URL. ``fake_channel.remove()`` must delete exactly those rows and
never touch an unrelated provider.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

SIDECAR_ROOT = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "sidecar"
if str(SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIDECAR_ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

import fake_channel  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import (  # noqa: E402
    AIModel,
    ProviderConnection,
    ProviderKey,
    ProviderProfile,
)


def _channel_factory(db_session):
    """Session factory for fake_channel's module-global SessionLocal."""

    return sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )


def _plant_unrelated_provider(db) -> None:
    profile = ProviderProfile(name="Real Provider", enabled=True)
    db.add(profile)
    db.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="real-connection",
        protocol="COMPATIBLE",
        base_url="http://127.0.0.1:1/real",
        enabled=True,
    )
    db.add(connection)
    db.flush()
    db.add(
        AIModel(
            connection_id=connection.id,
            provider_model_id="real-model",
            display_name="Real Model",
            model_type="TEXT",
        )
    )
    db.commit()


def test_remove_deletes_seeded_rows_and_spares_unrelated(db_session, monkeypatch):
    monkeypatch.setattr(fake_channel, "SessionLocal", _channel_factory(db_session))
    # encrypt_secret must not depend on (or create) a local master-key file.
    monkeypatch.setattr(
        get_settings(),
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    )

    fake_channel._seed_catalog()
    with _channel_factory(db_session)() as db:
        _plant_unrelated_provider(db)
        assert db.query(ProviderProfile).count() == 2
        assert db.query(AIModel).count() == 3  # 2 seeded + 1 unrelated

    removed = fake_channel.remove()
    assert removed == {"profiles": 1, "connections": 1, "keys": 1, "models": 2}, removed

    with _channel_factory(db_session)() as db:
        assert (
            db.query(ProviderProfile)
            .filter_by(name=fake_channel.PROFILE_NAME)
            .one_or_none()
            is None
        )
        # The unrelated provider survives whole: profile, connection, model.
        survivors = db.query(ProviderProfile).all()
        assert [profile.name for profile in survivors] == ["Real Provider"]
        assert db.query(ProviderConnection).count() == 1
        assert db.query(AIModel).count() == 1
        assert db.query(ProviderKey).count() == 0


def test_remove_is_idempotent_without_seeded_rows(db_session, monkeypatch):
    monkeypatch.setattr(fake_channel, "SessionLocal", _channel_factory(db_session))
    with _channel_factory(db_session)() as db:
        _plant_unrelated_provider(db)

    assert fake_channel.remove() == {
        "profiles": 0,
        "connections": 0,
        "keys": 0,
        "models": 0,
    }
    with _channel_factory(db_session)() as db:
        assert db.query(ProviderProfile).count() == 1


def test_seed_catalog_is_idempotent(db_session, monkeypatch):
    """Re-running the seeding on an already-seeded DB adds nothing (#443's
    residue grows once, not per run)."""

    monkeypatch.setattr(fake_channel, "SessionLocal", _channel_factory(db_session))
    monkeypatch.setattr(
        get_settings(),
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    )

    fake_channel._seed_catalog()
    fake_channel._seed_catalog()
    with _channel_factory(db_session)() as db:
        assert (
            db.query(ProviderProfile)
            .filter_by(name=fake_channel.PROFILE_NAME)
            .count()
            == 1
        )
        assert db.query(AIModel).count() == 2

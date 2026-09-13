"""Regression: explicit nulls on required PATCH fields return 422, not 500.

PATCH handlers applied ``exclude_unset`` bodies straight onto the ORM, so an
explicit ``{"field": null}`` for a NOT NULL column surfaced as
AttributeError (``None.strip()``), TypeError, or a raw IntegrityError 500.
A shared guard now inspects the mapped column nullability and rejects the
offending fields with 422 while still allowing nulls that clear genuinely
nullable columns.

The provider tests below cover issue #664: ``update_connection`` and
``update_model`` applied explicit nulls straight onto NOT NULL columns
(IntegrityError 500), while ``update_provider`` silently dropped nulls and
still bumped the optimistic-lock version, burning a token on a no-op.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.helpers import reject_required_nulls
from app.database import get_db
from app.main import app
from app.models import AIModel, Project, ProviderConnection, ProviderProfile


def test_guard_flags_only_non_nullable_columns():
    reject_required_nulls(Project, {"description": None})


def test_guard_raises_listing_offenders():
    with pytest.raises(Exception) as exc_info:
        reject_required_nulls(Project, {"name": None})
    detail = getattr(exc_info.value, "detail", "")
    assert "name" in str(detail)


def test_project_patch_rejects_null_name(client, db_session):
    from app.models import Project

    project = Project(name="null-guard")
    db_session.add(project)
    db_session.commit()

    response = client.patch(
        f"/api/v1/projects/{project.id}",
        json={"version": project.version, "name": None},
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]

    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "null-guard"


def test_character_patch_rejects_null_primary_name(client, db_session):
    from app.models import Character

    project = Project(name="null-guard-char")
    db_session.add(project)
    db_session.flush()
    character = Character(
        project_id=project.id,
        primary_name="张三",
        aliases=[],
        aliases_normalized=[],
        canonical_description="主角",
    )
    db_session.add(character)
    db_session.commit()

    response = client.patch(
        f"/api/v1/characters/{character.id}",
        json={"version": character.version, "primary_name": None},
    )
    assert response.status_code == 422


def test_nullable_project_field_still_clears(client, db_session):
    project = Project(name="null-clear", text_model_alias="image.fast")
    db_session.add(project)
    db_session.commit()

    response = client.patch(
        f"/api/v1/projects/{project.id}",
        json={"version": project.version, "text_model_alias": None},
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(Project, project.id).text_model_alias is None


def _raw_patch(db_session, path, json):
    """PATCH with unhandled exceptions rendered as HTTP 500 responses.

    The shared ``client`` fixture re-raises server errors, which would turn a
    missing guard into a raised IntegrityError instead of the 500 the API
    actually returns. Going through a no-raise client keeps these tests
    pinned on the user-visible 422-versus-500 distinction (#664).
    """

    def override_db():
        yield db_session

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, raise_server_exceptions=False) as raw_client:
            return raw_client.patch(path, json=json)
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)


def _seed_provider_fixtures(db_session):
    profile = ProviderProfile(name="null-guard-provider")
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        protocol="OPENAI",
        base_url="https://null-guard.example.com",
    )
    db_session.add(connection)
    db_session.flush()
    model = AIModel(
        connection_id=connection.id,
        provider_model_id="null-guard-model",
        display_name="Null Guard Model",
        operations=["structured_text"],
        source="MANUAL",
        confidence="MANUAL",
    )
    db_session.add(model)
    db_session.commit()
    return profile, connection, model


def test_connection_patch_rejects_null_name(client, db_session):
    _, connection, _ = _seed_provider_fixtures(db_session)

    response = _raw_patch(
        db_session,
        f"/api/v1/providers/connections/{connection.id}",
        json={"version": connection.version, "name": None},
    )
    assert response.status_code == 422, response.text
    assert "name" in response.json()["detail"]

    db_session.expire_all()
    assert db_session.get(ProviderConnection, connection.id).name == "默认连接"


def test_connection_patch_rejects_null_json_columns(client, db_session):
    # The JSON columns carry ``default=dict`` and are NOT NULL. An explicit
    # null used to skip the field validators and hit the column directly:
    # on SQLite the JSON serializer stores the literal ``null`` document
    # (silently corrupting the row; the list view masks it back to ``{}``
    # via ``or {}``), while PostgreSQL would reject SQL NULL with a raw
    # IntegrityError 500 (#664).
    _, connection, _ = _seed_provider_fixtures(db_session)

    response = _raw_patch(
        db_session,
        f"/api/v1/providers/connections/{connection.id}",
        json={"version": connection.version, "extra_headers": None},
    )
    assert response.status_code == 422, response.text
    assert "extra_headers" in response.json()["detail"]

    db_session.expire_all()
    assert db_session.get(ProviderConnection, connection.id).extra_headers == {}


def test_model_patch_rejects_null_display_name(client, db_session):
    _, _, model = _seed_provider_fixtures(db_session)

    response = _raw_patch(
        db_session,
        f"/api/v1/providers/models/{model.id}",
        json={"version": model.version, "display_name": None},
    )
    assert response.status_code == 422, response.text
    assert "display_name" in response.json()["detail"]

    db_session.expire_all()
    assert db_session.get(AIModel, model.id).display_name == "Null Guard Model"


def test_provider_patch_rejects_null_name_without_consuming_version(client, db_session):
    profile, _, _ = _seed_provider_fixtures(db_session)

    response = _raw_patch(
        db_session,
        f"/api/v1/providers/{profile.id}",
        json={"version": profile.version, "name": None},
    )
    assert response.status_code == 422, response.text
    assert "name" in response.json()["detail"]

    db_session.expire_all()
    assert db_session.get(ProviderProfile, profile.id).name == "null-guard-provider"
    # A rejected PATCH must not burn the optimistic-lock token: the same
    # version still succeeds afterwards. The old None-filtering path
    # returned 200 and silently consumed the token on a no-op (#664).
    accepted = client.patch(
        f"/api/v1/providers/{profile.id}",
        json={"version": profile.version, "name": "重命名供应商"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["name"] == "重命名供应商"


def test_guard_accepts_null_for_nullable_connection_columns():
    # ConnectionUpdate exposes no genuinely nullable column (its JSON fields
    # are NOT NULL), so an endpoint-level "null clears a nullable field"
    # case cannot be constructed for providers. The guard itself must keep
    # passing nulls for ProviderConnection's real nullable columns so any
    # future schema addition stays clearable.
    reject_required_nulls(ProviderConnection, {"error_code": None})

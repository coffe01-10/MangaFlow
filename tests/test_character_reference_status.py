"""Character status recompute regressions for issue #632.

Removing a character reference must recompute ``character.status`` with the
same shape as ``retract_asset_reference`` (asset_generation.py) on both
teardown paths:

- ``DELETE /character-references/{id}`` (unbind_reference);
- ``DELETE /assets/{id}`` soft delete, whose ``_detach_reference_asset``
  already recomputed outfit/style/scene assets but left the character alone.

A character left without any live (non-tombstoned) reference must drop to
NEEDS_CONFIRMATION with a version bump; a surviving live reference keeps
CANONICAL. Before the fix, both paths left a reference-less character
CANONICAL, so the confirmation guidance never came back.
"""

from datetime import UTC, datetime

from app.models import Asset, CharacterReference


def _orm(db, obj):
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def _project(client, name: str) -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _character(client, project_id: str, name: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/characters",
        json={"primary_name": name, "aliases": []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _reference_asset(db, project_id: str, digest: str, *, tombstoned=False) -> Asset:
    return _orm(
        db,
        Asset(
            project_id=project_id,
            kind="CHARACTER_REFERENCE",
            original_name=f"{digest[:6]}.png",
            storage_key=f"uploads/{project_id}/{digest[:6]}.png",
            mime_type="image/png",
            byte_size=8,
            sha256=digest,
            source="USER_UPLOAD",
            deleted_at=datetime.now(UTC) if tombstoned else None,
        ),
    )


def _bind(client, character_id: str, asset_id: str) -> dict:
    response = client.post(
        f"/api/v1/characters/{character_id}/references",
        json={"asset_id": asset_id, "is_canonical": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _orm_bind(db, character_id: str, asset_id: str) -> None:
    """Seed a binding the API refuses: one that points at a tombstoned asset."""
    _orm(
        db,
        CharacterReference(
            character_id=character_id,
            asset_id=asset_id,
            angle="front",
            is_canonical=False,
        ),
    )


def _promote_to_canonical(client, character: dict) -> dict:
    """Promote through the real PATCH path: a live reference yields CANONICAL."""
    response = client.patch(
        f"/api/v1/characters/{character['id']}",
        json={"version": character["version"]},
    )
    assert response.status_code == 200, response.text
    promoted = response.json()
    assert promoted["status"] == "CANONICAL", promoted
    return promoted


def _character_state(client, project_id: str, character_id: str) -> dict:
    characters = client.get(f"/api/v1/projects/{project_id}/characters").json()
    return next(item for item in characters if item["id"] == character_id)


# --- unbind_reference: DELETE /character-references/{id} ----------------------


def test_unbind_last_reference_demotes_character(client, db_session):
    """Unbinding the only reference drops the character to NEEDS_CONFIRMATION."""
    project = _project(client, "解绑唯一参考")
    character = _character(client, project["id"], "林澈")
    asset = _reference_asset(db_session, project["id"], "a" * 64)
    reference = _bind(client, character["id"], asset.id)
    promoted = _promote_to_canonical(client, character)

    response = client.delete(f"/api/v1/character-references/{reference['id']}")
    assert response.status_code == 204, response.text

    state = _character_state(client, project["id"], character["id"])
    assert state["status"] == "NEEDS_CONFIRMATION", state
    assert state["version"] == promoted["version"] + 1
    assert state["references"] == []


def test_unbind_one_of_two_references_keeps_canonical(client, db_session):
    """Unbinding one reference while another live one remains keeps CANONICAL."""
    project = _project(client, "解绑之一参考")
    character = _character(client, project["id"], "林澈")
    first_asset = _reference_asset(db_session, project["id"], "b" * 64)
    second_asset = _reference_asset(db_session, project["id"], "c" * 64)
    first = _bind(client, character["id"], first_asset.id)
    second = _bind(client, character["id"], second_asset.id)
    promoted = _promote_to_canonical(client, character)

    response = client.delete(f"/api/v1/character-references/{first['id']}")
    assert response.status_code == 204, response.text

    state = _character_state(client, project["id"], character["id"])
    assert state["status"] == "CANONICAL", state
    assert state["version"] == promoted["version"] + 1
    assert [item["id"] for item in state["references"]] == [second["id"]]


def test_unbind_when_remaining_reference_is_tombstoned_demotes(client, db_session):
    """A remaining reference to a soft-deleted asset is not a live reference."""
    project = _project(client, "解绑后仅剩软删参考")
    character = _character(client, project["id"], "林澈")
    live = _reference_asset(db_session, project["id"], "d" * 64)
    tombstoned = _reference_asset(db_session, project["id"], "e" * 64, tombstoned=True)
    reference = _bind(client, character["id"], live.id)
    _orm_bind(db_session, character["id"], tombstoned.id)
    promoted = _promote_to_canonical(client, character)

    response = client.delete(f"/api/v1/character-references/{reference['id']}")
    assert response.status_code == 204, response.text

    state = _character_state(client, project["id"], character["id"])
    assert state["status"] == "NEEDS_CONFIRMATION", state
    assert state["version"] == promoted["version"] + 1


# --- _detach_reference_asset: DELETE /assets/{id} soft delete -----------------


def test_soft_deleting_last_bound_asset_demotes_character(client, db_session):
    """Soft-deleting the only bound asset drops the character to NEEDS_CONFIRMATION."""
    project = _project(client, "软删唯一素材")
    character = _character(client, project["id"], "林澈")
    asset = _reference_asset(db_session, project["id"], "f" * 64)
    _bind(client, character["id"], asset.id)
    promoted = _promote_to_canonical(client, character)

    response = client.delete(f"/api/v1/assets/{asset.id}")
    assert response.status_code == 204, response.text

    state = _character_state(client, project["id"], character["id"])
    assert state["status"] == "NEEDS_CONFIRMATION", state
    assert state["version"] == promoted["version"] + 1
    assert state["references"] == []


def test_soft_deleting_one_of_two_bound_assets_keeps_canonical(client, db_session):
    """Soft-deleting one asset while another live binding remains keeps CANONICAL."""
    project = _project(client, "软删之一素材")
    character = _character(client, project["id"], "林澈")
    first = _reference_asset(db_session, project["id"], "1" * 64)
    second = _reference_asset(db_session, project["id"], "2" * 64)
    _bind(client, character["id"], first.id)
    second_reference = _bind(client, character["id"], second.id)
    promoted = _promote_to_canonical(client, character)

    response = client.delete(f"/api/v1/assets/{first.id}")
    assert response.status_code == 204, response.text

    state = _character_state(client, project["id"], character["id"])
    assert state["status"] == "CANONICAL", state
    assert state["version"] == promoted["version"] + 1
    assert [item["id"] for item in state["references"]] == [second_reference["id"]]


def test_soft_deleting_asset_with_tombstoned_remaining_reference_demotes(
    client, db_session,
):
    """A binding left pointing at a soft-deleted asset does not keep CANONICAL."""
    project = _project(client, "软删后仅剩软删参考")
    character = _character(client, project["id"], "林澈")
    live = _reference_asset(db_session, project["id"], "3" * 64)
    tombstoned = _reference_asset(db_session, project["id"], "4" * 64, tombstoned=True)
    _bind(client, character["id"], live.id)
    _orm_bind(db_session, character["id"], tombstoned.id)
    promoted = _promote_to_canonical(client, character)

    response = client.delete(f"/api/v1/assets/{live.id}")
    assert response.status_code == 204, response.text

    state = _character_state(client, project["id"], character["id"])
    assert state["status"] == "NEEDS_CONFIRMATION", state
    assert state["version"] == promoted["version"] + 1

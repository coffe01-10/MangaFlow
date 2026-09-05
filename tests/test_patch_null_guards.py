"""Regression: explicit nulls on required PATCH fields return 422, not 500.

PATCH handlers applied ``exclude_unset`` bodies straight onto the ORM, so an
explicit ``{"field": null}`` for a NOT NULL column surfaced as
AttributeError (``None.strip()``), TypeError, or a raw IntegrityError 500.
A shared guard now inspects the mapped column nullability and rejects the
offending fields with 422 while still allowing nulls that clear genuinely
nullable columns.
"""

import pytest

from app.api.helpers import reject_required_nulls
from app.models import Project


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

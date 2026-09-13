"""Regression: package entry points refuse a soft-deleted project (issue #662).

``_package`` used to verify only package ownership, so every package writer
(PATCH workspace, activate, bind/unbind references and outfits, draft delete)
plus the diff read kept mutating or serving a package whose project had been
soft-deleted — the exact defect class ``ensure_project_scope`` eliminated for
object-id routes in issue #236. The accessor now checks project liveness
first, and ``get_package``/``create_package`` delegate to that single check
instead of re-resolving the project.
"""

import io

from PIL import Image
from sqlalchemy import select

from app.models import (
    CharacterModelPackage,
    CharacterModelPackageVersionReference,
    Project,
    utcnow,
)


def _png_bytes(counter: int) -> bytes:
    # Unique bytes per call: the upload endpoint dedupes by (project, sha256).
    buffer = io.BytesIO()
    level = counter % 256
    Image.new("RGB", (8 + counter % 4, 8), (level, level, level)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def _project(client, name="角色包存活测试项目") -> dict:
    return client.post("/api/v1/projects", json={"name": name}).json()


def _character(client, project_id: str, name="林澈") -> dict:
    return client.post(
        f"/api/v1/projects/{project_id}/characters",
        json={"primary_name": name, "aliases": ["阿澈"]},
    ).json()


def _upload_asset(client, project_id: str, counter: int, name="参考图.png") -> dict:
    response = client.post(
        "/api/v1/assets/upload",
        files={"file": (name, _png_bytes(counter), "image/png")},
        data={"project_id": project_id, "kind": "CHARACTER_REFERENCE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_package(client, project_id: str, character_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/characters/{character_id}/package",
        json={},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _package_url(project_id: str, character_id: str) -> str:
    return f"/api/v1/projects/{project_id}/characters/{character_id}/package"


def _soft_delete_project(db_session, project_id: str) -> None:
    row = db_session.get(Project, project_id)
    assert row is not None
    row.deleted_at = utcnow()
    db_session.commit()


def _publish_with_front_reference(
    client, project_id: str, character_id: str, package: dict, counter: int
) -> None:
    """Bind one front reference and publish the package's DRAFT version."""
    draft = package["versions"][0]
    front = _upload_asset(client, project_id, counter=counter, name="front.png")
    bound = client.post(
        f"{_package_url(project_id, character_id)}/versions/{draft['id']}/references",
        json={"asset_id": front["id"], "role": "front", "version": draft["version"]},
    )
    assert bound.status_code == 201, bound.text
    published = client.post(
        f"{_package_url(project_id, character_id)}/versions/{draft['id']}/publish",
        json={},
    )
    assert published.status_code == 200, published.text


def test_draft_edits_rejected_after_project_soft_delete(client, db_session):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    draft = package["versions"][0]
    url = _package_url(project["id"], character["id"])
    asset = _upload_asset(client, project["id"], counter=1)

    _soft_delete_project(db_session, project["id"])

    # The read entry point hides the package behind the project 404.
    read = client.get(url)
    assert read.status_code == 404
    assert read.json()["detail"] == "项目不存在"

    patched = client.patch(
        url,
        json={"identity_spec": {"gender": "女"}, "version": package["version"]},
    )
    assert patched.status_code == 404
    assert patched.json()["detail"] == "项目不存在"

    bound = client.post(
        f"{url}/versions/{draft['id']}/references",
        json={"asset_id": asset["id"], "role": "front", "version": draft["version"]},
    )
    assert bound.status_code == 404
    assert bound.json()["detail"] == "项目不存在"

    # The refused writes must not have touched the filed-away project.
    package_row = db_session.get(CharacterModelPackage, package["id"])
    assert package_row.identity_spec == {}
    references = db_session.scalars(
        select(CharacterModelPackageVersionReference).where(
            CharacterModelPackageVersionReference.version_id == draft["id"]
        )
    ).all()
    assert references == []


def test_activate_and_diff_rejected_after_project_soft_delete(client, db_session):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    v1_id = package["versions"][0]["id"]
    url = _package_url(project["id"], character["id"])

    _publish_with_front_reference(client, project["id"], character["id"], package, 2)
    derived = client.post(f"{url}/versions", json={})
    assert derived.status_code == 201, derived.text
    v2_id = derived.json()["id"]
    published_v2 = client.post(f"{url}/versions/{v2_id}/publish", json={})
    assert published_v2.status_code == 200, published_v2.text

    _soft_delete_project(db_session, project["id"])

    # Pre-fix this returned 200 and moved the publish pointer of a
    # filed-away project from V2 back to V1.
    activated = client.post(
        f"{url}/activate",
        json={"version_id": v1_id, "expected_published_version_id": v2_id},
    )
    assert activated.status_code == 404
    assert activated.json()["detail"] == "项目不存在"

    diffed = client.get(
        f"{url}/diff",
        params={"base_version_id": v1_id, "target_version_id": v2_id},
    )
    assert diffed.status_code == 404
    assert diffed.json()["detail"] == "项目不存在"

    package_row = db_session.get(CharacterModelPackage, package["id"])
    assert package_row.published_version_id == v2_id


def test_live_project_package_entries_still_work(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    draft = package["versions"][0]
    url = _package_url(project["id"], character["id"])

    patched = client.patch(
        url,
        json={"identity_spec": {"gender": "男"}, "version": package["version"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["identity_spec"] == {"gender": "男"}
    assert patched.json()["version"] == package["version"] + 1

    _publish_with_front_reference(client, project["id"], character["id"], package, 3)
    derived = client.post(f"{url}/versions", json={})
    assert derived.status_code == 201, derived.text
    v2_id = derived.json()["id"]
    published_v2 = client.post(f"{url}/versions/{v2_id}/publish", json={})
    assert published_v2.status_code == 200, published_v2.text

    activated = client.post(
        f"{url}/activate",
        json={"version_id": draft["id"], "expected_published_version_id": v2_id},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["published_version_id"] == draft["id"]

    diffed = client.get(
        f"{url}/diff",
        params={"base_version_id": draft["id"], "target_version_id": v2_id},
    )
    assert diffed.status_code == 200, diffed.text

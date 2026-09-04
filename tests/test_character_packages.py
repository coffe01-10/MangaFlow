"""V02-22B character model package regression suite (contract §11 PKG-S3~S12).

Real PostgreSQL upgrade/downgrade concurrency (PKG-S14) and real provider
generation stay NOT RUN; SQLite round trips cover migration behavior and the
offline API surface. Generation chain tests (PKG-S9~S11) live in the same file.
"""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings


_png_counter = 0


def _png_bytes() -> bytes:
    # Unique bytes per call: the upload endpoint dedupes by (project, sha256),
    # so identical images would collapse into one Asset row in the tests.
    global _png_counter
    _png_counter += 1
    buffer = io.BytesIO()
    level = _png_counter % 256
    Image.new("RGB", (8 + _png_counter % 4, 8), (level, level, level)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def _project(client, name="角色包测试项目") -> dict:
    return client.post("/api/v1/projects", json={"name": name}).json()


def _character(client, project_id: str, name="林澈") -> dict:
    return client.post(
        f"/api/v1/projects/{project_id}/characters",
        json={"primary_name": name, "aliases": ["阿澈"]},
    ).json()


def _upload_asset(
    client, project_id: str, kind="CHARACTER_REFERENCE", name="参考图.png"
) -> dict:
    response = client.post(
        "/api/v1/assets/upload",
        files={"file": (name, _png_bytes(), "image/png")},
        data={"project_id": project_id, "kind": kind},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_package(client, project_id: str, character_id: str, **spec) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/characters/{character_id}/package",
        json=spec,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _package_url(project_id: str, character_id: str) -> str:
    return f"/api/v1/projects/{project_id}/characters/{character_id}/package"


def _outfit(client, project_id: str, character_id: str, name="校服") -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/outfits",
        json={
            "character_id": character_id,
            "name": name,
            "components": {"top": "衬衫"},
            "reference_asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def file_sessions(tmp_path):
    """File-backed SQLite for multi-session concurrency tests (P2-8 pattern)."""
    db_path = tmp_path / "character_packages_concurrency.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()

    @event.listens_for(engine, "begin")
    def do_begin(conn):
        raw_conn = getattr(conn.connection, "dbapi_connection", None)
        if raw_conn and not getattr(raw_conn, "in_transaction", False):
            conn.exec_driver_sql("BEGIN")

    from app.database import Base

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_package_create_and_read_roundtrip(client):
    project = _project(client)
    character = _character(client, project["id"])
    created = _create_package(
        client,
        project["id"],
        character["id"],
        identity_spec={"gender": "男", "personality": "沉稳"},
        visual_spec={"hair": "黑发"},
        negative_constraints=["不要第四面墙", "不要现代服饰"],
    )
    assert created["character_id"] == character["id"]
    assert created["status"] == "ACTIVE"
    assert created["published_version_id"] is None
    assert created["identity_spec"] == {"gender": "男", "personality": "沉稳"}
    assert created["negative_constraints"] == ["不要第四面墙", "不要现代服饰"]
    assert len(created["versions"]) == 1
    draft = created["versions"][0]
    assert draft["version_number"] == 1
    assert draft["status"] == "DRAFT"
    assert draft["spec_snapshot"]["frozen_from"] == "package"
    assert draft["spec_snapshot"]["identity_spec"] == created["identity_spec"]

    # Duplicate create is 409; project/character 404.
    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/characters/{character['id']}/package",
        json={},
    )
    assert duplicate.status_code == 409
    missing = client.post(
        f"/api/v1/projects/{project['id']}/characters/missing/package", json={}
    )
    assert missing.status_code == 404

    listed = client.get(f"/api/v1/projects/{project['id']}/character-packages").json()
    assert [item["character_id"] for item in listed] == [character["id"]]
    assert listed[0]["published_version_number"] is None
    assert listed[0]["published_completeness"] is None


def test_package_spec_validation_and_optimistic_lock(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    assert package["version"] == 1

    bad_key = client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"mystery": "x"}, "version": 1},
    )
    assert bad_key.status_code == 422

    too_long = client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"personality": "长" * 801}, "version": 1},
    )
    assert too_long.status_code == 422

    stale = client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"gender": "男"}, "version": 99},
    )
    assert stale.status_code == 409

    updated = client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"gender": "女"}, "version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["identity_spec"] == {"gender": "女"}
    assert updated.json()["version"] == 2

    # Zero references -> publish is refused by the 0-reference guard.
    version_id = updated.json()["versions"][0]["id"]
    response = client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{version_id}/publish",
        json={},
    )
    assert response.status_code == 422


def test_package_bind_reference_validation(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"

    asset = _upload_asset(client, project["id"])
    other_project = _project(client, "别的项目")
    other_asset = _upload_asset(client, other_project["id"])

    cross_project = client.post(
        f"{base_url}/references",
        json={"asset_id": other_asset["id"], "role": "front", "version": token},
    )
    assert cross_project.status_code == 409

    bad_role = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "top_view", "version": token},
    )
    assert bad_role.status_code == 422

    missing_label = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "expression", "label": "", "version": token},
    )
    assert missing_label.status_code == 422

    core_label = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "front", "label": "x", "version": token},
    )
    assert core_label.status_code == 422

    bound = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "front", "label": "", "version": token},
    )
    assert bound.status_code == 201, bound.text
    reference_id = bound.json()["id"]

    # Duplicate slot 409 even with a fresh token.
    duplicate = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "front", "version": token + 1},
    )
    assert duplicate.status_code == 409

    # Stale token on an unrelated unbind is a CAS conflict.
    stale = client.request(
        "DELETE", f"{base_url}/references/{reference_id}", json={"version": token}
    )
    assert stale.status_code == 409

    unbound = client.request(
        "DELETE", f"{base_url}/references/{reference_id}", json={"version": token + 1}
    )
    assert unbound.status_code == 204

    # Rebind after unbind succeeds.
    rebound = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "side", "version": token + 2},
    )
    assert rebound.status_code == 201, rebound.text


def test_package_cover_set_replace_and_outfit_default_flow(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"

    asset_a = _upload_asset(client, project["id"], name="a.png")
    asset_b = _upload_asset(client, project["id"], name="b.png")
    cover = client.put(
        f"{base_url}/cover", json={"asset_id": asset_a["id"], "version": token}
    )
    assert cover.status_code == 200, cover.text
    assert cover.json()["role"] == "cover"
    replaced = client.put(
        f"{base_url}/cover",
        json={"asset_id": asset_b["id"], "version": token + 1},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["asset_id"] == asset_b["id"]

    outfit = _outfit(client, project["id"], character["id"])
    o_asset = _upload_asset(client, project["id"], kind="OUTFIT_REFERENCE", name="衣图.png")
    client.patch(
        f"/api/v1/outfits/{outfit['id']}",
        json={"reference_asset_ids": [o_asset["id"]], "version": outfit["version"]},
    )
    another = _outfit(client, project["id"], character["id"], name="常服")
    o_asset2 = _upload_asset(client, project["id"], kind="OUTFIT_REFERENCE", name="衣图2.png")
    client.patch(
        f"/api/v1/outfits/{another['id']}",
        json={"reference_asset_ids": [o_asset2["id"]], "version": another["version"]},
    )

    bound = client.post(
        f"{base_url}/outfits",
        json={"outfit_id": outfit["id"], "is_default": True, "version": token + 2},
    )
    assert bound.status_code == 201, bound.text
    added = client.post(
        f"{base_url}/outfits",
        json={"outfit_id": another["id"], "is_default": False, "version": token + 3},
    )
    assert added.status_code == 201, added.text
    second_default = client.post(
        f"{base_url}/outfits",
        json={"outfit_id": another["id"], "is_default": True, "version": token + 4},
    )
    # Another default in the same version is refused; the PATCH is the swap path.
    assert second_default.status_code == 409

    swapped = client.patch(
        f"{base_url}/outfits/{another['id']}",
        json={"is_default": True, "version": token + 4},
    )
    assert swapped.status_code == 200, swapped.text
    detail_token = token + 5
    detail = client.get(_package_url(project["id"], character["id"])).json()
    draft_details = detail["versions"][0]["outfits"]
    by_id = {item["outfit_id"]: item for item in draft_details}
    assert by_id[outfit["id"]]["is_default"] is False
    assert by_id[another["id"]]["is_default"] is True

    cancel_via_patch = client.patch(
        f"{base_url}/outfits/{another['id']}",
        json={"is_default": False, "version": detail_token},
    )
    assert cancel_via_patch.status_code == 422

    unbound = client.request(
        "DELETE",
        f"{base_url}/outfits/{another['id']}",
        json={"version": detail_token},
    )
    assert unbound.status_code == 204


def test_package_publish_activate_archive_restore_flow(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"

    # Publish without references is refused (422).
    assert client.post(f"{base_url}/publish", json={}).status_code == 422

    front = _upload_asset(client, project["id"], name="front.png")
    client.post(
        f"{base_url}/references",
        json={"asset_id": front["id"], "role": "front", "version": token},
    )
    published = client.post(f"{base_url}/publish", json={})
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "READY"
    package_after = client.get(_package_url(project["id"], character["id"])).json()
    assert package_after["published_version_id"] == version_id
    assert package_after["versions"][0]["published_at"] is not None

    # Publish again is a state conflict.
    assert client.post(f"{base_url}/publish", json={}).status_code == 409

    # Derive V2 from V1.
    derived = client.post(
        f"{_package_url(project['id'], character['id'])}/versions",
        json={},
    )
    assert derived.status_code == 201, derived.text
    v2_id = derived.json()["id"]
    assert derived.json()["version_number"] == 2
    assert derived.json()["status"] == "DRAFT"
    assert derived.json()["derived_from_version_id"] == version_id
    assert len(derived.json()["references"]) == 1
    assert derived.json()["spec_snapshot"]["frozen_from"] == "derive"

    # Activate with a wrong CAS token is 409; right token succeeds.
    wrong_cas = client.post(
        f"{_package_url(project['id'], character['id'])}/activate",
        json={"version_id": version_id, "expected_published_version_id": "some-other"},
    )
    assert wrong_cas.status_code == 409
    activated = client.post(
        f"{_package_url(project['id'], character['id'])}/activate",
        json={"version_id": version_id, "expected_published_version_id": version_id},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["published_version_id"] == version_id

    # While V1 is the pointer, archiving it is refused.
    refuse = client.post(f"{base_url}/archive", json={})
    assert refuse.status_code == 409
    # Publishing V2 moves the pointer to V2 in the same transaction.
    publish_v2 = client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{v2_id}/publish",
        json={},
    )
    assert publish_v2.status_code == 200, publish_v2.text
    assert (
        client.get(_package_url(project["id"], character["id"])).json()[
            "published_version_id"
        ]
        == v2_id
    )
    # V1 is no longer the pointer: archiving and restoring it both work.
    archived = client.post(f"{base_url}/archive", json={})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"
    restored = client.post(f"{base_url}/restore", json={})
    assert restored.status_code == 200
    assert restored.json()["status"] == "READY"

    # Activate with the correct CAS token moves the pointer backward.
    activated_back = client.post(
        f"{_package_url(project['id'], character['id'])}/activate",
        json={"version_id": version_id, "expected_published_version_id": v2_id},
    )
    assert activated_back.status_code == 200, activated_back.text
    assert activated_back.json()["published_version_id"] == version_id

    # Package archive/restore.
    acked = client.post(
        f"{_package_url(project['id'], character['id'])}/archive", json={}
    )
    assert acked.status_code == 200, acked.text


def test_package_archive_restore_package_roundtrip(client):
    project = _project(client)
    character = _character(client, project["id"])
    _create_package(client, project["id"], character["id"])
    url = _package_url(project["id"], character["id"])
    archived = client.post(f"{url}/archive", json={})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"
    active_only = client.get(
        "/api/v1/projects/{0}/character-packages".format(project["id"]),
        params={"status": "ACTIVE"},
    ).json()
    assert active_only == []
    archived_only = client.get(
        "/api/v1/projects/{0}/character-packages".format(project["id"]),
        params={"status": "ARCHIVED"},
    ).json()
    assert [item["character_id"] for item in archived_only] == [character["id"]]
    restored = client.post(f"{url}/restore", json={})
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"


def test_package_relations_immutable_after_publish(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    asset = _upload_asset(client, project["id"])
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    bind = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "front", "version": token},
    )
    assert bind.status_code == 201
    reference_id = bind.json()["id"]
    published = client.post(f"{base_url}/publish", json={})
    assert published.status_code == 200, published.text

    modify = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "side", "version": token + 1},
    )
    assert modify.status_code == 409
    unbind = client.request(
        "DELETE", f"{base_url}/references/{reference_id}", json={"version": token + 1}
    )
    assert unbind.status_code == 409
    delete_version = client.delete(
        f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    )
    assert delete_version.status_code == 409


def test_package_delete_draft_guards(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    # The last version cannot be deleted (no DERIVE possible afterwards).
    assert client.delete(url).status_code == 409


def test_package_completeness_deterministic_and_live_draft(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    completeness_url = f"{base_url}/completeness"

    first = client.get(completeness_url).json()
    second = client.get(completeness_url).json()
    assert first == second
    assert first["score"] == 0
    codes = {item["code"] for item in first["missing"]}
    assert {"MISSING_IDENTITY", "MISSING_VIEW", "MISSING_EXPRESSION", "MISSING_OUTFIT"} <= codes

    # Editing the draft spec must move the DRAFT score immediately (workspec read).
    client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"gender": "男"}, "version": package["version"]},
    )
    scored = client.get(completeness_url).json()
    assert scored["score"] == 5
    assert all(item["field"] != "gender" for item in scored["missing"])

    # Views raise the score deterministically.
    front = _upload_asset(client, project["id"], name="正面.png")
    client.post(
        f"{base_url}/references",
        json={"asset_id": front["id"], "role": "front", "version": token},
    )
    assert client.get(completeness_url).json()["score"] == 20


def test_package_completeness_published_uses_frozen_snapshot(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    front = _upload_asset(client, project["id"])
    client.post(
        f"{base_url}/references",
        json={"asset_id": front["id"], "role": "front", "version": token},
    )
    # Seed the workspec before the publish freeze.
    client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"gender": "女"}, "version": 1},
    )
    published = client.post(f"{base_url}/publish", json={})
    assert published.status_code == 200, published.text
    before = client.get(f"{base_url}/completeness").json()
    assert before["score"] == 20
    # Post-publish spec edits do not change READY completeness.
    client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"gender": "女", "personality": "冷"}, "version": 2},
    )
    after = client.get(f"{base_url}/completeness").json()
    assert after == before


def _seed_package_rows(factory):
    """Create a project/character/package with one reference for concurrency tests."""
    from app.models import (
        Asset,
        Character,
        CharacterModelPackage,
        CharacterModelPackageVersion,
        CharacterModelPackageVersionReference,
        Project,
    )

    db = factory()
    project = Project(id="concurrent-project", name="并发测试项目")
    character = Character(
        id="concurrent-character",
        project_id=project.id,
        primary_name="并发角色",
        aliases=[],
        aliases_normalized=[],
        status="UPLOADED",
    )
    asset = Asset(
        id="concurrent-asset",
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="front.png",
        storage_key="concurrent/front.png",
        mime_type="image/png",
        byte_size=100,
        sha256="c" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db.add(project)
    db.flush()
    db.add(character)
    db.flush()
    db.add(asset)
    db.flush()
    package = CharacterModelPackage(
        character_id=character.id,
        project_id=project.id,
        identity_spec={},
        visual_spec={},
        negative_constraints=[],
        status="ACTIVE",
    )
    db.add(package)
    db.flush()
    version = CharacterModelPackageVersion(
        package_id=package.id,
        version_number=1,
        status="DRAFT",
        spec_snapshot={
            "identity_spec": {},
            "visual_spec": {},
            "negative_constraints": [],
            "frozen_from": "package",
        },
    )
    db.add(version)
    db.flush()
    db.add(
        CharacterModelPackageVersionReference(
            version_id=version.id, asset_id=asset.id, role="front", label="", sort_order=0
        )
    )
    db.commit()
    db.close()
    return project.id, character.id, package.id, version.id


def _concurrent_pair(factory, first, second, errors=None):
    def run(fn, errors, label):
        db = factory()
        try:
            return fn(db)
        except HTTPException as error:
            errors[label] = error
            return None
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            label: executor.submit(run, fn, errors, label)
            for label, fn in (("first", first), ("second", second))
        }
        return {label: future.result() for label, future in futures.items()}


def test_package_concurrent_publish_single_winner(file_sessions):
    from app.models import CharacterModelPackage, CharacterModelPackageVersion
    from app.services.character_packages import publish_version

    project_id, character_id, _package_id, version_id = _seed_package_rows(file_sessions)
    errors: dict[str, HTTPException] = {}
    results = _concurrent_pair(
        file_sessions,
        lambda db: publish_version(db, project_id, character_id, version_id),
        lambda db: publish_version(db, project_id, character_id, version_id),
        errors,
    )
    winners = [key for key, value in results.items() if value is not None]
    assert len(winners) == 1, errors
    db = file_sessions()
    package = db.get(CharacterModelPackage, _package_id)
    assert package.published_version_id == version_id
    assert (
        db.scalar(
            select(CharacterModelPackageVersion.status).where(
                CharacterModelPackageVersion.id == version_id
            )
        )
        == "READY"
    )
    db.close()


def test_package_publish_derive_race_serialized(file_sessions):
    from app.models import CharacterModelPackageVersion
    from app.services.character_packages import derive_version, publish_version

    project_id, character_id, _package_id, version_id = _seed_package_rows(file_sessions)
    errors: dict[str, HTTPException] = {}
    results = _concurrent_pair(
        file_sessions,
        lambda db: publish_version(db, project_id, character_id, version_id),
        lambda db: derive_version(db, project_id, character_id),
        errors,
    )
    assert results["first"] is not None or results["second"] is not None, errors
    db = file_sessions()
    version_numbers = list(
        db.scalars(
            select(CharacterModelPackageVersion.version_number).where(
                CharacterModelPackageVersion.package_id == _package_id
            )
        )
    )
    assert sorted(version_numbers) in ([1, 2], [1])
    db.close()


# --- generation chain (contract §8, PKG-S9~S11) -----------------------------


def _skip_page_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.page_readiness.ensure_page_ready", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.services.ordinal_allocator.ensure_page_ready", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.api.routes.workflow.generation.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )


def _generation_page(db_session, project_id: str, character_id: str, panel_outfits=None):
    """Build a page with one visible character without readiness machinery."""
    from app.domain.states import PageStatus
    from app.models import (
        Beat,
        Chapter,
        MangaPage,
        Panel,
        Scene,
        SourceRevision,
        SourceSegment,
    )

    chapter = Chapter(project_id=project_id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id="revision-pkg",
        chapter_id=chapter.id,
        revision=1,
        source_type="PASTE",
        original_text="她站起身走向窗边。",
        sha256="pkg-revision",
        character_count=8,
    )
    db_session.add(revision)
    db_session.flush()
    db_session.get(Chapter, chapter.id).status = "SCRIPT_READY"
    segment = SourceSegment(
        source_revision_id=revision.id,
        ordinal=1,
        text="她站起身走向窗边。",
        start_offset=0,
        end_offset=8,
        sha256="pkg-segment",
    )
    db_session.add(segment)
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        location="老洋房客厅",
        source_range={"segment_ids": [segment.id]},
    )
    db_session.add(scene)
    db_session.flush()
    beat = Beat(
        scene_id=scene.id,
        ordinal=1,
        action="她站起身走向窗边。",
        speaker_name="林澈",
        dialogue="我想一个人待一会儿。",
        source_range={"segment_ids": [segment.id]},
    )
    db_session.add(beat)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        page_function="dialogue",
        source_coverage={
            "complete": True,
            "ranges": [{"text": "她站起身走向窗边。", "segment_ids": [segment.id]}],
        },
        scene_ids=[scene.id],
        beat_ids=[beat.id],
        storyboard_version=1,
        status=PageStatus.STORYBOARDED,
    )
    db_session.add(page)
    db_session.flush()
    panel = Panel(
        page_id=page.id,
        reading_order=1,
        bounds={"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
        characters=[character_id],
        character_presence={character_id: "VISIBLE"},
        outfits=panel_outfits or {},
    )
    db_session.add(panel)
    db_session.commit()
    return chapter, page


def _package_published_fixture(client, db_session, monkeypatch):
    """Project/character/package/published version with front ref + default outfit."""
    project = _project(client)
    character = _character(client, project["id"])
    front = _upload_asset(client, project["id"], name="正面.png")
    client.post(
        f"/api/v1/characters/{character['id']}/references",
        json={"asset_id": front["id"], "angle": "front"},
    )
    package = _create_package(
        client,
        project["id"],
        character["id"],
        identity_spec={"gender": "男", "personality": "沉稳"},
        visual_spec={"hair": "黑发"},
        negative_constraints=["不要现代服饰"],
    )
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    bind = client.post(
        f"{url}/references",
        json={"asset_id": front["id"], "role": "front", "version": token},
    )
    assert bind.status_code == 201, bind.text
    outfit = _outfit(client, project["id"], character["id"])
    o_asset = _upload_asset(
        client, project["id"], kind="OUTFIT_REFERENCE", name="服装图.png"
    )
    client.patch(
        f"/api/v1/outfits/{outfit['id']}",
        json={"reference_asset_ids": [o_asset["id"]], "version": outfit["version"]},
    )
    obind = client.post(
        f"{url}/outfits",
        json={"outfit_id": outfit["id"], "is_default": True, "version": token + 1},
    )
    assert obind.status_code == 201, obind.text
    published = client.post(f"{url}/publish", json={})
    assert published.status_code == 200, published.text
    return project, character, front, outfit, o_asset, version_id


def test_package_gate_default_outfit_satisfies_assignment(client, db_session, monkeypatch):
    from types import SimpleNamespace

    from app.models import StyleProfile

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, character, front, outfit, o_asset, version_id = _package_published_fixture(
        client, db_session, monkeypatch
    )
    # Satisfy the non-package readiness conditions so only the outfit gate matters.
    style = StyleProfile(
        project_id=project["id"],
        name="日漫彩稿",
        color_mode="color",
        profile={
            "palette_confirmed": True,
            "test_image_approved": True,
            "approved_test_candidate_id": "test-1",
        },
        status="ACTIVE",
    )
    db_session.add(style)
    db_session.commit()
    from app.models import Project

    project_row = db_session.get(Project, project["id"])
    project_row.default_style_id = style.id
    db_session.commit()
    monkeypatch.setattr(
        "app.services.page_readiness._catalog_model_availability",
        lambda *_args, **_kwargs: {
            "text": 1,
            "image": 1,
            "auto_text": 1,
            "auto_image": 1,
        },
    )
    monkeypatch.setattr(
        "app.services.page_readiness.queue_execution_state",
        lambda *_args, **_kwargs: SimpleNamespace(
            queue_mode="LOCAL",
            actual_executor="LOCAL",
            can_execute=True,
            redis_state="NOT_USED",
        ),
    )
    # The storyboard assigns no outfit: the package default outfit with a live
    # reference satisfies MISSING_OUTFIT_ASSIGNMENT at batch start (contract §8.1).
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201, batch.text
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
            # No explicit outfit_id: the version default outfit must be resolved
            # into the normalized selection (contract §8.1 outfit chain).
            "reference_selections": {
                character["id"]: {
                    "outfit_asset_id": o_asset["id"],
                }
            },
        },
    )
    assert queued.status_code == 202, queued.text
    snapshot = queued.json()["candidate"]["prompt_snapshot"]
    facts = snapshot["character_packages"][character["id"]]
    assert facts["outfit_id"] == outfit["id"]
    assert snapshot["reference_selections"][character["id"]]["outfit_id"] == outfit["id"]
    facts = snapshot["character_packages"][character["id"]]
    assert facts["package_version_id"] == version_id
    assert facts["version_number"] == 1
    assert facts["character_asset_id"] == front["id"]
    assert facts["reference_role"] == "front"
    assert facts["identity_spec"] == {"gender": "男", "personality": "沉稳"}
    assert facts["spec_fingerprint"].startswith("sha256:")
    assert facts["style_profile_id"] == style.id

    # The same transaction marked the version IN_PRODUCTION.
    db_session.expire_all()
    from app.models import CharacterModelPackageVersion

    assert (
        db_session.get(CharacterModelPackageVersion, version_id).status
        == "IN_PRODUCTION"
    )


def test_package_explicit_version_ownership_enforced(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project = _project(client)
    character = _character(client, project["id"])
    other = _character(client, project["id"], name="陈昊")
    other_package = _create_package(client, project["id"], other["id"])
    other_version = other_package["versions"][0]["id"]
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201, batch.text
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
            "reference_selections": {
                character["id"]: {"package_version_id": other_version}
            },
        },
    )
    assert queued.status_code == 409


def test_package_patch_keeps_omitted_blocks(client):
    """Round-2 P1: a partial PATCH must not erase the omitted spec blocks."""
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(
        client,
        project["id"],
        character["id"],
        identity_spec={"gender": "男"},
        visual_spec={"hair": "黑发"},
        negative_constraints=["不要现代服饰"],
    )
    patched = client.patch(
        _package_url(project["id"], character["id"]),
        json={"identity_spec": {"gender": "女"}, "version": package["version"]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["identity_spec"] == {"gender": "女"}
    assert patched.json()["visual_spec"] == {"hair": "黑发"}
    assert patched.json()["negative_constraints"] == ["不要现代服饰"]


def test_package_worker_consumes_queue_snapshot(client, db_session, monkeypatch):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from app.domain.states import JobStatus
    from app.model_adapters.base import ModelResponse
    from app.worker_tasks import _run_page_generate

    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = get_settings()
        monkeypatch.setattr(settings, "queue_enabled", False)
        monkeypatch.setattr(settings, "storage_root", root / "storage")
        monkeypatch.setattr(settings, "upload_root", root / "uploads")
        _skip_page_readiness(monkeypatch)

        project, character, front, outfit, o_asset, version_id = (
            _package_published_fixture(client, db_session, monkeypatch)
        )
        _chapter, page = _generation_page(db_session, project["id"], character["id"])
        batch = client.post(f"/api/v1/pages/{page.id}/batches")
        assert batch.status_code == 201, batch.text
        queued = client.post(
            f"/api/v1/batches/{batch.json()['id']}/candidates",
            json={
                "model_alias": "image.nano_banana_2",
                "resolution": "1K",
                "storyboard_version": page.storyboard_version,
                "reference_selections": {
                    character["id"]: {
                        "outfit_id": outfit["id"],
                        "outfit_asset_id": o_asset["id"],
                    }
                },
            },
        )
        assert queued.status_code == 202, queued.text
        candidate = queued.json()["candidate"]
        queued_facts = dict(candidate["prompt_snapshot"]["character_packages"][character["id"]])

        # The same character now edits their current row: the frozen facts must
        # survive the run.
        renamed = client.patch(
            f"/api/v1/characters/{character['id']}",
            json={"primary_name": "改名后的林澈", "aliases": ["新绰号"], "version": 1},
        )
        assert renamed.status_code == 200

        captured: list[str] = []

        class FakePageAdapter:
            def generate_page(self, request):
                captured.append(request.prompt)
                return ModelResponse(
                    model_id="fake-vertex-image",
                    request_id="fake-request-1",
                    usage={"fake": True},
                    images=(_png_bytes(),),
                )

        monkeypatch.setattr("app.worker_tasks._adapter", lambda _alias: FakePageAdapter())
        from app.models import GenerationJob, PageCandidate

        db_session.expire_all()
        job = db_session.get(GenerationJob, candidate["job_id"])
        assert job is not None
        job.status = JobStatus.PREPARING
        job.attempt_count += 1
        _run_page_generate(db_session, job)
        job.status = JobStatus.COMPLETED
        db_session.commit()

        db_session.expire_all()
        from app.models import GenerationRecord

        record = db_session.get(PageCandidate, candidate["id"])
        generation_record = db_session.get(GenerationRecord, record.generation_record_id)
        snapshot = record.prompt_snapshot
        assert snapshot["character_packages"][character["id"]]["primary_name"] == "林澈"
        assert snapshot["character_packages"][character["id"]]["package_version_id"] == version_id
        compact = generation_record.input_versions["character_packages"][character["id"]]
        assert compact["package_version_id"] == version_id
        assert compact["version_number"] == 1
        assert compact["spec_fingerprint"] == queued_facts["spec_fingerprint"]
        prompt = captured[0]
        # Frozen name and frozen spec appear in the compiled prompt.
        assert "林澈" in prompt
        assert "沉稳" in prompt
        assert "不要现代服饰" in prompt


def test_package_in_flight_asset_delete_blocked(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project, character, front, outfit, o_asset, version_id = _package_published_fixture(
        client, db_session, monkeypatch
    )
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201, batch.text
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
            "reference_selections": {
                character["id"]: {
                    "outfit_id": outfit["id"],
                    "outfit_asset_id": o_asset["id"],
                }
            },
        },
    )
    assert queued.status_code == 202, queued.text
    # The leased reference asset cannot be deleted while the job is active.
    deleted = client.delete(f"/api/v1/assets/{front['id']}")
    assert deleted.status_code == 409


def test_package_asset_soft_delete_clears_draft_rows_keeps_frozen(client, db_session):
    """Contract §10.3: DRAFT relation rows vanish; READY+ rows keep the fact."""
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    front = _upload_asset(client, project["id"], name="正面2.png")
    bind = client.post(
        f"{base_url}/references",
        json={"asset_id": front["id"], "role": "front", "version": token},
    )
    assert bind.status_code == 201, bind.text
    published = client.post(f"{base_url}/publish", json={})
    assert published.status_code == 200, published.text

    # Derive V2 and bind a DRAFT-only reference first.
    derived = client.post(f"{_package_url(project['id'], character['id'])}/versions", json={})
    assert derived.status_code == 201, derived.text
    v2_id = derived.json()["id"]
    v2_token = derived.json()["version"]
    side = _upload_asset(client, project["id"], name="侧面2.png")
    bind = client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{v2_id}/references",
        json={"asset_id": side["id"], "role": "side", "version": v2_token},
    )
    assert bind.status_code == 201, bind.text

    delete = client.delete(f"/api/v1/assets/{front['id']}")
    assert delete.status_code == 204
    # Contract §10.3: DRAFT rows referencing the asset are physically cleared
    # (derived copy too); READY V1 rows keep the frozen fact.
    detail = client.get(_package_url(project["id"], character["id"])).json()
    versions = {item["version_number"]: item for item in detail["versions"]}
    assert any(item["role"] == "front" for item in versions[1]["references"])
    assert all(item["role"] != "front" for item in versions[2]["references"])
    assert any(item["role"] == "side" for item in versions[2]["references"])
    # Frozen V1 completeness drops deterministically after the asset vanishes.
    completeness_url = (
        f"{_package_url(project['id'], character['id'])}/versions/{version_id}/completeness"
    )
    assert client.get(completeness_url).json()["score"] == 0


def test_package_legacy_candidate_replays_without_facts(client, db_session, monkeypatch):
    """PKG-S11: characters without a package keep the legacy path bytewise."""
    from app.models import Project
    from app.services.prompt_compiler import compile_page_prompt

    project = _project(client)
    character = _character(client, project["id"])
    front = _upload_asset(client, project["id"], name="传统正面.png")
    client.post(
        f"/api/v1/characters/{character['id']}/references",
        json={"asset_id": front["id"], "angle": "front"},
    )
    assert client.get(f"/api/v1/projects/{project['id']}/character-packages").json() == []
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    # Compile twice with and without facts: without facts the payload is stable.
    project_row = db_session.get(Project, project["id"])
    prompt_a, _snapshot = compile_page_prompt(db_session, page, project_row)
    prompt_b, _snapshot = compile_page_prompt(
        db_session, page, project_row, character_package_facts=None
    )
    assert prompt_a == prompt_b


def test_package_explicit_archived_version_usable_for_generation(
    client, db_session, monkeypatch
):
    """§8.1: an explicitly selected ARCHIVED version stays selectable."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project, character, front, outfit, o_asset, version_id = _package_published_fixture(
        client, db_session, monkeypatch
    )
    derived = client.post(
        f"{_package_url(project['id'], character['id'])}/versions", json={}
    )
    assert derived.status_code == 201, derived.text
    v2_id = derived.json()["id"]
    publish_v2 = client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{v2_id}/publish",
        json={},
    )
    assert publish_v2.status_code == 200, publish_v2.text
    archived = client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{version_id}/archive",
        json={},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"

    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201, batch.text
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
            "reference_selections": {
                character["id"]: {
                    "package_version_id": version_id,
                    "outfit_id": outfit["id"],
                    "outfit_asset_id": o_asset["id"],
                }
            },
        },
    )
    assert queued.status_code == 202, queued.text
    facts = queued.json()["candidate"]["prompt_snapshot"]["character_packages"][character["id"]]
    assert facts["package_version_id"] == version_id
    assert facts["version_number"] == 1


def test_package_repair_candidate_inherits_queued_snapshot(
    client, db_session, monkeypatch
):
    """PKG-S11/§8.6-3: repairs inherit the original snapshot unre-resolved."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from app.domain.states import JobStatus
    from app.model_adapters.base import ModelResponse
    from app.worker_tasks import _run_page_generate

    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = get_settings()
        monkeypatch.setattr(settings, "queue_enabled", False)
        monkeypatch.setattr(settings, "storage_root", root / "storage")
        monkeypatch.setattr(settings, "upload_root", root / "uploads")
        _skip_page_readiness(monkeypatch)

        project, character, front, outfit, o_asset, version_id = (
            _package_published_fixture(client, db_session, monkeypatch)
        )
        _chapter, page = _generation_page(db_session, project["id"], character["id"])
        batch = client.post(f"/api/v1/pages/{page.id}/batches")
        assert batch.status_code == 201, batch.text
        queued = client.post(
            f"/api/v1/batches/{batch.json()['id']}/candidates",
            json={
                "model_alias": "image.nano_banana_2",
                "resolution": "1K",
                "storyboard_version": page.storyboard_version,
                "reference_selections": {
                    character["id"]: {
                        "outfit_id": outfit["id"],
                        "outfit_asset_id": o_asset["id"],
                    }
                },
            },
        )
        assert queued.status_code == 202, queued.text
        original_candidate = queued.json()["candidate"]

        class FakePageAdapter:
            def generate_page(self, request):
                return ModelResponse(
                    model_id="fake-vertex-image",
                    request_id="fake-request-1",
                    usage={"fake": True},
                    images=(_png_bytes(),),
                )

        monkeypatch.setattr("app.worker_tasks._adapter", lambda _alias: FakePageAdapter())
        from app.models import GenerationJob, InspectionResult, PageCandidate

        db_session.expire_all()
        job = db_session.get(GenerationJob, original_candidate["job_id"])
        job.status = JobStatus.PREPARING
        job.attempt_count += 1
        _run_page_generate(db_session, job)
        job.status = JobStatus.COMPLETED
        db_session.commit()

        db_session.expire_all()
        original = db_session.get(PageCandidate, original_candidate["id"])
        inspection = InspectionResult(
            candidate_id=original.id,
            category="CHARACTER",
            outcome="MISMATCH",
            severity="ERROR",
            details={},
            regions=[{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}],
        )
        db_session.add(inspection)
        db_session.commit()
        created = client.post(
            f"/api/v1/candidates/{original.id}/repairs",
            json={
                "inspection_result_id": inspection.id,
                "repair_type": "BUBBLE_REGION",
                "target_regions": [],
                "target_fields": [],
                "model_alias": "image.nano_banana_2",
                "resolution": "1K",
            },
        )
        assert created.status_code == 202, created.text
        repair_candidate = created.json()["candidate"]
        snapshot = repair_candidate["prompt_snapshot"]
        assert snapshot["character_packages"][character["id"]]["package_version_id"] == version_id
        assert (
            snapshot["reference_selections"][character["id"]]["character_asset_id"]
            == front["id"]
        )
        # The inherited reference assets enter the repair job's lease (§8.4).
        from app.models import JobAssetReference

        leased = set(
            db_session.scalars(
                select(JobAssetReference.asset_id).where(
                    JobAssetReference.job_id == repair_candidate["job_id"]
                )
            )
        )
        assert {front["id"], o_asset["id"], original.asset_id} <= leased


def test_package_prompt_speaker_uses_frozen_name(client, db_session):
    """Round-2 P2: dialogue speakers come from the frozen package fact."""
    from app.models import Dialogue, Panel, Project
    from app.services.prompt_compiler import compile_page_prompt

    project = _project(client)
    character = _character(client, project["id"])
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    panel = db_session.scalars(
        select(Panel).where(Panel.page_id == page.id)
    ).first()
    db_session.add(
        Dialogue(
            panel_id=panel.id,
            speaker_character_id=character["id"],
            target_text="我想一个人待一会儿。",
            reading_order=1,
        )
    )
    db_session.commit()
    project_row = db_session.get(Project, project["id"])
    prompt, _snapshot = compile_page_prompt(
        db_session,
        page,
        project_row,
        character_package_facts={
            character["id"]: {
                "primary_name": "冻结林澈",
                "aliases": [],
                "identity_spec": {},
                "visual_spec": {},
                "negative_constraints": [],
            }
        },
    )
    assert "冻结林澈" in prompt
    assert '"speaker":"冻结林澈"' in prompt
    assert "林澈" not in prompt.replace("冻结林澈", "")


def test_package_diff_reports_slot_and_spec_changes(client):
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(
        client,
        project["id"],
        character["id"],
        identity_spec={"gender": "男"},
    )
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    base_url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    front = _upload_asset(client, project["id"], name="前视图.png")
    client.post(
        f"{base_url}/references",
        json={"asset_id": front["id"], "role": "front", "version": token},
    )
    assert client.post(f"{base_url}/publish", json={}).status_code == 200

    derived = client.post(f"{_package_url(project['id'], character['id'])}/versions", json={})
    assert derived.status_code == 201, derived.text
    v2_id = derived.json()["id"]
    # The derived draft copies V1: swap the front reference and add a side view.
    reference_row = derived.json()["references"][0]
    side = _upload_asset(client, project["id"], name="侧视图.png")
    unbind = client.request(
        "DELETE",
        f"{_package_url(project['id'], character['id'])}/versions/{v2_id}/references/{reference_row['id']}",
        json={"version": derived.json()["version"]},
    )
    assert unbind.status_code == 204
    client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{v2_id}/references",
        json={"asset_id": side["id"], "role": "side", "version": 2},
    )
    diff = client.get(
        f"{_package_url(project['id'], character['id'])}/diff",
        params={"base_version_id": version_id, "target_version_id": v2_id},
    )
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["references"]["removed"] == [
        {
            "role": "front",
            "label": "",
            "asset_id": front["id"],
            "asset_deleted": False,
        }
    ]
    assert body["references"]["added"] == [
        {
            "role": "side",
            "label": "",
            "asset_id": side["id"],
            "asset_deleted": False,
        }
    ]
    assert body["references"]["changed"] == []
    assert body["identity_spec"]["added"] == {}
    assert body["identity_spec"]["removed"] == {}


def test_package_cross_character_rebind_guard(client):
    """Contract §10.3a: a package-referenced asset cannot serve another character."""
    project = _project(client)
    character = _character(client, project["id"])
    other = _character(client, project["id"], name="陈昊")
    asset = _upload_asset(client, project["id"], name="共享参考.png")
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    bind = client.post(
        f"{url}/references",
        json={"asset_id": asset["id"], "role": "front", "version": token},
    )
    assert bind.status_code == 201, bind.text
    # Binding the asset into another character's package matrix is refused.
    other_package = _create_package(client, project["id"], other["id"])
    other_version = other_package["versions"][0]["id"]
    other_bind = client.post(
        f"{_package_url(project['id'], other['id'])}/versions/{other_version}/references",
        json={"asset_id": asset["id"], "role": "front", "version": 1},
    )
    assert other_bind.status_code == 409
    # The asset is not yet a CharacterReference: bind it to a second character
    # must be refused because the package version of the first one references it.
    rebound = client.post(
        f"/api/v1/characters/{other['id']}/references",
        json={"asset_id": asset["id"], "angle": "front"},
    )
    assert rebound.status_code == 409
    # Unbinding the package slot frees the asset again.
    reference_id = bind.json()["id"]
    unbind = client.request(
        "DELETE", f"{url}/references/{reference_id}", json={"version": token + 1}
    )
    assert unbind.status_code == 204
    rebound = client.post(
        f"/api/v1/characters/{other['id']}/references",
        json={"asset_id": asset["id"], "angle": "front"},
    )
    assert rebound.status_code == 201


def test_package_outfit_delete_guard(client):
    """Contract §10.4: an outfit bound to a version cannot be deleted."""
    project = _project(client)
    character = _character(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    url = f"{_package_url(project['id'], character['id'])}/versions/{version_id}"
    outfit = _outfit(client, project["id"], character["id"])
    bind = client.post(
        f"{url}/outfits",
        json={"outfit_id": outfit["id"], "version": token},
    )
    assert bind.status_code == 201, bind.text
    deleted = client.delete(f"/api/v1/outfits/{outfit['id']}")
    assert deleted.status_code == 409
    unbind = client.request(
        "DELETE", f"{url}/outfits/{outfit['id']}", json={"version": token + 1}
    )
    assert unbind.status_code == 204
    deleted = client.delete(f"/api/v1/outfits/{outfit['id']}")
    assert deleted.status_code == 204



def test_package_concurrent_default_outfit_one_winner(file_sessions):
    from app.models import (
        Character,
        CharacterModelPackage,
        CharacterModelPackageVersion,
        CharacterModelPackageVersionOutfit,
        Outfit,
        Project,
    )
    from app.services.character_packages import set_default_outfit

    db = file_sessions()
    project = Project(id="default-project", name="默认服装项目")
    character = Character(
        id="default-character",
        project_id=project.id,
        primary_name="角色",
        aliases=[],
        aliases_normalized=[],
        status="UPLOADED",
    )
    db.add(project)
    db.flush()
    db.add(character)
    db.flush()
    package = CharacterModelPackage(
        character_id=character.id, project_id=project.id, status="ACTIVE"
    )
    db.add(package)
    db.flush()
    version = CharacterModelPackageVersion(
        package_id=package.id, version_number=1, status="DRAFT", spec_snapshot={}
    )
    db.add(version)
    db.flush()
    outfit_a = Outfit(
        id="outfit-a-id",
        project_id=project.id,
        character_id=character.id,
        name="校服",
        reference_asset_ids=[],
    )
    db.add(outfit_a)
    db.flush()
    outfit_b = Outfit(
        id="outfit-b-id",
        project_id=project.id,
        character_id=character.id,
        name="常服",
        reference_asset_ids=[],
    )
    db.add(outfit_b)
    db.flush()
    relation_a = CharacterModelPackageVersionOutfit(
        version_id=version.id, outfit_id=outfit_a.id, sort_order=0
    )
    db.add(relation_a)
    db.flush()
    db.add(
        CharacterModelPackageVersionOutfit(
            version_id=version.id, outfit_id=outfit_b.id, sort_order=1
        )
    )
    db.commit()
    token = version.version
    db.close()

    errors: dict[str, HTTPException] = {}
    results = _concurrent_pair(
        file_sessions,
        lambda db: set_default_outfit(
            db, project.id, character.id, version.id, outfit_a.id,
            is_default=True, token=token,
        ),
        lambda db: set_default_outfit(
            db, project.id, character.id, version.id, outfit_b.id,
            is_default=True, token=token,
        ),
        errors,
    )
    assert sum(1 for value in results.values() if value is not None) == 1, errors
    db = file_sessions()
    defaults = list(
        db.scalars(
            select(CharacterModelPackageVersionOutfit.outfit_id).where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.is_default.is_(True),
            )
        )
    )
    assert len(defaults) == 1
    db.close()


def test_package_second_default_candidate_keeps_in_production(
    client, db_session, monkeypatch
):
    """Default inheritance must accept a published version already IN_PRODUCTION."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project, character, _front, outfit, o_asset, version_id = _package_published_fixture(
        client, db_session, monkeypatch
    )
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201, batch.text
    payload = {
        "model_alias": "image.nano_banana_2",
        "resolution": "1K",
        "storyboard_version": page.storyboard_version,
        "reference_selections": {
            character["id"]: {
                "outfit_id": outfit["id"],
                "outfit_asset_id": o_asset["id"],
            }
        },
    }
    first = client.post(f"/api/v1/batches/{batch.json()['id']}/candidates", json=payload)
    assert first.status_code == 202, first.text
    second = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates", json=payload
    )
    assert second.status_code == 202, second.text
    db_session.expire_all()
    from app.models import CharacterModelPackageVersion

    assert (
        db_session.get(CharacterModelPackageVersion, version_id).status
        == "IN_PRODUCTION"
    )
    facts = second.json()["candidate"]["prompt_snapshot"]["character_packages"][
        character["id"]
    ]
    assert facts["package_version_id"] == version_id


def test_package_default_resolution_fills_omitted_outfit_asset(
    client, db_session, monkeypatch
):
    """Omitted outfit_asset_id inherits the first live reference of the default outfit."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    _skip_page_readiness(monkeypatch)
    project, character, _front, outfit, o_asset, _version_id = (
        _package_published_fixture(client, db_session, monkeypatch)
    )
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    batch = client.post(f"/api/v1/pages/{page.id}/batches")
    assert batch.status_code == 201, batch.text
    queued = client.post(
        f"/api/v1/batches/{batch.json()['id']}/candidates",
        json={
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
            "storyboard_version": page.storyboard_version,
            "reference_selections": {},
        },
    )
    assert queued.status_code == 202, queued.text
    selection = queued.json()["candidate"]["prompt_snapshot"]["reference_selections"][
        character["id"]
    ]
    assert selection["outfit_id"] == outfit["id"]
    assert selection["outfit_asset_id"] == o_asset["id"]


def _workflow_generate_candidate(db_session, monkeypatch, page_id: str, project_id: str):
    from app.config import get_settings
    from app.services.provider_presets import ensure_provider_presets
    from app.services.workflow_engine import (
        approve_node,
        create_workflow_run,
        default_graph,
        publish_workflow,
    )
    from app.models import WorkflowDefinition, WorkflowNodeRun

    ensure_provider_presets(db_session, get_settings(), auto_commit=True)
    workflow = WorkflowDefinition(
        project_id=project_id,
        name="角色包工作流生成",
        draft_graph=default_graph(),
        is_active=True,
    )
    db_session.add(workflow)
    db_session.commit()
    publish_workflow(db_session, workflow)
    run = create_workflow_run(
        db_session,
        workflow,
        scope_type="PAGE",
        scope_id=page_id,
        start_node_ids=["generate"],
        stop_node_ids=["generate"],
    )
    node_run = db_session.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.status == "WAITING_APPROVAL",
        )
    )
    assert node_run is not None
    monkeypatch.setattr(
        "app.services.workflow_engine.enqueue_job", lambda db, job: job
    )
    # 本辅助只验证包冻结语义；统一 readiness 门禁（无风格时 409）由
    # test_approve_node_enforces_readiness_and_freezes_scene_snapshot 锁定。
    monkeypatch.setattr(
        "app.services.workflow_engine.lifecycle.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )
    approve_node(
        db_session,
        run.id,
        node_run.node_id,
        image_model_alias="image.nano_banana_2",
        resolution="1K",
    )
    from app.models import PageCandidate

    return db_session.scalar(
        select(PageCandidate).where(PageCandidate.page_id == page_id)
    )


def test_workflow_approve_freezes_published_package(
    client, db_session, monkeypatch
):
    """Workflow GENERATE must freeze the published package, not live Character rows."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, character, front, outfit, o_asset, version_id = _package_published_fixture(
        client, db_session, monkeypatch
    )
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    candidate = _workflow_generate_candidate(
        db_session, monkeypatch, page.id, project["id"]
    )
    assert candidate is not None
    facts = candidate.prompt_snapshot["character_packages"][character["id"]]
    assert facts["package_version_id"] == version_id
    assert facts["character_asset_id"] == front["id"]
    assert facts["outfit_id"] == outfit["id"]
    assert facts["outfit_asset_id"] == o_asset["id"]
    db_session.expire_all()
    from app.models import CharacterModelPackageVersion

    assert (
        db_session.get(CharacterModelPackageVersion, version_id).status
        == "IN_PRODUCTION"
    )


def test_workflow_approve_without_legacy_character_reference(
    client, db_session, monkeypatch
):
    """A published package reference is enough; CharacterReference is not required."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, character, front, _outfit, _o_asset, version_id = (
        _package_published_fixture(client, db_session, monkeypatch)
    )
    from app.models import CharacterReference

    for reference in db_session.scalars(
        select(CharacterReference).where(
            CharacterReference.character_id == character["id"]
        )
    ):
        db_session.delete(reference)
    db_session.commit()
    _chapter, page = _generation_page(db_session, project["id"], character["id"])
    candidate = _workflow_generate_candidate(
        db_session, monkeypatch, page.id, project["id"]
    )
    assert candidate is not None
    facts = candidate.prompt_snapshot["character_packages"][character["id"]]
    assert facts["package_version_id"] == version_id
    assert facts["character_asset_id"] == front["id"]


def test_legacy_character_bind_locks_asset(client, monkeypatch):
    """The CharacterReference bind path shares the same asset ownership lock."""
    from app.api.routes import characters as characters_route

    locked: list[str] = []
    real = characters_route.lock_asset_for_ownership

    def wrapped(db, asset_id):
        locked.append(asset_id)
        return real(db, asset_id)

    monkeypatch.setattr(characters_route, "lock_asset_for_ownership", wrapped)
    project = _project(client)
    character = _character(client, project["id"])
    asset = _upload_asset(client, project["id"])
    bind = client.post(
        f"/api/v1/characters/{character['id']}/references",
        json={"asset_id": asset["id"], "angle": "front"},
    )
    assert bind.status_code == 201, bind.text
    assert locked == [asset["id"]]


def test_package_bind_and_cleanup_lock_the_asset(client, db_session, monkeypatch):
    """Bind and DRAFT cleanup must take the shared asset-level ownership lock."""
    from app.services import character_packages as packages

    locked: list[str] = []
    real = packages.lock_asset_for_ownership

    def wrapped(db, asset_id):
        locked.append(asset_id)
        return real(db, asset_id)

    monkeypatch.setattr(packages, "lock_asset_for_ownership", wrapped)
    project = _project(client)
    character = _character(client, project["id"])
    asset = _upload_asset(client, project["id"])
    package = _create_package(client, project["id"], character["id"])
    version_id = package["versions"][0]["id"]
    token = package["versions"][0]["version"]
    bind = client.post(
        f"{_package_url(project['id'], character['id'])}/versions/{version_id}/references",
        json={"asset_id": asset["id"], "role": "front", "version": token},
    )
    assert bind.status_code == 201, bind.text
    assert asset["id"] in locked
    locked.clear()
    packages.detach_draft_package_references_for_asset(db_session, asset["id"])
    db_session.commit()
    assert locked == [asset["id"]]
    remaining = db_session.scalar(
        select(packages.CharacterModelPackageVersionReference.id).where(
            packages.CharacterModelPackageVersionReference.asset_id == asset["id"]
        )
    )
    assert remaining is None


def test_package_bind_and_asset_delete_keep_draft_detached(file_sessions):
    """Bind vs asset cleanup must not leave a DRAFT slot on a deleted asset."""
    from app.models import (
        Asset,
        Character,
        CharacterModelPackage,
        CharacterModelPackageVersion,
        CharacterModelPackageVersionReference,
        Project,
        utcnow,
    )
    from app.services.character_packages import (
        bind_reference,
        detach_draft_package_references_for_asset,
    )

    db = file_sessions()
    project = Project(id="lock-project", name="资产锁项目")
    character = Character(
        id="lock-character",
        project_id=project.id,
        primary_name="锁角色",
        aliases=[],
        aliases_normalized=[],
        status="UPLOADED",
    )
    asset = Asset(
        id="lock-asset",
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="front.png",
        storage_key="lock/front.png",
        mime_type="image/png",
        byte_size=100,
        sha256="d" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db.add(project)
    db.flush()
    db.add(character)
    db.flush()
    db.add(asset)
    db.flush()
    package = CharacterModelPackage(
        character_id=character.id,
        project_id=project.id,
        identity_spec={},
        visual_spec={},
        negative_constraints=[],
        status="ACTIVE",
    )
    db.add(package)
    db.flush()
    version = CharacterModelPackageVersion(
        package_id=package.id,
        version_number=1,
        status="DRAFT",
        spec_snapshot={
            "identity_spec": {},
            "visual_spec": {},
            "negative_constraints": [],
            "frozen_from": "package",
        },
    )
    db.add(version)
    db.commit()
    project_id, character_id, version_id, asset_id = (
        project.id,
        character.id,
        version.id,
        asset.id,
    )
    db.close()

    errors: dict[str, HTTPException] = {}

    def do_bind(session):
        return bind_reference(
            session,
            project_id,
            character_id,
            version_id,
            asset_id=asset_id,
            role="front",
            token=1,
        )

    def do_delete(session):
        detach_draft_package_references_for_asset(session, asset_id)
        row = session.get(Asset, asset_id)
        row.deleted_at = utcnow()
        session.commit()
        return True

    _concurrent_pair(file_sessions, do_bind, do_delete, errors)
    verify = file_sessions()
    gone = verify.get(Asset, asset_id)
    refs = list(
        verify.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.asset_id == asset_id
            )
        )
    )
    if gone is not None and gone.deleted_at is not None:
        assert refs == []
    verify.close()


def test_package_concurrent_cross_character_asset_bind_single_winner(file_sessions):
    """The same unbound asset cannot enter two characters' package matrices."""
    from app.models import (
        Asset,
        Character,
        CharacterModelPackage,
        CharacterModelPackageVersion,
        CharacterModelPackageVersionReference,
        Project,
    )
    from app.services.character_packages import bind_reference

    db = file_sessions()
    project = Project(id="cross-lock-project", name="跨角色资产锁")
    db.add(project)
    db.flush()
    characters = []
    versions = []
    for suffix in ("a", "b"):
        character = Character(
            id=f"cross-lock-character-{suffix}",
            project_id=project.id,
            primary_name=f"角色{suffix}",
            aliases=[],
            aliases_normalized=[],
            status="UPLOADED",
        )
        db.add(character)
        db.flush()
        package = CharacterModelPackage(
            character_id=character.id,
            project_id=project.id,
            identity_spec={},
            visual_spec={},
            negative_constraints=[],
            status="ACTIVE",
        )
        db.add(package)
        db.flush()
        version = CharacterModelPackageVersion(
            package_id=package.id,
            version_number=1,
            status="DRAFT",
            spec_snapshot={
                "identity_spec": {},
                "visual_spec": {},
                "negative_constraints": [],
                "frozen_from": "package",
            },
        )
        db.add(version)
        db.flush()
        characters.append(character.id)
        versions.append(version.id)
    asset = Asset(
        id="cross-lock-asset",
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="shared.png",
        storage_key="cross/shared.png",
        mime_type="image/png",
        byte_size=100,
        sha256="e" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db.add(asset)
    db.commit()
    project_id = project.id
    asset_id = asset.id
    db.close()

    errors: dict[str, HTTPException] = {}
    results = _concurrent_pair(
        file_sessions,
        lambda session: bind_reference(
            session,
            project_id,
            characters[0],
            versions[0],
            asset_id=asset_id,
            role="front",
            token=1,
        ),
        lambda session: bind_reference(
            session,
            project_id,
            characters[1],
            versions[1],
            asset_id=asset_id,
            role="front",
            token=1,
        ),
        errors,
    )
    winners = [key for key, value in results.items() if value is not None]
    assert len(winners) == 1, errors
    verify = file_sessions()
    refs = list(
        verify.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.asset_id == asset_id
            )
        )
    )
    assert len(refs) == 1
    verify.close()


def test_package_detach_retries_sqlite_lock(client, db_session, monkeypatch):
    """DRAFT cleanup must roll back and retry on SQLITE_BUSY instead of 500."""
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from app.services import character_packages as packages

    project = _project(client)
    asset = _upload_asset(client, project["id"])
    calls = {"n": 0}
    real = packages.lock_asset_for_ownership

    def flaky(db, asset_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError(
                "statement", {}, sqlite3.OperationalError("database is locked")
            )
        return real(db, asset_id)

    monkeypatch.setattr(packages, "lock_asset_for_ownership", flaky)
    packages.detach_draft_package_references_for_asset(db_session, asset["id"])
    assert calls["n"] == 2


def test_package_detach_lock_exhaustion_is_controlled_409(
    client, db_session, monkeypatch
):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from app.services import character_packages as packages

    project = _project(client)
    asset = _upload_asset(client, project["id"])

    def always_busy(db, asset_id):
        raise OperationalError(
            "statement", {}, sqlite3.OperationalError("database is locked")
        )

    monkeypatch.setattr(packages, "lock_asset_for_ownership", always_busy)
    monkeypatch.setattr(packages, "pause_before_ordinal_retry", lambda *_args: None)
    with pytest.raises(HTTPException) as raised:
        packages.detach_draft_package_references_for_asset(db_session, asset["id"])
    assert raised.value.status_code == 409
    assert raised.value.detail == "素材绑定清理冲突，请稍后重试"


def test_legacy_bind_retries_sqlite_lock(client, monkeypatch):
    """Legacy CharacterReference bind shares the lock-retry boundary."""
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from app.api.routes import characters as characters_route

    project = _project(client)
    character = _character(client, project["id"])
    asset = _upload_asset(client, project["id"])
    calls = {"n": 0}
    real = characters_route.lock_asset_for_ownership

    def flaky(db, asset_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError(
                "statement", {}, sqlite3.OperationalError("database is locked")
            )
        return real(db, asset_id)

    monkeypatch.setattr(characters_route, "lock_asset_for_ownership", flaky)
    bind = client.post(
        f"/api/v1/characters/{character['id']}/references",
        json={"asset_id": asset["id"], "angle": "front"},
    )
    assert bind.status_code == 201, bind.text
    assert calls["n"] == 2

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app.config import get_settings


def test_create_and_update_project(client):
    response = client.post(
        "/api/v1/projects",
        json={"name": "雾港来信", "last_image_model_alias": "image.nano_banana_2"},
    )
    assert response.status_code == 201
    project = response.json()
    assert project["workflow_mode"] == "SEMI_AUTO"
    assert project["draft_resolution"] == "1K"

    updated = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "version": project["version"],
            "last_image_model_alias": "image.nano_banana_pro",
            "default_concurrency": 2,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["last_image_model_alias"] == "image.nano_banana_pro"
    assert updated.json()["version"] == 2


def test_project_optimistic_lock(client):
    project = client.post("/api/v1/projects", json={"name": "测试项目"}).json()
    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": 99, "name": "过期修改"},
    )
    assert response.status_code == 409


def test_model_registry_reports_preview_resolution(client):
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    models = {item["logical_alias"]: item for item in response.json()}
    assert models["text.fast"]["model_id"] == "gemini-3.5-flash"
    assert models["image.nano_banana_2"]["model_id"] == "gemini-3.1-flash-image"
    assert models["image.nano_banana_pro"]["model_id"] == "gemini-3-pro-image"
    assert models["image.nano_banana_2"]["preview_resolutions"] == ["4K"]


def test_upload_image_is_validated_and_registered(client, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "素材测试"}).json()
    image_bytes = BytesIO()
    Image.new("RGB", (32, 48), "white").save(image_bytes, format="PNG")

    with TemporaryDirectory() as directory:
        monkeypatch.setattr(get_settings(), "upload_root", Path(directory))
        response = client.post(
            "/api/v1/assets/upload",
            data={"project_id": project["id"], "kind": "character"},
            files={"file": ("hero.png", image_bytes.getvalue(), "image/png")},
        )
        assert response.status_code == 201
        asset = response.json()
        assert asset["width"] == 32
        assert asset["height"] == 48
        assert asset["kind"] == "character"
        assert list(Path(directory).rglob("*.png"))
    assert not Path(directory).exists()

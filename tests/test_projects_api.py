from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import GenerationJob


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
    assert project["last_image_model_alias"] is None
    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": 99, "name": "过期修改"},
    )
    assert response.status_code == 409


def test_archiving_project_cancels_non_terminal_jobs(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "待删除项目"}).json()
    jobs = [
        GenerationJob(
            project_id=project["id"],
            target_type="CHAPTER",
            target_id=f"target-{status.value}",
            job_type="SOURCE_PARSE",
            status=status,
        )
        for status in (JobStatus.WAITING, JobStatus.GENERATING, JobStatus.COMPLETED)
    ]
    db_session.add_all(jobs)
    db_session.commit()

    response = client.delete(
        f"/api/v1/projects/{project['id']}", params={"confirm_name": "待删除项目"}
    )

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(GenerationJob, jobs[0].id).status == JobStatus.CANCELLED
    assert db_session.get(GenerationJob, jobs[1].id).status == JobStatus.CANCELLED
    assert db_session.get(GenerationJob, jobs[2].id).status == JobStatus.COMPLETED


def test_model_registry_reports_preview_resolution(client):
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    models = {item["logical_alias"]: item for item in response.json()}
    assert models["text.fast"]["model_id"] == "gemini-3.5-flash"
    assert models["image.nano_banana_2"]["model_id"] == "gemini-3.1-flash-image"
    assert models["image.nano_banana_pro"]["model_id"] == "gemini-3-pro-image-preview"
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
        assert asset["kind"] == "CHARACTER_REFERENCE"
        assert asset["thumbnail_url"].endswith(f"/{asset['id']}/thumbnail/640")
        thumbnail = client.get(asset["thumbnail_url"])
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"].startswith("image/webp")
        with Image.open(BytesIO(thumbnail.content)) as preview:
            assert preview.size == (32, 48)
            assert preview.format == "WEBP"
        assert list(Path(directory).rglob("320.webp"))
        assert list(Path(directory).rglob("640.webp"))
        changed = client.patch(
            f"/api/v1/assets/{asset['id']}", json={"kind": "STYLE_REFERENCE"}
        )
        assert changed.status_code == 200
        assert changed.json()["kind"] == "STYLE_REFERENCE"
        assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 204
        assert client.get(f"/api/v1/assets?project_id={project['id']}").json() == []
        restored = client.post(
            "/api/v1/assets/upload",
            data={"project_id": project["id"], "kind": "style"},
            files={"file": ("hero-restored.png", image_bytes.getvalue(), "image/png")},
        )
        assert restored.status_code == 201
        assert restored.json()["id"] == asset["id"]
        assert restored.json()["kind"] == "STYLE_REFERENCE"
        assert [item["id"] for item in client.get(
            f"/api/v1/assets?project_id={project['id']}"
        ).json()] == [asset["id"]]
        assert list(Path(directory).rglob("*.png"))
    assert not Path(directory).exists()


def test_reference_upload_rejects_non_image_content(client):
    project = client.post("/api/v1/projects", json={"name": "文本素材"}).json()
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project["id"], "kind": "STYLE_REFERENCE"},
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 415


def test_reupload_repairs_active_asset_with_missing_file(client, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "素材自愈"}).json()
    image_bytes = BytesIO()
    Image.new("RGB", (24, 36), "white").save(image_bytes, format="PNG")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        monkeypatch.setattr(get_settings(), "upload_root", root)
        uploaded = client.post(
            "/api/v1/assets/upload",
            data={"project_id": project["id"], "kind": "character"},
            files={"file": ("hero.png", image_bytes.getvalue(), "image/png")},
        ).json()
        next(root.rglob("*.png")).unlink()

        repaired = client.post(
            "/api/v1/assets/upload",
            data={"project_id": project["id"], "kind": "character"},
            files={"file": ("hero-again.png", image_bytes.getvalue(), "image/png")},
        )

        assert repaired.status_code == 201, repaired.text
        assert repaired.json()["id"] == uploaded["id"]
        assert client.get(repaired.json()["content_url"]).status_code == 200


def test_restoring_asset_tolerates_locked_stale_file(client, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "素材恢复"}).json()
    image_bytes = BytesIO()
    Image.new("RGB", (24, 36), "white").save(image_bytes, format="PNG")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        monkeypatch.setattr(get_settings(), "upload_root", root)
        uploaded = client.post(
            "/api/v1/assets/upload",
            data={"project_id": project["id"], "kind": "style"},
            files={"file": ("style.png", image_bytes.getvalue(), "image/png")},
        ).json()
        stale_path = next(root.rglob("*.png")).resolve()
        assert client.delete(f"/api/v1/assets/{uploaded['id']}").status_code == 204

        original_unlink = Path.unlink

        def locked_stale_file(path: Path, *args, **kwargs):
            if path.resolve() == stale_path:
                raise PermissionError("locked")
            return original_unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", locked_stale_file)
            restored = client.post(
                "/api/v1/assets/upload",
                data={"project_id": project["id"], "kind": "style"},
                files={"file": ("style-restored.png", image_bytes.getvalue(), "image/png")},
            )

        assert restored.status_code == 201, restored.text
        assert restored.json()["id"] == uploaded["id"]
        assert client.get(restored.json()["content_url"]).status_code == 200

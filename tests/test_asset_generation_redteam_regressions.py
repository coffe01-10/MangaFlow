"""Red-team remediation regressions for the asset-generation surface.

Covers issue #157 (approve-reference §10.3a cross-character package guard),
#158 (delete_outfit must detach DRAFT package matrix rows of the assets it
soft-deletes), the #126 tail (approve_style_test / activate_style must fail
closed on soft-deleted test images) and #138 (a late STYLE_TEST completion
must not regress CONFIRMED/ACTIVE styles; concurrent activate_style calls
keep exactly one ACTIVE style). Real PostgreSQL concurrency and real
provider calls stay NOT RUN: the concurrency test uses file-backed SQLite
sessions and the STYLE_TEST handler runs against the offline fake adapter.
"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.domain.states import Resolution
from app.models import (
    Asset,
    AssetCandidate,
    CharacterModelPackageVersionReference,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    Project,
    StyleProfile,
    utcnow,
)
from app.services.provider_presets import ensure_provider_presets


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


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


def _upload_reference(client, project_id: str, name: str) -> dict:
    response = client.post(
        "/api/v1/assets/upload",
        files={"file": (name, _png_bytes((10, 20, 30)), "image/png")},
        data={"project_id": project_id, "kind": "CHARACTER_REFERENCE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _package_with_draft(client, project_id: str, character_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/characters/{character_id}/package", json={}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _orm_asset(db, project_id: str, *, kind: str, sha256: str, source: str) -> Asset:
    asset = Asset(
        project_id=project_id,
        kind=kind,
        original_name=f"{kind}.png",
        storage_key=f"redteam/{kind}-{sha256[:6]}.png",
        mime_type="image/png",
        byte_size=64,
        sha256=sha256,
        source=source,
        status="GENERATED" if source != "USER_UPLOAD" else "UPLOADED",
    )
    db.add(asset)
    db.flush()
    return asset


def _orm_candidate(db, batch_id: str, asset_id: str, *, variant: str) -> AssetCandidate:
    candidate = AssetCandidate(
        batch_id=batch_id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant=variant,
        status="READY",
    )
    db.add(candidate)
    db.flush()
    candidate.asset_id = asset_id
    db.commit()
    return candidate


def _orm_batch(db, project_id: str, *, target_type: str, target_id: str, kind: str):
    batch = GenerationBatch(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        generation_kind=kind,
        ordinal=1,
    )
    db.add(batch)
    db.commit()
    return batch


@pytest.fixture
def style_sessions(tmp_path):
    """File-backed SQLite for the concurrent activate_style test (P2-8 pattern)."""
    db_path = tmp_path / "style_activation_concurrency.db"
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


# --- issue #157: approve_asset_reference §10.3a guard -------------------------


def test_approve_reference_refuses_asset_in_foreign_package_matrix(client, db_session):
    """A sheet bound in X's package matrix must not become Y's reference."""
    project = _project(client, "跨角色包防护")
    character_x = _character(client, project["id"], "林澈")
    character_y = _character(client, project["id"], "陈昊")
    asset = _upload_reference(client, project["id"], "shared-sheet.png")

    # X binds the asset into the package DRAFT matrix — no legacy row exists.
    package = _package_with_draft(client, project["id"], character_x["id"])
    version = package["versions"][0]
    base_url = (
        f"/api/v1/projects/{project['id']}/characters/{character_x['id']}"
        f"/package/versions/{version['id']}"
    )
    bind = client.post(
        f"{base_url}/references",
        json={"asset_id": asset["id"], "role": "front", "version": version["version"]},
    )
    assert bind.status_code == 201, bind.text

    # Y's ready SHEET candidate points at the same asset row (sha256 dedup).
    batch = _orm_batch(
        db_session,
        project["id"],
        target_type="CHARACTER",
        target_id=character_y["id"],
        kind="CHARACTER",
    )
    candidate = _orm_candidate(
        db_session, batch.id, asset["id"], variant="SHEET"
    )

    refused = client.post(
        f"/api/v1/asset-candidates/{candidate.id}/approve-reference",
        json={"character_id": character_y["id"], "bind_character_reference": True},
    )
    assert refused.status_code == 409, refused.text
    assert "模型包版本" in refused.json()["detail"]
    # Y got no legacy row and X's matrix is untouched.
    assert (
        db_session.scalar(
            select(CharacterReference).where(
                CharacterReference.character_id == character_y["id"],
                CharacterReference.asset_id == asset["id"],
            )
        )
        is None
    )
    assert (
        db_session.scalar(
            select(CharacterModelPackageVersionReference.id).where(
                CharacterModelPackageVersionReference.asset_id == asset["id"]
            )
        )
        is not None
    )

    # Unbinding X's slot frees the asset: the same approval now succeeds.
    unbind = client.request(
        "DELETE",
        f"{base_url}/references/{bind.json()['id']}",
        json={"version": version["version"] + 1},
    )
    assert unbind.status_code == 204, unbind.text
    approved = client.post(
        f"/api/v1/asset-candidates/{candidate.id}/approve-reference",
        json={"character_id": character_y["id"], "bind_character_reference": True},
    )
    assert approved.status_code == 200, approved.text


# --- issue #158: delete_outfit clears DRAFT package matrix rows ---------------


def test_delete_outfit_detaches_draft_package_references(client, db_session):
    """Outfit teardown physically clears DRAFT slot rows of the dead assets."""
    project = _project(client, "服装清理矩阵")
    character = _character(client, project["id"], "林澈")
    package = _package_with_draft(client, project["id"], character["id"])
    version = package["versions"][0]
    base_url = (
        f"/api/v1/projects/{project['id']}/characters/{character['id']}"
        f"/package/versions/{version['id']}"
    )
    outfit = client.post(
        f"/api/v1/projects/{project['id']}/outfits",
        json={
            "character_id": character["id"],
            "name": "常服",
            "components": {"top": "衬衫"},
            "reference_asset_ids": [],
        },
    )
    assert outfit.status_code == 201, outfit.text

    batch = _orm_batch(
        db_session,
        project["id"],
        target_type="OUTFIT",
        target_id=outfit.json()["id"],
        kind="OUTFIT",
    )
    generated = _orm_asset(
        db_session, project["id"], kind="outfit", sha256="a" * 64, source="AI_GENERATED"
    )
    candidate = _orm_candidate(
        db_session, batch.id, generated.id, variant="OUTFIT_SHEET"
    )
    bind = client.post(
        f"{base_url}/references",
        json={
            "asset_id": generated.id,
            "role": "front",
            "version": version["version"],
        },
    )
    assert bind.status_code == 201, bind.text

    deleted = client.delete(f"/api/v1/outfits/{outfit.json()['id']}")
    assert deleted.status_code == 204, deleted.text

    db_session.expire_all()
    assert db_session.get(Asset, generated.id).deleted_at is not None
    assert candidate.deleted_at is not None
    # Contract §10.3 item 3: DRAFT relation rows vanish with the asset.
    assert (
        db_session.scalar(
            select(CharacterModelPackageVersionReference.id).where(
                CharacterModelPackageVersionReference.asset_id == generated.id
            )
        )
        is None
    )

    # The slot rebinds and the version publishes — no dead-reference 409/422,
    # and the detach bumped the parent token exactly once (bind + detach).
    detail = client.get(
        f"/api/v1/projects/{project['id']}/characters/{character['id']}/package"
    ).json()
    current_token = detail["versions"][0]["version"]
    assert current_token == version["version"] + 2
    fresh = _upload_reference(client, project["id"], "fresh-front.png")
    rebind = client.post(
        f"{base_url}/references",
        json={"asset_id": fresh["id"], "role": "front", "version": current_token},
    )
    assert rebind.status_code == 201, rebind.text
    published = client.post(f"{base_url}/publish", json={})
    assert published.status_code == 200, published.text


# --- issue #126 tail: deleted style-test images fail closed --------------------


def test_approve_style_test_refuses_deleted_asset(client, db_session):
    """A soft-deleted test image must not be approvable."""
    project = _project(client, "风格审批守卫")
    reference = _orm_asset(
        db_session,
        project["id"],
        kind="STYLE_REFERENCE",
        sha256="b" * 64,
        source="USER_UPLOAD",
    )
    style = client.post(
        f"/api/v1/projects/{project['id']}/styles",
        json={
            "name": "彩稿",
            "color_mode": "color",
            "reference_asset_ids": [reference.id],
        },
    )
    assert style.status_code == 201, style.text
    batch = _orm_batch(
        db_session,
        project["id"],
        target_type="STYLE",
        target_id=style.json()["id"],
        kind="STYLE_TEST",
    )
    test_asset = _orm_asset(
        db_session, project["id"], kind="style_test", sha256="c" * 64,
        source="AI_GENERATED",
    )
    candidate = _orm_candidate(
        db_session, batch.id, test_asset.id, variant="STYLE_TEST"
    )
    test_asset.deleted_at = utcnow()
    db_session.commit()

    refused = client.post(
        f"/api/v1/styles/{style.json()['id']}/style-test-approve",
        json={
            "candidate_id": candidate.id,
            "approved": True,
            "version": style.json()["version"],
        },
    )
    assert refused.status_code == 409, refused.text
    assert "已被删除" in refused.json()["detail"]
    db_session.expire_all()
    assert (
        db_session.get(StyleProfile, style.json()["id"]).profile.get(
            "test_image_approved"
        )
        is None
    )


def test_activate_style_refuses_deleted_test_image(client, db_session):
    """Approval flags must not outlive the approved test image at activation."""
    project = _project(client, "风格激活守卫")
    reference = _orm_asset(
        db_session,
        project["id"],
        kind="STYLE_REFERENCE",
        sha256="d" * 64,
        source="USER_UPLOAD",
    )
    style = client.post(
        f"/api/v1/projects/{project['id']}/styles",
        json={
            "name": "彩稿",
            "color_mode": "color",
            "profile": {"palette_confirmed": True, "test_image_approved": True},
            "reference_asset_ids": [reference.id],
        },
    )
    assert style.status_code == 201, style.text
    batch = _orm_batch(
        db_session,
        project["id"],
        target_type="STYLE",
        target_id=style.json()["id"],
        kind="STYLE_TEST",
    )
    test_asset = _orm_asset(
        db_session, project["id"], kind="style_test", sha256="e" * 64,
        source="AI_GENERATED",
    )
    candidate = _orm_candidate(
        db_session, batch.id, test_asset.id, variant="STYLE_TEST"
    )
    # The flags say approved while the recorded image is already soft-deleted.
    style_row = db_session.get(StyleProfile, style.json()["id"])
    style_row.profile = {
        **style_row.profile,
        "test_candidate_id": candidate.id,
    }
    test_asset.deleted_at = utcnow()
    db_session.commit()

    refused = client.post(
        f"/api/v1/projects/{project['id']}/styles/{style.json()['id']}/activate"
    )
    assert refused.status_code == 409, refused.text
    assert "已被删除" in refused.json()["detail"]
    db_session.expire_all()
    assert db_session.get(StyleProfile, style.json()["id"]).status != "ACTIVE"


# --- issue #138-A: late STYLE_TEST completion must not regress a style --------


def _seed_style_test_job(db, tmp_path, monkeypatch, *, style_status: str):
    from app.domain.states import JobStatus

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    project = Project(name="风格测试回归")
    db.add(project)
    db.flush()
    reference = Asset(
        project_id=project.id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="redteam/style-ref.png",
        mime_type="image/png",
        byte_size=len(_png_bytes((90, 90, 90))),
        sha256="1" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db.add(reference)
    db.flush()
    reference_file = settings.upload_root / reference.storage_key
    reference_file.parent.mkdir(parents=True, exist_ok=True)
    reference_file.write_bytes(_png_bytes((90, 90, 90)))
    style = StyleProfile(
        project_id=project.id,
        name="回归风格",
        color_mode="color",
        profile={"reference_asset_ids": [reference.id], "palette_confirmed": True},
        status=style_status,
    )
    db.add(style)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        generation_kind="STYLE_TEST",
        ordinal=1,
    )
    db.add(batch)
    db.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="STYLE_TEST",
        status="QUEUED",
    )
    db.add(candidate)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
    )
    db.add(job)
    db.flush()
    candidate.job_id = job.id
    job.attempt_count += 1
    db.info["job_id"] = job.id
    db.commit()
    ensure_provider_presets(db, get_settings(), auto_commit=False)
    db.commit()
    return style, candidate, job


class _FakeImageAdapter:
    def __init__(self, color: tuple[int, int, int]) -> None:
        self.color = color

    def generate_asset(self, request):
        from app.model_adapters.base import ModelResponse

        return ModelResponse(
            model_id="fake-vertex-image",
            request_id="fake-style-test",
            usage={"fake": True},
            images=(_png_bytes(self.color),),
        )


@pytest.mark.parametrize("style_status", ["ACTIVE", "CONFIRMED"])
def test_late_style_test_completion_keeps_judged_style(
    db_session, monkeypatch, tmp_path, style_status
):
    """A late STYLE_TEST job records its result without status regression."""
    from app.worker_tasks import _run_asset_generate

    style, candidate, job = _seed_style_test_job(
        db_session, tmp_path, monkeypatch, style_status=style_status
    )
    version_before = style.version
    monkeypatch.setattr(
        "app.worker_tasks._adapter", lambda _alias: _FakeImageAdapter((200, 210, 220))
    )
    _run_asset_generate(db_session, job)
    db_session.commit()

    db_session.expire_all()
    assert style.status == style_status
    assert style.version == version_before
    # The paid result itself is still recorded: candidate READY with its asset.
    assert candidate.status == "READY"
    assert candidate.asset_id


def test_style_test_completion_marks_awaiting_style(
    db_session, monkeypatch, tmp_path
):
    """Positive control: DRAFT/ANALYZING styles still reach TEST_GENERATED."""
    from app.models import StyleStatus
    from app.worker_tasks import _run_asset_generate

    style, candidate, job = _seed_style_test_job(
        db_session, tmp_path, monkeypatch, style_status="DRAFT"
    )
    monkeypatch.setattr(
        "app.worker_tasks._adapter", lambda _alias: _FakeImageAdapter((201, 211, 221))
    )
    _run_asset_generate(db_session, job)
    db_session.commit()

    db_session.expire_all()
    assert style.status == StyleStatus.TEST_GENERATED
    assert candidate.status == "READY"


# --- issue #138-B: concurrent activate_style keeps one ACTIVE -----------------


def test_concurrent_style_activation_leaves_single_active(style_sessions, monkeypatch):
    """Two concurrent activations of different styles → exactly one ACTIVE."""
    from app.api.routes.asset_generation import activate_style

    monkeypatch.setattr(
        "app.services.character_packages.pause_before_ordinal_retry",
        lambda *_args: None,
    )
    db = style_sessions()
    project = Project(id="style-activation-project", name="并发激活项目")
    db.add(project)
    db.flush()
    style_ids = []
    for suffix in ("a", "b"):
        style = StyleProfile(
            id=f"style-{suffix}",
            project_id=project.id,
            name=f"风格{suffix}",
            color_mode="color",
            profile={"palette_confirmed": True, "test_image_approved": True},
            status="CONFIRMED",
        )
        db.add(style)
        db.flush()
        style_ids.append(style.id)
    db.commit()
    project_id = project.id
    db.close()

    # A session that caches the no-default project state and keeps its read
    # snapshot open emulates the loser of the activation race: its next
    # activate must re-read under the project lock instead of trusting the
    # stale pointer (the unlocked read-modify-write used to read previous=None
    # and leave a second ACTIVE style behind).
    stale = style_sessions()
    stale.get(Project, project_id)

    errors: dict[str, HTTPException] = {}

    def run(fn, label):
        session = style_sessions()
        try:
            return fn(session)
        except HTTPException as error:
            errors[label] = error
            return None
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            label: executor.submit(
                run,
                lambda session, style_id=style_id: activate_style(
                    project_id, style_id, session
                ),
                label,
            )
            for label, style_id in zip(("first", "second"), style_ids)
        }
        results = {label: future.result() for label, future in futures.items()}

    assert any(value is not None for value in results.values()), errors
    verify = style_sessions()
    active = list(
        verify.scalars(
            select(StyleProfile).where(
                StyleProfile.project_id == project_id,
                StyleProfile.status == "ACTIVE",
            )
        )
    )
    assert len(active) == 1
    assert verify.get(Project, project_id).default_style_id == active[0].id
    verify.close()

    # Deterministic stale-snapshot phase: the session holding the pre-race
    # project state activates the OTHER style; under the project lock it must
    # demote the current ACTIVE instead of stacking a second one.
    loser_style_id = next(sid for sid in style_ids if sid != active[0].id)
    late = activate_style(project_id, loser_style_id, stale)
    assert late.status == "ACTIVE"
    stale.close()

    verify = style_sessions()
    active_after = list(
        verify.scalars(
            select(StyleProfile).where(
                StyleProfile.project_id == project_id,
                StyleProfile.status == "ACTIVE",
            )
        )
    )
    assert len(active_after) == 1
    assert active_after[0].id == loser_style_id
    assert verify.get(Project, project_id).default_style_id == loser_style_id
    verify.close()

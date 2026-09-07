"""Route-side storage, liveness and CAS regressions for the Red Team sweep.

Covers the verified defects from issues #210/#211/#230/#235/#236/#237/#246
whose route halves live in uploads/asset_generation/workflow routes:

- upload resurrect is a version-CAS conditional UPDATE (409 on lost claim);
- byte-identical re-upload with a DIFFERENT requested kind is a 409;
- on-demand thumbnail regeneration enforces the configured pixel caps (422);
- reference teardown recomputes outfit/scene-asset/character status from
  remaining LIVE references only;
- approval gates preflight the backing blob, not just the row;
- favorite/delete-job route writes are conditional claims (409 on loss);
- retry of a job inside a terminal run answers 409;
- archived (soft-deleted) projects hide their object-id routes;
- next_page refuses to close batches when no following page exists;
- repair/upscale re-read the parent after taking the batch locks and
  inherit the parent's storyboard stamp (staleness is not laundered);
- repair rank guard rejects paid downgrades;
- page generation jobs lease the style profile's live reference images.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.sql.dml import Delete, Update

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    AssetCandidate,
    AssetStatus,
    Chapter,
    Character,
    CharacterReference,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    JobAssetReference,
    MangaPage,
    Outfit,
    PageCandidate,
    Project,
    Scene,
    SceneAsset,
    SceneAssetReference,
    StyleProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)


def _png_bytes(size: tuple[int, int] = (16, 12)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


_PNG_COUNTER = 0


def _unique_png_bytes() -> bytes:
    """Distinct bytes per call: uploads dedupe by (project, sha256)."""

    global _PNG_COUNTER
    _PNG_COUNTER += 1
    buffer = BytesIO()
    level = _PNG_COUNTER % 256
    Image.new("RGB", (8 + _PNG_COUNTER % 4, 8), (level, level, level)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def _uploads_to(monkeypatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path)
    monkeypatch.setattr(settings, "max_image_pixels", 2_000_000)
    monkeypatch.setattr(settings, "max_image_side", 4_096)


def _upload(client, project_id: str, kind: str, name: str = "参考图.png", *, unique=False):
    response = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project_id, "kind": kind},
        files={"file": (name, _unique_png_bytes() if unique else _png_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_tree(db_session, *, project_deleted=False):
    project = Project(name="存储清扫回归")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, storyboard_version=1)
    db_session.add(page)
    if project_deleted:
        project.deleted_at = utcnow()
    db_session.commit()
    return project, chapter, page


# ---------------------------------------------------------------------------
# #210-1 / #210-4: upload resurrect CAS + kind mismatch
# ---------------------------------------------------------------------------


def test_reupload_resurrects_tombstoned_asset_with_cas(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)

    created = _upload(client, project.id, "character")
    asset = db_session.get(Asset, created["id"])
    asset.deleted_at = utcnow()
    db_session.commit()
    old_key = asset.storage_key

    revived = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project.id, "kind": "character"},
        files={"file": ("again.png", _png_bytes(), "image/png")},
    )
    assert revived.status_code == 201, revived.text
    body = revived.json()
    assert body["id"] == created["id"]
    assert body["kind"] == "CHARACTER_REFERENCE"

    db_session.expire_all()
    row = db_session.get(Asset, created["id"])
    assert row.deleted_at is None
    assert row.version == 2
    assert row.storage_key != old_key
    assert (tmp_path / row.storage_key).is_file()
    # The replaced blob must not survive as an orphan.
    assert not (tmp_path / old_key).exists()


def test_reupload_lost_resurrect_claim_returns_409(client, db_session, monkeypatch, tmp_path):
    """A rival writer bumping the row version between the route's read and its
    claim must turn the resurrect into a 409 with the loser's file cleaned."""

    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    created = _upload(client, project.id, "character")
    asset = db_session.get(Asset, created["id"])
    asset.deleted_at = utcnow()
    db_session.commit()

    real_execute = db_session.execute

    def rival_execute(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "assets":
            # The concurrent winner commits a version bump right before every
            # claim attempt, so both the initial claim and the bounded retry
            # observe a moved row.
            real_execute(
                update(Asset)
                .where(Asset.id == asset.id)
                .values(version=Asset.version + 1)
                .execution_options(synchronize_session=False)
            )
            db_session.commit()
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", rival_execute)

    lost = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project.id, "kind": "character"},
        files={"file": ("loser.png", _png_bytes(), "image/png")},
    )

    assert lost.status_code == 409, lost.text
    assert "素材状态已变化" in lost.json()["detail"]
    db_session.expire_all()
    row = db_session.get(Asset, created["id"])
    assert row.deleted_at is not None  # the tombstone was never resurrected
    # Only the original upload's file remains; the loser's bytes were cleaned.
    assert len(list(tmp_path.rglob("*.png"))) == 1


def test_reupload_same_bytes_different_kind_conflicts(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    created = _upload(client, project.id, "character")

    mismatch = client.post(
        "/api/v1/assets/upload",
        data={"project_id": project.id, "kind": "outfit"},
        files={"file": ("same.png", _png_bytes(), "image/png")},
    )

    assert mismatch.status_code == 409, mismatch.text
    assert "其他参考用途" in mismatch.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Asset, created["id"]).kind == "CHARACTER_REFERENCE"


# ---------------------------------------------------------------------------
# #210-3: on-demand thumbnail regeneration respects pixel caps
# ---------------------------------------------------------------------------


def _seed_stored_asset(db_session, project_id, *, storage_key, deleted=False):
    asset = Asset(
        project_id=project_id,
        kind="CHARACTER_REFERENCE",
        original_name=storage_key.rsplit("/", 1)[-1],
        storage_key=storage_key,
        mime_type="image/png",
        byte_size=16,
        sha256=uuid4().hex,
        source="USER_UPLOAD",
        status=AssetStatus.UPLOADED,
        deleted_at=utcnow() if deleted else None,
    )
    db_session.add(asset)
    db_session.commit()
    return asset


def test_thumbnail_regen_rejects_oversized_stored_image(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    monkeypatch.setattr(get_settings(), "max_image_pixels", 10_000)
    project, _chapter, _page = _seed_tree(db_session)

    big = _seed_stored_asset(db_session, project.id, storage_key="p/legacy-big.png")
    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "legacy-big.png").write_bytes(_png_bytes((200, 200)))
    small = _seed_stored_asset(db_session, project.id, storage_key="p/legacy-small.png")
    (tmp_path / "p" / "legacy-small.png").write_bytes(_png_bytes((40, 40)))

    rejected = client.get(f"/api/v1/assets/{big.id}/thumbnail/320")
    assert rejected.status_code == 422, rejected.text

    accepted = client.get(f"/api/v1/assets/{small.id}/thumbnail/320")
    assert accepted.status_code == 200, accepted.text
    assert accepted.headers["content-type"].startswith("image/webp")


# ---------------------------------------------------------------------------
# #211-1: detach recomputes outfit/scene-asset status from live references
# ---------------------------------------------------------------------------


def test_delete_reference_recomputes_entity_status_from_live_refs(
    client, db_session, monkeypatch, tmp_path
):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    character = Character(project_id=project.id, primary_name="林澈", status="CANONICAL")
    db_session.add(character)
    db_session.flush()

    doomed = _seed_stored_asset(db_session, project.id, storage_key="p/doomed.png")
    survivor = _seed_stored_asset(db_session, project.id, storage_key="p/survivor.png")
    scene_only = _seed_stored_asset(db_session, project.id, storage_key="p/scene-only.png")
    # Pre-tombstoned directly in the DB so its reference ROW survives (the
    # route delete of `doomed` physically clears that asset's own rows).
    ghost = _seed_stored_asset(db_session, project.id, storage_key="p/ghost.png", deleted=True)

    outfit = Outfit(
        project_id=project.id,
        character_id=character.id,
        name="正装",
        reference_asset_ids=[doomed.id, survivor.id],
        status=AssetStatus.CANONICAL,
    )
    scene_asset = SceneAsset(
        project_id=project.id,
        name="京都街道",
        normalized_name="kyoto-street",
        status=AssetStatus.CANONICAL,
    )
    db_session.add_all([outfit, scene_asset])
    db_session.flush()
    db_session.add_all(
        [
            SceneAssetReference(scene_asset_id=scene_asset.id, asset_id=doomed.id),
            SceneAssetReference(scene_asset_id=scene_asset.id, asset_id=scene_only.id),
            SceneAssetReference(scene_asset_id=scene_asset.id, asset_id=ghost.id),
        ]
    )
    db_session.commit()

    # Deleting one of the outfit's references keeps it CANONICAL: a live
    # reference (survivor) remains. Pre-fix it was blindly demoted.
    first = client.delete(f"/api/v1/assets/{doomed.id}")
    assert first.status_code == 204, first.text
    db_session.expire_all()
    outfit = db_session.get(Outfit, outfit.id)
    assert outfit.reference_asset_ids == [survivor.id]
    assert outfit.status == AssetStatus.CANONICAL
    scene_asset = db_session.get(SceneAsset, scene_asset.id)
    assert scene_asset.status == AssetStatus.CANONICAL

    # Removing the last LIVE scene reference demotes the scene asset: the only
    # remaining reference row points at the tombstoned `ghost`, which the
    # live-ref recompute must not count.
    second = client.delete(f"/api/v1/assets/{scene_only.id}")
    assert second.status_code == 204, second.text
    db_session.expire_all()
    scene_asset = db_session.get(SceneAsset, scene_asset.id)
    assert scene_asset.status == AssetStatus.NEEDS_CONFIRMATION


# ---------------------------------------------------------------------------
# #210-2 / #211-2: approval blob preflight + affected-character recompute
# ---------------------------------------------------------------------------


def _character_batch_candidate(db_session, project, character, asset_id, *, variant="SHEET"):
    batch = GenerationBatch(
        project_id=project.id,
        target_type="CHARACTER",
        target_id=character.id,
        generation_kind="CHARACTER",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant=variant,
        status="READY",
        asset_id=asset_id,
        prompt_snapshot={},
    )
    db_session.add(candidate)
    db_session.commit()
    return batch, candidate


def test_approve_reference_requires_backing_blob(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    character = Character(project_id=project.id, primary_name="林澈")
    db_session.add(character)
    db_session.flush()
    created = _upload(client, project.id, "character")
    _batch, candidate = _character_batch_candidate(
        db_session, project, character, created["id"]
    )
    # Row is live; the bytes are gone (boot sweep / manual cleanup).
    (tmp_path / db_session.get(Asset, created["id"]).storage_key).unlink()

    response = client.post(
        f"/api/v1/asset-candidates/{candidate.id}/approve-reference",
        json={"character_id": character.id, "bind_character_reference": True,
              "set_canonical": True},
    )

    assert response.status_code == 409, response.text
    assert "文件缺失" in response.json()["detail"]


def test_approve_reference_recomputes_other_characters(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    target = Character(project_id=project.id, primary_name="林澈")
    rival = Character(
        project_id=project.id, primary_name="陈昊", status=AssetStatus.CANONICAL
    )
    db_session.add_all([target, rival])
    db_session.flush()
    created = _upload(client, project.id, "character")
    db_session.add(
        CharacterReference(character_id=rival.id, asset_id=created["id"], is_canonical=True)
    )
    _batch, candidate = _character_batch_candidate(db_session, project, target, created["id"])

    response = client.post(
        f"/api/v1/asset-candidates/{candidate.id}/approve-reference",
        json={"character_id": target.id, "bind_character_reference": True,
              "set_canonical": True},
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    # The rival lost its only reference row and must not stay CANONICAL.
    assert (
        db_session.scalar(
            select(CharacterReference.id).where(CharacterReference.character_id == rival.id)
        )
        is None
    )
    assert db_session.get(Character, rival.id).status == AssetStatus.NEEDS_CONFIRMATION
    assert db_session.get(Character, target.id).status == AssetStatus.CANONICAL


# ---------------------------------------------------------------------------
# #210-2: style test approval / activation blob preflight
# ---------------------------------------------------------------------------


def _style_with_test_candidate(db_session, project, asset_id, *, variant="STYLE_TEST"):
    style = StyleProfile(
        project_id=project.id,
        name="黑白网点",
        color_mode="color",
        profile={},
        status="CONFIRMED",
    )
    db_session.add(style)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        generation_kind="STYLE_TEST",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant=variant,
        status="READY",
        asset_id=asset_id,
        prompt_snapshot={},
    )
    db_session.add(candidate)
    db_session.commit()
    return style, candidate


def test_style_test_approval_requires_backing_blob(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    created = _upload(client, project.id, "style")
    style, candidate = _style_with_test_candidate(db_session, project, created["id"])
    (tmp_path / db_session.get(Asset, created["id"]).storage_key).unlink()

    response = client.post(
        f"/api/v1/styles/{style.id}/style-test-approve",
        json={"candidate_id": candidate.id, "approved": True, "version": style.version},
    )

    assert response.status_code == 409, response.text
    assert "文件缺失" in response.json()["detail"]


def test_style_activation_requires_recorded_test_blob(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    project, _chapter, _page = _seed_tree(db_session)
    created = _upload(client, project.id, "style")
    style, candidate = _style_with_test_candidate(db_session, project, created["id"])
    style.profile = {
        "palette_confirmed": True,
        "test_image_approved": True,
        "test_candidate_id": candidate.id,
    }
    db_session.commit()
    (tmp_path / db_session.get(Asset, created["id"]).storage_key).unlink()

    response = client.post(f"/api/v1/projects/{project.id}/styles/{style.id}/activate")

    assert response.status_code == 409, response.text
    assert "文件缺失" in response.json()["detail"]


# ---------------------------------------------------------------------------
# #211-3: retract counts only live references
# ---------------------------------------------------------------------------


def test_retract_demotes_character_with_only_tombstoned_refs(client, db_session):
    project, _chapter, _page = _seed_tree(db_session)
    character = Character(project_id=project.id, primary_name="林澈", status="CANONICAL")
    db_session.add(character)
    db_session.flush()
    live_asset = _seed_stored_asset(db_session, project.id, storage_key="p/live.png")
    dead_asset = _seed_stored_asset(
        db_session, project.id, storage_key="p/dead.png", deleted=True
    )
    db_session.add_all(
        [
            CharacterReference(character_id=character.id, asset_id=live_asset.id),
            CharacterReference(character_id=character.id, asset_id=dead_asset.id),
        ]
    )
    batch = GenerationBatch(
        project_id=project.id,
        target_type="CHARACTER",
        target_id=character.id,
        generation_kind="CHARACTER",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="SHEET",
        status="READY",
        asset_id=live_asset.id,
        prompt_snapshot={
            "reference_approval": {"approved": True, "character_id": character.id}
        },
    )
    db_session.add(candidate)
    db_session.commit()

    response = client.delete(f"/api/v1/asset-candidates/{candidate.id}/approve-reference")

    assert response.status_code == 200, response.text
    db_session.expire_all()
    # The only remaining reference row points at a soft-deleted asset, so the
    # character must be demoted (pre-fix it stayed CANONICAL).
    assert db_session.get(Character, character.id).status == AssetStatus.NEEDS_CONFIRMATION


# ---------------------------------------------------------------------------
# #211-5: favorite conditional claim
# ---------------------------------------------------------------------------


def _seed_page_candidate(db_session, project, page, *, resolution=Resolution.DRAFT_1K):
    batch = GenerationBatch(project_id=project.id, page_id=page.id, ordinal=1)
    db_session.add(batch)
    db_session.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=resolution,
        status="READY",
        based_on_storyboard_version=page.storyboard_version,
    )
    db_session.add(candidate)
    db_session.commit()
    return candidate


def test_favorite_lost_claim_returns_409(client, db_session, monkeypatch):
    project, _chapter, page = _seed_tree(db_session)
    candidate = _seed_page_candidate(db_session, project, page)

    real_execute = db_session.execute

    def rival_execute(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "page_candidates":
            real_execute(
                update(PageCandidate)
                .where(PageCandidate.id == candidate.id)
                .values(version=PageCandidate.version + 1)
                .execution_options(synchronize_session=False)
            )
            db_session.commit()
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", rival_execute)

    response = client.patch(
        f"/api/v1/candidates/{candidate.id}/favorite",
        json={"is_favorite": True},
    )

    assert response.status_code == 409, response.text
    assert "候选状态已变化" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(PageCandidate, candidate.id).is_favorite is False


# ---------------------------------------------------------------------------
# #211-5 / #236: delete_job conditional delete + terminal-run retry 409
# ---------------------------------------------------------------------------


def _seed_job(db_session, project, *, status=JobStatus.FAILED):
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=str(uuid4()),
        job_type="PAGE_GENERATE",
        status=status,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_delete_job_revived_between_read_and_delete_returns_409(
    client, db_session, monkeypatch
):
    project, _chapter, _page = _seed_tree(db_session)
    job = _seed_job(db_session, project)

    real_execute = db_session.execute

    def rival_execute(statement, *args, **kwargs):
        if isinstance(statement, Delete) and statement.table.name == "generation_jobs":
            # A concurrent retry revived the job to WAITING after the route's
            # status read but before its delete.
            real_execute(
                update(GenerationJob)
                .where(GenerationJob.id == job.id)
                .values(status=JobStatus.WAITING)
                .execution_options(synchronize_session=False)
            )
            db_session.commit()
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", rival_execute)

    response = client.delete(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 409, response.text
    assert "任务状态已变化" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.WAITING


def test_delete_failed_job_without_references_succeeds(client, db_session):
    project, _chapter, _page = _seed_tree(db_session)
    job = _seed_job(db_session, project)

    response = client.delete(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 204, response.text
    assert db_session.get(GenerationJob, job.id) is None


def test_retry_job_of_terminal_run_returns_409(client, db_session):
    project, _chapter, _page = _seed_tree(db_session)
    job = _seed_job(db_session, project)
    workflow = WorkflowDefinition(project_id=project.id, name="终端运行")
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id, revision=1, graph={}, graph_checksum="0" * 64
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        status="CANCELLED",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        WorkflowNodeRun(
            workflow_run_id=run.id,
            node_id="node-1",
            node_type="GENERATE_PAGE",
            status="FAILED",
            job_id=job.id,
        )
    )
    db_session.commit()

    response = client.post(f"/api/v1/jobs/{job.id}/retry")

    assert response.status_code == 409, response.text
    assert "所属运行已取消或已结束" in response.json()["detail"]


# ---------------------------------------------------------------------------
# #236-1 / #246-3: archived project hides object-id and export routes
# ---------------------------------------------------------------------------


def test_archived_project_hides_object_routes(client, db_session):
    project, _chapter, page = _seed_tree(db_session, project_deleted=True)
    candidate = _seed_page_candidate(db_session, project, page)

    favorite = client.patch(
        f"/api/v1/candidates/{candidate.id}/favorite",
        json={"is_favorite": True},
    )
    assert favorite.status_code == 404, favorite.text
    assert "所属项目已删除" in favorite.json()["detail"]

    asset = _seed_stored_asset(db_session, project.id, storage_key="p/orphan.png")
    content = client.get(f"/api/v1/assets/{asset.id}/content")
    assert content.status_code == 404, content.text
    assert "所属项目已删除" in content.json()["detail"]


def test_archived_project_hides_export_routes(client, db_session):
    live_project, live_chapter, _live_page = _seed_tree(db_session)
    dead_project, dead_chapter, _dead_page = _seed_tree(db_session, project_deleted=True)
    live_bundle = ExportBundle(
        project_id=live_project.id,
        chapter_id=live_chapter.id,
        export_type="PNG",
        storage_key="exports/live.zip",
        byte_size=1,
        sha256="a" * 64,
        page_count=1,
    )
    dead_bundle = ExportBundle(
        project_id=dead_project.id,
        chapter_id=dead_chapter.id,
        export_type="PNG",
        storage_key="exports/dead.zip",
        byte_size=1,
        sha256="b" * 64,
        page_count=1,
    )
    db_session.add_all([live_bundle, dead_bundle])
    db_session.commit()

    assert client.get(f"/api/v1/projects/{dead_project.id}/exports").status_code == 404
    assert client.get(f"/api/v1/exports/{dead_bundle.id}/download").status_code == 404
    assert client.get(f"/api/v1/projects/{live_project.id}/exports").status_code == 200


# ---------------------------------------------------------------------------
# #236-4: next_page resolves the following page before closing batches
# ---------------------------------------------------------------------------


def test_next_page_keeps_batches_open_when_no_following_page(client, db_session, monkeypatch):
    import app.api.routes.workflow.generation as generation_module

    monkeypatch.setattr(
        generation_module,
        "build_page_production_readiness",
        lambda _db, _page: SimpleNamespace(ready=True),
    )
    project, _chapter, page = _seed_tree(db_session)
    batch = GenerationBatch(project_id=project.id, page_id=page.id, ordinal=1, status="OPEN")
    db_session.add(batch)
    db_session.commit()

    response = client.post(f"/api/v1/pages/{page.id}/next")

    assert response.status_code == 409, response.text
    assert "最后一页" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(GenerationBatch, batch.id).status == "OPEN"


def test_next_page_closes_batches_when_following_page_exists(client, db_session, monkeypatch):
    import app.api.routes.workflow.generation as generation_module

    monkeypatch.setattr(
        generation_module,
        "build_page_production_readiness",
        lambda _db, _page: SimpleNamespace(ready=True),
    )
    project, chapter, page = _seed_tree(db_session)
    following = MangaPage(chapter_id=chapter.id, page_number=2, storyboard_version=1)
    db_session.add(following)
    batch = GenerationBatch(project_id=project.id, page_id=page.id, ordinal=1, status="OPEN")
    db_session.add(batch)
    db_session.commit()

    response = client.post(f"/api/v1/pages/{page.id}/next")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == following.id
    db_session.expire_all()
    assert db_session.get(GenerationBatch, batch.id).status == "CLOSED"


# ---------------------------------------------------------------------------
# #230 residual / #237-1 / #246-1: repair/upscale route guards and stamps
# ---------------------------------------------------------------------------


def _repair_context(client, db_session, monkeypatch, *, resolution=Resolution.DRAFT_1K):
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project = client.post("/api/v1/projects", json={"name": "修复路由守卫"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=3)
    db_session.add(page)
    db_session.flush()
    asset = Asset(
        project_id=project["id"],
        kind="page_candidate",
        original_name="parent.png",
        storage_key="generated/parent.png",
        mime_type="image/png",
        byte_size=10,
        sha256="c" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    batch = GenerationBatch(
        project_id=project["id"], page_id=page.id, ordinal=1, generation_kind="PAGE"
    )
    db_session.add_all([asset, batch])
    db_session.flush()
    parent = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=resolution,
        status="READY",
        asset_id=asset.id,
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    db_session.add(parent)
    db_session.flush()
    inspection = InspectionResult(
        candidate_id=parent.id,
        storyboard_version=page.storyboard_version,
        category="CHARACTER",
        outcome="MISMATCH",
        score=0.4,
        severity="ERROR",
        regions=[{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}],
    )
    db_session.add(inspection)
    db_session.commit()
    db_session.refresh(parent)
    return project, page, parent, inspection


REPAIR_PAYLOAD = {
    "repair_type": "BUBBLE_REGION",
    "target_regions": [],
    "target_fields": [],
    "model_alias": "image.nano_banana_2",
    "resolution": "2K",
}


def test_repair_rank_guard_rejects_resolution_downgrade(client, db_session, monkeypatch):
    _project, _page, parent, _inspection = _repair_context(
        client, db_session, monkeypatch, resolution=Resolution.HIGH_4K
    )
    payload = dict(REPAIR_PAYLOAD)
    payload["inspection_result_id"] = str(uuid4())

    response = client.post(f"/api/v1/candidates/{parent.id}/repairs", json=payload)

    assert response.status_code == 409, response.text
    assert "修复清晰度不能低于当前候选清晰度" in response.json()["detail"]


def test_repair_refuses_parent_deleted_after_batch_locks(client, db_session, monkeypatch):
    import app.api.routes.workflow.inspection as inspection_module
    from app.services.ordinal_allocator import create_generation_batch as real_batch

    _project, _page, parent, inspection = _repair_context(client, db_session, monkeypatch)

    def batch_with_tombstoned_parent(*args, **kwargs):
        db_session.execute(
            update(PageCandidate)
            .where(PageCandidate.id == parent.id)
            .values(deleted_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        db_session.commit()
        return real_batch(*args, **kwargs)

    monkeypatch.setattr(
        inspection_module, "create_generation_batch", batch_with_tombstoned_parent
    )
    payload = dict(REPAIR_PAYLOAD)
    payload["inspection_result_id"] = inspection.id

    response = client.post(f"/api/v1/candidates/{parent.id}/repairs", json=payload)

    assert response.status_code == 409, response.text
    assert "原始候选已被删除" in response.json()["detail"]
    assert (
        db_session.scalar(select(func.count(GenerationJob.id)).where(
            GenerationJob.job_type == "PAGE_REPAIR"
        ))
        == 0
    )


def test_upscale_refuses_parent_deleted_after_batch_locks(client, db_session, monkeypatch):
    import app.api.routes.workflow.inspection as inspection_module
    from app.services.ordinal_allocator import create_generation_batch as real_batch

    _project, _page, parent, _inspection = _repair_context(client, db_session, monkeypatch)

    def batch_with_tombstoned_parent(*args, **kwargs):
        db_session.execute(
            update(PageCandidate)
            .where(PageCandidate.id == parent.id)
            .values(deleted_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        db_session.commit()
        return real_batch(*args, **kwargs)

    monkeypatch.setattr(
        inspection_module, "create_generation_batch", batch_with_tombstoned_parent
    )

    response = client.post(
        f"/api/v1/candidates/{parent.id}/upscale",
        json={"model_alias": "image.nano_banana_2", "resolution": "2K"},
    )

    assert response.status_code == 409, response.text
    assert "原始候选已被删除" in response.json()["detail"]


def test_derived_children_inherit_parent_storyboard_stamp(client, db_session, monkeypatch):
    _project, page, parent, inspection = _repair_context(client, db_session, monkeypatch)
    # The storyboard moved after the parent was generated: the parent is STALE.
    page.storyboard_version += 1
    page.version += 1
    db_session.commit()

    payload = dict(REPAIR_PAYLOAD)
    payload["inspection_result_id"] = inspection.id
    repair = client.post(f"/api/v1/candidates/{parent.id}/repairs", json=payload)
    assert repair.status_code == 202, repair.text
    repair_child = db_session.get(PageCandidate, repair.json()["candidate"]["id"])
    assert repair_child.based_on_storyboard_version == 1

    upscale = client.post(
        f"/api/v1/candidates/{parent.id}/upscale",
        json={"model_alias": "image.nano_banana_2", "resolution": "2K"},
    )
    assert upscale.status_code == 202, upscale.text
    upscale_child = db_session.get(PageCandidate, upscale.json()["candidate"]["id"])
    assert upscale_child.based_on_storyboard_version == 1

    from app.api.helpers import candidate_version_state

    assert candidate_version_state(repair_child, page)[0] == "STALE"
    assert candidate_version_state(upscale_child, page)[0] == "STALE"


# ---------------------------------------------------------------------------
# #236-2 route half: page jobs lease the style profile's live reference images
# ---------------------------------------------------------------------------


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


def test_page_generate_leases_style_reference_assets(client, db_session, monkeypatch, tmp_path):
    _uploads_to(monkeypatch, tmp_path)
    _skip_page_readiness(monkeypatch)
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, _chapter, page = _seed_tree(db_session)

    style_ref = _upload(client, project.id, "style", name="风格参考.png", unique=True)
    tombstoned_ref = _upload(client, project.id, "style", name="过期参考.png", unique=True)
    style = StyleProfile(
        project_id=project.id,
        name="黑白网点",
        color_mode="monochrome",
        profile={
            "reference_asset_ids": [style_ref["id"], tombstoned_ref["id"]],
        },
        status="ACTIVE",
    )
    db_session.add(style)
    db_session.flush()
    project_row = db_session.get(Project, project.id)
    project_row.default_style_id = style.id
    tombstone = db_session.get(Asset, tombstoned_ref["id"])
    tombstone.deleted_at = utcnow()
    db_session.commit()

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

    job = db_session.get(GenerationJob, queued.json()["job_id"])
    leased = set(
        db_session.scalars(
            select(JobAssetReference.asset_id).where(JobAssetReference.job_id == job.id)
        )
    )
    assert style_ref["id"] in leased
    assert tombstoned_ref["id"] not in leased


# ---------------------------------------------------------------------------
# #226 route halves: optional optimistic-lock version tokens
# ---------------------------------------------------------------------------


def test_favorite_version_token_mismatch_returns_409(client, db_session):
    project, _chapter, page = _seed_tree(db_session)
    candidate = _seed_page_candidate(db_session, project, page)

    stale = client.patch(
        f"/api/v1/candidates/{candidate.id}/favorite",
        json={"is_favorite": True, "version": candidate.version + 5},
    )
    assert stale.status_code == 409, stale.text
    assert "候选已更新" in stale.json()["detail"]
    db_session.expire_all()
    assert db_session.get(PageCandidate, candidate.id).is_favorite is False

    current = client.patch(
        f"/api/v1/candidates/{candidate.id}/favorite",
        json={"is_favorite": True, "version": candidate.version},
    )
    assert current.status_code == 200, current.text
    db_session.expire_all()
    assert db_session.get(PageCandidate, candidate.id).is_favorite is True


def test_update_asset_version_token_mismatch_returns_409(client, db_session):
    project, _chapter, _page = _seed_tree(db_session)
    asset = _seed_stored_asset(db_session, project.id, storage_key="p/token.png")

    stale = client.patch(
        f"/api/v1/assets/{asset.id}",
        json={"display_name": "新名字", "version": asset.version + 5},
    )
    assert stale.status_code == 409, stale.text
    assert "素材已更新" in stale.json()["detail"]
    db_session.expire_all()
    assert db_session.get(Asset, asset.id).display_name is None

    current = client.patch(
        f"/api/v1/assets/{asset.id}",
        json={"display_name": "新名字", "version": asset.version},
    )
    assert current.status_code == 200, current.text
    db_session.expire_all()
    assert db_session.get(Asset, asset.id).display_name == "新名字"


def test_assign_scene_outfits_version_token_mismatch_returns_409(client, db_session):
    project, chapter, _page = _seed_tree(db_session)
    scene = Scene(chapter_id=chapter.id, ordinal=1)
    db_session.add(scene)
    db_session.commit()

    stale = client.patch(
        f"/api/v1/scenes/{scene.id}/outfits",
        json={"assignments": {}, "version": scene.version + 5},
    )
    assert stale.status_code == 409, stale.text
    assert "场景已被更新" in stale.json()["detail"]


# ---------------------------------------------------------------------------
# media sweep: upload_root orphans are covered (boot sweep extension)
# ---------------------------------------------------------------------------


def test_orphan_sweep_covers_upload_root(tmp_path):
    from datetime import timedelta
    import os
    import time as time_module

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.services.media import sweep_orphan_generated_files

    engine = create_engine(f"sqlite:///{(tmp_path / 'sweep.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    from app.config import Settings

    settings = Settings(storage_root=tmp_path / "storage", upload_root=tmp_path / "uploads")
    settings.ensure_directories()
    with factory() as db:
        project = Project(name="上传清扫")
        db.add(project)
        db.commit()
        project_id = project.id

        live_key = f"{project_id}/live.png"
        orphan_key = f"{project_id}/orphan.png"
        for key in (live_key, orphan_key):
            path = settings.upload_root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"img")
        stale = time_module.time() - 8 * 86400
        os.utime(settings.upload_root / orphan_key, (stale, stale))
        db.add(
            Asset(
                project_id=project_id,
                kind="CHARACTER_REFERENCE",
                original_name="live.png",
                storage_key=live_key,
                mime_type="image/png",
                byte_size=3,
                sha256="d" * 64,
                source="USER_UPLOAD",
            )
        )
        db.commit()

    counts = sweep_orphan_generated_files(settings, factory, older_than=timedelta(days=7))

    assert not (settings.upload_root / orphan_key).exists()
    assert (settings.upload_root / live_key).is_file()
    assert counts["removed"] == 1
    engine.dispose()

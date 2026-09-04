"""V02-42B candidate lineage: contract L1–L3 and B2 (isolated SQLite).

Real providers are never called; the paid worker never runs in this suite.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    Asset,
    CandidateLineage,
    Chapter,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    JobAssetReference,
    LineageKind,
    MangaPage,
    ModelCallAttempt,
    PageCandidate,
    Panel,
)


def _uid() -> str:
    return str(uuid4())


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _setup(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "候选血缘"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=3)
    db_session.add(page)
    db_session.flush()
    panel = Panel(page_id=page.id, reading_order=1)
    db_session.add(panel)
    db_session.flush()
    return {"project": project, "chapter": chapter, "page": page, "panel": panel}


def _ready_parent(db_session, ctx, *, model_alias="image.fast") -> PageCandidate:
    asset = Asset(
        project_id=ctx["project"]["id"],
        kind="page_candidate",
        original_name="parent.png",
        storage_key=f"generated/{_uid()}.png",
        mime_type="image/png",
        byte_size=10,
        sha256=_uid().replace("-", ""),
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    batch = GenerationBatch(
        project_id=ctx["project"]["id"],
        page_id=ctx["page"].id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add_all([asset, batch])
    db_session.flush()
    parent = PageCandidate(
        batch_id=batch.id,
        page_id=ctx["page"].id,
        ordinal=1,
        model_alias=model_alias,
        resolution="1K",
        status="READY",
        asset_id=asset.id,
        based_on_storyboard_version=ctx["page"].storyboard_version,
        prompt_snapshot={"reference_selections": {}, "compiled": " ParentFacts"},
    )
    db_session.add(parent)
    db_session.flush()
    ctx["page"].selected_candidate_id = parent.id
    db_session.commit()
    db_session.refresh(parent)
    return parent


def _envelope(ctx, payload, *, command_id=None, group_id=None):
    return {
        "schema_version": 1,
        "command_id": command_id or _uid(),
        "command_group_id": group_id or _uid(),
        "created_at": _now(),
        "target": {"project_id": ctx["project"]["id"], "page_id": ctx["page"].id},
        "expected_version": {
            "scope": "storyboard",
            "value": ctx["page"].storyboard_version,
        },
        "operation": "regenerate_region",
        "payload": payload,
        "source": {"user_prompt": "把雨势加强"},
    }


def _propose(client, ctx, envelopes):
    group_id = envelopes[0]["command_group_id"]
    return client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/command-groups",
        json={"command_group_id": group_id, "commands": envelopes},
    )


@pytest.fixture
def mask_capable_model(db_session):
    from app.config import get_settings
    from app.models import AIModel
    from app.services.provider_presets import ensure_provider_presets

    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()
    capable = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2")
    )
    capable.capabilities = {**(capable.capabilities or {}), "accepts_explicit_mask": True}
    db_session.commit()
    return capable


@pytest.fixture
def region_storage(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    return tmp_path


def test_l1_l2_accept_creates_derived_candidate_lineage_and_job(
    client, db_session, region_storage, mask_capable_model
):
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)
    parent_before = {
        column: getattr(parent, column)
        for column in (
            "asset_id",
            "prompt_snapshot",
            "status",
            "batch_id",
            "ordinal",
            "model_alias",
            "resolution",
            "is_favorite",
            "is_selected",
            "deleted_at",
            "based_on_storyboard_version",
        )
    }
    command_id = _uid()
    body = _envelope(
        ctx,
        {
            "instruction": "雨势加强，雨丝更密",
            "mask": [{"points": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.35], [0.1, 0.35]]}],
        },
        command_id=command_id,
    )
    proposed = _propose(client, ctx, [body])
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["commands"][0]["status"] == "PREVIEWED", proposed.text

    # Propose stays idempotent for regenerate_region too (frozen first result).
    replay = _propose(client, ctx, [body])
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["commands"][0]["command_id"] == command_id

    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{command_id}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["commands"][0]["status"] == "EXECUTED"

    lineage = db_session.scalar(
        select(CandidateLineage).where(CandidateLineage.source_command_id == command_id)
    )
    assert lineage is not None
    # L1: exactly one lineage row per child.
    assert lineage.id == db_session.scalar(
        select(CandidateLineage.id).where(
            CandidateLineage.child_candidate_id == lineage.child_candidate_id
        )
    )
    assert lineage.parent_candidate_id == parent.id
    assert lineage.lineage_kind == "REGION_REGENERATED"
    assert lineage.model_alias == "image.fast"
    assert lineage.catalog_model_id == mask_capable_model.id
    assert lineage.resolution == "DRAFT_1K"

    child = db_session.get(PageCandidate, lineage.child_candidate_id)
    assert child is not None
    child_batch = db_session.get(GenerationBatch, child.batch_id)
    parent_batch = db_session.get(GenerationBatch, parent.batch_id)
    assert child_batch.id != parent_batch.id
    assert child_batch.generation_kind == "REGION_REGENERATED"
    assert child_batch.ordinal > parent_batch.ordinal
    assert parent_batch.status == "CLOSED"
    assert child.ordinal == 1
    assert child.status == "QUEUED"
    assert child.based_on_storyboard_version == ctx["page"].storyboard_version
    assert child.catalog_model_id == mask_capable_model.id
    # Parent snapshot keys are preserved; only the lineage section is appended.
    assert child.prompt_snapshot["compiled"] == " ParentFacts"
    assert child.prompt_snapshot["lineage"]["parent_candidate_id"] == parent.id
    assert child.prompt_snapshot["lineage"]["source_command_id"] == command_id

    # L2: the parent candidate is untouched; page adoption state does not move.
    for column, value in parent_before.items():
        assert getattr(parent, column) == value, column
    db_session.refresh(ctx["page"])
    assert ctx["page"].selected_candidate_id == parent.id
    assert ctx["page"].storyboard_version == 1

    # L3: the mask asset is server-owned, content-addressed polygon JSON.
    mask_asset = db_session.get(Asset, lineage.mask_asset_id)
    assert mask_asset is not None
    assert mask_asset.kind == "region_mask"
    assert mask_asset.source == "AI_GENERATED"
    assert mask_asset.mime_type == "application/json"
    mask_file = region_storage / mask_asset.storage_key
    assert mask_file.is_file()
    document = json.loads(mask_file.read_text(encoding="utf-8"))
    assert document["kind"] == "region_mask"
    assert document["source_command_id"] == command_id
    assert document["regions"][0]["points"][0] == [0.1, 0.1]

    job = db_session.get(GenerationJob, child.job_id)
    assert job is not None
    assert job.job_type == "PAGE_REGION_REGENERATE"
    assert job.idempotency_key == f"region:{command_id}"
    assert job.request_parameters["original_candidate_id"] == parent.id
    assert job.request_parameters["mask_asset_id"] == mask_asset.id
    assert job.request_parameters["lineage_kind"] == "REGION_REGENERATED"
    assert job.request_parameters["instruction"] == "雨势加强，雨丝更密"
    reference_ids = set(
        db_session.scalars(
            select(JobAssetReference.asset_id).where(JobAssetReference.job_id == job.id)
        )
    )
    assert {parent.asset_id, mask_asset.id} <= reference_ids
    # No paid call: the offline queue never executes the handler.
    assert list(db_session.scalars(select(ModelCallAttempt))) == []


def test_l3_b2_region_requests_fail_closed_without_mask_or_capability(
    client, db_session, region_storage, mask_capable_model
):
    from app.config import get_settings
    from app.models import AIModel

    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)

    no_mask = _propose(
        client, ctx, [_envelope(ctx, {"instruction": "雨势加强"}, group_id=_uid())]
    )
    assert no_mask.status_code == 200, no_mask.text
    assert no_mask.json()["commands"][0]["status"] == "REJECTED"
    assert "mask" in str(no_mask.json()["commands"][0]["error"]).lower()

    # A model without accepts_explicit_mask must never be treated as a local
    # editor: deterministic UNSUPPORTED_CAPABILITY, no job, no payment.
    unsupported_alias = "image.nano_banana_pro"
    unsupported = _propose(
        client,
        ctx,
        [
            _envelope(
                ctx,
                {
                    "instruction": "雨势加强",
                    "model_alias": unsupported_alias,
                    "mask": [{"points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
                },
                group_id=_uid(),
            )
        ],
    )
    assert unsupported.status_code == 200, unsupported.text
    error = str(unsupported.json()["commands"][0]["error"])
    assert "UNSUPPORTED_CAPABILITY" in error

    unknown_model = _propose(
        client,
        ctx,
        [
            _envelope(
                ctx,
                {
                    "instruction": "雨势加强",
                    "model_alias": "image.unknown",
                    "mask": [{"points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
                },
                group_id=_uid(),
            )
        ],
    )
    assert unknown_model.status_code == 200, unknown_model.text
    assert "未识别的模型" in str(unknown_model.json()["commands"][0]["error"])

    assert list(db_session.scalars(select(CandidateLineage))) == []
    assert list(db_session.scalars(select(GenerationJob))) == []
    assert list(db_session.scalars(select(ModelCallAttempt))) == []
    db_session.refresh(ctx["page"])
    assert ctx["page"].selected_candidate_id == parent.id

    # Capability bit present → the same request previews and accepts.
    capable = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == unsupported_alias)
    )
    capable.capabilities = {**(capable.capabilities or {}), "accepts_explicit_mask": True}
    db_session.commit()
    retry_command_id = _uid()
    retry = _envelope(
        ctx,
        {
            "instruction": "雨势加强",
            "model_alias": unsupported_alias,
            "mask": [{"points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
        },
        command_id=retry_command_id,
    )
    proposed = _propose(client, ctx, [retry])
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["commands"][0]["status"] == "PREVIEWED", proposed.text
    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{retry_command_id}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    lineage = db_session.scalar(
        select(CandidateLineage).where(
            CandidateLineage.source_command_id == retry_command_id
        )
    )
    assert lineage is not None
    assert get_settings().storage_root is not None


def test_accept_after_parent_deleted_fails_without_job_or_lineage(
    client, db_session, region_storage, mask_capable_model
):
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)
    command_id = _uid()
    proposed = _propose(
        client,
        ctx,
        [
            _envelope(
                ctx,
                {
                    "instruction": "雨势加强",
                    "mask": [{"points": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}],
                },
                command_id=command_id,
            )
        ],
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["commands"][0]["status"] == "PREVIEWED", proposed.text

    parent.deleted_at = datetime.now(UTC)
    db_session.commit()

    accepted = client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/commands/{command_id}/accept"
    )
    assert accepted.status_code == 422, accepted.text
    assert "父候选" in accepted.json()["detail"]
    assert list(db_session.scalars(select(CandidateLineage))) == []
    assert list(db_session.scalars(select(PageCandidate))) == [parent]
    assert list(db_session.scalars(select(GenerationJob))) == []


def test_repair_and_upscale_write_lineage_rows(
    client, db_session, monkeypatch, mask_capable_model
):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx, model_alias="image.nano_banana_2")
    inspection = InspectionResult(
        candidate_id=parent.id,
        storyboard_version=ctx["page"].storyboard_version,
        category="CHARACTER",
        outcome="MISMATCH",
        score=0.4,
        severity="ERROR",
        details={"expected": "一致", "observed": "偏离"},
        regions=[{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}],
    )
    db_session.add(inspection)
    db_session.commit()

    repaired = client.post(
        f"/api/v1/candidates/{parent.id}/repairs",
        json={
            "inspection_result_id": inspection.id,
            "repair_type": "PANEL",
            "target_regions": [],
            "target_fields": [],
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
        },
    )
    assert repaired.status_code == 202, repaired.text
    repair_child_id = repaired.json()["candidate"]["id"]
    repair_lineage = db_session.scalar(
        select(CandidateLineage).where(CandidateLineage.child_candidate_id == repair_child_id)
    )
    assert repair_lineage is not None
    assert repair_lineage.parent_candidate_id == parent.id
    assert repair_lineage.lineage_kind == LineageKind.REPAIRED
    repair_child = db_session.get(PageCandidate, repair_child_id)
    assert repair_child.prompt_snapshot["lineage"]["parent_candidate_id"] == parent.id

    upscaled = client.post(
        f"/api/v1/candidates/{parent.id}/upscale",
        json={"model_alias": "image.nano_banana_2", "resolution": "2K"},
    )
    assert upscaled.status_code == 202, upscaled.text
    upscale_child_id = upscaled.json()["candidate"]["id"]
    upscale_lineage = db_session.scalar(
        select(CandidateLineage).where(CandidateLineage.child_candidate_id == upscale_child_id)
    )
    assert upscale_lineage is not None
    assert upscale_lineage.parent_candidate_id == parent.id
    assert upscale_lineage.lineage_kind == LineageKind.UPSCALED


def test_inspection_family_rejects_soft_deleted_candidates(client, db_session):
    """inspect/repair/upscale must not create paid jobs for candidates the
    user already soft-deleted (parity with the director path)."""

    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx, model_alias="image.nano_banana_2")
    parent.deleted_at = datetime.now(UTC)
    db_session.commit()

    inspected = client.post(f"/api/v1/candidates/{parent.id}/inspect", json={})
    assert inspected.status_code == 409

    repaired = client.post(
        f"/api/v1/candidates/{parent.id}/repairs",
        json={
            "inspection_result_id": "irrelevant",
            "repair_type": "PANEL",
            "model_alias": "image.nano_banana_2",
            "resolution": "1K",
        },
    )
    assert repaired.status_code == 409

    upscaled = client.post(
        f"/api/v1/candidates/{parent.id}/upscale",
        json={"model_alias": "image.nano_banana_2", "resolution": "2K"},
    )
    assert upscaled.status_code == 409

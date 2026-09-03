"""V02-44B capability acceptance (matrix §7 frozen bits, §8 M1–M2/M4).

Isolated SQLite + fake adapters only: no real provider, no real mask/inpaint,
no paid call. Proves the fail-closed region capability gates and the
no-silent-degrade red line end to end — catalog serialization → preset
declarations → director route → worker handler → attempt/record ledger.
"""

from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.models import (
    AIModel,
    Asset,
    CandidateLineage,
    Chapter,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    MangaPage,
    ModelCallAttempt,
    PageCandidate,
    Panel,
    Project,
    utcnow,
)
from app.model_adapters.base import ProviderAdapterError
from app.services.model_capabilities import (
    REGION_CAPABILITY_KEYS,
    model_region_edit_surface,
    region_capability_enabled,
    region_capability_source,
)
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers.execution import JobCancelledError


def _uid() -> str:
    return str(uuid4())


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def catalog(db_session):
    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()


@pytest.fixture
def region_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# §8-M2: catalog serialization is fail-closed and carries readable sources.
# --------------------------------------------------------------------------


def test_models_endpoint_exposes_region_capability_bits_fail_closed(
    client, db_session, catalog
):
    vertex_model = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2")
    )
    undeclared = AIModel(
        connection_id=vertex_model.connection_id,
        provider_model_id="undeclared-editor",
        display_name="未声明编辑器",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        capabilities={"resolutions": ["1K"]},
        source="DISCOVERED",
        confidence="DECLARED",
        enabled=True,
        priority=10,
    )
    db_session.add(undeclared)
    db_session.commit()

    response = client.get("/api/v1/models")
    assert response.status_code == 200
    rows = {item["catalog_id"]: item for item in response.json()}

    declared = rows[vertex_model.id]
    assert declared["accepts_explicit_mask"] is False
    assert declared["supports_instruction_region_edit"] is False
    assert declared["preserves_outside_region"] is False
    assert declared["whole_image_reference_only"] is True
    assert declared["region_capability_sources"] == {
        key: "DECLARED" for key in REGION_CAPABILITY_KEYS
    }

    plain = rows[undeclared.id]
    for key in REGION_CAPABILITY_KEYS:
        assert plain[key] is False
        assert plain["region_capability_sources"][key] == "UNSPECIFIED"


def test_region_capability_reader_is_fail_closed_on_unknown_provenance():
    capabilities = {
        "accepts_explicit_mask": True,
        "region_capability_sources": {
            "accepts_explicit_mask": "NOT_A_REAL_SOURCE",
            "whole_image_reference_only": "VERIFIED",
        },
    }
    # The bit itself is honored only when explicitly declared; an unknown
    # provenance string never upgrades the declaration.
    assert region_capability_enabled(capabilities, "accepts_explicit_mask") is True
    assert (
        region_capability_source(capabilities, "accepts_explicit_mask") == "UNSPECIFIED"
    )
    assert region_capability_source(capabilities, "whole_image_reference_only") == "VERIFIED"
    assert region_capability_source({}, "accepts_explicit_mask") == "UNSPECIFIED"


# --------------------------------------------------------------------------
# §7: presets declare the surface they actually have — whole-image reference
# only, no native mask, no outside-region guarantee.
# --------------------------------------------------------------------------


def test_presets_declare_whole_image_reference_only(db_session, catalog):
    image_aliases = [
        "image.nano_banana_2",
        "image.nano_banana_pro",
    ]
    models = list(
        db_session.scalars(select(AIModel).where(AIModel.legacy_alias.in_(image_aliases)))
    )
    models.extend(
        list(
            db_session.scalars(
                select(AIModel).where(
                    AIModel.provider_model_id.in_(
                        ["codex-imagegen", "antigravity-imagegen", "grok-build-imagine"]
                    )
                )
            )
        )
    )
    assert len(models) == 5
    for model in models:
        capabilities = model.capabilities or {}
        assert capabilities["accepts_explicit_mask"] is False
        assert capabilities["supports_instruction_region_edit"] is False
        assert capabilities["preserves_outside_region"] is False
        assert capabilities["whole_image_reference_only"] is True
        assert model_region_edit_surface(model) == "WHOLE_IMAGE_REFERENCE"
        for key in REGION_CAPABILITY_KEYS:
            assert region_capability_source(capabilities, key) == "DECLARED"

    text_model = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "text.fast")
    )
    assert model_region_edit_surface(text_model) == "UNSUPPORTED"


# --------------------------------------------------------------------------
# §8-M2: the director route refuses instruction-only / whole-image models
# with the surface named, before any job, candidate, mask asset or attempt.
# --------------------------------------------------------------------------


def _setup(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "能力验收"}).json()
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


def _ready_parent(db_session, ctx) -> PageCandidate:
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
        model_alias="image.nano_banana_2",
        resolution="1K",
        status="READY",
        asset_id=asset.id,
        based_on_storyboard_version=ctx["page"].storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    db_session.add(parent)
    db_session.flush()
    ctx["page"].selected_candidate_id = parent.id
    db_session.commit()
    db_session.refresh(parent)
    return parent


def _envelope(ctx, payload, *, command_id=None, group_id=None):
    from datetime import UTC, datetime

    return {
        "schema_version": 1,
        "command_id": command_id or _uid(),
        "command_group_id": group_id or _uid(),
        "created_at": datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "target": {"project_id": ctx["project"]["id"], "page_id": ctx["page"].id},
        "expected_version": {"scope": "storyboard", "value": ctx["page"].storyboard_version},
        "operation": "regenerate_region",
        "payload": payload,
        "source": {"user_prompt": "把雨势加强"},
    }


def _propose(client, ctx, envelopes):
    return client.post(
        f"/api/v1/projects/{ctx['project']['id']}/director/command-groups",
        json={
            "command_group_id": envelopes[0]["command_group_id"],
            "commands": envelopes,
        },
    )


def _instruction_only_model(db_session) -> AIModel:
    vertex_model = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2")
    )
    model = AIModel(
        connection_id=vertex_model.connection_id,
        provider_model_id="instruction-only-editor",
        display_name="Instruction Only Editor",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        capabilities={
            "resolutions": ["1K"],
            "max_reference_images": 2,
            "supports_instruction_region_edit": True,
            "region_capability_sources": {
                "supports_instruction_region_edit": "DECLARED"
            },
        },
        source="PRESET",
        confidence="DECLARED",
        enabled=True,
        priority=40,
    )
    db_session.add(model)
    db_session.commit()
    return model


def test_region_route_refuses_instruction_only_and_whole_image_models_by_surface(
    client, db_session, catalog
):
    ctx = _setup(client, db_session)
    _ready_parent(db_session, ctx)
    instruction_only = _instruction_only_model(db_session)
    mask_regions = [{"points": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]}]

    cases = [
        (
            instruction_only.legacy_alias or instruction_only.id,
            "仅 instruction 区域编辑",
        ),
        ("image.nano_banana_pro", "仅整图参考编辑"),
    ]
    for alias, expected_label in cases:
        proposed = _propose(
            client,
            ctx,
            [
                _envelope(
                    ctx,
                    {
                        "instruction": "雨势加强",
                        "model_alias": alias,
                        "mask": mask_regions,
                    },
                )
            ],
        )
        assert proposed.status_code == 200, proposed.text
        error = str(proposed.json()["commands"][0]["error"])
        assert proposed.json()["commands"][0]["status"] == "REJECTED"
        assert "UNSUPPORTED_CAPABILITY" in error
        assert expected_label in error

    # 确定性拒绝没有任何副作用：无派生候选、无血缘、无任务、无 mask 资产、无付费。
    assert list(db_session.scalars(select(CandidateLineage))) == []
    assert list(db_session.scalars(select(GenerationJob))) == []
    assert list(db_session.scalars(select(ModelCallAttempt))) == []
    assert list(db_session.scalars(select(Asset).where(Asset.kind == "region_mask"))) == []
    assert (
        list(
            db_session.scalars(
                select(GenerationBatch).where(
                    GenerationBatch.generation_kind == "REGION_REGENERATED"
                )
            )
        )
        == []
    )


# --------------------------------------------------------------------------
# §8-M2/M4: the worker re-checks the capability bit before the paid call; a
# capability failure converges job/candidate without any ledger rows, while a
# declared-capable run keeps attempt/GenerationRecord/candidate consistent.
# --------------------------------------------------------------------------


class _ForbiddenAdapter:
    """Any adapter call after a capability refusal is a silent-degrade bug."""

    def __init__(self) -> None:
        self.calls = 0

    def _forbidden(self, request):
        self.calls += 1
        raise AssertionError("能力门禁失败后不得进入付费模型调用")

    generate_page = _forbidden
    generate_asset = _forbidden
    edit_region = _forbidden


def _region_job(db_session, storage_root, *, model_alias="image.nano_banana_2"):
    project = Project(name="局部能力验收")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        scene_ids=["scene-1"],
        beat_ids=["beat-1"],
        source_coverage={"complete": True},
    )
    db_session.add(page)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
    )
    db_session.add(batch)
    db_session.flush()
    parent_asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="parent.png",
        storage_key=f"generated/{_uid()}.png",
        mime_type="image/png",
        byte_size=0,
        sha256=_uid().replace("-", ""),
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    mask_asset = Asset(
        project_id=project.id,
        kind="region_mask",
        original_name="mask.json",
        storage_key=f"generated/{_uid()}.json",
        mime_type="application/json",
        byte_size=2,
        sha256=_uid().replace("-", ""),
        source="AI_GENERATED",
        status="GENERATED",
    )
    db_session.add_all([parent_asset, mask_asset])
    db_session.flush()
    parent_file = storage_root / parent_asset.storage_key
    parent_file.parent.mkdir(parents=True, exist_ok=True)
    parent_file.write_bytes(_png_bytes())
    parent = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=model_alias,
        resolution="1K",
        status="READY",
        asset_id=parent_asset.id,
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    child = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=2,
        model_alias=model_alias,
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"lineage": {"parent_candidate_id": parent.id}},
    )
    db_session.add_all([parent, child])
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=child.id,
        job_type="PAGE_REGION_REGENERATE",
        status=JobStatus.PREPARING,
        model_alias=model_alias,
        request_parameters={
            "original_candidate_id": parent.id,
            "mask_asset_id": mask_asset.id,
            "instruction": "雨势加强",
            "target_regions": [{"points": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]}],
        },
    )
    db_session.add(job)
    db_session.flush()
    child.job_id = job.id
    db_session.commit()
    return parent, child, job


def _own_lease(db_session, job, owner="owner-capability"):
    db_session.info["job_id"] = job.id
    db_session.info["job_lease_owner"] = owner
    job.lease_owner = owner
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    # A real execution shell claims the job before the handler runs; mirror
    # that so the audit ledger sees job_attempt >= 1.
    job.attempt_count = max(job.attempt_count or 0, 1)
    db_session.commit()


def test_worker_region_job_without_capability_fails_closed_before_paid_call(
    db_session, catalog, region_storage, monkeypatch
):
    adapter = _ForbiddenAdapter()
    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: adapter)
    parent, child, job = _region_job(db_session, region_storage)
    _own_lease(db_session, job)

    from app.worker_tasks import _mark_worker_failure, _run_page_generate

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_page_generate(db_session, job)
    assert excinfo.value.code == "UNSUPPORTED_CAPABILITY"
    # nano_banana_2 honestly declares whole-image-reference-only.
    assert "仅整图参考编辑" in excinfo.value.user_message
    assert excinfo.value.retryable is False
    assert adapter.calls == 0

    db_session.rollback()
    assert list(db_session.scalars(select(ModelCallAttempt))) == []
    assert list(db_session.scalars(select(GenerationRecord))) == []

    marked, _, is_final = _mark_worker_failure(
        db_session,
        job.id,
        "owner-capability",
        "UNSUPPORTED_CAPABILITY",
        excinfo.value.user_message,
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.FAILED
    failed_child = db_session.get(PageCandidate, child.id)
    assert failed_child.status == "FAILED"
    assert failed_child.asset_id is None
    assert db_session.get(PageCandidate, parent.id).status == "READY"
    assert list(db_session.scalars(select(ModelCallAttempt))) == []
    assert list(db_session.scalars(select(GenerationRecord))) == []


def test_region_job_with_declared_capability_keeps_ledger_consistent(
    db_session, catalog, region_storage, monkeypatch
):
    from app.model_adapters.fake_acceptance import FakeAcceptanceImageAdapter
    from app.worker_tasks import _run_page_generate

    model = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2")
    )
    model.capabilities = {**(model.capabilities or {}), "accepts_explicit_mask": True}
    db_session.commit()

    adapter = FakeAcceptanceImageAdapter()
    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: adapter)
    parent, child, job = _region_job(db_session, region_storage)
    _own_lease(db_session, job)

    _run_page_generate(db_session, job)
    # The execution shell commits the finalized candidate and then flushes the
    # staged attempt outputs; mirror both before reading the ledger.
    db_session.commit()
    from app.services.worker_handlers import provider as provider_handler

    provider_handler.flush_staged_attempt_outputs(db_session)
    db_session.expire_all()

    assert adapter.call_count == 1
    done = db_session.get(PageCandidate, child.id)
    assert done.status == "READY"
    assert done.asset_id is not None
    record = db_session.scalar(select(GenerationRecord))
    assert record is not None
    assert record.status == "COMPLETED"
    assert done.asset_id in (record.output_asset_ids or [])
    attempts = list(db_session.scalars(select(ModelCallAttempt)))
    assert len(attempts) == 1
    assert attempts[0].outcome == "SUCCEEDED"
    assert attempts[0].output_asset_ids == [done.asset_id]
    # 父候选保持不变（无静默整页重生）。
    assert db_session.get(PageCandidate, parent.id).asset_id is not None
    assert db_session.get(PageCandidate, parent.id).status == "READY"


def test_cancelled_region_job_stops_before_paid_call(
    db_session, catalog, region_storage, monkeypatch
):
    from app.model_adapters.fake_acceptance import FakeAcceptanceImageAdapter
    from app.worker_tasks import _run_page_generate

    model = db_session.scalar(
        select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2")
    )
    model.capabilities = {**(model.capabilities or {}), "accepts_explicit_mask": True}
    db_session.commit()

    adapter = FakeAcceptanceImageAdapter()
    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: adapter)
    _, child, job = _region_job(db_session, region_storage)
    _own_lease(db_session, job)
    job.status = JobStatus.CANCELLED
    job.cancelled_at = utcnow()
    db_session.commit()

    with pytest.raises(JobCancelledError):
        _run_page_generate(db_session, job)

    assert adapter.call_count == 0
    db_session.rollback()
    assert list(db_session.scalars(select(ModelCallAttempt))) == []
    assert list(db_session.scalars(select(GenerationRecord))) == []
    assert db_session.get(PageCandidate, child.id).status == "QUEUED"

"""Duplicate paid repair/upscale guards: one ACTIVE derived job per kind.

Regression cover for two verified defects in the candidate inspection routes:

- P1 ``POST /candidates/{id}/repairs`` had no duplicate protection and read its
  max_auto_repairs budget with a plain pre-lock SELECT, so two concurrent
  requests both passed the cap and both enqueued a paid PAGE_REPAIR job.
- P2 ``POST /candidates/{id}/upscale`` had the same pattern: a fresh batch and
  a fresh idempotency key per request, so the same resolution could be paid for
  unlimited times while one upscale was still running.

The jobs target the NEW child candidate, so a naive ``has_active_job``
(target_id=original) is vacuous; the guards go through CandidateLineage instead.
Everything here is offline SQLite: the queue is disabled (jobs stay WAITING,
which is an ACTIVE status) and no paid provider call is ever made.
"""

import pytest
from sqlalchemy import func, insert, select

import app.services.ordinal_allocator as ordinal_allocator
from app.config import get_settings
from app.domain.states import JobStatus
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    RepairPlan,
)

REPAIR_PAYLOAD_TEMPLATE = {
    "repair_type": "BUBBLE_REGION",
    "target_regions": [],
    "target_fields": [],
    "model_alias": "image.nano_banana_2",
    "resolution": "1K",
}


@pytest.fixture
def image_models(db_session):
    """Seed the provider preset models used by repair/upscale resolution checks."""

    from app.models import AIModel
    from app.services.provider_presets import ensure_provider_presets

    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()
    return db_session.scalar(select(AIModel).where(AIModel.legacy_alias == "image.nano_banana_2"))


def _setup(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "修复升清防重"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=3)
    db_session.add(page)
    db_session.flush()
    return {"project": project, "chapter": chapter, "page": page}


def _ready_parent(db_session, ctx) -> PageCandidate:
    asset = Asset(
        project_id=ctx["project"]["id"],
        kind="page_candidate",
        original_name="parent.png",
        storage_key="generated/parent-guard.png",
        mime_type="image/png",
        byte_size=10,
        sha256="b" * 64,
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


def _inspection(db_session, ctx, parent) -> InspectionResult:
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
    return inspection


def _post_repair(client, parent, inspection, repair_type="BUBBLE_REGION"):
    payload = dict(REPAIR_PAYLOAD_TEMPLATE)
    payload["repair_type"] = repair_type
    payload["inspection_result_id"] = inspection.id
    return client.post(f"/api/v1/candidates/{parent.id}/repairs", json=payload)


def _post_upscale(client, parent, resolution):
    return client.post(
        f"/api/v1/candidates/{parent.id}/upscale",
        json={"model_alias": "image.nano_banana_2", "resolution": resolution},
    )


def _counts(db_session):
    return {
        "plans": db_session.scalar(select(func.count(RepairPlan.id))),
        "jobs": db_session.scalar(select(func.count(GenerationJob.id))),
        "candidates": db_session.scalar(select(func.count(PageCandidate.id))),
        "batches": db_session.scalar(select(func.count(GenerationBatch.id))),
    }


def test_t1_active_same_type_repair_blocks_duplicate(client, db_session, monkeypatch, image_models):
    """T1: an ACTIVE PAGE_REPAIR of type X for a child of O → same-type POST → 409.

    Pre-fix this returned 202 and created a second RepairPlan plus a second paid
    PAGE_REPAIR job (fresh repair:{plan_id} key, so create_job never deduped).
    """

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)
    inspection = _inspection(db_session, ctx, parent)

    first = _post_repair(client, parent, inspection, "BUBBLE_REGION")
    assert first.status_code == 202, first.text
    first_job_id = first.json()["job_id"]
    assert db_session.get(GenerationJob, first_job_id).status == JobStatus.WAITING

    duplicate = _post_repair(client, parent, inspection, "BUBBLE_REGION")
    assert duplicate.status_code == 409, duplicate.text
    assert "同类修复任务" in duplicate.json()["detail"]

    counts = _counts(db_session)
    assert counts["plans"] == 1
    assert counts["jobs"] == 1
    assert counts["candidates"] == 2  # parent + first repair child
    assert counts["batches"] == 2  # seed batch + first repair batch; rolled-back batch gone
    assert db_session.get(GenerationJob, first_job_id) is not None


def test_t2_different_type_repair_still_escalates_while_active(
    client, db_session, monkeypatch, image_models
):
    """T2: BUBBLE_REGION → PANEL → PAGE all 202 while the previous job is ACTIVE.

    Compatibility pin for tests/test_mvp_workflow.py's sequential escalation
    loop: the guard matches on repair_type, so distinct types never conflict.
    """

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)
    inspection = _inspection(db_session, ctx, parent)

    for index, repair_type in enumerate(["BUBBLE_REGION", "PANEL", "PAGE"]):
        response = _post_repair(client, parent, inspection, repair_type)
        assert response.status_code == 202, response.text
        job = db_session.get(GenerationJob, response.json()["job_id"])
        assert job.job_type == "PAGE_REPAIR"
        assert job.status == JobStatus.WAITING

    attempts = [
        plan.automatic_attempts
        for plan in db_session.scalars(
            select(RepairPlan).order_by(RepairPlan.automatic_attempts)
        )
    ]
    assert attempts == [1, 2, 3]
    assert db_session.scalar(select(func.count(GenerationJob.id))) == 3


def test_t3_upscale_same_resolution_blocked_higher_allowed(
    client, db_session, monkeypatch, image_models
):
    """T3: ACTIVE 2K upscale → second 2K POST → 409; 4K POST → 202.

    Pre-fix the resolution check compared against the unchanged original, so
    every repeat 2K request paid for another upscale.
    """

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)

    first = _post_upscale(client, parent, "2K")
    assert first.status_code == 202, first.text
    assert db_session.get(GenerationJob, first.json()["job_id"]).job_type == "PAGE_UPSCALE"

    duplicate = _post_upscale(client, parent, "2K")
    assert duplicate.status_code == 409, duplicate.text
    assert "同分辨率放大任务" in duplicate.json()["detail"]

    higher = _post_upscale(client, parent, "4K")
    assert higher.status_code == 202, higher.text

    upscale_jobs = list(
        db_session.scalars(select(GenerationJob).where(GenerationJob.job_type == "PAGE_UPSCALE"))
    )
    assert len(upscale_jobs) == 2
    child_resolutions = sorted(
        db_session.scalar(
            select(PageCandidate.resolution).where(PageCandidate.id == job.target_id)
        ).value
        for job in upscale_jobs
    )
    assert child_resolutions == ["2K", "4K"]


def test_t4_budget_rechecked_after_lock_closes_concurrent_commit(
    client, db_session, monkeypatch, image_models
):
    """T4: a sibling plan committing before our post-lock budget read → cap 409.

    Simulates the verified race: request A commits RepairPlan(automatic_attempts
    =3) while request B is between its old pre-lock SELECT and batch creation.
    The injected sibling lands right after the route takes the page lock inside
    create_generation_batch (exactly when a serialized sibling's commit becomes
    visible), so only a post-lock budget read can see it. Pre-fix the route read
    attempts=2 before the lock, passed the cap, and wrote attempts=3 anyway.
    """

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)
    inspection = _inspection(db_session, ctx, parent)

    # Committed state before the request: one earlier repair, attempts=2 < cap 3.
    db_session.add(
        RepairPlan(
            inspection_result_id=inspection.id,
            repair_type="BUBBLE_REGION",
            target_regions=[],
            target_fields=[],
            lock_conflicts=[],
            automatic_attempts=2,
            max_automatic_attempts=3,
        )
    )
    db_session.commit()

    real_lock = ordinal_allocator.lock_entity
    injected = False

    def locking_with_sibling(db, model_cls, entity_id):
        nonlocal injected
        locked = real_lock(db, model_cls, entity_id)
        if not injected and model_cls is MangaPage:
            injected = True
            # The concurrent sibling's commit lands after we hold the page
            # lock but before this request re-reads the budget post-lock.
            db.execute(
                insert(RepairPlan).values(
                    inspection_result_id=inspection.id,
                    repair_type="PANEL",
                    automatic_attempts=3,
                    max_automatic_attempts=3,
                )
            )
        return locked

    monkeypatch.setattr(ordinal_allocator, "lock_entity", locking_with_sibling)

    response = _post_repair(client, parent, inspection, "PAGE")
    assert response.status_code == 409, response.text
    assert "最大自动修复次数" in response.json()["detail"]

    counts = _counts(db_session)
    # Only the pre-seeded committed plan survives; neither the injected sibling
    # (discarded with the rolled-back request) nor a route-written attempts=3
    # plan exists, and no paid job or child candidate was created.
    assert counts["plans"] == 1
    assert counts["jobs"] == 0
    assert counts["candidates"] == 1
    assert counts["batches"] == 1


def test_t5_terminal_repair_failure_allows_same_type_retry(
    client, db_session, monkeypatch, image_models
):
    """T5: a FAILED repair job is not ACTIVE → same-type POST returns 202.

    Behavior preservation: the guard must only block while a job is still
    running; terminal failures keep the manual retry path open.
    """

    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    ctx = _setup(client, db_session)
    parent = _ready_parent(db_session, ctx)
    inspection = _inspection(db_session, ctx, parent)

    first = _post_repair(client, parent, inspection, "BUBBLE_REGION")
    assert first.status_code == 202, first.text
    failed_job = db_session.get(GenerationJob, first.json()["job_id"])
    failed_job.status = JobStatus.FAILED
    failed_job.error_code = "PROVIDER_ERROR"
    child = db_session.get(PageCandidate, failed_job.target_id)
    child.status = "FAILED"
    db_session.commit()

    retry = _post_repair(client, parent, inspection, "BUBBLE_REGION")
    assert retry.status_code == 202, retry.text
    assert retry.json()["job_id"] != failed_job.id

    plans = list(db_session.scalars(select(RepairPlan).order_by(RepairPlan.automatic_attempts)))
    assert [plan.automatic_attempts for plan in plans] == [1, 2]
    assert db_session.scalar(select(func.count(GenerationJob.id))) == 2

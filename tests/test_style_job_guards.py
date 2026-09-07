"""Guards: one paid STYLE_ANALYZE call per style, and a duplicate's terminal
exit must not force the shared style row while a sibling job is still active.

The analyze/palette-draft routes embed the bumped style version in their
idempotency keys, so sequential duplicates never collapse and two ACTIVE jobs
for one style used to run duplicate paid multimodal calls; worse, finalizing
either duplicate forced the shared style row out of ANALYZING while its
sibling was still analyzing.
"""

from datetime import timedelta

from sqlalchemy import select, update

import pytest

from app.config import get_settings
from app.domain.states import JobStatus
from app.models import (
    Asset,
    GenerationJob,
    Project,
    StyleProfile,
    StyleStatus,
    utcnow,
)
from app.services import job_service
from app.model_adapters.base import ProviderAdapterError
from app.worker_tasks import _mark_worker_failure


def _style_reference(db, project_id: str, digest: str) -> Asset:
    asset = Asset(
        project_id=project_id,
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style.png",
        mime_type="image/png",
        byte_size=10,
        sha256=digest * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add(asset)
    db.commit()
    return asset


def _seed_style(client, db, *, name: str) -> tuple[dict, dict]:
    project = client.post("/api/v1/projects", json={"name": name}).json()
    reference = _style_reference(db, project["id"], "b")
    style = client.post(
        f"/api/v1/projects/{project['id']}/styles",
        json={
            "name": f"{name}风格",
            "color_mode": "color",
            "reference_asset_ids": [reference.id],
        },
    ).json()
    return project, style


def _orm_style(db, name: str) -> tuple[Project, StyleProfile]:
    project = Project(name=name)
    db.add(project)
    db.flush()
    style = StyleProfile(
        project_id=project.id,
        name=f"{name}风格",
        color_mode="monochrome",
        status=StyleStatus.ANALYZING,
    )
    db.add(style)
    db.commit()
    return project, style


def _style_job(
    db,
    project_id: str,
    style_id: str,
    status: JobStatus,
    **kwargs,
) -> GenerationJob:
    job = GenerationJob(
        project_id=project_id,
        target_type="STYLE",
        target_id=style_id,
        job_type="STYLE_ANALYZE",
        status=status,
        **kwargs,
    )
    db.add(job)
    db.commit()
    return job


def _own_lease(db, job, owner: str) -> str:
    db.info["job_id"] = job.id
    db.info["job_lease_owner"] = owner
    job.lease_owner = owner
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    job.attempt_count = max(job.attempt_count or 0, 1)
    db.commit()
    return owner


def _style_job_ids(db, style_id: str) -> list[str]:
    return list(
        db.scalars(
            select(GenerationJob.id).where(
                GenerationJob.job_type == "STYLE_ANALYZE",
                GenerationJob.target_type == "STYLE",
                GenerationJob.target_id == style_id,
            )
        )
    )


def test_analyze_style_rejects_while_analyze_job_is_active(
    client, db_session, monkeypatch
):
    """TA1: an ACTIVE STYLE_ANALYZE job blocks re-analysis with 409 instead of
    silently spawning a second paid duplicate (pre-fix: 202 + second job)."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, style = _seed_style(client, db_session, name="分析去重")
    active = _style_job(
        db_session,
        project["id"],
        style["id"],
        JobStatus.QUEUED,
        idempotency_key=f"style-analyze:{style['id']}:{style['version']}",
    )
    before = db_session.get(StyleProfile, style["id"])
    version_before = before.version

    response = client.post(f"/api/v1/styles/{style['id']}/analyze")

    assert response.status_code == 409, response.json()
    assert _style_job_ids(db_session, style["id"]) == [active.id]
    row = db_session.get(StyleProfile, style["id"])
    assert row.status == StyleStatus.DRAFT
    assert row.version == version_before


def test_palette_draft_rejects_while_analyze_job_is_active(
    client, db_session, monkeypatch
):
    """TA2: the palette-draft route creates a STYLE_ANALYZE job too, so an
    active analysis (from either entry point) blocks drafting with 409."""
    monkeypatch.setattr(get_settings(), "queue_enabled", False)
    project, style = _seed_style(client, db_session, name="色板去重")
    active = _style_job(
        db_session,
        project["id"],
        style["id"],
        JobStatus.QUEUED,
        idempotency_key=f"style-analyze:{style['id']}:{style['version']}",
    )
    before = db_session.get(StyleProfile, style["id"])
    version_before = before.version

    response = client.post(
        f"/api/v1/styles/{style['id']}/palette-draft",
        json={"atmosphere": "低饱和、雨后京都"},
    )

    assert response.status_code == 409, response.json()
    assert _style_job_ids(db_session, style["id"]) == [active.id]
    row = db_session.get(StyleProfile, style["id"])
    assert row.status == StyleStatus.DRAFT
    assert row.version == version_before


def test_terminal_style_failure_keeps_analyzing_while_sibling_active(db_session):
    """TA3 (failure path): forcing the shared style row to DRAFT must wait
    until the last ACTIVE sibling STYLE_ANALYZE job exits (pre-fix: the first
    terminal failure flips it while the sibling is still analyzing)."""
    project, style = _orm_style(db_session, "失败兄弟保护")
    failing = _style_job(db_session, project.id, style.id, JobStatus.GENERATING)
    sibling = _style_job(db_session, project.id, style.id, JobStatus.GENERATING)
    owner = _own_lease(db_session, failing, "owner-style-sibling-a")

    marked, _, is_final = _mark_worker_failure(
        db_session,
        failing.id,
        owner,
        "WORKER_ERROR",
        "风格分析失败",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, failing.id).status == JobStatus.FAILED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.ANALYZING

    owner_sibling = _own_lease(db_session, sibling, "owner-style-sibling-b")
    marked_sibling, _, is_final_sibling = _mark_worker_failure(
        db_session,
        sibling.id,
        owner_sibling,
        "WORKER_ERROR",
        "风格分析失败",
        retryable=False,
    )
    assert marked_sibling is True and is_final_sibling is True
    db_session.expire_all()
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.DRAFT


def test_cancelled_style_job_keeps_analyzing_while_sibling_active(db_session):
    """TA3 (cancel path): same sibling protection for mark_job_cancelled —
    cancelling one duplicate must not reset the style while the other is live."""
    project, style = _orm_style(db_session, "取消兄弟保护")
    cancelled = _style_job(db_session, project.id, style.id, JobStatus.GENERATING)
    sibling = _style_job(db_session, project.id, style.id, JobStatus.GENERATING)

    job_service.mark_job_cancelled(db_session, cancelled)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(GenerationJob, cancelled.id).status == JobStatus.CANCELLED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.ANALYZING

    job_service.mark_job_cancelled(db_session, sibling)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(GenerationJob, sibling.id).status == JobStatus.CANCELLED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.DRAFT


def test_lease_expiry_reset_keeps_analyzing_while_sibling_active(
    db_session, monkeypatch
):
    """TA3 (recovery path): recover_pending_jobs' terminal branch must also
    leave the style ANALYZING while another ACTIVE job still targets it."""
    monkeypatch.setattr(get_settings(), "queue_enabled", True)
    project, style = _orm_style(db_session, "恢复兄弟保护")
    expired = _style_job(
        db_session,
        project.id,
        style.id,
        JobStatus.GENERATING,
        max_attempts=1,
        attempt_count=1,
        lease_owner="owner-expired",
        lease_expires_at=utcnow() - timedelta(minutes=1),
    )
    sibling = _style_job(
        db_session,
        project.id,
        style.id,
        JobStatus.GENERATING,
        lease_owner="owner-live",
        lease_expires_at=utcnow() + timedelta(minutes=5),
    )

    recovered = job_service.recover_pending_jobs(db_session)

    db_session.expire_all()
    assert recovered == 0
    assert db_session.get(GenerationJob, expired.id).status == JobStatus.FAILED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.ANALYZING

    sibling_id = sibling.id
    db_session.execute(
        update(GenerationJob)
        .where(GenerationJob.id == sibling_id)
        .values(
            lease_owner="owner-expired-2",
            lease_expires_at=utcnow() - timedelta(minutes=1),
            attempt_count=sibling.max_attempts,
        )
    )
    db_session.commit()

    job_service.recover_pending_jobs(db_session)
    db_session.expire_all()
    assert db_session.get(GenerationJob, sibling_id).status == JobStatus.FAILED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.DRAFT


def test_terminal_style_failure_resets_style_without_sibling(db_session):
    """TA4: the normal path is preserved — a terminal failure with no sibling
    still returns the style to DRAFT."""
    project, style = _orm_style(db_session, "单任务失败")
    failing = _style_job(db_session, project.id, style.id, JobStatus.GENERATING)
    owner = _own_lease(db_session, failing, "owner-style-single")

    marked, _, is_final = _mark_worker_failure(
        db_session,
        failing.id,
        owner,
        "WORKER_ERROR",
        "风格分析失败",
        retryable=False,
    )
    assert marked is True and is_final is True
    db_session.expire_all()
    assert db_session.get(GenerationJob, failing.id).status == JobStatus.FAILED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.DRAFT


def test_cancelled_style_job_resets_style_without_sibling(db_session):
    """TA4: cancelling the only STYLE_ANALYZE job still resets the style."""
    project, style = _orm_style(db_session, "单任务取消")
    job = _style_job(db_session, project.id, style.id, JobStatus.GENERATING)

    job_service.mark_job_cancelled(db_session, job)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(GenerationJob, job.id).status == JobStatus.CANCELLED
    assert db_session.get(StyleProfile, style.id).status == StyleStatus.DRAFT


def test_younger_style_analysis_fails_before_paid_call_when_sibling_active(
    db_session, monkeypatch
):
    """Worker-side oldest-wins arbitration for STYLE_ANALYZE (the story_parse
    pattern): the route guard is check-then-act and the two entry keys are
    disjoint, so a concurrent analyze + palette-draft can commit two ACTIVE
    jobs. The younger claimant must fail terminally before the paid multimodal
    call instead of double-paying and clobbering the winner's profile write."""

    from app.models import GenerationJob, utcnow
    from app.services.worker_handlers.style_analyze import _run_style_analyze

    project = Project(name="风格仲裁")
    db_session.add(project)
    db_session.flush()
    reference = _style_reference(db_session, project.id, "c")
    style = StyleProfile(
        project_id=project.id,
        name="风格仲裁风格",
        color_mode="monochrome",
        status=StyleStatus.ANALYZING,
        profile={"reference_asset_ids": [reference.id]},
    )
    db_session.add(style)
    db_session.flush()
    older = GenerationJob(
        project_id=style.project_id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        status="PREPARING",
        # Pin the ordering: both created_at defaults can land on the same
        # clock tick, and the arbitration's UUID id tie-break would then crown
        # the younger job as the oldest ACTIVE sibling.
        created_at=utcnow() - timedelta(seconds=5),
    )
    db_session.add(older)
    db_session.flush()
    younger = GenerationJob(
        project_id=style.project_id,
        target_type="STYLE",
        target_id=style.id,
        job_type="STYLE_ANALYZE",
        status="PREPARING",
    )
    db_session.add(younger)
    db_session.commit()

    def forbid_binding(*_args, **_kwargs):
        raise AssertionError("较新的重复分析不得发起模型调用")

    monkeypatch.setattr(
        "app.services.worker_handlers.style_analyze.provider._binding", forbid_binding
    )

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_style_analyze(db_session, younger)
    assert excinfo.value.code == "STYLE_ANALYSIS_CONFLICT"
    assert excinfo.value.retryable is False

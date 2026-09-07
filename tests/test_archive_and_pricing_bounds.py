"""Regression: archive, failure marking and pricing tie-breaks are atomic.

Archive routes selected terminal jobs and then wrote archived_at in a
separate step, so a job retried back to active between the two was archived
out of the job list while it still ran; mark_job_failed was the last
read-then-write job-state mutator that could clear a live worker's lease;
and _active_price tie-broke equal pricing windows by list order, letting
the same attempt flip price versions between calls.
"""

from sqlalchemy import update as sa_update

from app.domain.states import JobStatus
from app.models import GenerationJob, ModelPricingVersion, Project
from app.services.job_service import mark_job_failed
from app.services.model_costs import _active_price


def _job(db, name: str, *, project: Project | None = None, **overrides) -> GenerationJob:
    if project is None:
        project = db.query(Project).filter_by(name=name).one_or_none()
    if project is None:
        project = Project(name=name)
        db.add(project)
        db.flush()
    from app.domain.states import JobStatus

    fields = {
        "project_id": project.id,
        "target_type": "CHAPTER",
        "target_id": "chapter-1",
        "job_type": "SOURCE_PARSE",
        "status": JobStatus.FAILED,
    }
    fields.update(overrides)
    fields["status"] = JobStatus(fields["status"])
    job = GenerationJob(**fields)
    db.add(job)
    db.commit()
    return job


def test_archive_completed_skips_job_retried_to_waiting(client, db_session):
    from app.domain.states import JobStatus

    job = _job(db_session, "archive-race")
    # Concurrent retry flips the job back to an active status after the
    # route's read model of the world was formed.
    db_session.execute(
        sa_update(GenerationJob)
        .where(GenerationJob.id == job.id)
        .values(status=JobStatus.WAITING, error_code=None)
    )
    db_session.commit()

    response = client.post(f"/api/v1/projects/{job.project_id}/jobs/archive-completed")
    assert response.status_code == 200
    assert response.json()["archived_count"] == 0

    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.archived_at is None
    assert row.status == "WAITING"


def test_bulk_archive_archives_only_rows_still_terminal(client, db_session):
    failed = _job(db_session, "bulk-archive-shared")
    waiting = _job(db_session, "bulk-archive-shared", status=JobStatus.WAITING)

    response = client.post(
        f"/api/v1/projects/{failed.project_id}/jobs/bulk-archive",
        json={"job_ids": [failed.id, waiting.id]},
    )
    assert response.status_code == 409  # non-terminal member still 409s

    response = client.post(
        f"/api/v1/projects/{failed.project_id}/jobs/bulk-archive",
        json={"job_ids": [failed.id]},
    )
    assert response.status_code == 200
    assert response.json()["archived_count"] == 1

    # Flip the row back: a non-terminal member is rejected outright, and the
    # row must not carry an archive stamp from the earlier successful call.
    db_session.execute(
        sa_update(GenerationJob)
        .where(GenerationJob.id == failed.id)
        .values(status=JobStatus.WAITING, archived_at=None)
    )
    db_session.commit()
    response = client.post(
        f"/api/v1/projects/{failed.project_id}/jobs/bulk-archive",
        json={"job_ids": [failed.id]},
    )
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(GenerationJob, failed.id).archived_at is None


def test_mark_job_failed_refuses_claimed_job(db_session):
    from datetime import UTC, datetime, timedelta

    job = _job(db_session, "fail-claimed", status="GENERATING")
    db_session.execute(
        sa_update(GenerationJob)
        .where(GenerationJob.id == job.id)
        .values(
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
    )
    db_session.commit()
    stale = GenerationJob(id=job.id, status=job.status)

    assert mark_job_failed(db_session, stale, "WORKER_ERROR", "延迟的失败") is None

    db_session.expire_all()
    row = db_session.get(GenerationJob, job.id)
    assert row.status == "GENERATING"
    assert row.lease_owner == "worker-1"


def test_active_price_tie_break_is_deterministic(db_session):
    from datetime import UTC, datetime

    window = datetime(2026, 1, 1, tzinfo=UTC)
    for version in ("zz-later-row", "aa-earlier-row"):
        db_session.add(
            ModelPricingVersion(
                provider="tie",
                model_id="m",
                pricing_version=version,
                effective_from=window,
                currency="USD",
                output_tokens_per_million=1,
            )
        )
    db_session.commit()

    prices = (
        db_session.query(ModelPricingVersion)
        .filter(ModelPricingVersion.provider == "tie")
        .all()
    )
    chosen = _active_price(prices, datetime(2026, 6, 1, tzinfo=UTC))
    assert chosen is not None
    assert chosen.pricing_version == "zz-later-row"

"""Project archive: serialize cancellation and the soft-delete in one transaction.

Archive locks the project row first and then sweeps run rows (through
cancel_run) and standalone job rows in the same transaction, so a concurrent
run start, job retry, or GENERATE approval that shares the parent-first lock
order cannot interleave with a half-archived project.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.states import JobStatus
from app.models import GenerationJob, Project, WorkflowRun, utcnow
from app.services.job_service import mark_job_cancelled
from app.services.ordinal_allocator import lock_entity
from app.services.workflow_engine.lifecycle import cancel_run


def archive_project(db: Session, project_id: str, *, confirm_name: str) -> None:
    """Cancel a project's live runs/jobs and write the archive marker once.

    Callers own the session; the single trailing commit releases the project
    lock only after every sweep has succeeded, and any sweep failure rolls
    the whole unit back (the marker, run and job cancellations included).
    """
    project = lock_entity(db, Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    # Project names are stored unstripped (create performs no trimming), so a
    # stored " 名称" must still be archivable by typing "名称": compare
    # stripped-to-stripped instead of a bare confirm_name.strip() against the
    # raw stored name (#226).
    if confirm_name.strip() != project.name.strip():
        raise HTTPException(status_code=409, detail="项目名称不匹配，未执行删除")
    terminal_statuses = {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.NEEDS_REVIEW,
    }
    # Run-before-job order matches cancel_run. All sweeps share this project's
    # transaction: a nested commit would release the serialization lock early.
    stale_runs = list(
        db.scalars(
            select(WorkflowRun).where(
                WorkflowRun.project_id == project_id,
                WorkflowRun.status.not_in(["COMPLETED", "CANCELLED", "FAILED"]),
            ).order_by(WorkflowRun.id)
        )
    )
    for run in stale_runs:
        cancel_run(db, run, auto_commit=False)
    active_jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.status.not_in(terminal_statuses),
            ).order_by(GenerationJob.id)
        )
    )
    for job in active_jobs:
        mark_job_cancelled(db, job)
    project.deleted_at = utcnow()
    project.version += 1
    db.commit()

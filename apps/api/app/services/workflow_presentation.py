"""Read-only enrichment: attach exact, known usage for this run's job attempts."""

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelCallAttempt, WorkflowRun
from app.workflow_schemas import WorkflowRunRead


def run_read(db: Session, run: WorkflowRun) -> WorkflowRunRead:
    result = WorkflowRunRead.model_validate(run)
    job_ids = {node.job_id for node in result.node_runs if node.job_id}
    if not job_ids or not result.started_at:
        return result
    attempts = list(db.scalars(select(ModelCallAttempt).where(
        ModelCallAttempt.job_id.in_(job_ids),
        ModelCallAttempt.project_id == result.project_id,
        ModelCallAttempt.started_at >= result.started_at,
    )))
    for node in result.node_runs:
        rows = [row for row in attempts if row.job_id == node.job_id]
        # A reused job may acquire more attempts in a later run. Never leak
        # those into the historical run's total. SQLite returns naive UTC.
        if result.finished_at:
            end = result.finished_at.replace(tzinfo=UTC)
            rows = [row for row in rows if row.started_at.replace(tzinfo=UTC) <= end]
        if rows and all(
            row.input_tokens is not None and row.output_tokens is not None
            for row in rows
        ):
            node.total_tokens = sum(int(row.input_tokens) + int(row.output_tokens) for row in rows)
    return result

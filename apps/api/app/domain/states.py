from enum import StrEnum

# Status-transition semantics (issue #128): this module intentionally exports
# NO transition tables. The former JOB_TRANSITIONS/PAGE_TRANSITIONS maps and
# ``ensure_transition`` had zero runtime call sites — every real status write
# is guarded by a conditional UPDATE (WHERE status/lease/owner/cancelled_at)
# at its own write point, so the tables described guarantees the runtime did
# not enforce and never blocked a transition it forbade. Actual semantics:
#
# - GenerationJob: WAITING/QUEUED -> PREPARING is the lease claim CAS
#   (worker_tasks._claim_job); the leased pipeline PREPARING ->
#   UPLOADING_REFERENCES/GENERATING/OCR_CHECKING/CONSISTENCY_CHECKING/
#   REPAIRING steps are owned-progress CASes (worker_handlers.execution);
#   COMPLETED/FAILED are completion/failure CASes fenced on lease_owner;
#   retry/recovery migrations (GENERATING -> WAITING lease reclaim, FAILED ->
#   WAITING reset_for_retry, QUEUED -> WAITING defer/queue-unavailable,
#   FAILED/CANCELLED -> CANCELLED cancel sweeps) are the conditional updates
#   in job_service (recover_pending_jobs, reset_for_retry, mark_job_*) and
#   worker_tasks (_mark_worker_failure, _claim_job's concurrency fallback).
# - WorkflowRun/WorkflowNodeRun: status strings are mutated by the workflow
#   engine's lifecycle/reconciliation/planning paths under their own row
#   guards; no enumerated table exists by the same design.
# - MangaPage: page statuses are written by the content workflow and restore
#   helpers next to their own guards.
#
# If a future change wants centralized enforcement, it must wire the tables
# into EVERY write point enumerated above first; a table without call sites
# is documentation that silently rots.


class WorkflowMode(StrEnum):
    AUTO = "AUTO"
    DIRECTOR = "DIRECTOR"
    SEMI_AUTO = "SEMI_AUTO"


class Resolution(StrEnum):
    DRAFT_1K = "1K"
    STANDARD_2K = "2K"
    HIGH_4K = "4K"


class CharacterPresence(StrEnum):
    VISIBLE = "VISIBLE"
    OFFSCREEN = "OFFSCREEN"
    MENTIONED = "MENTIONED"


class PageStatus(StrEnum):
    PLANNED = "PLANNED"
    STORYBOARDED = "STORYBOARDED"
    DRAFT_GENERATING = "DRAFT_GENERATING"
    DRAFT_READY = "DRAFT_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    FINAL_GENERATING = "FINAL_GENERATING"
    FINAL_CHECKING = "FINAL_CHECKING"
    FINAL_READY = "FINAL_READY"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


class JobStatus(StrEnum):
    WAITING = "WAITING"
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    UPLOADING_REFERENCES = "UPLOADING_REFERENCES"
    GENERATING = "GENERATING"
    OCR_CHECKING = "OCR_CHECKING"
    CONSISTENCY_CHECKING = "CONSISTENCY_CHECKING"
    REPAIRING = "REPAIRING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


def ensure_unlocked(locked_fields: list[str], target_fields: list[str]) -> None:
    for locked in locked_fields:
        locked_prefix = locked.rstrip("/") + "/"
        for target in target_fields:
            target_prefix = target.rstrip("/") + "/"
            if (
                target == locked
                or target.startswith(locked_prefix)
                or locked.startswith(target_prefix)
            ):
                raise ValueError(f"目标字段已锁定：{target}")

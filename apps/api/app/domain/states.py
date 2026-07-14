from enum import StrEnum


class WorkflowMode(StrEnum):
    AUTO = "AUTO"
    DIRECTOR = "DIRECTOR"
    SEMI_AUTO = "SEMI_AUTO"


class Resolution(StrEnum):
    DRAFT_1K = "1K"
    STANDARD_2K = "2K"
    HIGH_4K = "4K"


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


PAGE_TRANSITIONS: dict[PageStatus, frozenset[PageStatus]] = {
    PageStatus.PLANNED: frozenset({PageStatus.STORYBOARDED}),
    PageStatus.STORYBOARDED: frozenset({PageStatus.DRAFT_GENERATING}),
    PageStatus.DRAFT_GENERATING: frozenset({PageStatus.DRAFT_READY, PageStatus.FAILED}),
    PageStatus.DRAFT_READY: frozenset({PageStatus.REVIEW_REQUIRED}),
    PageStatus.REVIEW_REQUIRED: frozenset({PageStatus.APPROVED, PageStatus.NEEDS_REPAIR}),
    PageStatus.APPROVED: frozenset({PageStatus.FINAL_GENERATING}),
    PageStatus.FINAL_GENERATING: frozenset({PageStatus.FINAL_CHECKING, PageStatus.FAILED}),
    PageStatus.FINAL_CHECKING: frozenset(
        {PageStatus.FINAL_READY, PageStatus.NEEDS_REPAIR, PageStatus.NEEDS_MANUAL_REVIEW}
    ),
    PageStatus.FINAL_READY: frozenset({PageStatus.EXPORTED}),
    PageStatus.EXPORTED: frozenset(),
    PageStatus.FAILED: frozenset(
        {PageStatus.DRAFT_GENERATING, PageStatus.FINAL_GENERATING, PageStatus.NEEDS_MANUAL_REVIEW}
    ),
    PageStatus.NEEDS_REPAIR: frozenset(
        {PageStatus.DRAFT_GENERATING, PageStatus.FINAL_GENERATING, PageStatus.NEEDS_MANUAL_REVIEW}
    ),
    PageStatus.NEEDS_MANUAL_REVIEW: frozenset(
        {PageStatus.DRAFT_GENERATING, PageStatus.FINAL_GENERATING}
    ),
}

JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.WAITING: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.QUEUED: frozenset({JobStatus.PREPARING, JobStatus.CANCELLED}),
    JobStatus.PREPARING: frozenset(
        {
            JobStatus.UPLOADING_REFERENCES,
            JobStatus.GENERATING,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.UPLOADING_REFERENCES: frozenset(
        {JobStatus.GENERATING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.GENERATING: frozenset(
        {JobStatus.OCR_CHECKING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.OCR_CHECKING: frozenset(
        {JobStatus.CONSISTENCY_CHECKING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.CONSISTENCY_CHECKING: frozenset(
        {JobStatus.REPAIRING, JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED}
    ),
    JobStatus.REPAIRING: frozenset(
        {JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.NEEDS_REVIEW}),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.NEEDS_REVIEW: frozenset(),
}


def ensure_transition(current: StrEnum, target: StrEnum, transitions: dict) -> None:
    if target not in transitions.get(current, frozenset()):
        raise ValueError(f"非法状态迁移：{current.value} → {target.value}")


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

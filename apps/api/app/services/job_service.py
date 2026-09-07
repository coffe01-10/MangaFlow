import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock, Thread

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.states import JobStatus, PageStatus
from app.models import (
    AssetCandidate,
    CandidateLineage,
    GenerationJob,
    JobAssetReference,
    JobDependency,
    MangaPage,
    ModelCallAttempt,
    PageCandidate,
    StyleProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    utcnow,
)
from app.services.runtime_settings import apply_runtime_overrides, read_queue_mode

LOGGER = logging.getLogger("mangaflow.jobs")

LOCAL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mangaflow-local")
LOCAL_SUBMISSION_LOCK = Lock()
LOCAL_SUBMITTED_JOB_IDS: set[str] = set()
LEASED_JOB_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.UPLOADING_REFERENCES,
    JobStatus.GENERATING,
    JobStatus.OCR_CHECKING,
    JobStatus.CONSISTENCY_CHECKING,
    JobStatus.REPAIRING,
}
ACTIVE_JOB_STATUSES = {JobStatus.WAITING, JobStatus.QUEUED, *LEASED_JOB_STATUSES}
# Job types whose paid call must stay one-active-per-target across ALL entry
# points (manual route, workflow node, and retry). PAGE_INSPECT: a retried
# FAILED inspect is invisible to the route's idempotency key because
# create_job collapses the dead row's key to closed:{id} (issue #125).
# SOURCE_PARSE: route and workflow entries use disjoint idempotency-key
# namespaces, so a retried FAILED parse would otherwise run next to a live
# one (#124). STYLE_ANALYZE: the analyze and palette-draft routes share a
# one-active-per-style guard, but a retried FAILED analyze keyed on the
# pre-bump version is invisible to it once the sibling bumped the version.
# Extend this set instead of adding ad-hoc per-route guards when
# new one-per-target kinds appear.
RETRY_MUTEX_JOB_TYPES = {"PAGE_INSPECT", "SOURCE_PARSE", "STYLE_ANALYZE"}
# LOCAL wall-clock cap markers. The lease heartbeat stamps LOCAL_TIMEOUT once
# the job's job_timeout_seconds budget is spent while the lease is still live;
# recovery preserves that cause through reclaim/requeue exactly like the RQ
# kill path's JOB_TIMEOUT. All LOCAL_TIMEOUT user-facing strings live here.
LOCAL_TIMEOUT_ERROR_CODE = "LOCAL_TIMEOUT"
LOCAL_TIMEOUT_WAITING_MESSAGE = "本地执行超过墙钟上限，等待租约过期回收"
LOCAL_TIMEOUT_REQUEUE_MESSAGE = "本地执行超过墙钟上限，任务等待重新执行"
LOCAL_TIMEOUT_TERMINAL_MESSAGE = "本地执行超过墙钟上限，且已达到最大尝试次数"


def has_active_job(
    db: Session,
    *,
    job_type: str,
    target_id: str,
    target_type: str | None = None,
    exclude_job_id: str | None = None,
) -> bool:
    filters = [
        GenerationJob.job_type == job_type,
        GenerationJob.target_id == target_id,
        GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
    ]
    if target_type is not None:
        filters.append(GenerationJob.target_type == target_type)
    if exclude_job_id is not None:
        # Self-exclusion for revival paths (reset_for_retry): the caller's own
        # row is (or is about to be) ACTIVE again and must not trip its own
        # duplicate guard.
        filters.append(GenerationJob.id != exclude_job_id)
    return db.scalar(select(GenerationJob.id).where(*filters).limit(1)) is not None


def oldest_active_job_id(
    db: Session,
    *,
    job_type: str,
    target_id: str,
    target_type: str | None = None,
) -> str | None:
    """Id of the oldest ACTIVE job for the target (``created_at``, then id).

    Deterministic loser for same-window duplicate claims (#124): when two
    claimants both see the other ACTIVE, a plain "any other active job
    blocks me" guard kills both and leaves the target with zero runs. Callers
    let exactly one claimant proceed — the row this read-only query names.
    The id tie-break keeps the winner unique even when created_at collides.
    """

    filters = [
        GenerationJob.job_type == job_type,
        GenerationJob.target_id == target_id,
        GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
    ]
    if target_type is not None:
        filters.append(GenerationJob.target_type == target_type)
    return db.scalar(
        select(GenerationJob.id)
        .where(*filters)
        .order_by(GenerationJob.created_at, GenerationJob.id)
        .limit(1)
    )


def style_has_active_sibling_job(
    db: Session,
    *,
    style_id: str,
    exclude_job_id: str | None = None,
) -> bool:
    """True when another ACTIVE STYLE_ANALYZE job still targets the style.

    Duplicate style jobs existed before the route guards (and a retried job is
    active again), so the terminal-exit reset paths must only force the shared
    style row back to DRAFT once no sibling is still analyzing it. The job
    being finalized is excluded by id: terminal-exit paths claim the row via a
    Core UPDATE with synchronize_session=False, and exclusion keeps the check
    deterministic regardless of session/transaction visibility.
    """

    filters = [
        GenerationJob.job_type == "STYLE_ANALYZE",
        GenerationJob.target_type == "STYLE",
        GenerationJob.target_id == style_id,
        GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
    ]
    if exclude_job_id is not None:
        filters.append(GenerationJob.id != exclude_job_id)
    return db.scalar(select(GenerationJob.id).where(*filters).limit(1)) is not None


def has_active_derived_job(
    db: Session,
    *,
    job_types: set[str] | str,
    parent_candidate_id: str,
    repair_type: str | None = None,
    resolution: str | None = None,
    exclude_job_id: str | None = None,
) -> bool:
    """True when an ACTIVE derived-generation job still targets a child of the
    given parent candidate.

    Repair/upscale/region jobs point at the freshly created CHILD candidate, so
    ``has_active_job(target_id=original.id)`` is vacuous for them and duplicate
    requests each enqueued another paid job (the per-request idempotency keys —
    ``repair:{plan_id}``, ``upscale:{batch_id}:{resolution}`` — are always
    fresh). Match through CandidateLineage instead: the lineage row is written
    in the same transaction as the child and its job, so any committed ACTIVE
    job is reachable from its parent. JSON request_parameters matching (the
    exact repair_type / target_resolution the routes store) stays in Python on
    the small ACTIVE set, which keeps the query portable across SQLite and
    PostgreSQL — no dialect-specific JSON containment operators.
    """

    return bool(
        active_derived_jobs(
            db,
            job_types=job_types,
            parent_candidate_id=parent_candidate_id,
            repair_type=repair_type,
            resolution=resolution,
            exclude_job_id=exclude_job_id,
        )
    )


def active_derived_jobs(
    db: Session,
    *,
    job_types: set[str] | str,
    parent_candidate_id: str,
    repair_type: str | None = None,
    resolution: str | None = None,
    exclude_job_id: str | None = None,
) -> list[GenerationJob]:
    """ACTIVE derived-generation jobs under a parent, oldest first.

    Shared query for the creation guards (existence check) and the retry
    arbitration (oldest-wins loser selection).
    """

    if isinstance(job_types, str):
        job_types = {job_types}
    active_jobs = db.scalars(
        select(GenerationJob)
        .join(PageCandidate, PageCandidate.id == GenerationJob.target_id)
        .join(CandidateLineage, CandidateLineage.child_candidate_id == PageCandidate.id)
        .where(
            GenerationJob.job_type.in_(set(job_types)),
            GenerationJob.target_type == "PAGE_CANDIDATE",
            GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            CandidateLineage.parent_candidate_id == parent_candidate_id,
            *(
                [GenerationJob.id != exclude_job_id]
                if exclude_job_id is not None
                else []
            ),
        )
        .order_by(GenerationJob.created_at, GenerationJob.id)
    )
    matched: list[GenerationJob] = []
    for job in active_jobs:
        parameters = job.request_parameters or {}
        if repair_type is not None and parameters.get("repair_type") != repair_type:
            continue
        if resolution is not None and parameters.get("target_resolution") != resolution:
            continue
        matched.append(job)
    return matched


def create_job(
    db: Session,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    job_type: str,
    model_alias: str | None = None,
    catalog_model_id: str | None = None,
    request_parameters: dict | None = None,
    reference_asset_ids: list[str] | None = None,
    priority: int = 50,
    max_attempts: int = 3,
    idempotency_key: str | None = None,
    dependency_ids: list[str] | None = None,
    auto_commit: bool = True,
) -> GenerationJob:
    if idempotency_key:
        existing = db.scalar(
            select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
        )
        if existing:
            # Collapse in-flight and successful duplicates only. A FAILED or
            # CANCELLED row must not poison retries (PAGE_INSPECT target_id is
            # the inspected READY candidate; returning that terminal job makes
            # re-inspect a silent no-op).
            if existing.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                existing.idempotency_key = f"closed:{existing.id}"
                db.flush()
            else:
                return existing
    job = GenerationJob(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        job_type=job_type,
        model_alias=model_alias,
        catalog_model_id=catalog_model_id,
        request_parameters=request_parameters or {},
        priority=priority,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        status=JobStatus.WAITING,
    )
    try:
        # Keep the caller's pending work intact if another request won the
        # idempotency race between the initial lookup and this insert.
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        if idempotency_key:
            existing = db.scalar(
                select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
            )
            if existing:
                return existing
        raise
    for dependency_id in dependency_ids or []:
        db.add(JobDependency(job_id=job.id, depends_on_job_id=dependency_id))
    for asset_id in dict.fromkeys(reference_asset_ids or []):
        db.add(JobAssetReference(job_id=job.id, asset_id=asset_id))
    db.flush()
    if auto_commit:
        db.commit()
        db.refresh(job)
    return job


def dependencies_complete(db: Session, job: GenerationJob) -> bool:
    dependencies = list(
        db.scalars(
            select(GenerationJob)
            .join(
                JobDependency,
                JobDependency.depends_on_job_id == GenerationJob.id,
            )
            .where(JobDependency.job_id == job.id)
        )
    )
    return all(item.status == JobStatus.COMPLETED for item in dependencies)


def rq_retry_policy(job: GenerationJob):
    from rq import Retry

    remaining_retries = job.max_attempts - job.attempt_count - 1
    return Retry(max=remaining_retries, interval=[10, 30, 90]) if remaining_retries > 0 else None


def _job_already_advanced(job: GenerationJob) -> bool:
    """True when a worker or terminal path already owns the job row."""
    return (
        job.status in LEASED_JOB_STATUSES
        or job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}
        or job.lease_owner is not None
        or job.cancelled_at is not None
    )


def _transition_waiting_to_queued(
    db: Session,
    job: GenerationJob,
    *,
    error_code: str | None,
    error_message: str | None,
) -> bool:
    """Atomically mark WAITING as QUEUED. Returns False if another party advanced."""
    updated = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status == JobStatus.WAITING,
            GenerationJob.lease_owner.is_(None),
            GenerationJob.cancelled_at.is_(None),
        )
        .values(
            status=JobStatus.QUEUED,
            error_code=error_code,
            error_message=error_message,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    return updated.rowcount == 1


def enqueue_job(db: Session, job: GenerationJob) -> GenerationJob:
    settings = get_settings()
    apply_runtime_overrides(db, settings)
    db.refresh(job)
    if _job_already_advanced(job):
        return job
    if not dependencies_complete(db, job):
        return job
    # Legacy environment-level maintenance switch. Runtime LOCAL no longer
    # toggles this flag, so selecting LOCAL still executes immediately.
    if not settings.queue_enabled:
        if _job_already_advanced(job):
            return job
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job.id,
                GenerationJob.status == JobStatus.WAITING,
                GenerationJob.lease_owner.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="QUEUE_DISABLED",
                error_message="任务已保存，后台执行器当前未启用",
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        db.refresh(job)
        return job
    queue_mode = read_queue_mode(db)
    if queue_mode == "LOCAL":
        return _enqueue_locally(db, job, "本地后台执行器正在处理任务")

    # RQ_PENDING marks the crash window between the QUEUED commit and the
    # Redis enqueue: a process death here used to strand the row as QUEUED
    # with no payload and no recovery path. recover_pending_jobs re-enqueues
    # stale RQ_PENDING rows; a successful enqueue clears the marker.
    if not _transition_waiting_to_queued(
        db, job, error_code="RQ_PENDING", error_message=None
    ):
        return job

    connection = None
    try:
        from redis import Redis
        from rq import Queue

        connection = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
        )
        connection.ping()
        queue = Queue(settings.queue_name, connection=connection)
        queue.enqueue(
            "app.worker_tasks.execute_job",
            job.id,
            job_id=job.id,
            job_timeout=settings.job_timeout_seconds,
            retry=rq_retry_policy(job),
        )
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job.id,
                GenerationJob.status == JobStatus.QUEUED,
                GenerationJob.error_code == "RQ_PENDING",
            )
            .values(error_code=None, error_message=None)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.refresh(job)
        if _job_already_advanced(job):
            return job
        if queue_mode == "AUTO" and settings.environment == "development":
            return _adopt_queued_job_locally(db, job, "Redis 不可用，已切换到本地后台执行")
        db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job.id,
                GenerationJob.status == JobStatus.QUEUED,
                GenerationJob.lease_owner.is_(None),
            )
            .values(
                status=JobStatus.WAITING,
                error_code="QUEUE_UNAVAILABLE",
                error_message=(
                    "任务已保存；REDIS 模式要求 Redis 可用"
                    if queue_mode == "REDIS"
                    else "任务已保存；Redis 队列暂时不可用"
                ),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
    finally:
        if connection is not None:
            connection.close()
    db.refresh(job)
    return job


def _enqueue_locally(db: Session, job: GenerationJob, message: str) -> GenerationJob:
    if not _transition_waiting_to_queued(
        db, job, error_code="LOCAL_WORKER", error_message=message
    ):
        return job
    _submit_local(job.id)
    return job


def _adopt_queued_job_locally(db: Session, job: GenerationJob, message: str) -> GenerationJob:
    """Keep a QUEUED row for local execution without clobbering a worker advance."""
    db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status == JobStatus.QUEUED,
            GenerationJob.lease_owner.is_(None),
            GenerationJob.cancelled_at.is_(None),
        )
        .values(error_code="LOCAL_WORKER", error_message=message)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(job)
    if job.status == JobStatus.QUEUED and job.lease_owner is None:
        _submit_local(job.id)
    return job


def _submit_local(job_id: str) -> bool:
    """Submit once per API process, including during startup recovery."""

    with LOCAL_SUBMISSION_LOCK:
        if job_id in LOCAL_SUBMITTED_JOB_IDS:
            return False
        LOCAL_SUBMITTED_JOB_IDS.add(job_id)
    try:
        LOCAL_EXECUTOR.submit(_execute_locally, job_id)
    except Exception:
        with LOCAL_SUBMISSION_LOCK:
            LOCAL_SUBMITTED_JOB_IDS.discard(job_id)
        raise
    return True


def _warn_lease_lost_output_discarded(job_id: str, *, reason: str) -> None:
    """Issue #130: surface a lease-lost executor's discarded paid output.

    Losing the lease means the handler's uncommitted (possibly paid) output is
    rolled back while the reclaiming executor may re-run the provider call —
    a double spend that used to vanish with zero observability. Best-effort
    row lookup for job_type/attempt context; a lookup failure logs and falls
    back to placeholders rather than masking the warning.
    """

    from app.database import SessionLocal

    job_type, attempt = "unknown", "?"
    try:
        with SessionLocal() as db:
            row = db.get(GenerationJob, job_id)
            if row is not None:
                job_type, attempt = row.job_type, row.attempt_count
    except Exception:
        LOGGER.exception("lease-lost job row lookup failed for job %s", job_id)
    LOGGER.warning(
        "job %s (%s, attempt %s) lost its lease (%s); its uncommitted output "
        "was rolled back and discarded; the provider call may already have "
        "been billed and another executor may re-run it (double-spend risk)",
        job_id,
        job_type,
        attempt,
        reason,
    )


def _execute_locally(job_id: str) -> None:
    from app.model_adapters.base import ProviderAdapterError
    from app.worker_tasks import JobCancelledError, JobLeaseLostError, execute_job

    # Bounded-pin design: while the project sits at concurrency this loop
    # would otherwise poll forever, pinning a LOCAL executor thread and
    # rewriting the CONCURRENCY_LIMIT marker every pass. job_timeout_seconds
    # is the same wall-clock budget a real execution gets. When it trips, the
    # row is deliberately left WAITING+CONCURRENCY_LIMIT: this thread's id was
    # discarded from LOCAL_SUBMITTED_JOB_IDS in the finally below, so the next
    # recover_pending_jobs pass re-submits the job into a free slot.
    wait_started = time.monotonic()
    delay = 0.1
    try:
        while True:
            try:
                execute_job(job_id)
            except JobLeaseLostError as error:
                # execute_job's own handler already logged the rich warning
                # before returning; this catch is defense in depth for
                # lease-lost raises from seams outside its try block and must
                # stay equally loud (issue #130: paid output discarded).
                _warn_lease_lost_output_discarded(job_id, reason=str(error))
                return
            except JobCancelledError:
                return
            except ProviderAdapterError as error:
                if not getattr(error, "retryable", True):
                    return
            except Exception:
                # The backoff loop still self-heals, but a silent pass would
                # hide real failures (DB lock, programming error) with zero
                # observability while the job appears stuck.
                LOGGER.exception(
                    "local executor retry loop hit an unexpected error for job %s",
                    job_id,
                )
            from app.database import SessionLocal

            with SessionLocal() as db:
                job = db.get(GenerationJob, job_id)
                if not job or job.status in {
                    JobStatus.COMPLETED,
                    JobStatus.CANCELLED,
                    JobStatus.FAILED,
                }:
                    return
                if (
                    job.status == JobStatus.WAITING
                    and job.error_code == "CONCURRENCY_LIMIT"
                ):
                    waited = time.monotonic() - wait_started
                    if waited >= get_settings().job_timeout_seconds:
                        LOGGER.warning(
                            "job %s waited %.0fs for a project concurrency slot; "
                            "releasing this executor so recovery can re-submit it",
                            job_id,
                            waited,
                        )
                        return
            Event().wait(delay)
            delay = min(delay * 2, 5.0)
    finally:
        with LOCAL_SUBMISSION_LOCK:
            LOCAL_SUBMITTED_JOB_IDS.discard(job_id)


def restore_page_after_generation_exit(db: Session, page_candidate: PageCandidate) -> None:
    """Return a page from DRAFT_GENERATING to a stable status once its
    generating candidate reached a terminal state (failed/cancelled).

    Keeps the page operable instead of showing "generating" forever after a
    paid call failure; mirrors the reset previously only done on cancellation.
    """

    # Lock the page row before reading sibling statuses: two concurrent
    # terminal failures on sibling candidates otherwise each see the other's
    # uncommitted GENERATING stamp (READ COMMITTED), both hold, and the page
    # strands in DRAFT_GENERATING forever with no sweeper to free it. Mirrors
    # restore_page_after_inspection_exit; the lock serializes the restores so
    # the loser re-reads committed statuses.
    from app.services.ordinal_allocator import lock_entity

    page = lock_entity(db, MangaPage, page_candidate.page_id)
    if page is None or str(getattr(page.status, "value", page.status)) != "DRAFT_GENERATING":
        return
    # An ACTIVE sibling candidate (still queued/generating in the same batch)
    # owns the page's in-flight state: restoring to STORYBOARDED here would
    # leave the page stranded when the sibling later completes READY (the
    # success fence requires DRAFT_GENERATING). Hold DRAFT_GENERATING until
    # every sibling reaches a terminal candidate status.
    active_sibling = db.scalar(
        select(PageCandidate.id).where(
            PageCandidate.page_id == page.id,
            PageCandidate.id != page_candidate.id,
            PageCandidate.status.in_({"QUEUED", "GENERATING"}),
            PageCandidate.deleted_at.is_(None),
        )
    )
    if active_sibling:
        return
    other_ready = db.scalar(
        select(PageCandidate.id).where(
            PageCandidate.page_id == page.id,
            PageCandidate.id != page_candidate.id,
            PageCandidate.status.in_({"READY", "INSPECTED", "NEEDS_REVIEW"}),
            PageCandidate.deleted_at.is_(None),
        )
    )
    page.status = "DRAFT_READY" if page.selected_candidate_id or other_ready else "STORYBOARDED"


def restore_page_after_inspection_exit(
    db: Session,
    candidate: PageCandidate,
    error_code: str | None = None,
) -> None:
    """Return a page from FINAL_CHECKING to NEEDS_REPAIR once the inspection of
    its adopted candidate reached a terminal failure/cancel.

    A PAGE_INSPECT job owns no PageCandidate row (``candidate.job_id`` is only
    set for generation-type jobs), so ``restore_page_after_generation_exit``
    never matches it and the page used to show "checking" forever, blocking
    production readiness. Writes the same fields the inspection success path
    writes for failing results (NEEDS_REPAIR + continuity NEEDS_REVIEW), a
    terminal state the UI and readiness checks already render; the candidate
    row itself is never touched here because it may hold adopted work.

    #237-2: ``STALE_STORYBOARD_VERSION`` failures skip the flip when the page
    carries no review flag — the inspect was stale, the page is not broken.
    Fabricating NEEDS_REPAIR there routed the user into repair flows for a
    healthy page; the page simply stays parked in FINAL_CHECKING awaiting a
    fresh inspect (the inspect route enqueues from any page status, and the
    job's own error already tells the user to re-inspect). A page that WAS
    flagged mid-flight (NEEDS_REVIEW/NEEDS_RECHECK) still converges to the
    blocking restore so the flag keeps gating production.
    """

    if not candidate.is_selected:
        return
    # Deferred import: ordinal_allocator imports create_job from this module,
    # so a module-level import would be circular.
    from app.services.ordinal_allocator import lock_entity

    # Page row lock: the version fence bump below must serialize against
    # route-side storyboard edits (same convention as the routes).
    page = lock_entity(db, MangaPage, candidate.page_id)
    if page is None or page.selected_candidate_id != candidate.id:
        return
    if str(getattr(page.status, "value", page.status)) != "FINAL_CHECKING":
        return
    if error_code == "STALE_STORYBOARD_VERSION" and str(
        page.continuity_status or ""
    ) not in {"NEEDS_REVIEW", "NEEDS_RECHECK"}:
        LOGGER.info(
            "PAGE_INSPECT failed stale for page %s; leaving FINAL_CHECKING state "
            "intact (page not broken, fresh inspect required)",
            page.id,
        )
        return
    page.continuity_status = "NEEDS_REVIEW"
    page.status = PageStatus.NEEDS_REPAIR
    page.version += 1


def start_periodic_recovery() -> tuple[Thread, Event]:
    """Reclaim expired leases and re-enqueue waiting jobs while the API runs.

    RQ retries fire within the lease window and then stop, so a killed REDIS
    worker used to leave its job ACTIVE until the next API restart. A periodic
    pass in the long-lived API process closes that gap (and re-enqueues jobs
    parked as WAITING/QUEUE_UNAVAILABLE after a Redis outage). Returns the
    daemon thread with its stop event so embedders (tests) can park it.
    """

    stop = Event()

    def _loop() -> None:
        from app.database import SessionLocal
        from app.services.cli_executor import recover_abandoned_cli_runs

        settings = get_settings()
        interval = max(30.0, settings.job_lease_seconds / 2)
        while True:
            time.sleep(interval)
            if stop.is_set():
                return
            try:
                with SessionLocal() as db:
                    recover_pending_jobs(db)
            except Exception:
                LOGGER.exception("periodic job recovery pass failed")
            try:
                for run_id in recover_abandoned_cli_runs():
                    LOGGER.warning("released abandoned CLI run %s", run_id)
            except Exception:
                LOGGER.exception("periodic CLI run recovery failed")

    thread = Thread(target=_loop, name="mangaflow-job-recovery", daemon=True)
    thread.start()
    return thread, stop


def _workflow_node_blocks_recovery(db: Session, node_run_id: str) -> bool:
    """True when the owning workflow node forbids recovery-side scheduling.

    Recovery re-runs interrupted WORK: a legitimate requeue target's node_run
    is RUNNING, because reconcile_run flips the node to RUNNING immediately
    before enqueueing its job. A node_run still WAITING has never been
    scheduled — enqueueing it here would duplicate (and race) reconcile's
    scheduling role. The default graph makes that race catastrophic: the
    output.page job is planned with zero dependency rows (its only upstream,
    quality.inspect, gets no planning job), so its WAITING row looked
    recoverable during any approval pause; executing it raises
    PAGE_NOT_PRODUCTION_READY, retries burn, and reconcile flips the PAUSED
    run to FAILED mid-approval. A PAUSED run is likewise never auto-advanced
    by recovery, whatever the node's state.
    """

    node_run = db.get(WorkflowNodeRun, node_run_id)
    if node_run is None:
        return False
    if node_run.status == "WAITING":
        return True
    run = db.get(WorkflowRun, node_run.workflow_run_id)
    return run is not None and run.status == "PAUSED"


def _lease_reclaim_grace_seconds(settings) -> float:
    """How long past its expiry a lease must stay cold before reclaim.

    Issue #130 fence: an expired lease alone proves "no renewal in one full
    lease period", NOT "the executor is dead". The heartbeat thread can be
    transiently starved (GIL contention, a DB lock) while the paid provider
    call legitimately runs on — job_timeout_seconds (900s) is 7.5x the default
    lease (120s), so a slow-but-legal call occupies ~1/7 of its own timeout
    window. Reclaiming at first observed expiry re-ran the job under the live
    executor, and the loser's lease-fenced completion CAS then rolled back and
    discarded its already-paid output: a silent double spend.

    The janitor therefore reclaims only after the expiry has been cold for
    longer than two full heartbeat windows, so an executor that comes back
    within the fence can still finish and win the completion CAS (the row's
    lease_owner is untouched while unreclaimed):

        grace = max(2 * heartbeat_interval, lease / 3)
              = max(2 * 30s, 120s / 3) = 60s at the default 120s lease

    The lease/3 term keeps the tolerance proportional to the lease (a 3600s
    lease gets a 1200s grace) because longer leases guard longer, pricier
    calls where a wrong reclaim wastes more. The default derivation is
    deliberately capped at 60s so the reclaim boundary pinned by the existing
    recovery regression tests (a 60s-cold lease must still reclaim,
    tests/test_local_worker.py, tests/test_style_job_guards.py) keeps
    holding. settings.job_lease_reclaim_grace_seconds overrides the
    derivation (0 disables the fence). A NULL lease_expires_at carries no
    liveness trace at all (legacy/anomalous row) and stays immediately
    reclaimable.
    """

    from app.worker_tasks import _heartbeat_interval_seconds

    if settings.job_lease_reclaim_grace_seconds is not None:
        return float(settings.job_lease_reclaim_grace_seconds)
    lease_seconds = float(settings.job_lease_seconds)
    return max(2.0 * _heartbeat_interval_seconds(lease_seconds), lease_seconds / 3.0)


def recover_pending_jobs(db: Session) -> int:
    """Reclaim expired worker leases and re-enqueue recoverable jobs."""
    # Lazy import: keeps rq off the API import graph and avoids a module-level
    # cycle with the worker class that writes the timeout marker.
    from app.rq_windows import JOB_TIMEOUT_ERROR_CODE, JOB_TIMEOUT_ERROR_MESSAGE

    settings = get_settings()
    apply_runtime_overrides(db, settings)
    if not settings.queue_enabled:
        return 0
    now = utcnow()
    # Executor-liveness fence (issue #130): reclaim only leases that stayed
    # cold beyond the grace window — "confirmed silent", not "just expired".
    # The grace is at least two heartbeat windows (60s at defaults), during
    # which a starved-but-alive executor can still return and complete under
    # its own lease_owner. See _lease_reclaim_grace_seconds for the derivation.
    reclaim_cutoff = now - timedelta(seconds=_lease_reclaim_grace_seconds(settings))
    expired_jobs = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.status.in_(LEASED_JOB_STATUSES),
                or_(
                    GenerationJob.lease_expires_at.is_(None),
                    GenerationJob.lease_expires_at <= reclaim_cutoff,
                ),
            )
        )
    )
    workflow_run_ids: set[str] = set()
    for job in expired_jobs:
        observed_status = job.status
        observed_owner = job.lease_owner

        base_filter = [
            GenerationJob.id == job.id,
            GenerationJob.status == observed_status,
        ]
        if observed_owner is not None:
            base_filter.append(GenerationJob.lease_owner == observed_owner)
        else:
            base_filter.append(GenerationJob.lease_owner.is_(None))

        base_filter.append(
            or_(
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at <= reclaim_cutoff,
            )
        )

        if job.attempt_count >= job.max_attempts:
            # A force-killed horse leaves JOB_TIMEOUT on the row while the RQ
            # retry no-ops inside the live lease; a LOCAL thread pinned past
            # job_timeout_seconds leaves LOCAL_TIMEOUT (the heartbeat stopped
            # renewing and the lease expired afterwards). Surface the recorded
            # cause instead of the generic lease expiry. Status transitions
            # stay identical.
            if job.error_code == JOB_TIMEOUT_ERROR_CODE:
                terminal_code, terminal_message = (
                    JOB_TIMEOUT_ERROR_CODE,
                    JOB_TIMEOUT_ERROR_MESSAGE,
                )
            elif job.error_code == LOCAL_TIMEOUT_ERROR_CODE:
                terminal_code, terminal_message = (
                    LOCAL_TIMEOUT_ERROR_CODE,
                    LOCAL_TIMEOUT_TERMINAL_MESSAGE,
                )
            else:
                terminal_code, terminal_message = (
                    "LEASE_EXPIRED",
                    "执行器租约已过期，且已达到最大尝试次数",
                )
            updated = db.execute(
                update(GenerationJob)
                .where(*base_filter)
                .values(
                    status=JobStatus.FAILED,
                    error_code=terminal_code,
                    error_message=terminal_message,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if updated.rowcount == 1:
                # Best-effort closeout of attempts left unfinalized. Safe for
                # RQ/JOB_TIMEOUT: a force-killed horse can never finalize
                # afterwards. NOT safe for LOCAL_TIMEOUT: the local thread
                # stays alive past the expired lease, so its wedged provider
                # call may still return and finalize late — this guess can
                # stamp a genuinely-SUCCEEDED paid call as FAILED.
                # finalize_model_call_attempt repairs exactly that case (a
                # SUCCEEDED finalize over a FAILED row carrying one of these
                # terminal codes with usage still NULL upgrades the row), so
                # nothing here may write usage columns: the sweep cannot know
                # them, and NULL usage is the upgrade's discriminator.
                db.execute(
                    update(ModelCallAttempt)
                    .where(
                        ModelCallAttempt.job_id == job.id,
                        ModelCallAttempt.outcome.is_(None),
                    )
                    .values(
                        outcome="FAILED",
                        error_code=terminal_code,
                        error_message=terminal_message,
                        finished_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                page_candidate = db.scalar(
                    select(PageCandidate).where(PageCandidate.job_id == job.id)
                )
                if page_candidate:
                    page_candidate.status = "FAILED"
                    restore_page_after_generation_exit(db, page_candidate)
                asset_candidate = db.scalar(
                    select(AssetCandidate).where(AssetCandidate.job_id == job.id)
                )
                if asset_candidate:
                    asset_candidate.status = "FAILED"
                style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
                # #231: same guard as the worker-failure path — a confirmed
                # (CONFIRMED) or activated (ACTIVE) style must not be demoted
                # to DRAFT by a swept/expired sibling analysis job.
                if (
                    style
                    and str(getattr(style.status, "value", style.status) or "")
                    not in {"CONFIRMED", "ACTIVE"}
                    and not style_has_active_sibling_job(
                        db, style_id=job.target_id, exclude_job_id=job.id
                    )
                ):
                    style.status = "DRAFT"
                if job.job_type == "PAGE_INSPECT":
                    # The candidate/asset lookups above match nothing: an inspect
                    # job owns no PageCandidate row (job_id is only set for
                    # generation-type jobs) and its target_id names the inspected
                    # candidate, which must NOT be stamped FAILED — it may hold
                    # adopted work. Only the page state is restored, so a swept
                    # inspect lease cannot leave the page stuck FINAL_CHECKING
                    # (same guard as the worker-failure and cancel paths).
                    inspected = db.get(PageCandidate, job.target_id)
                    if inspected:
                        restore_page_after_inspection_exit(db, inspected)
                node_run = db.scalar(
                    select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id)
                )
                workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
                if node_run:
                    workflow_run_id = workflow_run_id or node_run.workflow_run_id
                    if node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        node_run.status = "FAILED"
                        node_run.error_code = terminal_code
                        node_run.error_message = terminal_message
                        node_run.finished_at = now
                if workflow_run_id:
                    workflow_run_ids.add(workflow_run_id)
        else:
            if job.error_code == JOB_TIMEOUT_ERROR_CODE:
                requeue_code, requeue_message = (
                    JOB_TIMEOUT_ERROR_CODE,
                    JOB_TIMEOUT_ERROR_MESSAGE,
                )
            elif job.error_code == LOCAL_TIMEOUT_ERROR_CODE:
                requeue_code, requeue_message = (
                    LOCAL_TIMEOUT_ERROR_CODE,
                    LOCAL_TIMEOUT_REQUEUE_MESSAGE,
                )
            else:
                requeue_code, requeue_message = (
                    "LEASE_EXPIRED",
                    "执行器已退出，任务等待重新执行",
                )
            db.execute(
                update(GenerationJob)
                .where(*base_filter)
                .values(
                    status=JobStatus.WAITING,
                    error_code=requeue_code,
                    error_message=requeue_message,
                    started_at=None,
                    scheduled_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
    if expired_jobs:
        db.commit()
        db.expire_all()
    for workflow_run_id in workflow_run_ids:
        # One poisoned run must not skip the remaining reconciliations or the
        # WAITING/QUEUED requeue below; this pass also runs inside the API
        # lifespan at startup, where an unguarded raise would abort boot.
        try:
            from app.services.workflow_engine import reconcile_run

            reconcile_run(db, workflow_run_id)
        except Exception:
            LOGGER.exception("workflow run %s reconcile failed during recovery", workflow_run_id)
            # Drop partial writes from the failed reconcile so they cannot
            # leak into the next run's pass or the requeue phase below.
            db.rollback()

    rq_pending_cutoff = utcnow() - timedelta(seconds=10)
    # Payload-loss healing: a QUEUED row whose RQ_PENDING marker was already
    # cleared by a successful enqueue can still strand when Redis loses the
    # payload afterwards (flush, maxmemory eviction, an external Redis without
    # persistence). No branch below matched QUEUED + error_code IS NULL, so
    # such a row waited forever while retry_run refuses a QUEUED row. The long
    # cutoff keeps this clear of both the in-flight enqueue (10s grace above)
    # and healthy queue backlog; re-enqueueing a job whose payload is actually
    # still queued is harmless — the claim CAS arbitrates and the duplicate
    # payload no-ops against the already-claimed row.
    stranded_queued_cutoff = utcnow() - timedelta(seconds=300)
    jobs = list(
        db.scalars(
            select(GenerationJob)
            .where(
                or_(
                    GenerationJob.status == JobStatus.WAITING,
                    and_(
                        GenerationJob.status == JobStatus.QUEUED,
                        GenerationJob.error_code == "LOCAL_WORKER",
                    ),
                    # Stranded Redis handoffs: the QUEUED commit landed but
                    # the process died before (or while) enqueueing. The age
                    # threshold keeps this from racing the in-flight enqueue.
                    and_(
                        GenerationJob.status == JobStatus.QUEUED,
                        GenerationJob.error_code == "RQ_PENDING",
                        GenerationJob.updated_at < rq_pending_cutoff,
                    ),
                    and_(
                        GenerationJob.status == JobStatus.QUEUED,
                        GenerationJob.error_code.is_(None),
                        GenerationJob.updated_at < stranded_queued_cutoff,
                    ),
                )
            )
            .order_by(GenerationJob.priority.desc(), GenerationJob.created_at)
        )
    )
    recovered = 0
    reconciled_run_ids: set[str] = set()
    for job in jobs:
        with LOCAL_SUBMISSION_LOCK:
            already_submitted = job.id in LOCAL_SUBMITTED_JOB_IDS
        if already_submitted or not dependencies_complete(db, job):
            continue
        node_run_id = (job.request_parameters or {}).get("workflow_node_run_id")
        if node_run_id and _workflow_node_blocks_recovery(db, node_run_id):
            # Unscheduled workflow nodes (and every node of a PAUSED run) are
            # reconcile's to schedule, not recovery's. Reconciling the run
            # instead of enqueueing keeps the crash-window self-heal — a child
            # stranded WAITING because a reconcile commit was lost still gets
            # scheduled — while barrier/pause-gated nodes stay untouched
            # (reconcile's parent gate refuses them). Skipped rows do not
            # count as recovered.
            node_run = db.get(WorkflowNodeRun, node_run_id)
            run_id = node_run.workflow_run_id if node_run else None
            if run_id and run_id not in reconciled_run_ids:
                reconciled_run_ids.add(run_id)
                try:
                    from app.services.workflow_engine import reconcile_run

                    reconcile_run(db, run_id)
                except Exception:
                    LOGGER.exception(
                        "workflow run %s reconcile failed during recovery", run_id
                    )
                    db.rollback()
            continue
        if job.status == JobStatus.QUEUED:
            # Re-adopt a local or stranded-Redis row without clobbering
            # concurrent progress: if a worker already claimed the row, the
            # conditional update misses and this round skips the job instead
            # of writing WAITING over PREPARING.
            readopted = db.execute(
                update(GenerationJob)
                .where(
                    GenerationJob.id == job.id,
                    GenerationJob.status == JobStatus.QUEUED,
                    or_(
                        GenerationJob.error_code.in_(["LOCAL_WORKER", "RQ_PENDING"]),
                        and_(
                            GenerationJob.error_code.is_(None),
                            GenerationJob.updated_at < stranded_queued_cutoff,
                        ),
                    ),
                    GenerationJob.lease_owner.is_(None),
                    GenerationJob.cancelled_at.is_(None),
                )
                .values(status=JobStatus.WAITING, error_code=None, error_message=None)
                .execution_options(synchronize_session=False)
            )
            if readopted.rowcount != 1:
                continue
            db.commit()
            db.expire(job)
        # enqueue_job refreshes the row first: a job deleted between the
        # SELECT above and this call raises ObjectDeletedError, which used to
        # starve the requeue of every remaining job in the pass.
        try:
            enqueue_job(db, job)
        except Exception:
            LOGGER.exception("job %s requeue failed during recovery", job.id)
            db.rollback()
            continue
        recovered += 1
    return recovered + sweep_lost_model_call_attempts(db)


WORKER_LOST_MARGIN_SECONDS = 300


def sweep_lost_model_call_attempts(db: Session) -> int:
    """Converge unfinalized audit rows to FAILED after every deadline passed.

    A worker killed between ``begin_model_call_attempt`` and finalize leaves
    the row with ``outcome IS NULL`` forever: cost views exclude it (real
    money invisible), the summary counts it as pending eternally, and no
    operator signal distinguishes "in flight" from "lost in a crash". Once
    the attempt is older than the job's hard timeout plus lease plus margin,
    no live worker can still be planning to finalize it, so the row is
    provably lost. The finalize CAS still wins if a straggler ever shows up.
    """

    settings = get_settings()
    cutoff = utcnow() - timedelta(
        seconds=settings.job_timeout_seconds
        + settings.job_lease_seconds
        + WORKER_LOST_MARGIN_SECONDS
    )
    updated = db.execute(
        update(ModelCallAttempt)
        .where(
            ModelCallAttempt.outcome.is_(None),
            ModelCallAttempt.started_at < cutoff,
        )
        .values(
            outcome="FAILED",
            error_code="WORKER_LOST",
            error_message="执行器在调用期间丢失，审计行按失败收敛",
            finished_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount:
        db.commit()
    return updated.rowcount


def mark_job_failed(
    db: Session,
    job: GenerationJob,
    error_code: str,
    error_message: str,
    *,
    candidate_status: str = "FAILED",
) -> str | None:
    """Mark a job and its visible targets as failed without committing."""

    now = utcnow()
    # Conditional claim instead of a read-check write: never clobber a live
    # worker's unexpired lease, but still fail stuck rows whose lease died.
    claimed = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status.not_in(
                {JobStatus.COMPLETED, JobStatus.CANCELLED}
            ),
            or_(
                GenerationJob.lease_owner.is_(None),
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at <= now,
            ),
        )
        .values(
            status=JobStatus.FAILED,
            error_code=error_code,
            error_message=error_message[:500],
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        return None
    state = sa_inspect(job)
    if state.persistent:
        # The caller's copy still shows the pre-claim status; reload it.
        db.expire(job)
    page_candidate = db.scalar(select(PageCandidate).where(PageCandidate.job_id == job.id))
    if page_candidate:
        page_candidate.status = candidate_status
        restore_page_after_generation_exit(db, page_candidate)
    asset_candidate = db.scalar(
        select(AssetCandidate).where(AssetCandidate.job_id == job.id)
    )
    if asset_candidate:
        asset_candidate.status = "FAILED"
    style = db.get(StyleProfile, job.target_id) if job.target_type == "STYLE" else None
    if style:
        style.status = "DRAFT"

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id
        if node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            node_run.status = "FAILED"
            node_run.error_code = error_code
            node_run.error_message = error_message[:500]
            node_run.finished_at = now
    return workflow_run_id


def arbitrate_inspection_creation(db: Session, job: GenerationJob) -> GenerationJob:
    """Post-insert oldest-wins arbitration for PAGE_INSPECT creation.

    The idempotency key embeds candidate/page versions, so the two creation
    sides (route, workflow reconciler) mint different-keyed jobs for the
    same candidate when a fence bumps the page between their version reads:
    key equality collapses same-key races only, and the version-agnostic
    adoption SELECT plus the route's ``has_active_job`` only cover their own
    check-then-act windows. Whichever job committed first is the older and
    wins; the younger duplicate is cancelled (its sweep touches no candidate
    — inspect jobs never set ``candidate.job_id``) and the older ACTIVE job
    is returned for the caller to use.
    """

    oldest = oldest_active_job_id(
        db,
        job_type="PAGE_INSPECT",
        target_id=str(job.target_id),
        target_type=job.target_type,
    )
    if oldest is None or oldest == job.id:
        return job
    older = db.get(GenerationJob, oldest)
    mark_job_cancelled(db, job)
    return older


def mark_job_cancelled(db: Session, job: GenerationJob) -> GenerationJob:
    """Mark a job and its visible target as cancelled without committing."""

    now = utcnow()
    claimed = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status.not_in(
                {JobStatus.COMPLETED, JobStatus.CANCELLED}
            ),
        )
        .values(
            status=JobStatus.CANCELLED,
            cancelled_at=now,
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.refresh(job)
    if claimed.rowcount != 1:
        return job
    page_candidate = db.scalar(select(PageCandidate).where(PageCandidate.job_id == job.id))
    if page_candidate and page_candidate.status not in {
        "READY",
        "FAILED",
        "CANCELLED",
        "INSPECTED",
        "NEEDS_REVIEW",
    }:
        page_candidate.status = "CANCELLED"
        restore_page_after_generation_exit(db, page_candidate)
    asset_candidate = db.scalar(select(AssetCandidate).where(AssetCandidate.job_id == job.id))
    if asset_candidate and asset_candidate.status not in {"READY", "FAILED", "CANCELLED"}:
        asset_candidate.status = "CANCELLED"

    if job.job_type == "STYLE_ANALYZE":
        style = db.get(StyleProfile, job.target_id)
        if (
            style
            and style.status.value == "ANALYZING"
            # A duplicate may still be analyzing the same style row; forcing
            # DRAFT here would let the UI fire another paid analyze while the
            # sibling runs (last-writer-wins on style.profile otherwise).
            and not style_has_active_sibling_job(
                db, style_id=job.target_id, exclude_job_id=job.id
            )
        ):
            style.status = "DRAFT"
            style.version += 1

    if job.job_type == "PAGE_INSPECT":
        # The candidate/asset lookups above match nothing: an inspect job owns
        # no PageCandidate row (job_id is only set for generation-type jobs)
        # and its target_id names an existing candidate that must NOT be
        # stamped CANCELLED — it may hold adopted work. Only the page state is
        # restored, so a swept inspect job cannot leave the page FINAL_CHECKING.
        inspected = db.get(PageCandidate, job.target_id)
        if inspected:
            restore_page_after_inspection_exit(db, inspected)

    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    if node_run and node_run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
        node_run.status = "CANCELLED"
        node_run.finished_at = utcnow()
    return job


TERMINAL_RUN_STATUSES = {"COMPLETED", "CANCELLED", "FAILED"}


def _escalate_cancel_to_revived_run(
    db: Session, job: GenerationJob, node_run: WorkflowNodeRun, run: WorkflowRun | None
) -> bool:
    """Stale-FAILED revival escalation for cancel_job; returns True on escalation.

    Re-reads the committed run status (a column select, not the possibly stale
    identity-map copy) and, when non-terminal, escalates through the same
    cancel_run path as cancel_job's early branch so the revived run, its
    nodes, and its late jobs are all terminalized; the node_run refresh first
    drops the stale pre-escalation identity-map read so cancel_run's node
    sweep sees the revived RUNNING node. Callers must hold NO job-row write
    lock: cancel_run takes the run lock and then job locks (run→job), and
    calling it while holding a job-row lock would invert that order against a
    concurrent direct cancel_run on the same run.
    """

    current_run_status = db.scalar(
        select(WorkflowRun.status).where(WorkflowRun.id == node_run.workflow_run_id)
    )
    if current_run_status in ({None} | TERMINAL_RUN_STATUSES):
        return False
    from app.services.workflow_engine import cancel_run

    if run is None:
        run = db.get(WorkflowRun, node_run.workflow_run_id)
    db.refresh(node_run)
    cancel_run(db, run)
    db.refresh(job)
    return True


def cancel_job(db: Session, job: GenerationJob) -> GenerationJob:
    if job.status == JobStatus.COMPLETED:
        return job
    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    run = None
    if node_run:
        run = db.get(WorkflowRun, node_run.workflow_run_id)
        if run and run.status not in TERMINAL_RUN_STATUSES:
            from app.services.workflow_engine import cancel_run

            cancel_run(db, run)
            db.refresh(job)
            return job
        # Stale-FAILED revival escalation, before the claim: the ORM read
        # above can say FAILED while a concurrent reset_for_retry has already
        # committed a FAILED→RUNNING revival. Take the run lock BEFORE any
        # job-row claim (lock order run→job, same as a direct cancel_run), so
        # a revival that is already committed is cancelled atomically through
        # the early-branch path instead of falling into mark_job_cancelled.
        if _escalate_cancel_to_revived_run(db, job, node_run, run):
            return job
    mark_job_cancelled(db, job)
    # Commit before any escalation: mark_job_cancelled's UPDATE holds the
    # job-row write lock until commit, and calling cancel_run under that lock
    # would acquire the run lock in a job→run order — the inverse of a
    # concurrent direct cancel_run (which holds the run lock while its sweeps
    # take job locks), an AB-BA deadlock window. Committing here releases the
    # job-row lock first.
    db.commit()
    if node_run is not None:
        # Stale-FAILED revival escalation, after the claim (the serialization
        # point): a concurrent reset_for_retry can commit its FAILED→RUNNING
        # revival while our mark_job_cancelled claim blocks on the retry's job
        # row lock. In that interleave our earlier run reads said FAILED, the
        # claim then lands on the retry's freshly committed WAITING row, and
        # without this re-read the revived run stays RUNNING forever behind a
        # CANCELLED mid-chain node — a zombie run permanently locking the
        # project scope. Re-reading AFTER our commit means no job-row lock is
        # held when cancel_run takes the run lock.
        _escalate_cancel_to_revived_run(db, job, node_run, run)
    db.refresh(job)
    return job


def _restore_revived_run(db: Session, revival_snapshot: dict) -> bool:
    """CAS the revived run (and its revived nodes/jobs) back to pre-retry values.

    Returns False when the run is no longer in the non-terminal state the
    revival created (RUNNING/PAUSED) — by then another owner (the cancel_job
    escalation's cancel_run, or reconcile) has terminalized it and owns the
    outcome. Every write is conditional on the exact revived value, so a row
    already re-terminalized by someone else is left untouched.
    """

    run_snapshot = revival_snapshot["run"]
    run_status = db.scalar(
        select(WorkflowRun.status).where(WorkflowRun.id == run_snapshot["id"])
    )
    if run_status not in {"RUNNING", "PAUSED"}:
        return False
    # run.version is deliberately left incremented: it is a monotonic change
    # counter, and rewinding it could clobber a concurrent writer's bump.
    db.execute(
        update(WorkflowRun)
        .where(
            WorkflowRun.id == run_snapshot["id"],
            WorkflowRun.status.in_(("RUNNING", "PAUSED")),
        )
        .values(status=run_snapshot["status"], finished_at=run_snapshot["finished_at"])
        .execution_options(synchronize_session=False)
    )
    node_snapshot = revival_snapshot["node"]
    if node_snapshot is not None:
        db.execute(
            update(WorkflowNodeRun)
            .where(
                WorkflowNodeRun.id == node_snapshot["id"],
                WorkflowNodeRun.status == "RUNNING",
            )
            .values(
                status=node_snapshot["status"],
                finished_at=node_snapshot["finished_at"],
                error_code=node_snapshot["error_code"],
                error_message=node_snapshot["error_message"],
            )
            .execution_options(synchronize_session=False)
        )
    for entry in revival_snapshot["revived_nodes"]:
        db.execute(
            update(WorkflowNodeRun)
            .where(
                WorkflowNodeRun.id == entry["id"],
                WorkflowNodeRun.status == "WAITING",
            )
            .values(
                status=entry["status"],
                finished_at=entry["finished_at"],
                error_code=entry["error_code"],
                error_message=entry["error_message"],
            )
            .execution_options(synchronize_session=False)
        )
        job_entry = entry["job"]
        if job_entry is not None:
            db.execute(
                update(GenerationJob)
                .where(
                    GenerationJob.id == job_entry["id"],
                    GenerationJob.status == JobStatus.WAITING,
                )
                .values(
                    status=JobStatus.CANCELLED,
                    cancelled_at=job_entry["cancelled_at"],
                    finished_at=job_entry["finished_at"],
                    error_code=job_entry["error_code"],
                    error_message=job_entry["error_message"],
                )
                .execution_options(synchronize_session=False)
            )
    return True


def _compensate_retry_revival(
    db: Session,
    job: GenerationJob,
    revival_snapshot: dict,
    *,
    observed_status: JobStatus,
) -> bool:
    """Undo a committed reset_for_retry revival in its own transaction.

    Separate from reset_for_retry so the compensation is deterministically
    testable and can never leave half a revival in the caller's transaction.
    The job row CAS claims the exact post-commit status the caller observed
    (CANCELLED for the cancel race, WAITING for the sibling-arbitration loss);
    a miss means the row moved on again and belongs to whoever moved it. The
    run is restored only while it still carries the revival's non-terminal
    shape — a run already terminalized by the cancel-side escalation keeps
    that outcome, and the whole compensation rolls back so it does not
    contradict a user-visible CANCELLED run by re-failing the job.
    Returns True when the revival was rolled back.
    """

    if revival_snapshot.get("job") is None:
        return False
    values = dict(revival_snapshot["job"])
    preimage_terminal = values.get("status") in {
        JobStatus.FAILED,
        JobStatus.NEEDS_REVIEW,
        JobStatus.CANCELLED,
    }
    if not preimage_terminal and observed_status in {
        JobStatus.WAITING,
        JobStatus.QUEUED,
    }:
        # Arbitration undo of an ACTIVE revival whose pre-retry state was
        # itself dispatchable (a WAITING slot-stuck row): "restoring" that
        # preimage writes WAITING over WAITING — reported as compensated
        # while the duplicate stays alive. The loser must never dispatch,
        # so it is cancelled instead.
        values["status"] = JobStatus.CANCELLED
        values["cancelled_at"] = utcnow()
        values["finished_at"] = utcnow()
    try:
        claimed = db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == job.id,
                GenerationJob.status == observed_status,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            db.rollback()
            return False
        if revival_snapshot["run"] is not None and not _restore_revived_run(
            db, revival_snapshot
        ):
            db.rollback()
            return False
        db.commit()
        return True
    except Exception:
        # The compensation is best-effort by design: its counterpart
        # (cancel_job's post-claim escalation) converges the same zombie from
        # the other side, so a failed compensation must surface loudly but
        # never mask the caller's original outcome.
        db.rollback()
        LOGGER.exception("post-retry revival compensation failed for job %s", job.id)
        return False


def _verify_retry_revival_post_commit(
    db: Session, job: GenerationJob, revival_snapshot: dict
) -> bool:
    """Re-read the committed revival; compensate when it lost a post-commit race.

    Returns True when the revival survived and reset_for_retry should enqueue.
    Two losers are handled:

    - CANCELLED flip: a concurrent cancel_job whose run read predated the
      revival claims the freshly committed WAITING row right after our
      commit. The cancel side also escalates to cancel_run (see cancel_job),
      but this compensation restores the exact pre-retry FAILED state when
      the revived run is still non-terminal, so the two heals converge on one
      consistent outcome instead of a zombie RUNNING run.
    - Sibling arbitration: two concurrent retries of sibling FAILED jobs can
      both pass the pre-CAS has_active_job guard (each revival is invisible
      until it commits) and both commit, which previously meant either two
      paid runs or — when each guard did see the other — both retries 409ing
      with zero runs. Oldest-wins arbitration (the same rule the R1
      story_parse guard uses) lets exactly one of the concurrent RETRIES
      survive: a strictly older committed ACTIVE sibling (created_at, then
      id) means this revival is the duplicate and must be undone. A
      legitimately pre-existing sibling never reaches here — the pre-CAS
      guard already rejected it. Residual windows: (a) the single-survivor
      guarantee covers concurrent retries only — a manual-route job created
      on the same target between this retry's pre-CAS guard and its commit is
      younger than the revived row, so oldest-wins keeps this revival and
      never arbitrates the manual job, and both dispatches can proceed;
      (b) a retry whose verification lands entirely inside another retry's
      claim-to-commit window sees only itself and also survives (two
      dispatches); (c) a job the worker already moved past WAITING verifies
      as advanced and skips arbitration. Closing (b)/(c) fully would need a
      lock anchored on the parent or serializable isolation.
    """

    db.refresh(job)
    if job.status == JobStatus.CANCELLED:
        if _compensate_retry_revival(
            db, job, revival_snapshot, observed_status=JobStatus.CANCELLED
        ):
            db.refresh(job)
            raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试")
        db.refresh(job)
        return False
    if job.status not in {JobStatus.WAITING, JobStatus.QUEUED}:
        # Terminal or already-leased: the row belongs to a worker or a
        # terminal transition — never compensate over it.
        return True
    if job.lease_owner is not None:
        # Leased QUEUED row: a worker owns the dispatch; the arbitration
        # lost the race and cannot undo an in-flight call.
        return True
    if job.job_type in RETRY_MUTEX_JOB_TYPES and job.target_id:
        oldest = oldest_active_job_id(
            db,
            job_type=job.job_type,
            target_id=str(job.target_id),
            target_type=job.target_type,
        )
        if oldest is not None and oldest != job.id:
            compensated = _compensate_retry_revival(
                db, job, revival_snapshot, observed_status=job.status
            )
            if not compensated:
                # The undo CAS missed (or the run moved on): the younger
                # revival stays committed and dispatchable — the duplicate
                # this arbitration exists to prevent — while the caller only
                # sees the generic 409 below. Surface it for operator triage.
                LOGGER.warning(
                    "sibling-arbitration loss for job %s on target %s/%s could not be "
                    "compensated; the revived job stays committed and a duplicate "
                    "dispatch is now likely",
                    job.id,
                    job.target_type,
                    job.target_id,
                )
            raise HTTPException(status_code=409, detail="任务状态已变化，请重试")
    # Derived jobs target their own child candidate, so the target-scoped
    # arbitration above is vacuous for them; match through the parent lineage
    # with the same intent filters the pre-CAS guard uses. Oldest wins: the
    # younger concurrent revival compensates instead of double-dispatching.
    if job.job_type in {"PAGE_REPAIR", "PAGE_UPSCALE", "PAGE_REGION_REGENERATE"}:
        parameters = job.request_parameters or {}
        parent_id = parameters.get("original_candidate_id")
        if parent_id:
            siblings = active_derived_jobs(
                db,
                job_types={job.job_type},
                parent_candidate_id=str(parent_id),
                repair_type=parameters.get("repair_type"),
                resolution=parameters.get("target_resolution"),
            )
            if siblings and siblings[0].id != job.id:
                compensated = _compensate_retry_revival(
                    db, job, revival_snapshot, observed_status=job.status
                )
                if not compensated:
                    LOGGER.warning(
                        "derived sibling-arbitration loss for job %s under parent "
                        "%s could not be compensated; a duplicate dispatch is now "
                        "likely",
                        job.id,
                        parent_id,
                    )
                raise HTTPException(status_code=409, detail="任务状态已变化，请重试")
    return True


def reset_for_retry(db: Session, job: GenerationJob) -> GenerationJob:
    if job.status not in {JobStatus.FAILED, JobStatus.NEEDS_REVIEW, JobStatus.WAITING}:
        return job
    # Pre-image of every column the revival below rewrites, read from the
    # committed row (the caller's ORM copy can be stale). If the committed
    # revival later loses a post-commit race, the compensation restores these
    # exact values instead of guessing at the pre-retry shape. The lease pair
    # is included for contract completeness: the revival CAS's
    # lease_owner IS NULL precondition pins the pre-image to None/None here,
    # because every lease writer (the cancel/fail/sweep/retry claims in this
    # module and the worker_tasks claim/finalize CASes) writes lease_owner and
    # lease_expires_at together — so snapshotting them lets the compensation
    # restore them explicitly instead of leaning on that writer invariant.
    preimage = db.execute(
        select(
            GenerationJob.status,
            GenerationJob.error_code,
            GenerationJob.error_message,
            GenerationJob.progress,
            GenerationJob.started_at,
            GenerationJob.finished_at,
            GenerationJob.cancelled_at,
            GenerationJob.scheduled_at,
            GenerationJob.lease_owner,
            GenerationJob.lease_expires_at,
        ).where(GenerationJob.id == job.id)
    ).first()
    revival_snapshot: dict = {
        "job": (
            {
                "status": preimage.status,
                "error_code": preimage.error_code,
                "error_message": preimage.error_message,
                "progress": preimage.progress,
                "started_at": preimage.started_at,
                "finished_at": preimage.finished_at,
                "cancelled_at": preimage.cancelled_at,
                "scheduled_at": preimage.scheduled_at,
                "lease_owner": preimage.lease_owner,
                "lease_expires_at": preimage.lease_expires_at,
            }
            if preimage is not None
            else None
        ),
        "run": None,
        "node": None,
        "revived_nodes": [],
    }
    # Single conditional claim instead of read-modify-write: a worker lease or
    # a cancellation landing between the caller's read and this write must win
    # over the retry (no clobbered lease, no resurrection), mirroring
    # _transition_waiting_to_queued and mark_job_cancelled.
    claimed = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status.in_(
                [JobStatus.FAILED, JobStatus.NEEDS_REVIEW, JobStatus.WAITING]
            ),
            GenerationJob.lease_owner.is_(None),
            GenerationJob.cancelled_at.is_(None),
        )
        .values(
            status=JobStatus.WAITING,
            error_code=None,
            error_message=None,
            progress=0,
            started_at=None,
            finished_at=None,
            cancelled_at=None,
            lease_owner=None,
            lease_expires_at=None,
            scheduled_at=utcnow() + timedelta(seconds=1),
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        # Another party advanced the row; the caller must not enqueue over it.
        db.rollback()
        raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试")
    # Same-target mutex for the third entry point, retry (issues #125/#124):
    # the manual route guards with has_active_job, but a retried FAILED job is
    # invisible to that path's idempotency key — create_job collapsed the dead
    # row's key to closed:{id} when a newer job took the key (PAGE_INSPECT), or
    # the entries never shared a key namespace at all (SOURCE_PARSE route vs
    # workflow agent.parse) — so reviving it here could run a second paid call
    # next to a live manual/workflow job on the same target. The claim above
    # just moved this row back to WAITING, so it must be excluded by id (a
    # WAITING job being retried would otherwise trip its own guard).
    if (
        job.job_type in RETRY_MUTEX_JOB_TYPES
        and job.target_id
        and has_active_job(
            db,
            job_type=job.job_type,
            target_id=str(job.target_id),
            target_type=job.target_type,
            exclude_job_id=job.id,
        )
    ):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="该目标已有进行中的同类任务，请等待完成后再重试",
        )
    # Derived jobs (repair/upscale/region) target their own CHILD candidate,
    # so the target-scoped mutex above is vacuous for them: match through the
    # parent lineage instead, with the same intent filters the creation
    # routes use (repair_type / target_resolution).
    if job.job_type in {"PAGE_REPAIR", "PAGE_UPSCALE", "PAGE_REGION_REGENERATE"}:
        parameters = job.request_parameters or {}
        parent_id = parameters.get("original_candidate_id")
        if (
            parent_id
            and has_active_derived_job(
                db,
                job_types={job.job_type},
                parent_candidate_id=str(parent_id),
                repair_type=parameters.get("repair_type"),
                resolution=parameters.get("target_resolution"),
                exclude_job_id=job.id,
            )
        ):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="该原始候选已有进行中的同类任务，请等待完成后再重试",
            )
    # The claim above owns the job row, which serializes this revival against
    # concurrent cancel_run/worker claims on the same job, so the cross-table
    # writes below cannot clobber a concurrent terminal transition.
    # Resolve the run via the node link too: an adopted route-created inspect
    # job carries no workflow_run_id in request_parameters, and without the
    # fallback its retry would skip the WorkflowRun/WorkflowNodeRun revival.
    node_run = db.scalar(select(WorkflowNodeRun).where(WorkflowNodeRun.job_id == job.id))
    workflow_run_id = (job.request_parameters or {}).get("workflow_run_id")
    if node_run:
        workflow_run_id = workflow_run_id or node_run.workflow_run_id
    if workflow_run_id:
        run = db.get(WorkflowRun, workflow_run_id)
        if run and run.status == "FAILED":
            # Scope-level one-active-run guard (mirrors planning.py's
            # start guard): retry_run clones a fresh RUNNING run from the
            # FAILED one, and this FAILED run's kept cause-of-failure job
            # still offers Retry — reviving it here would run two concurrent
            # paid workflows on one scope. The claim above is pre-commit, so
            # the rollback cleanly undoes it. The check-then-act needs the
            # same serialization point create_workflow_run uses: lock the
            # definition row first, or a concurrent retry_run passes its own
            # committed-state check and both runs end up RUNNING.
            from app.services.ordinal_allocator import lock_entity

            lock_entity(db, WorkflowDefinition, run.workflow_id)
            sibling_active_run = db.scalar(
                select(WorkflowRun.id).where(
                    WorkflowRun.workflow_id == run.workflow_id,
                    WorkflowRun.scope_type == run.scope_type,
                    WorkflowRun.scope_id == run.scope_id,
                    WorkflowRun.id != run.id,
                    WorkflowRun.status.not_in({"COMPLETED", "CANCELLED", "FAILED"}),
                )
            )
            if sibling_active_run:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="该范围已有进行中的运行，不能通过重试任务再唤醒旧运行",
                )
            revival_snapshot["run"] = {
                "id": run.id,
                "status": run.status,
                "finished_at": run.finished_at,
            }
            run.status = "RUNNING"
            run.finished_at = None
            run.version += 1
            # Revive the swept CANCELLED tail together with the run. Under a
            # FAILED run a CANCELLED node/job can ONLY be
            # _sweep_stranded_children output: cancel_run terminalizes the
            # whole run CANCELLED and cancel_job cancels the run, so neither
            # can leave a CANCELLED child behind a FAILED run. Swept jobs
            # never ran, so their idempotency keys are intact (the
            # closed:{id} collapse only applies when create_job re-creates
            # the row), and any candidate the sweep stamped CANCELLED is
            # harmless on rerun — generation re-attaches the candidate by
            # target_id and resets its status, while inspect jobs own no
            # candidate row. Late inspect jobs with no node link stay
            # CANCELLED on purpose: they have no node_run row to revive, and
            # reconcile's _create_inspection_job re-creates a fresh one
            # (create_job collapses the CANCELLED row's key). Without this
            # revival the tail stays dead forever — reconcile only schedules
            # WAITING nodes and the tail breaks the all-COMPLETED check, so
            # the run falls through to a permanent RUNNING zombie whose
            # scope can never start another run; revived barrier nodes
            # re-barrier to WAITING_APPROVAL (run PAUSED), which is the
            # correct resume semantics.
            for stranded in db.scalars(
                select(WorkflowNodeRun).where(
                    WorkflowNodeRun.workflow_run_id == run.id,
                    WorkflowNodeRun.status == "CANCELLED",
                )
            ):
                stranded_entry = {
                    "id": stranded.id,
                    "status": stranded.status,
                    "finished_at": stranded.finished_at,
                    "error_code": stranded.error_code,
                    "error_message": stranded.error_message,
                    "job": None,
                }
                stranded.status = "WAITING"
                stranded.finished_at = None
                stranded.error_code = None
                stranded.error_message = None
                stranded_job = (
                    db.get(GenerationJob, stranded.job_id) if stranded.job_id else None
                )
                if stranded_job and stranded_job.status == JobStatus.CANCELLED:
                    stranded_entry["job"] = {
                        "id": stranded_job.id,
                        "cancelled_at": stranded_job.cancelled_at,
                        "finished_at": stranded_job.finished_at,
                        "error_code": stranded_job.error_code,
                        "error_message": stranded_job.error_message,
                    }
                    stranded_job.status = JobStatus.WAITING
                    stranded_job.cancelled_at = None
                    stranded_job.error_code = None
                    stranded_job.error_message = None
                    stranded_job.finished_at = None
                revival_snapshot["revived_nodes"].append(stranded_entry)
        if node_run and node_run.status == "FAILED":
            revival_snapshot["node"] = {
                "id": node_run.id,
                "status": node_run.status,
                "finished_at": node_run.finished_at,
                "error_code": node_run.error_code,
                "error_message": node_run.error_message,
            }
            node_run.status = "RUNNING"
            node_run.error_code = None
            node_run.error_message = None
            node_run.finished_at = None
    # Revival claim re-check: the claim above can be overtaken between the
    # initial CAS and this commit by a concurrent cancel — cancel_job reads
    # the run already FAILED (cancel_run's terminal claim no-ops) and then
    # mark_job_cancelled claims the very WAITING row this transaction just
    # created, so committing the run/node revival on top would leave a zombie
    # RUNNING run with a CANCELLED node. Re-verify the exact shape this
    # transaction created; a lost race rolls the whole revival back with the
    # same 409 as any other stale claim.
    verified = db.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job.id,
            GenerationJob.status == JobStatus.WAITING,
            GenerationJob.cancelled_at.is_(None),
        )
        .values(scheduled_at=utcnow() + timedelta(seconds=1))
        .execution_options(synchronize_session=False)
    )
    if verified.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试")
    db.commit()
    # Post-commit guard: the pre-commit re-verify above is a dead fence on a
    # real multi-connection database — the initial CAS holds the job row's
    # write lock until this commit, so no concurrent writer can flip the row
    # to CANCELLED where the guard could see it. The races that matter all
    # land AFTER this commit (the concurrent cancel's claim unblocks the
    # moment we release the row), so re-read the committed state here and
    # compensate when the revival lost.
    if not _verify_retry_revival_post_commit(db, job, revival_snapshot):
        db.refresh(job)
        return job
    return enqueue_job(db, job)


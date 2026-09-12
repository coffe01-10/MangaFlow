import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app import models  # noqa: F401
from app.api.router import api_router
from app.config import Settings, get_settings
from app.database import SessionLocal, engine
from app.request_limits import RequestBodyLimitMiddleware
from app.services.job_service import recover_pending_jobs, start_periodic_recovery
from app.services.provider_presets import ensure_provider_presets
from app.services.runtime_settings import apply_runtime_overrides

LOGGER = logging.getLogger("mangaflow.jobs")

# Retention window for the boot-time storage sweeps (retained CLI run
# directories and orphaned generated media). A constant here because
# ``app.config`` settings ownership is out of scope for this change; promoting
# it to a Settings field (e.g. storage_sweep_days) is a config follow-up.
_STORAGE_SWEEP_WINDOW = timedelta(days=7)


def _assert_database_is_current() -> None:
    """Fail closed when the database was not upgraded through Alembic."""

    settings = get_settings()
    alembic_config = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # set_main_option runs ConfigParser interpolation: a raw "%" in the URL
    # (a Windows user profile path) raises ValueError. Same class as the
    # migrations/env.py fix (#443 lineage).
    alembic_config.set_main_option(
        "sqlalchemy.url", settings.database_url.replace("%", "%%")
    )
    expected_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
    if current_heads != expected_heads:
        current = ", ".join(sorted(current_heads)) or "未初始化"
        expected = ", ".join(sorted(expected_heads)) or "未知"
        raise RuntimeError(
            "数据库迁移版本不匹配；请先执行 "
            f"alembic upgrade head（当前：{current}，需要：{expected}）"
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    # Tests replace the request-scoped database and create their own schema.
    # Production/development startup must use the checked-in Alembic schema;
    # silently calling create_all would skip migrations and hide drift.
    if not application.dependency_overrides:
        _assert_database_is_current()
    with SessionLocal() as db:
        # Tests replace the request-scoped database while SessionLocal still
        # points at a developer database. Do not read or recover that database.
        if not application.dependency_overrides:
            apply_runtime_overrides(db, settings)
            ensure_provider_presets(db, settings, auto_commit=True)
            # A persistently poisoned run or job must not abort API startup:
            # mirror the periodic loop's isolation and _recover_cli_runs
            # below — log the recovery failure and keep booting.
            try:
                recover_pending_jobs(db)
            except Exception:
                LOGGER.exception("job recovery failed at startup")
            _recover_cli_runs()
            _sweep_stale_storage(settings)
    recovery: tuple[Thread, Event] | None = None
    if not application.dependency_overrides:
        # REDIS-mode RQ retries fire inside the lease window and then stop, so
        # a dead worker's job would stay ACTIVE until the next API restart.
        # The periodic pass keeps reclaiming expired leases and re-enqueueing
        # parked WAITING jobs for the lifetime of the API process.
        recovery = start_periodic_recovery()
    yield
    if recovery is not None:
        # Graceful shutdown: stop the recovery loop before the server stops
        # serving — otherwise a waking pass can re-enqueue work and race the
        # teardown (start_periodic_recovery returns the handle for exactly
        # this parking purpose).
        recovery[1].set()


def _recover_cli_runs() -> None:
    """Release CLI channel slots whose controller died mid-run (contract §9.3)."""

    import logging

    from app.services.cli_executor import recover_abandoned_cli_runs

    logger = logging.getLogger("mangaflow.cli")
    try:
        recovered = recover_abandoned_cli_runs()
    except Exception:
        logger.exception("CLI run recovery failed at startup")
        return
    for run_id in recovered:
        logger.warning("released abandoned CLI run %s", run_id)


def _sweep_stale_storage(settings: Settings) -> None:
    """Boot-time retention sweeps: retained CLI run directories and orphaned
    generated media.

    Both address unbounded growth paths (retained-for-diagnosis CLI runs;
    generated files whose owning ``Asset`` row was rolled back by a failed
    completion CAS or a lost lease). Conservative windows (see
    ``_STORAGE_SWEEP_WINDOW``), and each sweep is failure-tolerant: a poisoned
    storage state is logged, never allowed to abort API startup.
    """

    from app.services.cli_executor import sweep_retained_cli_runs
    from app.services.media import sweep_orphan_generated_files

    try:
        counts = sweep_retained_cli_runs(settings, older_than=_STORAGE_SWEEP_WINDOW)
        if counts.get("failed"):
            LOGGER.warning("CLI run retention sweep reported failures: %s", counts)
    except Exception:
        LOGGER.exception("CLI run retention sweep failed at startup")
    try:
        counts = sweep_orphan_generated_files(
            settings, older_than=_STORAGE_SWEEP_WINDOW
        )
        if counts.get("failed"):
            LOGGER.warning("orphan media sweep reported failures: %s", counts)
    except Exception:
        LOGGER.exception("orphan media sweep failed at startup")


settings = get_settings()
web_origins = {
    settings.web_origin,
    settings.web_origin.replace("localhost", "127.0.0.1"),
    settings.web_origin.replace("127.0.0.1", "localhost"),
}
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
# Middleware order note: the LAST added middleware is the OUTERMOST. Desired
# chain: TrustedHost (outermost — reject rebinded hosts before anything else)
# → CORS (must wrap the upload limiter so browser 413 responses still carry
# Access-Control-Allow-Origin) → RequestBodyLimitMiddleware.
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(web_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# The API is unauthenticated; without a Host allowlist a DNS-rebinded attacker
# page reaches it same-origin, where CORS is irrelevant.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=sorted(
        host.strip()
        for host in get_settings().api_trusted_hosts.split(",")
        if host.strip()
    ),
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ready", "docs": "/api/docs"}

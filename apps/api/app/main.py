import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app import models  # noqa: F401
from app.api.router import api_router
from app.config import get_settings
from app.database import SessionLocal, engine
from app.request_limits import RequestBodyLimitMiddleware
from app.services.job_service import recover_pending_jobs, start_periodic_recovery
from app.services.provider_presets import ensure_provider_presets
from app.services.runtime_settings import apply_runtime_overrides

LOGGER = logging.getLogger("mangaflow.jobs")


def _assert_database_is_current() -> None:
    """Fail closed when the database was not upgraded through Alembic."""

    settings = get_settings()
    alembic_config = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
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
    if not application.dependency_overrides:
        # REDIS-mode RQ retries fire inside the lease window and then stop, so
        # a dead worker's job would stay ACTIVE until the next API restart.
        # The periodic pass keeps reclaiming expired leases and re-enqueueing
        # parked WAITING jobs for the lifetime of the API process.
        start_periodic_recovery()
    yield


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

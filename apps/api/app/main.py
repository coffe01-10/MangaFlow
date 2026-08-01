from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401
from app.api.router import api_router
from app.config import get_settings
from app.database import SessionLocal, engine
from app.services.job_service import recover_pending_jobs
from app.services.provider_presets import ensure_provider_presets
from app.services.runtime_settings import apply_runtime_overrides


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
        apply_runtime_overrides(db, settings)
        # Tests commonly replace the request-scoped database while the global
        # SessionLocal still points at a developer database. Never recover that
        # unrelated database when dependency overrides are active.
        if not application.dependency_overrides:
            ensure_provider_presets(db, settings)
            recover_pending_jobs(db)
    yield


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(web_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ready", "docs": "/api/docs"}

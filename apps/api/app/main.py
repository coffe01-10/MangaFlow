from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401
from app.api.router import api_router
from app.config import get_settings
from app.database import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.ensure_directories()
    Base.metadata.create_all(bind=engine)
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ready", "docs": "/api/docs"}

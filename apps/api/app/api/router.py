from fastapi import APIRouter

from app.api.routes import (
    asset_generation,
    characters,
    exports,
    health,
    models,
    projects,
    sources,
    uploads,
    workflow,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(uploads.router, prefix="/assets", tags=["assets"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(sources.router, tags=["sources"])
api_router.include_router(characters.router, tags=["characters"])
api_router.include_router(workflow.router, tags=["workflow"])
api_router.include_router(exports.router, tags=["exports"])
api_router.include_router(asset_generation.router, tags=["asset-generation"])

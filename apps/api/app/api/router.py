from fastapi import APIRouter

from app.api.routes import (
    asset_generation,
    characters,
    exports,
    health,
    models,
    projects,
    providers,
    scene_assets,
    settings,
    sources,
    uploads,
    usage,
    workflow,
    workflow_definitions,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(uploads.router, prefix="/assets", tags=["assets"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(providers.router, tags=["providers"])
api_router.include_router(providers.routing_router, tags=["routing-policies"])
api_router.include_router(sources.router, tags=["sources"])
api_router.include_router(characters.router, tags=["characters"])
api_router.include_router(scene_assets.router, tags=["scene-assets"])
api_router.include_router(workflow.router, tags=["workflow"])
api_router.include_router(exports.router, tags=["exports"])
api_router.include_router(asset_generation.router, tags=["asset-generation"])
api_router.include_router(workflow_definitions.router, tags=["workflow-definitions"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(usage.router, tags=["usage"])

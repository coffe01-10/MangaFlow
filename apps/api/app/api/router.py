from fastapi import APIRouter

from app.api.routes import health, models, projects, uploads

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(uploads.router, prefix="/assets", tags=["assets"])
api_router.include_router(models.router, prefix="/models", tags=["models"])

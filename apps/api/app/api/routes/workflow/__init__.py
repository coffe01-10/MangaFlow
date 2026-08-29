"""Aggregate workflow router.

The concrete routes live in cohesive group modules under this package; the
registration order below mirrors the historical monolithic module so route
matching semantics stay unchanged.
"""

from fastapi import APIRouter

from app.api.routes.workflow.generation import router as generation_router
from app.api.routes.workflow.inspection import router as inspection_router
from app.api.routes.workflow.jobs import router as jobs_router
from app.api.routes.workflow.library import router as library_router
from app.api.routes.workflow.pages import router as pages_router
from app.api.routes.workflow.storyboard import router as storyboard_router

router = APIRouter()

router.include_router(pages_router)
router.include_router(storyboard_router)
router.include_router(generation_router)
router.include_router(library_router)
router.include_router(jobs_router)
router.include_router(inspection_router)

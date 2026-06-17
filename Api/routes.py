from __future__ import annotations

from fastapi import APIRouter

from ._common import api_resource_map
from .routers import project, workflow, checklist, endpoints, findings

router = APIRouter()
router.include_router(project.router)
router.include_router(workflow.router)
router.include_router(checklist.router)
router.include_router(endpoints.router)
router.include_router(findings.router)

__all__ = ["router", "api_resource_map"]

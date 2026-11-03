"""Management extension: agent-facing multi-project management (list / create / switch).

The core API only ever operates on the single loaded project — a human
operator creates and switches projects from the Web UI. This router
lets an *admin agent* do the same over the API: list projects, create a new
one (scope included), and switch the loaded project.

Because letting an agent set its own scope and switch the shared, single loaded
project is exactly what the core security model forbids, the whole capability is
gated behind the ALLOW_ADMIN_AGENTS environment variable (default: off). When it
is off the endpoints still exist — so agents discover them in /help and /docs —
but every call returns a descriptive 403 instead of acting.

Injected into the FastAPI app via the factory's `extra_api_routers` knob; the
core has no knowledge of any of this.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from Api.deps import get_service
from Application import ProjectService
from Domain import ProjectNotFoundError

_TRUTHY = {"1", "true", "yes", "on"}


def admin_agents_enabled() -> bool:
    """True when the deployment opted into agent-driven project management by
    starting ShiftGrid with ALLOW_ADMIN_AGENTS=true (docker `-e ALLOW_ADMIN_AGENTS=true`
    or the same in the environment before `python app.py`). Default is off."""
    return os.environ.get("ALLOW_ADMIN_AGENTS", "").strip().lower() in _TRUTHY


def require_admin_agents() -> None:
    """Gate the whole router. When ALLOW_ADMIN_AGENTS is not set, agents get a
    clear message pointing at the correct usage instead of a silent 404/403."""
    if not admin_agents_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "admin_agents_disabled",
                "message": (
                    "Agent-driven project management (list / create / switch projects, "
                    "including setting scope) is disabled on this deployment. By default "
                    "only the human operator creates projects and sets scope, via the Web "
                    "UI. To let admin agents do this over the API, the operator must start "
                    "ShiftGrid with the environment variable ALLOW_ADMIN_AGENTS=true."
                ),
            },
        )


# The router carries the admin gate; the factory additionally wraps every extra
# router in Depends(require_agent), so these calls also require an agent identity.
router = APIRouter(dependencies=[Depends(require_admin_agents)])


class ProjectListItem(BaseModel):
    id: str
    name: str
    workflow: str
    workflow_name: str
    path: str
    scope_count: int
    loaded: bool
    created_at: int


class ProjectsListResponse(BaseModel):
    projects: list[ProjectListItem]


class ProjectSummary(BaseModel):
    id: str
    name: str
    workflow: str
    scope: list[str]
    folder: str
    loaded: bool


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Human-readable project name.")
    workflow: str | None = Field(
        None,
        description="Workflow id the project runs (e.g. 'web_application'). Must be one of the "
        "loaded workflows. Omit it when supplying a custom workflow_def + checklist_def instead.",
    )
    scope: list[str] = Field(
        default_factory=list,
        description="Everything testers are allowed to pentest — one target per entry. "
        "This authorizes the test; set it deliberately.",
    )
    endpoints: list[str] = Field(
        default_factory=list,
        description="Optional initial endpoint names to seed the horizontal endpoint list.",
    )
    workflow_def: dict | None = Field(
        None,
        description="Optional custom workflow definition (the full workflow JSON: unique 'id', "
        "'name', 'phases'). Supply together with checklist_def to run a project on a bespoke "
        "workflow created on the fly instead of a built-in one. Stored inside the project folder. "
        "Its 'id' must not clash with a built-in workflow.",
    )
    checklist_def: dict | None = Field(
        None,
        description="Optional custom checklist definition (the full checklist JSON: unique 'id', "
        "'items' each with 'id' + 'title'). Required together with workflow_def; the workflow is "
        "wired to this checklist automatically. Its 'id' must not clash with a built-in checklist.",
    )


class SwitchProjectRequest(BaseModel):
    id: str | None = Field(None, description="Project id to switch to (from GET /projects).")
    path: str | None = Field(None, description="Project folder path to switch to (alternative to id).")


def _summary(project) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=project.name,
        workflow=project.workflow_id,
        scope=project.scope,
        folder=str(project.folder),
        loaded=True,
    )


@router.get("/projects", response_model=ProjectsListResponse)
def list_projects(service: ProjectService = Depends(get_service)):
    """List every project ShiftGrid can see (loaded first). Use the `id` or
    `path` of one with POST /projects/switch to load it."""
    return ProjectsListResponse(projects=service.list_projects())


@router.post("/projects", response_model=ProjectSummary, status_code=201)
def create_project(body: CreateProjectRequest, service: ProjectService = Depends(get_service)):
    """Create a new pentest project and load it (this also switches away from any
    currently loaded project). `scope` is set here and authorizes the test — the
    operator can still lock it in the Web UI.

    Run it on a built-in workflow (`workflow`), or create one on the fly by passing
    a custom `workflow_def` + `checklist_def` pair (both required together; omit
    `workflow`). Custom definitions are stored inside the project folder."""
    custom = body.workflow_def is not None or body.checklist_def is not None
    if custom and body.workflow:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "create_failed",
                "message": "pass either 'workflow' (a built-in id) or a custom "
                "workflow_def + checklist_def, not both",
            },
        )
    if not custom and not body.workflow:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "create_failed",
                "message": "provide 'workflow' (a built-in id) or a custom "
                "workflow_def + checklist_def",
            },
        )
    try:
        project = service.create(
            name=body.name,
            workflow_id=body.workflow,
            scope=body.scope,
            initial_endpoints=[{"name": n} for n in body.endpoints],
            workflow_def=body.workflow_def,
            checklist_def=body.checklist_def,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "create_failed", "message": str(e)})
    except OSError as e:
        raise HTTPException(status_code=500, detail={"error": "create_failed", "message": str(e)})
    return _summary(project)


@router.post("/projects/switch", response_model=ProjectSummary)
def switch_project(body: SwitchProjectRequest, service: ProjectService = Depends(get_service)):
    """Switch the single loaded project (shared by all agents). Identify the
    target by `id` or `path` — get either from GET /projects."""
    path = (body.path or "").strip()
    if not path and body.id:
        match = next((p for p in service.list_projects() if p["id"] == body.id.strip()), None)
        if match is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "project_not_found", "message": f"no project with id {body.id!r}"},
            )
        path = match["path"]
    if not path:
        raise HTTPException(
            status_code=400,
            detail={"error": "target_required", "message": "provide either 'id' or 'path'"},
        )
    try:
        project = service.switch(path)
    except (ProjectNotFoundError, ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail={"error": "switch_failed", "message": str(e)})
    return _summary(project)

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from Domain import (
    FocusRequiredError,
    NoProjectLoadedError,
    ObservationsTooLongError,
    ProjectNotFoundError,
    RunAgainError,
    ScopeLockedError,
    WorkflowOrderError,
)

from .routes import api_resource_map


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        # On 404 inside the API surface, return the resource map so the agent
        # can correct course instead of guessing more paths.
        if exc.status_code == 404 and request.url.path.startswith("/api/v1"):
            return JSONResponse(
                status_code=404,
                content={
                    "detail": exc.detail or "not found",
                    "hint": "this path does not exist; pick one from `resources` below or call GET /api/v1/help for the discovery root",
                    "resources": api_resource_map(),
                },
            )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(ProjectNotFoundError)
    async def _project_not_found(request: Request, exc: ProjectNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ScopeLockedError)
    async def _scope_locked(request: Request, exc: ScopeLockedError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NoProjectLoadedError)
    async def _no_project(request: Request, exc: NoProjectLoadedError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(WorkflowOrderError)
    async def _workflow_order(request: Request, exc: WorkflowOrderError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(FocusRequiredError)
    async def _focus_required(request: Request, exc: FocusRequiredError):
        # Mandatory focus gate: surface the same structured shape the agent gets
        # for other recoverable 409s — an error code, the message, and a `fix`.
        return JSONResponse(
            status_code=409,
            content={"detail": {"error": exc.kind, "message": str(exc), "fix": exc.fix}},
        )

    @app.exception_handler(RunAgainError)
    async def _run_again(request: Request, exc: RunAgainError):
        # Runs gate: this finish reset the item for another run instead of
        # completing it. 409 (same family as the other recoverable finish gates)
        # with a `run_again` code plus the run counters.
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "error": "run_again",
                    "message": str(exc),
                    "runs_completed": exc.runs_completed,
                    "runs_target": exc.target,
                }
            },
        )

    @app.exception_handler(ObservationsTooLongError)
    async def _observations_too_long(request: Request, exc: ObservationsTooLongError):
        return JSONResponse(
            status_code=400,
            content={
                "error": "observations_too_long",
                "field": exc.field,
                "word_count": exc.word_count,
                "limit": exc.limit,
                "detail": str(exc),
            },
        )

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

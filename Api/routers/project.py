from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from Application import ProjectService
from Domain import (
    NotesOldStringNotFound,
    NotesOldStringNotUnique,
    NotesImmutableRegionViolation,
)

from ..deps import require_loaded
from ..schemas import (
    ProjectInfoResponse,
    NotesResponse,
    EditNotesRequest,
)

router = APIRouter()


@router.get("/project/info", response_model=ProjectInfoResponse)
def project_info(service: ProjectService = Depends(require_loaded)):
    p = service.current
    d = p.data
    return ProjectInfoResponse(
        id=p.id,
        name=p.name,
        workflow=p.workflow_id,
        scope=p.scope,
        scope_locked=p.scope_locked,
        info=d.get("info", ""),
        context=d.get("context", ""),
        details=d.get("details", ""),
        current_phase=p.current_phase,
        folder=str(p.folder),
        created_at=d.get("created_at", 0),
        updated_at=d.get("updated_at", 0),
        notes_required=p.notes_required,
        observations_required=p.observations_required,
    )


def _project_info_blob(project) -> str:
    """Project-wide context + details, short form: context first, details after
    a newline. Empty string when both are blank."""
    context = (project.data.get("context") or "").strip()
    details = (project.data.get("details") or "").strip()
    if context and details:
        return f"{context}\n{details}"
    return context or details


@router.get("/notes", response_model=NotesResponse)
def get_notes(service: ProjectService = Depends(require_loaded)):
    """Short-term memory for the test — a single text blob the agent should read
    first and refresh as it works. The project itself is the long-term memory.
    Also surfaces the project-wide context/details so the agent has the operator's
    framing in one read."""
    p = service.current
    return NotesResponse(
        notes=p.data.get("notes", ""),
        project_info=_project_info_blob(p),
    )


@router.patch("/notes", response_model=NotesResponse)
def edit_notes(body: EditNotesRequest, service: ProjectService = Depends(require_loaded)):
    """Edit the short-term notes in place — never replace the whole blob.

    Mirrors the Edit tool: provide `old_string` (must match the current notes
    verbatim and uniquely) and `new_string`. This forces the caller to know
    the current contents (read GET /api/v1/notes first) and prevents blind
    overwrites of work done by other agents.

    The header inside <immutable>…</immutable> is read-only — any edit that
    would alter or drop it is rejected.

    Errors:
        400  old_string not found, not unique, or edit hits the immutable header.
    """
    p = service.current
    try:
        p.edit_notes(body.old_string, body.new_string)
    except NotesOldStringNotFound as e:
        raise HTTPException(status_code=400, detail={"error": "old_string_not_found", "message": str(e)})
    except NotesOldStringNotUnique as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "old_string_not_unique", "message": str(e), "match_count": e.count},
        )
    except NotesImmutableRegionViolation as e:
        raise HTTPException(status_code=400, detail={"error": "immutable_region", "message": str(e)})
    service.save(p)
    return NotesResponse(
        notes=p.data.get("notes", ""),
        project_info=_project_info_blob(p),
    )

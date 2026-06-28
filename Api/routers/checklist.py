from __future__ import annotations

import random

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from Application import ProjectService
from Domain import TryHarderError

from ..deps import require_loaded, require_agent, Agent
from ..schemas import (
    ChecklistCategorySummary,
    ChecklistItem,
    ChecklistItemResult,
    CheckStatusUpdateResponse,
    ChecklistTitleEntry,
    RawCaptureMeta,
    AddRawCaptureRequest,
    UpdateCheckObservationsRequest,
    UpdateCheckStatusRequest,
    FinishCheckResponse,
)
from .._common import (
    _enforce_notes_update,
    _endpoint_loop_next,
    _guard_observation_overwrite,
    _update_notes_hint,
    _reject_unless_attested,
    next_step_to_action,
)


# Status values that count as "the agent actually tested this check". When
# `notes_required=True`, only transitions to these statuses must carry a
# notes edit — skipping / failing / not-applicable don't (no new knowledge).
_CHECK_STATUS_REQUIRES_NOTES = ("passed", "warning", "vulnerable")

router = APIRouter()


@router.get("/checklist", response_model=list[ChecklistItem])
def get_checklist(service: ProjectService = Depends(require_loaded)):
    return service.current.checklist


@router.get("/checklist/categories", response_model=list[ChecklistCategorySummary])
def list_checklist_categories(service: ProjectService = Depends(require_loaded)):
    """Return the categories present in the project checklist with item counts."""
    p = service.current
    seen: dict[str, dict] = {}
    out: list[dict] = []
    for item in p.checklist:
        cat_id = item.get("category", "uncategorized")
        entry = seen.get(cat_id)
        if entry is None:
            entry = {
                "id": cat_id,
                "name": item.get("category_name", "Uncategorized"),
                "count": 0,
            }
            seen[cat_id] = entry
            out.append(entry)
        entry["count"] += 1
    return out


@router.get("/checklist/titles", response_model=list[ChecklistTitleEntry])
def list_checklist_titles(service: ProjectService = Depends(require_loaded)):
    """Return only the id/title/scope/category for each check in the checklist."""
    p = service.current
    return [
        {
            "id": item["id"],
            "title": item.get("title", item["id"]),
            "scope": item.get("scope", "global"),
            "category": item.get("category", "uncategorized"),
            "category_name": item.get("category_name", "Uncategorized"),
        }
        for item in p.checklist
    ]


@router.get("/checklist/next", response_model=ChecklistItem)
def next_pending_global_check(service: ProjectService = Depends(require_loaded)):
    """Return one random check with scope=global and status=pending — i.e. the next task for the tester."""
    p = service.current
    candidates = [
        item for item in p.checklist
        if item.get("scope") == "global"
        and (item.get("results", {}) or {}).get("_global", {}).get("status", "pending") == "pending"
    ]
    if not candidates:
        raise HTTPException(status_code=404, detail="no pending 'global' checks available")
    return random.choice(candidates)


@router.get("/checklist/filter", response_model=list[ChecklistItem])
def filter_checklist(
    status: list[str] | None = Query(None, description="Repeatable. Match if any result has one of these statuses."),
    scope: str | None = Query(None, description="'global' or 'per_endpoint'"),
    category: list[str] | None = Query(None, description="Repeatable. Match by category id."),
    endpoint_id: str | None = Query(None, description="Restrict status match to this endpoint's result."),
    service: ProjectService = Depends(require_loaded),
):
    """Filter the checklist by status / scope / category, mirroring the UI filter."""
    p = service.current
    out = []
    for item in p.checklist:
        if scope and item.get("scope") != scope:
            continue
        if category and item.get("category", "uncategorized") not in category:
            continue
        if status:
            results = item.get("results", {}) or {}
            if endpoint_id:
                r = results.get(endpoint_id) or {}
                if r.get("status", "pending") not in status:
                    continue
            else:
                statuses = [r.get("status", "pending") for r in results.values()] or ["pending"]
                if not any(s in status for s in statuses):
                    continue
        out.append(item)
    return out


@router.get("/check/{check_id}", response_model=ChecklistItem)
def get_check(check_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    try:
        return p.get_check(check_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/check/{check_id}/observations", response_model=ChecklistItemResult)
def put_check_observations(
    check_id: str,
    body: UpdateCheckObservationsRequest,
    service: ProjectService = Depends(require_loaded),
):
    p = service.current
    # Read-before-overwrite: refuse to clobber an existing observation unless the
    # agent echoed back the text it last read (see _guard_observation_overwrite).
    _guard_observation_overwrite(
        p.current_check_observations(check_id, endpoint_id=body.endpoint_id),
        body.observations, body.base_observations,
        field=f"check {check_id} observations",
        read_path=f"/api/v1/check/{check_id}",
    )
    r = p.set_check_observations(check_id, body.observations, endpoint_id=body.endpoint_id)
    service.save(p)
    return r


@router.put("/check/{check_id}/status", response_model=CheckStatusUpdateResponse)
def put_check_status(
    check_id: str,
    body: UpdateCheckStatusRequest,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Set a check's status. Focus is mandatory: a global-scope check must
    be claimed first by setting its status to `focused` (PUT this endpoint with
    {"status": "focused"}) before any result status (passed|vulnerable|warning|
    failed|not applicable) or observations can be recorded — otherwise 409
    `check_not_focused`. Per-endpoint checks cannot take `focused` directly; they
    are claimed via their endpoint's focus. When the project has
    `notes_required=True` AND the target status reflects that testing actually
    happened (passed|warning|vulnerable), the body must also carry
    `notes_old_string` + `notes_new_string`. Statuses that imply no new knowledge
    (skipped, failed, not applicable, pending, focused) are not gated on notes."""
    p = service.current
    if body.status in _CHECK_STATUS_REQUIRES_NOTES:
        _enforce_notes_update(p, body.notes_old_string, body.notes_new_string)
    try:
        r = p.set_check_status(check_id, body.status, endpoint_id=body.endpoint_id, agent=agent.tag())
    except ValueError as e:
        msg = str(e)
        if msg.startswith("unknown check"):
            status_code = 404
        elif msg.startswith("bad status"):
            status_code = 400
        else:
            status_code = 409
        raise HTTPException(status_code=status_code, detail=msg)
    service.save(p)
    next_action = None
    if body.endpoint_id:
        # Per-endpoint check: hand back the endpoint loop's next move. Global
        # (global-scope) checks get their hint from POST .../finish instead.
        next_action = _endpoint_loop_next(p, service.workflow_for(p), body.endpoint_id)
    return CheckStatusUpdateResponse(**r, update_notes=_update_notes_hint(p), next=next_action)


@router.post("/check/{check_id}/finish", response_model=FinishCheckResponse)
def finish_check(
    check_id: str,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Mark a global-scope check finished. Requires status != pending and a
    non-empty observations field. Implicitly clears the `focused` flag. The
    Work-on-Checklist phase advances only after every global-check is finished.
    The response's `next` hint points at the next pending check (or advance)."""
    p = service.current
    try:
        r = p.finish_global_check(check_id, agent=agent.tag())
    except TryHarderError:
        # Try-harder mode held this finish back; persist the nudge flag so the
        # second finish goes through, then let the global handler format the 409.
        service.save(p)
        raise
    except ValueError as e:
        msg = str(e)
        if msg.startswith("unknown check"):
            status_code = 404
        elif msg.startswith("cannot finish check"):
            status_code = 409
        else:
            status_code = 409
        raise HTTPException(status_code=status_code, detail=msg)
    service.save(p)
    wf = service.workflow_for(p)
    return {**r, "next": next_step_to_action(p.resolve_next_step(wf), p, wf)}


@router.put(
    "/check/{check_id}/raw-captures",
    response_model=RawCaptureMeta,
    status_code=201,
    summary="Put Check Raw Capture  (upload only logs, screenshots, HTTP responses, 3rd party tool output. NEVER EVER upload AI/Agent generated output / summaries / bash-script outputs)",
)
def put_check_raw_capture(
    check_id: str,
    body: AddRawCaptureRequest,
    service: ProjectService = Depends(require_loaded),
):
    _reject_unless_attested(
        body.agent_composed,
        body.this_really_is_raw_capture_and_not_an_ai_script,
    )
    p = service.current
    entry = p.add_check_evidence(
        check_id,
        body.name,
        body.data,
        mime_type=body.mime_type,
        endpoint_id=body.endpoint_id,
        source_type=body.source_type,
        description=body.description,
    )
    service.save(p)
    return entry


@router.get("/check/{check_id}/raw-captures/{capture_id}")
def download_check_raw_capture(
    check_id: str,
    capture_id: str,
    endpoint_id: str | None = None,
    service: ProjectService = Depends(require_loaded),
):
    p = service.current
    try:
        entry, abs_path = p.get_check_evidence(check_id, capture_id, endpoint_id=endpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="raw capture file missing on disk")
    return FileResponse(
        path=str(abs_path),
        media_type=entry.get("mime_type", "application/octet-stream"),
        filename=entry["name"],
    )

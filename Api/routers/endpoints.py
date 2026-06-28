from __future__ import annotations

import random

from fastapi import APIRouter, Body, Depends, HTTPException

from Application import ProjectService
from Domain import TryHarderError

from ..deps import require_loaded, require_agent, Agent
from ..schemas import (
    AddEndpointRequest,
    Endpoint,
    EndpointDetail,
    EndpointSlim,
    EndpointUpdateResponse,
    UnfocusEndpointRequest,
    CreateFeatureGroupRequest,
    FeatureGroup,
    FeatureGroupSummary,
    DeleteResponse,
    SetEndpointFeatureGroupRequest,
    UpdateEndpointRequest,
    EndpointActionResponse,
)
from .._common import (
    _update_notes_hint,
    _enforce_notes_update,
    _endpoint_loop_next,
    _guard_observation_overwrite,
)

router = APIRouter()


@router.get("/endpoints", response_model=list[Endpoint])
def list_endpoints(service: ProjectService = Depends(require_loaded)):
    return service.current.endpoints


@router.get("/endpoint/{endpoint_id}", response_model=EndpointDetail)
def get_endpoint(endpoint_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    endpoint = p.get_endpoint(endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail=f"unknown endpoint {endpoint_id}")
    return EndpointDetail(**endpoint, checklist_links=p.checklist_links_for_endpoint(endpoint_id))


@router.post("/endpoint", response_model=Endpoint, status_code=201)
def add_endpoint(body: AddEndpointRequest, service: ProjectService = Depends(require_loaded)):
    p = service.current
    endpoint = p.add_endpoint(
        body.name,
        source=body.source,
        observations=body.observations,
    )
    service.save(p)
    return endpoint


@router.put("/endpoint/{endpoint_id}", response_model=EndpointUpdateResponse)
def update_endpoint(
    endpoint_id: str,
    body: UpdateEndpointRequest,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Update an endpoint's type/observations and/or move it through the status
    state machine via `status`. Transitions are guarded and identical to the UI:
    todo <-> focused -> tested ; todo <-> out-of-scope ; tested -> todo (reopen).
    'focused' needs a feature group; 'tested' needs checks adjusted + all per-endpoint
    checks non-pending; 'out-of-scope' only from 'todo'. Illegal moves return 409.
    Dedicated shortcuts: POST .../focus, .../unfocus, .../checks-adjusted, .../finish,
    .../reopen."""
    p = service.current
    # Read-before-overwrite: refuse to clobber an existing observation unless the
    # agent echoed back the text it last read (see _guard_observation_overwrite).
    if body.observations is not None:
        existing = p.get_endpoint(endpoint_id)
        _guard_observation_overwrite(
            (existing or {}).get("observations", ""),
            body.observations, body.base_observations,
            field=f"endpoint {endpoint_id} observations",
            read_path=f"/api/v1/endpoint/{endpoint_id}",
        )
    fields = body.model_dump(exclude_unset=True, exclude={"base_observations"})
    try:
        endpoint = p.update_endpoint(endpoint_id, agent=agent.tag(), **fields)
    except TryHarderError:
        # status=tested while try-harder mode is on: hold the finish back, persist
        # the nudge flag, and let the global handler format the 409.
        service.save(p)
        raise
    except ValueError as e:
        status_code = 404 if str(e).startswith("unknown endpoint") else 409
        raise HTTPException(status_code=status_code, detail=str(e))
    service.save(p)
    return EndpointUpdateResponse(
        **endpoint,
        update_notes=_update_notes_hint(p),
        next=_endpoint_loop_next(p, service.workflow_for(p), endpoint_id),
    )


@router.post("/endpoint/{endpoint_id}/focus", response_model=EndpointActionResponse)
def focus_endpoint(
    endpoint_id: str,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    p = service.current
    try:
        endpoint = p.focus_endpoint(endpoint_id, agent=agent.tag())
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    service.save(p)
    return {**endpoint, "next": _endpoint_loop_next(p, service.workflow_for(p), endpoint_id)}


@router.post("/endpoint/{endpoint_id}/unfocus", response_model=Endpoint)
def unfocus_endpoint(
    endpoint_id: str,
    body: UnfocusEndpointRequest = Body(default=UnfocusEndpointRequest()),
    service: ProjectService = Depends(require_loaded),
):
    """Unfocus an endpoint after testing. When the project has
    `notes_required=True`, the body must also carry `notes_old_string` +
    `notes_new_string` — otherwise 409 `notes_required`."""
    p = service.current
    _enforce_notes_update(p, body.notes_old_string, body.notes_new_string)
    try:
        endpoint = p.unfocus_endpoint(endpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    service.save(p)
    return endpoint


@router.post("/endpoint/{endpoint_id}/checks-adjusted", response_model=EndpointActionResponse)
def confirm_endpoint_checks_adjusted(endpoint_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    try:
        endpoint = p.confirm_endpoint_checks_adjusted(endpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    service.save(p)
    return {**endpoint, "next": _endpoint_loop_next(p, service.workflow_for(p), endpoint_id)}


@router.post("/endpoint/{endpoint_id}/finish", response_model=EndpointUpdateResponse)
def finish_endpoint(
    endpoint_id: str,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Mark a focused endpoint tested — the endpoint loop's closing verb,
    mirroring POST /workflow/.../finish and POST /check/{id}/finish. Same guards
    as PUT {"status": "tested"}: observations recorded, checks adjusted, every
    per-endpoint check non-pending — 409 otherwise. The `next` hint then points
    at the next candidate (or phase advance)."""
    p = service.current
    try:
        endpoint = p.set_endpoint_status(endpoint_id, "tested", agent=agent.tag())
    except TryHarderError:
        # Try-harder mode held this finish back; persist the nudge flag so the
        # second finish goes through, then let the global handler format the 409.
        service.save(p)
        raise
    except ValueError as e:
        status_code = 404 if str(e).startswith("unknown endpoint") else 409
        raise HTTPException(status_code=status_code, detail=str(e))
    service.save(p)
    return EndpointUpdateResponse(
        **endpoint,
        update_notes=_update_notes_hint(p),
        next=_endpoint_loop_next(p, service.workflow_for(p), endpoint_id),
    )


@router.post("/endpoint/{endpoint_id}/reopen", response_model=Endpoint)
def reopen_endpoint(
    endpoint_id: str,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Reopen a *tested* endpoint back to 'todo' so it re-enters the testing pool
    (this clears the tested stamp). Reopen is the single edge out of 'tested' —
    `tested → todo` — and nothing else. From 'todo' the normal transitions take
    over, so you then focus it again (POST .../focus) or rescope it out
    (PUT /endpoint/{id} with {"status": "out-of-scope"}).

    It is NOT a generic 'set to todo': a focused endpoint goes back with
    POST .../unfocus, and an out-of-scope endpoint is rescoped with
    PUT /endpoint/{id} with {"status": "todo"}. Calling reopen on anything other
    than a 'tested' endpoint returns 409."""
    p = service.current
    endpoint = p.get_endpoint(endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail=f"unknown endpoint {endpoint_id}")
    status = endpoint.get("status", "todo")
    if status != "tested":
        hints = {
            "todo": "it is already 'todo' — there is nothing to reopen.",
            "focused": "it is 'focused' — use POST /api/v1/endpoint/{id}/unfocus to send it back to todo.",
            "out-of-scope": "it is 'out-of-scope' — use PUT /api/v1/endpoint/{id} with {\"status\": \"todo\"} to rescope it.",
        }
        raise HTTPException(
            status_code=409,
            detail=(
                f"reopen only applies to a 'tested' endpoint; this one is '{status}'. "
                + hints.get(status, "")
            ).strip(),
        )
    try:
        endpoint = p.set_endpoint_status(endpoint_id, "todo", agent=agent.tag())
    except ValueError as e:
        status_code = 404 if str(e).startswith("unknown endpoint") else 409
        raise HTTPException(status_code=status_code, detail=str(e))
    service.save(p)
    return endpoint


@router.delete("/endpoint/{endpoint_id}", response_model=DeleteResponse)
def delete_endpoint(endpoint_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    removed = p.remove_endpoint(endpoint_id)
    service.save(p)
    return DeleteResponse(removed=removed)


# -------- feature groups --------

UNASSIGNED_GROUP_ID = "_unassigned"


def _slim_endpoint(a: dict) -> dict:
    return {"id": a["id"], "name": a.get("name", ""), "status": a.get("status", "todo")}


@router.get("/feature-groups", response_model=list[FeatureGroupSummary])
def list_feature_groups(service: ProjectService = Depends(require_loaded)):
    """Return all feature groups with slim endpoint titles. Includes a synthetic '_unassigned' group."""
    p = service.current
    groups = [{"id": UNASSIGNED_GROUP_ID, "name": "Unassigned", "endpoints": []}]
    by_id = {UNASSIGNED_GROUP_ID: groups[0]}
    for g in p.feature_groups:
        entry = {"id": g["id"], "name": g["name"], "endpoints": []}
        groups.append(entry)
        by_id[g["id"]] = entry
    for a in p.endpoints:
        gid = a.get("feature_group") or UNASSIGNED_GROUP_ID
        target = by_id.get(gid, by_id[UNASSIGNED_GROUP_ID])
        target["endpoints"].append(_slim_endpoint(a))
    return groups


@router.post("/feature-groups", response_model=FeatureGroup, status_code=201)
def create_feature_group(body: CreateFeatureGroupRequest, service: ProjectService = Depends(require_loaded)):
    p = service.current
    group = p.add_feature_group(body.name)
    service.save(p)
    return group


@router.delete("/feature-groups/{group_id}", response_model=DeleteResponse)
def delete_feature_group(group_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    if group_id == UNASSIGNED_GROUP_ID:
        raise HTTPException(status_code=400, detail="cannot delete the synthetic unassigned group")
    removed = p.remove_feature_group(group_id)
    service.save(p)
    return DeleteResponse(removed=removed)


@router.put("/endpoint/{endpoint_id}/feature-group", response_model=Endpoint)
def set_endpoint_feature_group(
    endpoint_id: str,
    body: SetEndpointFeatureGroupRequest,
    service: ProjectService = Depends(require_loaded),
):
    """Move an endpoint into a feature group. Pass group_id=null (or empty) to mark as unassigned."""
    p = service.current
    gid = body.group_id
    if gid == UNASSIGNED_GROUP_ID:
        gid = ""
    try:
        endpoint = p.set_endpoint_feature_group(endpoint_id, gid)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    service.save(p)
    return endpoint


@router.get("/feature-groups/{group_id}/next-endpoint", response_model=EndpointSlim)
def next_endpoint_in_feature_group(group_id: str, service: ProjectService = Depends(require_loaded)):
    """Return one random endpoint from the given feature group whose status is 'todo'."""
    p = service.current
    if group_id == UNASSIGNED_GROUP_ID:
        candidates = [a for a in p.endpoints if not a.get("feature_group")]
    else:
        if not any(g["id"] == group_id for g in p.feature_groups):
            raise HTTPException(status_code=404, detail=f"unknown feature group {group_id}")
        candidates = [a for a in p.endpoints if a.get("feature_group") == group_id]
    todo = [a for a in candidates if a.get("status", "todo") == "todo"]
    if not todo:
        raise HTTPException(status_code=404, detail="no 'todo' endpoints in this feature group")
    return _slim_endpoint(random.choice(todo))

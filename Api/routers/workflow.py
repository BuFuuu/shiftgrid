from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from Application import ProjectService
from Domain import (
    PhaseIncompleteError,
    WorkflowOrderError,
    StepDisabledError,
    TryHarderError,
    TRY_HARDER_MESSAGE,
)

from ..deps import require_loaded, require_agent, Agent
from ..schemas import (
    AddRawCaptureRequest,
    AdvancePhaseRequest,
    AdvancePhaseResponse,
    EndpointWorkflowStatusResponse,
    FinishStepRequest,
    BulkStepUpdateRequest,
    FocusedEndpointsResponse,
    EndpointTestingStepResponse,
    RawCaptureMeta,
    StepEntry,
    WorkflowDefinition,
    WorkflowCurrentPhaseResponse,
    WorkflowPhasesResponse,
    PhaseStepsResponse,
    PhaseView,
    UpdatePhaseContextRequest,
    WorkflowStateResponse,
    WorkflowStepsResponse,
    StepRef,
    StepDetailResponse,
    FinishStepResponse,
    NowResponse,
    NextStepView,
)
from .._common import (
    _step_id,
    _finish_gating,
    _update_notes_hint,
    _enforce_notes_update,
    _guard_observation_overwrite,
    _reject_unless_attested,
    next_step_to_action,
)

router = APIRouter()


def _step_title(step: dict, step_id: str) -> str:
    return step.get("title") or step_id.replace("_", " ").title()


def _effective_status(state: dict) -> str:
    """API-facing status. When the human operator disabled the step in this
    project, we surface `disabled` so agents skip it instead of reading the
    stored underlying status (which is preserved for un-disable)."""
    if state.get("disabled"):
        return "disabled"
    return state.get("status", "pending")


def _step_entry(project, phase: dict, step: dict, workflow=None) -> dict:
    step_id = _step_id(step)
    state = project.get_step_state(phase["id"], step_id, workflow=workflow)
    overrides = project.workflow_phases.get(phase["id"], {})
    phase_description = overrides.get("description") or phase.get("description", "")
    return {
        "step_id": step_id,
        "phase_id": phase["id"],
        "phase_description": phase_description,
        "check_id": None,
        "title": _step_title(step, step_id),
        "scope": step.get("scope", "global"),
        "status": _effective_status(state),
        "finished": bool(state.get("finished")),
        "observations": state.get("observations", ""),
        "description": state.get("description", ""),
        "examples": state.get("examples", ""),
        "raw_captures": list(state.get("evidence", []) or []),
        "ts": state.get("ts"),
        "focused_by": state.get("focused_by"),
        "done_by": state.get("done_by"),
    }


def _step_ref(project, phase: dict, step: dict, workflow=None) -> dict:
    step_id = _step_id(step)
    state = project.get_step_state(phase["id"], step_id, workflow=workflow)
    return {
        "step_id": step_id,
        "phase_id": phase["id"],
        "title": _step_title(step, step_id),
        "scope": step.get("scope", "global"),
        "status": _effective_status(state),
        "finished": bool(state.get("finished")),
    }


@router.get("/workflow", response_model=WorkflowDefinition)
def get_workflow(service: ProjectService = Depends(require_loaded)):
    """Trimmed workflow index — id, name, checklist, and phases as id+name only.
    Use GET /workflow/phases for descriptions and /workflow/phases/{id}/steps for steps.
    Or just call GET /workflow/now to fetch the only step the agent should be
    working on right now."""
    p = service.current
    wf = service.workflow_for(p)
    return WorkflowDefinition(
        id=wf.id,
        name=wf.name,
        checklist=wf.checklist_id,
        phases=[{"id": ph["id"], "name": ph.get("name", ph["id"])} for ph in wf.phases],
    )


@router.get("/workflow/state", response_model=WorkflowStateResponse)
def workflow_state(service: ProjectService = Depends(require_loaded)):
    p = service.current
    wf = service.workflow_for(p)
    nxt = p.next_workflow_step(wf)
    return WorkflowStateResponse(
        workflow_id=wf.id,
        current_phase=p.current_phase,
        phases=[{"id": ph["id"], "name": ph.get("name", ph["id"])} for ph in wf.phases],
        next=nxt,
        phase_complete=nxt is None,
    )


@router.get("/workflow/current-phase", response_model=WorkflowCurrentPhaseResponse)
def current_workflow_phase(service: ProjectService = Depends(require_loaded)):
    p = service.current
    wf = service.workflow_for(p)
    current = wf.phase(p.current_phase)
    if current is None:
        raise HTTPException(
            status_code=409,
            detail=f"project current_phase {p.current_phase} is not part of workflow {wf.id}",
        )
    overrides = p.workflow_phases.get(current["id"], {})
    description = overrides.get("description") or current.get("description", "")
    return WorkflowCurrentPhaseResponse(
        workflow_id=wf.id,
        current_phase={
            "id": current["id"],
            "name": current.get("name", current["id"]),
            "description": description,
        },
    )


@router.get("/workflow/phases", response_model=WorkflowPhasesResponse)
def list_workflow_phases(service: ProjectService = Depends(require_loaded)):
    """Phases with id, name, and merged description. Steps are NOT included —
    fetch /workflow/phases/{phase_id}/steps for the step list."""
    p = service.current
    wf = service.workflow_for(p)
    overrides = p.workflow_phases
    phases = []
    for phase in wf.phases:
        override = overrides.get(phase["id"], {})
        description = override.get("description") or phase.get("description", "")
        phases.append({
            "id": phase["id"],
            "name": phase.get("name", phase["id"]),
            "description": description,
        })
    return WorkflowPhasesResponse(workflow_id=wf.id, phases=phases)


@router.get("/workflow/phases/{phase_id}", response_model=PhaseView)
def get_workflow_phase(phase_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    wf = service.workflow_for(p)
    if wf.phase(phase_id) is None:
        raise HTTPException(status_code=404, detail=f"phase {phase_id} not found in workflow {wf.id}")
    return PhaseView(**p.get_phase_view(wf, phase_id))


@router.post("/workflow/phases/{phase_id}/context", response_model=PhaseView)
def update_workflow_phase_context(
    phase_id: str,
    body: UpdatePhaseContextRequest,
    service: ProjectService = Depends(require_loaded),
):
    """Set a per-project description override for a phase. Saves to project.json
    (workflow_phases map); never modifies the source workflow file."""
    p = service.current
    wf = service.workflow_for(p)
    if wf.phase(phase_id) is None:
        raise HTTPException(status_code=404, detail=f"phase {phase_id} not found in workflow {wf.id}")
    p.update_phase_context(phase_id, description=body.description)
    service.save(p)
    return PhaseView(**p.get_phase_view(wf, phase_id))


@router.get("/workflow/endpoint-testing/status", response_model=EndpointWorkflowStatusResponse)
def endpoint_testing_status(service: ProjectService = Depends(require_loaded)):
    return service.current.endpoint_testing_ready()


@router.get("/workflow/endpoint-testing/focused", response_model=FocusedEndpointsResponse)
def focused_endpoints(service: ProjectService = Depends(require_loaded)):
    """Return all focused endpoints plus the candidate pool."""
    return service.current.focused_endpoints_workflow()


@router.get("/workflow/endpoint-testing/current-step", response_model=EndpointTestingStepResponse)
def endpoint_testing_current_step(
    endpoint_id: str | None = None,
    service: ProjectService = Depends(require_loaded),
):
    """Per-endpoint state machine. With endpoint_id, return that endpoint's current step:

      - adjust_checks — review the endpoint's assigned per-endpoint checks and mark any
        that don't apply to THIS endpoint as 'not applicable' (PUT /api/v1/check/{check_id}/status
        with endpoint_id + an observation), then POST .../checks-adjusted to confirm. This
        is what "adjust" means here: you prune via 'not applicable', you do NOT add or remove
        checks (the check set is curated by the operator in the Web UI).
      - run_check — run the next still-pending assigned check and record status + observations.
      - mark_tested — every assigned check is settled; POST .../finish to mark it tested.

    Without endpoint_id: suggest focus_endpoint if any candidate exists."""
    return EndpointTestingStepResponse(step=service.current.current_endpoint_testing_step(endpoint_id))


@router.get("/workflow/endpoint-testing/next-step", response_model=EndpointTestingStepResponse)
def endpoint_testing_next_step(
    endpoint_id: str | None = None,
    service: ProjectService = Depends(require_loaded),
):
    """The endpoint-testing step that would come after the current one for the given endpoint."""
    return EndpointTestingStepResponse(step=service.current.next_endpoint_testing_step(endpoint_id))


@router.post("/workflow/advance", response_model=AdvancePhaseResponse)
def advance_workflow(
    body: AdvancePhaseRequest = Body(default=AdvancePhaseRequest()),
    service: ProjectService = Depends(require_loaded),
):
    p = service.current
    wf = service.workflow_for(p)
    closing_modify_mode = body.phase_id is not None and (
        body.phase_id != p.current_phase or p.is_phase_in_modify_mode(body.phase_id)
    )
    if not closing_modify_mode:
        if not p.agent_advance_allowed:
            raise HTTPException(
                status_code=403,
                detail=(
                    "advancing phases is restricted to the human operator for this project "
                    "(agent advance is disabled); ask the operator to advance via the UI"
                ),
            )
        current = wf.phase(p.current_phase)
        if current is not None and current.get("human_advance"):
            raise HTTPException(
                status_code=403,
                detail=f"phase {current['id']} can only be advanced by a human operator via the UI",
            )
    _enforce_notes_update(p, body.notes_old_string, body.notes_new_string)
    before = p.current_phase
    try:
        p.advance_phase(wf, skip_optional=body.skip_optional, phase_id=body.phase_id)
    except PhaseIncompleteError as e:
        raise HTTPException(status_code=409, detail={"message": str(e), "unfinished": e.unfinished})
    except WorkflowOrderError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    service.save(p)
    return AdvancePhaseResponse(
        current_phase=p.current_phase,
        advanced=p.current_phase != before,
        update_notes=_update_notes_hint(p),
    )


def _steps_for_workflow(project, workflow) -> list[dict]:
    out = []
    for phase in workflow.phases:
        for step in phase.get("steps", []):
            if _step_id(step):
                out.append(_step_ref(project, phase, step, workflow=workflow))
    return out


def _steps_for_phase(project, phase: dict, workflow=None) -> list[dict]:
    out = []
    for step in phase.get("steps", []):
        if _step_id(step):
            out.append(_step_ref(project, phase, step, workflow=workflow))
    return out


@router.get("/workflow/phases/{phase_id}/steps", response_model=PhaseStepsResponse)
def list_phase_steps(phase_id: str, service: ProjectService = Depends(require_loaded)):
    """Trimmed list of step refs (id, title, status). Fetch
    /workflow/phases/{phase_id}/steps/{step_id} for description, examples, observations
    and raw captures."""
    p = service.current
    wf = service.workflow_for(p)
    phase = wf.phase(phase_id)
    if phase is None:
        raise HTTPException(status_code=404, detail=f"phase {phase_id} not found in workflow {wf.id}")
    return PhaseStepsResponse(workflow_id=wf.id, phase_id=phase_id, steps=_steps_for_phase(p, phase, workflow=wf))


@router.get("/workflow/phases/{phase_id}/steps/{step_id}", response_model=StepDetailResponse)
def get_phase_step(
    phase_id: str,
    step_id: str,
    service: ProjectService = Depends(require_loaded),
):
    """Full detail for one step: description, examples, current observations, raw captures,
    and a `next` hint telling the agent which call to make."""
    p = service.current
    wf = service.workflow_for(p)
    phase, step = _phase_step(wf, phase_id, step_id)
    entry = StepEntry(**_step_entry(p, phase, step, workflow=wf))
    state = p.get_step_state(phase_id, step_id, workflow=wf)
    can_finish, missing = _finish_gating(state, p, phase_id, step_id)
    return StepDetailResponse(
        step=entry,
        can_finish=can_finish,
        missing=missing,
        next=next_step_to_action(p.resolve_next_step(wf), p, wf),
    )


@router.get("/workflow/now", response_model=NowResponse)
def workflow_now(service: ProjectService = Depends(require_loaded)):
    """Single 'where am I?' endpoint. Returns the current step's full detail
    plus a `next` action hint. When the current phase is complete, returns
    phase_complete=True with a hint to POST /workflow/advance."""
    p = service.current
    wf = service.workflow_for(p)
    phase = wf.phase(p.current_phase)
    if phase is None:
        raise HTTPException(
            status_code=409,
            detail=f"project current_phase {p.current_phase} is not part of workflow {wf.id}",
        )

    ns = p.resolve_next_step(wf)
    next_step = NextStepView(
        kind=ns.kind, phase_id=ns.phase_id, label=ns.label,
        target=ns.target, waiting_on=ns.waiting_on, remaining=ns.remaining,
    )
    next_action = next_step_to_action(ns, p, wf)

    for step in phase.get("steps", []):
        sid = _step_id(step)
        if not sid:
            continue
        state = p.get_step_state(phase["id"], sid, workflow=wf)
        if state.get("disabled"):
            continue
        if state.get("finished"):
            continue
        entry = StepEntry(**_step_entry(p, phase, step, workflow=wf))
        can_finish, missing = _finish_gating(state, p, phase["id"], sid)
        return NowResponse(
            workflow_id=wf.id,
            phase_id=phase["id"],
            phase_complete=False,
            step=entry,
            can_finish=can_finish,
            missing=missing,
            next_step=next_step,
            next=next_action,
        )

    return NowResponse(
        workflow_id=wf.id,
        phase_id=phase["id"],
        phase_complete=True,
        step=None,
        next_step=next_step,
        next=next_action,
    )


@router.post("/workflow/phases/{phase_id}/steps/{step_id}/finish", response_model=FinishStepResponse)
def finish_phase_step(
    phase_id: str,
    step_id: str,
    body: FinishStepRequest = Body(default=FinishStepRequest()),
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Confirm a step is done. Requires status in {done, skipped} AND non-empty
    observations — otherwise 409 with `{detail: {error, missing, fix}}`. On success
    returns the next step's full detail in the same phase, or phase_complete=True
    with a hint to POST /workflow/advance.

    When the project has `notes_required=True`, the body must also carry
    `notes_old_string` + `notes_new_string` (Edit-tool semantics, same as
    PATCH /api/v1/notes) — otherwise 409 `notes_required`."""
    p = service.current
    wf = service.workflow_for(p)
    phase, step = _phase_step(wf, phase_id, step_id)
    state = p.get_step_state(phase_id, step_id, workflow=wf)
    already_finished = bool(state.get("finished"))
    can_finish, missing = _finish_gating(state, p, phase_id, step_id)
    if not can_finish and not already_finished:
        example_body = {}
        if "status" in missing:
            example_body["status"] = "done"
        if "observations" in missing:
            example_body["observations"] = "what you did and what you found"
        raise HTTPException(
            status_code=409,
            detail={
                "error": "step_not_finishable",
                "missing": missing,
                "fix": {
                    "method": "PUT",
                    "path": f"/api/v1/workflow/phases/{phase_id}/steps/{step_id}",
                    "example_body": example_body,
                },
            },
        )

    # Re-calling /finish on an already-finished step is idempotent and skips
    # the notes gate — the agent already paid that cost the first time.
    if not already_finished:
        # Try-harder gate runs before the notes gate so the first finish doesn't
        # consume a notes edit while leaving the step unfinished. Persist the
        # nudge flag so the second call (which goes through) is recognised.
        if p.try_harder_nudge_step(phase_id, step_id):
            service.save(p)
            raise TryHarderError(TRY_HARDER_MESSAGE)
        _enforce_notes_update(p, body.notes_old_string, body.notes_new_string)
        p.mark_step_finished(phase_id, step_id, agent=agent.tag())
        service.save(p)

    finished = StepRef(**_step_ref(p, phase, step, workflow=wf))

    nxt = _next_step_in_phase(p, wf, phase, step_id)
    if nxt is None:
        return FinishStepResponse(
            finished=finished,
            next_step=None,
            phase_complete=True,
            next=next_step_to_action(p.resolve_next_step(wf), p, wf),
            update_notes=_update_notes_hint(p),
        )

    next_step_def, next_state = nxt
    next_entry = StepEntry(**_step_entry(p, phase, next_step_def, workflow=wf))
    return FinishStepResponse(
        finished=finished,
        next_step=next_entry,
        phase_complete=False,
        next=next_step_to_action(p.resolve_next_step(wf), p, wf),
        update_notes=_update_notes_hint(p),
    )


@router.get("/workflow/steps", response_model=WorkflowStepsResponse)
def list_current_workflow_steps(service: ProjectService = Depends(require_loaded)):
    p = service.current
    wf = service.workflow_for(p)
    return WorkflowStepsResponse(workflow_id=wf.id, steps=_steps_for_workflow(p, wf))


@router.get("/workflow/{workflow_id}/steps", response_model=WorkflowStepsResponse)
def list_workflow_steps(workflow_id: str, service: ProjectService = Depends(require_loaded)):
    p = service.current
    wf = service.workflow_for(p)
    if wf.id != workflow_id:
        raise HTTPException(
            status_code=404,
            detail=f"workflow {workflow_id} is not loaded; use /workflow/phases/{{phase_id}}/steps for phase steps",
        )
    return WorkflowStepsResponse(workflow_id=wf.id, steps=_steps_for_workflow(p, wf))


def _phase_step(workflow, phase_id: str, step_id: str) -> tuple[dict, dict]:
    phase = workflow.phase(phase_id)
    if phase is None:
        raise HTTPException(status_code=404, detail=f"phase {phase_id} not found in workflow {workflow.id}")
    step = next((s for s in phase.get("steps", []) if _step_id(s) == step_id), None)
    if step is None:
        raise HTTPException(status_code=404, detail=f"step {step_id} not found in phase {phase_id}")
    return phase, step


def _next_step_in_phase(project, workflow, phase: dict, after_step_id: str) -> tuple[dict, dict] | None:
    """Return the (step_def, state) tuple for the next non-finished step in the
    same phase after `after_step_id`. None when phase is complete. A step is
    "finished" only after POST /workflow/.../finish — PUT status=done|skipped
    is not enough."""
    seen = False
    for step in phase.get("steps", []):
        sid = _step_id(step)
        if not sid:
            continue
        if not seen:
            if sid == after_step_id:
                seen = True
            continue
        state = project.get_step_state(phase["id"], sid, workflow=workflow)
        if state.get("disabled"):
            continue
        if state.get("finished"):
            continue
        return step, state
    return None


def _update_step_entry(project, workflow, phase: dict, step: dict, step_id: str, body: BulkStepUpdateRequest, agent: Agent) -> StepEntry:
    # Read-before-overwrite: refuse to clobber an existing observation unless the
    # agent echoed back the text it last read (base_observations). Agent-only gate
    # — the operator UI edits via a pre-filled form, so it stays in the API layer.
    if body.observations is not None:
        current_obs = project.get_step_state(phase["id"], step_id, workflow=workflow).get("observations", "")
        _guard_observation_overwrite(
            current_obs, body.observations, body.base_observations,
            field=f"step {phase['id']}/{step_id} observations",
            read_path=f"/api/v1/workflow/phases/{phase['id']}/steps/{step_id}",
        )
    # Focus is mandatory before any result write; the gate lives in the domain
    # (project.update_workflow_step / add_workflow_step_evidence) so it applies to
    # the operator UI too, and surfaces as FocusRequiredError -> structured 409.
    try:
        state = project.update_workflow_step(
            workflow,
            phase["id"],
            step_id,
            status=body.status,
            observations=body.observations,
            agent=agent.tag(),
        )
    except WorkflowOrderError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except StepDisabledError as e:
        raise HTTPException(status_code=409, detail={"error": "step_disabled", "message": str(e)})

    if body.raw_captures is not None:
        _reject_unless_attested(
            body.raw_captures.agent_composed,
            body.raw_captures.this_really_is_raw_capture_and_not_an_ai_script,
        )
        try:
            project.add_workflow_step_evidence(
                phase["id"],
                step_id,
                name=body.raw_captures.name,
                data_b64=body.raw_captures.data,
                mime_type=body.raw_captures.mime_type,
                source_type=body.raw_captures.source_type,
                description=body.raw_captures.description,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except StepDisabledError as e:
            raise HTTPException(status_code=409, detail={"error": "step_disabled", "message": str(e)})
        state = project.get_step_state(phase["id"], step_id, workflow=workflow)

    return StepEntry(
        step_id=step_id,
        phase_id=phase["id"],
        check_id=None,
        title=_step_title(step, step_id),
        scope=step.get("scope", "global"),
        status=state.get("status", "pending"),
        finished=bool(state.get("finished")),
        observations=state.get("observations", ""),
        raw_captures=list(state.get("evidence", []) or []),
        ts=state.get("ts"),
        focused_by=state.get("focused_by"),
        done_by=state.get("done_by"),
    )


@router.put("/workflow/phases/{phase_id}/steps/{step_id}", response_model=StepEntry)
def update_phase_step(
    phase_id: str,
    step_id: str,
    body: BulkStepUpdateRequest,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    """Set a step's status and/or observations. The step must be focused first:
    claim it with status='focused', then record observations / set it done|skipped.
    Writing observations or a done|skipped status to an unfocused step is rejected
    409 `step_not_focused`. Does NOT advance past the step on its own — even with
    status=done, the step remains the current step on /workflow/now until
    POST .../finish is called. Setting status back to a non-terminal value also
    clears any prior `finished` flag."""
    p = service.current
    wf = service.workflow_for(p)
    phase, step = _phase_step(wf, phase_id, step_id)
    entry = _update_step_entry(p, wf, phase, step, step_id, body, agent)
    service.save(p)
    return entry


@router.put("/workflow/{workflow_id}/step/{step_id}", response_model=StepEntry)
def update_workflow_step(
    workflow_id: str,
    step_id: str,
    body: BulkStepUpdateRequest,
    service: ProjectService = Depends(require_loaded),
    agent: Agent = Depends(require_agent),
):
    p = service.current
    wf = service.workflow_for(p)
    if wf.id != workflow_id:
        raise HTTPException(status_code=404, detail=f"workflow {workflow_id} not loaded")

    matches = []
    for phase in wf.phases:
        for step in phase.get("steps", []):
            if _step_id(step) == step_id:
                matches.append((phase, step))
    if not matches:
        raise HTTPException(status_code=404, detail=f"step {step_id} not found in workflow")
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"step {step_id} exists in multiple phases; use /workflow/phases/{{phase_id}}/steps/{step_id}",
        )

    target_phase, target_step = matches[0]
    entry = _update_step_entry(p, wf, target_phase, target_step, step_id, body, agent)
    service.save(p)
    return entry


@router.post(
    "/workflow/phases/{phase_id}/steps/{step_id}/raw-captures",
    response_model=RawCaptureMeta,
    status_code=201,
    summary="Post Step Raw Capture  (upload only logs, screenshots, HTTP responses, 3rd party tool output. NEVER EVER upload AI/Agent generated output / summaries / bash-script outputs)",
)
def post_step_raw_capture(
    phase_id: str,
    step_id: str,
    body: AddRawCaptureRequest,
    service: ProjectService = Depends(require_loaded),
):
    """Attach raw 3rd-party output (tool stdout, screenshot, log, HTTP response, target file)
    to a step. Self-written summaries belong in `observations`, not here. `data` is base64."""
    _reject_unless_attested(
        body.agent_composed,
        body.this_really_is_raw_capture_and_not_an_ai_script,
    )
    p = service.current
    wf = service.workflow_for(p)
    _phase_step(wf, phase_id, step_id)
    try:
        entry = p.add_workflow_step_evidence(
            phase_id,
            step_id,
            name=body.name,
            data_b64=body.data,
            mime_type=body.mime_type,
            source_type=body.source_type,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StepDisabledError as e:
        raise HTTPException(status_code=409, detail={"error": "step_disabled", "message": str(e)})
    service.save(p)
    return entry


@router.post("/workflow/phases/{phase_id}/modify-mode")
def toggle_phase_modify_mode(phase_id: str):
    """Re-opening a past phase for editing is a human operator decision in
    the web UI. Agents must not toggle it. If you (as the agent) believe a
    previous phase needs corrections, surface that to the operator — they
    can open modify mode for you. Once it is open, you can edit step status
    and observations as if the phase were current, then close it via
    POST /workflow/advance with phase_id={that_phase}."""
    raise HTTPException(
        status_code=403,
        detail={
            "error": "human_only",
            "message": (
                "toggling phase modify mode is only possible through the web UI "
                "and must not be done by an agent. Ask the operator to open it if "
                "you believe a past phase needs corrections. Once open, you can "
                "edit it and close modify mode by calling POST /workflow/advance "
                "with phase_id set to that phase."
            ),
        },
    )


@router.post("/workflow/phases/{phase_id}/steps/{step_id}/disable")
def disable_phase_step(phase_id: str, step_id: str):
    """Disabling a workflow step is a per-project decision made by the human
    operator in the web UI. Agents must not toggle it — if you think a step
    doesn't apply, set its status to `skipped` with observations instead."""
    raise HTTPException(
        status_code=403,
        detail={
            "error": "human_only",
            "message": (
                "disabling/enabling a workflow step is only possible through the web UI "
                "and must not be done by an agent. If a step does not apply, set its "
                "status to 'skipped' with observations explaining why."
            ),
        },
    )


@router.post("/workflow/phases/{phase_id}/steps/{step_id}/enable")
def enable_phase_step(phase_id: str, step_id: str):
    """See disable_phase_step — same restriction applies."""
    raise HTTPException(
        status_code=403,
        detail={
            "error": "human_only",
            "message": (
                "disabling/enabling a workflow step is only possible through the web UI "
                "and must not be done by an agent."
            ),
        },
    )


@router.get("/workflow/phases/{phase_id}/steps/{step_id}/raw-captures/{capture_id}")
def download_step_raw_capture(
    phase_id: str,
    step_id: str,
    capture_id: str,
    service: ProjectService = Depends(require_loaded),
):
    p = service.current
    try:
        entry, abs_path = p.get_workflow_step_evidence(phase_id, step_id, capture_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="raw capture file missing on disk")
    return FileResponse(
        path=str(abs_path),
        media_type=entry.get("mime_type", "application/octet-stream"),
        filename=entry["name"],
    )

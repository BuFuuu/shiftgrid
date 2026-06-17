from __future__ import annotations

from fastapi import HTTPException

from Domain import (
    NotesOldStringNotFound,
    NotesOldStringNotUnique,
    NotesImmutableRegionViolation,
)

from .schemas import NextAction


def _update_notes_hint(project) -> NextAction:
    if project.notes_required:
        return NextAction(
            action="edit_notes",
            method="PATCH",
            path="/api/v1/notes",
            why=(
                "This project has notes_required=true. Notes were updated inline with this "
                "action (via notes_old_string/notes_new_string in the body) and the next "
                "finish/advance/unfocus/check-done call MUST do the same — same Edit-tool "
                "semantics as this endpoint. PATCH /api/v1/notes is still available between "
                "those actions if you need to record free-form context. Read GET /api/v1/notes "
                "first so your old_string matches verbatim. <immutable>…</immutable> is read-only."
            ),
            example_body={"old_string": "## Findings\n", "new_string": "## Findings\n- SQLi at /login (finding: ab12cd34)\n"},
        )
    return NextAction(
        action="edit_notes",
        method="PATCH",
        path="/api/v1/notes",
        why=(
            "MUST PATCH /api/v1/notes if you gathered any important info during this action. "
            "Edit-tool semantics: provide `old_string` (must match current notes verbatim and "
            "uniquely) and `new_string`. Read GET /api/v1/notes first so you know the current "
            "content. Header inside <immutable>…</immutable> is read-only. "
            "Keep notes brief — short-term memory only, not a transcript."
        ),
        example_body={"old_string": "## Findings\n", "new_string": "## Findings\n- SQLi at /login (finding: ab12cd34)\n"},
    )


_NOTES_REQUIRED_EXAMPLE = {
    "notes_old_string": "## Findings\n",
    "notes_new_string": "## Findings\n- ... what you learned this step ...\n",
}


def _guard_observation_overwrite(current, new, base, *, field: str, read_path: str) -> None:
    """Hard 409 read-before-overwrite guard for an observations field. Fires only
    when there is already a non-empty observation and the incoming value differs
    from it: the caller must have read the current text and echo it back as
    `base_observations`. A missing base means the agent never confirmed what it is
    about to clobber; a stale base means someone wrote since it last read. Either
    way the write is refused and the current value is handed back so the agent can
    re-read and merge instead of silently overwriting a concurrent write."""
    current = current or ""
    if not current.strip():
        return  # nothing to overwrite — the first observation is written freely
    if new == current:
        return  # idempotent re-send: nothing is clobbered
    if base is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "observations_overwrite_unconfirmed",
                "message": (
                    f"{field} already has content; overwriting it blindly is refused so a "
                    "concurrent agent's or the operator's write isn't silently clobbered. "
                    f"Read the current value (GET {read_path}), merge your update into it, "
                    "and resend with `base_observations` set to the exact text you read."
                ),
                "current": current,
            },
        )
    if base != current:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "observations_changed",
                "message": (
                    f"{field} changed since you read it — another agent or the operator "
                    f"wrote to it. Re-read the current value (GET {read_path}), merge your "
                    "update into it, and resend with `base_observations` matching it."
                ),
                "current": current,
            },
        )


def _enforce_notes_update(project, notes_old_string, notes_new_string) -> None:
    """When `project.notes_required` is True, apply an Edit-tool-style notes
    update in-line with a finish/done/advance action. No-op when the flag is
    off. Raises HTTPException(409) when the fields are missing or unchanged,
    HTTPException(400) on edit validation failures (same shape as PATCH /notes)."""
    if not project.notes_required:
        return
    if notes_old_string is None or notes_new_string is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "notes_required",
                "message": (
                    "this project enforces note-taking on finish/done/advance actions. "
                    "Re-call this endpoint with `notes_old_string` and `notes_new_string` "
                    "in the body — Edit-tool semantics, same as PATCH /api/v1/notes. "
                    "Read GET /api/v1/notes first so you know the current contents, then "
                    "encode what you learned in this step into a single targeted edit."
                ),
                "fix": {
                    "example_body": {
                        "notes_old_string": "## Findings\n",
                        "notes_new_string": "## Findings\n- ... what you just learned ...\n",
                    },
                },
            },
        )
    if notes_old_string == notes_new_string:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "notes_unchanged",
                "message": (
                    "notes_old_string and notes_new_string are identical — encode what you "
                    "learned this step into the diff. If you truly have nothing to add, "
                    "still record a one-line breadcrumb (what you did, even if uneventful)."
                ),
            },
        )
    try:
        project.edit_notes(notes_old_string, notes_new_string)
    except NotesOldStringNotFound as e:
        raise HTTPException(status_code=400, detail={"error": "old_string_not_found", "message": str(e)})
    except NotesOldStringNotUnique as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "old_string_not_unique", "message": str(e), "match_count": e.count},
        )
    except NotesImmutableRegionViolation as e:
        raise HTTPException(status_code=400, detail={"error": "immutable_region", "message": str(e)})


# Flat map of every top-level resource. Lets agents discover paths without
# guessing and powers the 404 fallback (see `api_resource_map`).
RESOURCES: dict[str, Any] = {
    "openapi": "/api/v1/openapi.json",
    "docs": "/api/v1/docs",
    "notes": {"get": "/api/v1/notes", "patch": "/api/v1/notes"},
    "project": {
        "info": "/api/v1/project/info",
    },
    "workflow": {
        "now": "/api/v1/workflow/now",
        "state": "/api/v1/workflow/state",
        "current_phase": "/api/v1/workflow/current-phase",
        "phases": "/api/v1/workflow/phases",
        "phase": "/api/v1/workflow/phases/{phase_id}",
        "phase_steps": "/api/v1/workflow/phases/{phase_id}/steps",
        "step": "/api/v1/workflow/phases/{phase_id}/steps/{step_id}",
        "finish_step": "/api/v1/workflow/phases/{phase_id}/steps/{step_id}/finish",
        "step_raw_captures": "/api/v1/workflow/phases/{phase_id}/steps/{step_id}/raw-captures",
        "advance": "/api/v1/workflow/advance",
        "endpoint_testing_focused": "/api/v1/workflow/endpoint-testing/focused",
        "endpoint_testing_status": "/api/v1/workflow/endpoint-testing/status",
    },
    "checklist": {
        "all": "/api/v1/checklist",
        "categories": "/api/v1/checklist/categories",
        "titles": "/api/v1/checklist/titles",
        "next": "/api/v1/checklist/next",
        "filter": "/api/v1/checklist/filter",
    },
    "check": {
        "get": "/api/v1/check/{check_id}",
        "status": "/api/v1/check/{check_id}/status",
        "observations": "/api/v1/check/{check_id}/observations",
        "raw_captures": "/api/v1/check/{check_id}/raw-captures",
        "finish": "/api/v1/check/{check_id}/finish",
    },
    "endpoints": "/api/v1/endpoints",
    "endpoint": {
        "get": "/api/v1/endpoint/{endpoint_id}",
        "create": "/api/v1/endpoint",
        "update": "/api/v1/endpoint/{endpoint_id}",
        "focus": "/api/v1/endpoint/{endpoint_id}/focus",
        "unfocus": "/api/v1/endpoint/{endpoint_id}/unfocus",
        "checks_adjusted": "/api/v1/endpoint/{endpoint_id}/checks-adjusted",
        "reopen": "/api/v1/endpoint/{endpoint_id}/reopen",
        "feature_group": "/api/v1/endpoint/{endpoint_id}/feature-group",
    },
    "feature_groups": "/api/v1/feature-groups",
    "findings": "/api/v1/findings",
    "finding": {
        "get": "/api/v1/finding/{finding_id}",
        "create": "/api/v1/finding",
        "raw_captures": "/api/v1/finding/{finding_id}/raw-captures",
    },
}


def api_resource_map() -> dict[str, Any]:
    return RESOURCES


def _step_id(step: dict) -> str | None:
    return step.get("id") or step.get("check")


def _finish_gating(state: dict, project=None, phase_id: str | None = None, step_id: str | None = None) -> tuple[bool, list[str]]:
    """Return (can_finish, missing). A step can be finished when its status is
    done|skipped and its observations field is non-empty. Step-specific blockers
    (e.g. endpoint_testing wrap-up steps requiring all endpoints settled) are
    appended when project + phase_id + step_id are provided."""
    missing: list[str] = []
    if state.get("status", "pending") not in ("done", "skipped"):
        missing.append("status")
    if not (state.get("observations") or "").strip():
        missing.append("observations")
    if project is not None and phase_id and step_id:
        missing.extend(project.step_finish_blockers(phase_id, step_id))
    return (not missing, missing)


def _next_action_for_step(project, workflow, phase: dict, step: dict, state: dict) -> NextAction:
    sid = _step_id(step)
    # Focus is mandatory before any result write: a pending step must be claimed
    # (PUT status='focused') before its observations/status can be recorded.
    if state.get("status", "pending") == "pending":
        return NextAction(
            action="focus_step",
            method="PUT",
            path=f"/api/v1/workflow/phases/{phase['id']}/steps/{sid}",
            why="claim this step before working on it: set its status to 'focused' first",
            example_body={"status": "focused"},
        )
    can_finish, missing = _finish_gating(state, project, phase["id"], sid)
    if can_finish:
        return NextAction(
            action="finish_step",
            method="POST",
            path=f"/api/v1/workflow/phases/{phase['id']}/steps/{sid}/finish",
            why=(
                "status is done|skipped and observations are set"
                + ("; project requires inline notes diff (notes_old_string + notes_new_string)" if project.notes_required else "")
            ),
            example_body=dict(_NOTES_REQUIRED_EXAMPLE) if project.notes_required else None,
        )
    if "observations" in missing and "status" in missing:
        return NextAction(
            action="set_observations_and_status",
            method="PUT",
            path=f"/api/v1/workflow/phases/{phase['id']}/steps/{sid}",
            why="observations are empty and status is not done|skipped",
            example_body={"status": "done", "observations": "what you did and what you found"},
        )
    if "observations" in missing:
        return NextAction(
            action="set_observations",
            method="PUT",
            path=f"/api/v1/workflow/phases/{phase['id']}/steps/{sid}",
            why="observations field is empty",
            example_body={"observations": "what you did and what you found"},
        )
    return NextAction(
        action="set_status",
        method="PUT",
        path=f"/api/v1/workflow/phases/{phase['id']}/steps/{sid}",
        why="status is not yet done|skipped",
        example_body={"status": "done"},
    )


def next_step_to_action(next_step, project, workflow) -> NextAction:
    """AI-hint layer: decorate the structural NextStep with the concrete call to
    make. For a step it reuses the per-step gating; for checklist/endpoint phases
    it points at the relevant sub-flow; otherwise advance / done."""
    if next_step.kind == "do_step":
        phase = workflow.phase(next_step.phase_id)
        step = next((s for s in (phase or {}).get("steps", []) if _step_id(s) == next_step.target), None)
        if step is not None:
            state = project.get_step_state(next_step.phase_id, next_step.target, workflow=workflow)
            return _next_action_for_step(project, workflow, phase, step, state)
    if next_step.kind == "work_checklist":
        return NextAction(
            action="work_next_check", method="GET", path="/api/v1/checklist/next",
            why=f"{next_step.remaining} global-scope check(s) remain; focus one, set status + observations, then finish it",
        )
    if next_step.kind == "test_endpoints":
        return NextAction(
            action="test_endpoint", method="GET", path="/api/v1/workflow/endpoint-testing/current-step",
            why=f"{next_step.remaining} endpoint(s) still need testing; follow the endpoint-testing steps",
        )
    if next_step.kind == "close_modify":
        return NextAction(
            action="close_modify_mode", method="POST", path="/api/v1/workflow/advance",
            why=(
                f"phase {next_step.phase_id} is reopened in modify mode with no unfinished steps; "
                "edit its steps or close modify mode by advancing that phase"
            ),
            example_body={"phase_id": next_step.phase_id, "skip_optional": False},
        )
    if next_step.kind == "advance_phase":
        return NextAction(
            action="advance_phase", method="POST", path="/api/v1/workflow/advance",
            why=(
                "all work in this phase is complete"
                + ("; project requires inline notes diff (notes_old_string + notes_new_string)" if project.notes_required else "")
            ),
            example_body=({"skip_optional": False, **_NOTES_REQUIRED_EXAMPLE} if project.notes_required else {"skip_optional": False}),
        )
    return NextAction(
        action="workflow_complete", method="GET", path="/api/v1/workflow/state",
        why="all phases are complete",
    )


def _endpoint_loop_next(project, workflow, endpoint_id: str | None = None) -> NextAction:
    """AI-hint layer for the endpoint loop: decorate the per-endpoint state
    machine (current_endpoint_testing_step) with the concrete call to make.
    When the machine has nothing to say for this endpoint (just tested /
    unfocused), it retries without an id — "focus the next candidate" — and
    deliberately names no specific endpoint there, so concurrent agents pick
    from the pool instead of piling onto one. When the machine is silent
    entirely (no candidates / other phase) it falls back to the workflow
    resolver, handing the agent back to the outer loop (advance, etc.)."""
    step = project.current_endpoint_testing_step(endpoint_id)
    if step is None and endpoint_id is not None:
        step = project.current_endpoint_testing_step(None)
    if step is None:
        return next_step_to_action(project.resolve_next_step(workflow), project, workflow)
    kind = step["kind"]
    eid = step["endpoint_id"]
    if kind == "focus_endpoint":
        return NextAction(
            action="focus_endpoint", method="GET", path="/api/v1/workflow/endpoint-testing/focused",
            why=(
                "pick the next candidate from the pool (prefer unfocused ones), "
                "then POST /api/v1/endpoint/{endpoint_id}/focus"
            ),
        )
    if kind == "adjust_checks":
        return NextAction(
            action="adjust_checks", method="POST", path=f"/api/v1/endpoint/{eid}/checks-adjusted",
            why=(
                f"review the checks assigned to endpoint {step['endpoint_name']}: mark any that "
                "don't apply as 'not applicable' (PUT /api/v1/check/{check_id}/status with "
                "endpoint_id + an observation), then confirm with this call"
            ),
        )
    if kind == "run_check":
        return NextAction(
            action="run_check", method="PUT", path=f"/api/v1/check/{step['check_id']}/status",
            why=(
                f"run check '{step['check_title']}' against endpoint {step['endpoint_name']}; "
                f"record what you saw first (PUT /api/v1/check/{step['check_id']}/observations "
                "with endpoint_id), then set the status"
            ),
            example_body={"status": "passed", "endpoint_id": eid},
        )
    return NextAction(
        action="finish_endpoint", method="POST", path=f"/api/v1/endpoint/{eid}/finish",
        why=f"every assigned check on endpoint {step['endpoint_name']} is settled; mark it tested",
    )


def _raw_capture_rejection_message(agent_composed: bool, really_raw: bool) -> str:
    return (
        "The /raw-captures endpoints accept raw 3rd-party bytes only: tool stdout "
        "captured verbatim, screenshots, server logs, HTTP responses captured by a "
        "client, or files copied from the target unchanged. The clue is in the name — "
        "this slot is for RAW CAPTURES, not your analysis, plans, summaries, or "
        "anything you composed. If you had to type, edit, label, or wrap any of the "
        "bytes, it is not a raw capture.\n\n"
        "Two attestation flags gate this endpoint. BOTH default to the rejecting "
        "state, and BOTH must be flipped to upload:\n"
        "  - agent_composed: must be set to false (you are attesting you did NOT "
        "compose, write, narrate, label, edit, or wrap any of these bytes).\n"
        "  - this_really_is_raw_capture_and_not_an_ai_script: must be set to true "
        "(you are attesting every byte came verbatim from a 3rd-party source — not "
        "from a script you wrote).\n\n"
        f"Your values: agent_composed={agent_composed!s}, "
        f"this_really_is_raw_capture_and_not_an_ai_script={really_raw!s}. "
        "Required to pass: agent_composed=false AND "
        "this_really_is_raw_capture_and_not_an_ai_script=true.\n\n"
        "If you cannot honestly set both, the content is not a raw capture. Route it "
        "elsewhere:\n"
        "  (1) Self-authored prose, plans, analysis, summaries -> PATCH /api/v1/notes "
        "or the step's `observations` field.\n"
        "  (2) Real tool output that you wrapped with section headers, labels, or "
        "narration (e.g. `echo \"=== TEST 1 ===\"` between curl calls) -> re-upload "
        "ONLY the raw captured bytes with no headers or narration added. Split "
        "multiple captures into separate uploads (one per tool invocation) rather "
        "than concatenating them with your own dividers.\n"
        "  (3) Output of a bash/python script that you wrote -> the structure and "
        "selection are yours; this does not qualify. Re-run the underlying tool "
        "directly and upload only its raw stdout, OR put the analysis in /notes."
    )


def _reject_unless_attested(agent_composed: bool, really_raw: bool) -> None:
    if agent_composed or not really_raw:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "raw_capture_attestation_failed",
                "message": _raw_capture_rejection_message(agent_composed, really_raw),
            },
        )

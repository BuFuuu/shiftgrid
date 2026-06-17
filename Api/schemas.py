from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CheckStatus = Literal["pending", "focused", "failed", "passed", "warning", "vulnerable", "not applicable"]
StepStatus = Literal["pending", "focused", "done", "skipped", "disabled"]

BASE_OBSERVATIONS_DESC = (
    "Read-before-overwrite guard (only relevant when an observation already exists). "
    "To replace a non-empty observations field you must echo back the exact text you "
    "last read here. If omitted, or if it no longer matches the stored value because a "
    "concurrent agent/operator wrote in the meantime, the write is rejected 409 with the "
    "current value so you can re-read and merge. Leave unset when first writing an "
    "observation, when only changing status, or when re-sending the identical text."
)
EvidenceSourceType = Literal[
    "tool_output", "screenshot", "log", "raw_response", "config", "file_content", "other"
]


# -------- catalog --------

class AgentTag(BaseModel):
    """Who focused / finished an item. Stamped from the caller's
    X-Agent-Id / X-Agent-Alias headers (see Api.deps.Agent)."""

    id: str
    alias: str


class PhaseRef(BaseModel):
    id: str
    name: str


class PhaseDescription(BaseModel):
    id: str
    name: str
    description: str = ""


class WorkflowDefinition(BaseModel):
    """Trimmed workflow index. Returns phase id+name only — agents must call
    GET /workflow/phases or /workflow/phases/{id}/steps for descriptions and steps."""

    id: str
    name: str
    checklist: str | None = None
    phases: list[PhaseRef]


class ChecklistDefinition(BaseModel):
    id: str
    name: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


# -------- project state --------

class Credential(BaseModel):
    id: str
    username: str = ""
    password: str = ""
    observations: str = ""
    source: str = ""
    added_at: int


class Endpoint(BaseModel):
    id: str
    name: str
    title: str = ""
    type: str = "host"
    observations: str = ""
    source: str = ""
    added_at: int
    status: str = "todo"
    checks: list[str] = Field(default_factory=list)
    feature_group: str = ""
    checks_adjusted: bool = False
    focused_by: AgentTag | None = None
    done_by: AgentTag | None = None


class EndpointSlim(BaseModel):
    id: str
    name: str
    status: str = "todo"


class FeatureGroup(BaseModel):
    id: str
    name: str


class FeatureGroupSummary(BaseModel):
    id: str
    name: str
    endpoints: list[EndpointSlim] = Field(default_factory=list)


class ChecklistLink(BaseModel):
    check_id: str
    title: str
    phase: str = "checklist"
    category: str = "uncategorized"
    category_name: str = "Uncategorized"
    status: CheckStatus
    observations: str = ""


class EndpointDetail(Endpoint):
    checklist_links: list[ChecklistLink] = Field(default_factory=list)


class RawCaptureMeta(BaseModel):
    id: str
    name: str
    mime_type: str = "application/octet-stream"
    source_type: EvidenceSourceType = "other"
    description: str = ""
    path: str
    size: int
    added_at: int


class ChecklistItemResult(BaseModel):
    status: CheckStatus = "pending"
    observations: str = ""
    raw_captures: list[RawCaptureMeta] = Field(default_factory=list)
    ts: int | None = None
    focused_by: AgentTag | None = None
    done_by: AgentTag | None = None


class ChecklistItem(BaseModel):
    id: str
    phase: str = "checklist"
    title: str
    category: str = "uncategorized"
    category_name: str = "Uncategorized"
    scope: str
    repeatable: bool = False
    produces_endpoints: bool = False
    results: dict[str, ChecklistItemResult] = Field(default_factory=dict)


class ChecklistCategorySummary(BaseModel):
    id: str
    name: str
    count: int


class ChecklistTitleEntry(BaseModel):
    id: str
    title: str
    scope: str
    category: str
    category_name: str


class Finding(BaseModel):
    id: str
    title: str
    severity: str = "info"
    description: str = ""
    recommendation: str = ""
    raw_captures: list[RawCaptureMeta] = Field(default_factory=list)
    created_at: int
    updated_at: int


class ProjectInfoResponse(BaseModel):
    id: str
    name: str
    workflow: str
    scope: list[str]
    scope_locked: bool
    info: str = ""
    context: str = ""
    details: str = ""
    current_phase: str
    folder: str
    created_at: int
    updated_at: int
    notes_required: bool = True
    observations_required: bool = False


class WorkflowStateResponse(BaseModel):
    workflow_id: str
    current_phase: str
    phases: list["PhaseRef"]
    next: dict[str, Any] | None = None
    phase_complete: bool


class WorkflowCurrentPhaseResponse(BaseModel):
    workflow_id: str
    current_phase: "PhaseDescription"


class WorkflowPhasesResponse(BaseModel):
    workflow_id: str
    phases: list[PhaseDescription]


class PhaseView(BaseModel):
    id: str
    name: str
    description: str = ""
    optional: bool = False
    free: bool = False
    modify_mode: bool = False
    is_done: bool = False


class UpdatePhaseContextRequest(BaseModel):
    description: str


class StepEntry(BaseModel):
    step_id: str
    phase_id: str
    phase_description: str = ""
    check_id: str | None = None
    title: str
    scope: str
    status: StepStatus = "pending"
    finished: bool = False
    observations: str = ""
    description: str = ""
    examples: str = ""
    raw_captures: list[RawCaptureMeta] = Field(default_factory=list)
    ts: int | None = None
    focused_by: AgentTag | None = None
    done_by: AgentTag | None = None


class StepRef(BaseModel):
    """Trimmed step entry for index responses. No description/examples/observations/raw_captures —
    fetch the per-step detail endpoint for those."""

    step_id: str
    phase_id: str
    title: str
    scope: str = "global"
    status: StepStatus = "pending"
    finished: bool = False


class NextAction(BaseModel):
    """Hint to the agent about the recommended next API call."""

    action: str
    method: str
    path: str
    why: str = ""
    example_body: dict[str, Any] | None = None


class StepDetailResponse(BaseModel):
    step: StepEntry
    can_finish: bool = False
    missing: list[str] = Field(default_factory=list)
    next: NextAction


class FinishStepResponse(BaseModel):
    """Response to POST /workflow/.../finish. Field order matters for agents
    that skim: `update_notes` is first so the obligation isn't buried."""

    update_notes: NextAction
    finished: StepRef
    next: NextAction
    next_step: StepEntry | None = None
    phase_complete: bool = False


class NextStepView(BaseModel):
    """Structural 'what to do next' — drives the UI indicator. The agent hint in
    `next` is derived from this."""

    kind: str
    phase_id: str
    label: str
    target: str | None = None
    waiting_on: str | None = None
    remaining: int = 0


class NowResponse(BaseModel):
    workflow_id: str
    phase_id: str
    phase_complete: bool = False
    step: StepEntry | None = None
    can_finish: bool = False
    missing: list[str] = Field(default_factory=list)
    next_step: NextStepView
    next: NextAction


class WorkflowStepsResponse(BaseModel):
    workflow_id: str
    steps: list[StepRef]


class PhaseStepsResponse(BaseModel):
    workflow_id: str
    phase_id: str
    steps: list[StepRef]


class EndpointWorkflowStatusResponse(BaseModel):
    ready: bool
    blocking: int
    counts: dict[str, int]
    message: str


class FocusedEndpointView(BaseModel):
    endpoint: dict[str, Any]
    checks_adjusted: bool = False
    next_check: ChecklistLink | None = None
    checks: list[ChecklistLink] = Field(default_factory=list)
    done_count: int = 0
    pending_count: int = 0


class FocusedEndpointsResponse(BaseModel):
    focused: list[FocusedEndpointView] = Field(default_factory=list)
    candidates: list[EndpointSlim] = Field(default_factory=list)


class EndpointTestingStep(BaseModel):
    kind: Literal["focus_endpoint", "adjust_checks", "run_check", "mark_tested"] = Field(
        ...,
        description=(
            "Current sub-step of one endpoint's testing cycle. "
            "'focus_endpoint' = start testing the next candidate. "
            "'adjust_checks' = review the assigned per-endpoint checks and mark any that "
            "don't apply as 'not applicable', then POST .../checks-adjusted to confirm — "
            "you prune via 'not applicable', you cannot add/remove checks via the API. "
            "'run_check' = run the next still-pending assigned check. "
            "'mark_tested' = all checks settled, POST /endpoint/{id}/finish."
        ),
    )
    endpoint_id: str | None = None
    endpoint_name: str | None = None
    check_id: str | None = None
    check_title: str | None = None


class EndpointTestingStepResponse(BaseModel):
    step: EndpointTestingStep | None = None


class NotesResponse(BaseModel):
    notes: str
    project_info: str = ""


class CheckStatusUpdateResponse(ChecklistItemResult):
    """Result body for PUT /check/{id}/status — same fields as ChecklistItemResult
    plus a hint to refresh /api/v1/notes. When the update targets a per-endpoint
    check (endpoint_id set), `next` carries the endpoint loop's next call."""
    update_notes: NextAction
    next: NextAction | None = None


class FinishCheckResponse(ChecklistItemResult):
    """Result body for POST /check/{id}/finish — adds the `next` hint that keeps
    the checklist loop going: the next pending check, or phase advance."""
    next: NextAction


class EndpointUpdateResponse(Endpoint):
    """Endpoint shape for PUT /endpoint/{id} and POST /endpoint/{id}/finish —
    adds an `update_notes` hint and the endpoint loop's `next` call."""
    update_notes: NextAction
    next: NextAction | None = None


class EndpointActionResponse(Endpoint):
    """Endpoint shape for the loop shortcuts (focus, checks-adjusted) — adds the
    endpoint loop's `next` call so each response hands the agent its next move."""
    next: NextAction | None = None


class InfoResponse(BaseModel):
    info: str


class CredentialsListResponse(BaseModel):
    credentials: list[Credential]


# -------- requests --------

class AddEndpointRequest(BaseModel):
    name: str = Field(
        min_length=1,
        description=(
            "The endpoint's full address as a SINGLE string -- e.g. "
            "'https://host:8443/admin' or '10.0.0.5'. Put the whole address here; "
            "there are no separate port or path fields to fill."
        ),
    )
    title: str = Field(
        default="",
        description="Short human label for this endpoint, e.g. 'Admin login' or 'Orders API'.",
    )
    source: str = "ai"
    observations: str = ""


class UpdateEndpointRequest(BaseModel):
    # The endpoint address (name) is omitted on purpose: it is the endpoint's
    # identity and cannot be changed after creation. The title is editable.
    title: str | None = None
    type: str | None = None
    observations: str | None = None
    base_observations: str | None = Field(default=None, description=BASE_OBSERVATIONS_DESC)
    status: str | None = Field(
        default=None,
        description=(
            "One of: todo, focused, tested, out-of-scope. Routed through the guarded "
            "state machine: todo <-> focused -> tested ; todo <-> out-of-scope ; "
            "tested -> todo (reopen, clears tested_at). Guards: 'focused' needs a feature "
            "group; 'tested' needs checks adjusted + every per-endpoint check non-pending; "
            "'out-of-scope' is reachable only from 'todo'. Illegal moves return 409. "
            "Dedicated shortcuts exist: POST .../focus, .../unfocus, .../checks-adjusted, .../reopen."
        ),
    )


class CreateFeatureGroupRequest(BaseModel):
    name: str = Field(min_length=1)


class SetEndpointFeatureGroupRequest(BaseModel):
    group_id: str | None = None


class NotesEditFields(BaseModel):
    """Optional Edit-tool-style notes update piggy-backed on finish/done/advance
    actions. When the project has `notes_required=True`, both fields become
    mandatory on the gated endpoints and the API rejects the action otherwise.
    Semantics match PATCH /api/v1/notes: `notes_old_string` must match the
    current notes verbatim and uniquely; the <immutable>…</immutable> header
    is read-only."""

    notes_old_string: str | None = None
    notes_new_string: str | None = None


class UpdateCheckStatusRequest(NotesEditFields):
    status: CheckStatus
    endpoint_id: str | None = None

    # Reject unknown fields loudly (422) instead of silently dropping them — e.g.
    # `observations`, which belongs on PUT /check/{id}/observations, not here.
    model_config = {"extra": "forbid"}


class UpdateCheckObservationsRequest(BaseModel):
    observations: str
    base_observations: str | None = Field(default=None, description=BASE_OBSERVATIONS_DESC)
    endpoint_id: str | None = None

    # Symmetric guard: a misplaced `status` here should 422, not vanish.
    model_config = {"extra": "forbid"}


_AGENT_COMPOSED_DESC = (
    "Default true (the rejecting state). You MUST explicitly set this to false to "
    "upload — and only if every single byte in the payload was produced by a 3rd-party "
    "(tool stdout captured verbatim, screenshot, server log, captured HTTP response, "
    "file copied from the target unchanged). Set true (or leave default) whenever you "
    "composed, wrote, narrated, summarized, labeled, edited, OR wrapped the bytes in "
    "any way — including: prose plans, test write-ups, analyses, recommendations, OR "
    "real tool stdout that you wrapped with your own section headers like "
    "`echo \"=== TEST 1 ===\"`, labels, comments, or interleaved narration. If you "
    "composed it in any way at all, this is true."
)

_REALLY_RAW_CAPTURE_DESC = (
    "Default false (the rejecting state). You MUST explicitly set this to true to "
    "upload — and only as a positive attestation that every byte in this payload is a "
    "raw 3rd-party capture, not the output of an AI-authored script, not a write-up, "
    "not a plan, not a summary, not bytes you assembled or edited. If a bash/python "
    "script that YOU wrote produced these bytes (even if the script calls real tools), "
    "this is still false — because the structure and selection are yours. True means: "
    "this is what a tool literally emitted, untouched, OR a screenshot, OR a file you "
    "downloaded from the target unmodified."
)


class AddRawCaptureRequest(BaseModel):
    source_type: EvidenceSourceType = Field(
        description="Origin of the bytes. Tool output is raw 3rd-party output (tool stdout, "
        "screenshots, logs, HTTP responses, target files). Self-written summaries go in `observations`, "
        "not here. Use `other` only when none of the specific types fit."
    )
    agent_composed: bool = Field(default=True, description=_AGENT_COMPOSED_DESC)
    this_really_is_raw_capture_and_not_an_ai_script: bool = Field(
        default=False, description=_REALLY_RAW_CAPTURE_DESC
    )
    name: str = Field(min_length=1)
    mime_type: str = "application/octet-stream"
    data: str = Field(min_length=1, description="base64-encoded payload")
    description: str = Field(
        min_length=1,
        description="What this capture shows and why it matters as evidence — e.g. "
        "'sqlmap output confirming boolean-based SQLi on the id parameter'. Shown "
        "next to the file in the UI.",
    )
    endpoint_id: str | None = None


class AddFindingRawCaptureRequest(BaseModel):
    source_type: EvidenceSourceType = Field(
        description="Origin of the bytes. See AddRawCaptureRequest.source_type."
    )
    agent_composed: bool = Field(default=True, description=_AGENT_COMPOSED_DESC)
    this_really_is_raw_capture_and_not_an_ai_script: bool = Field(
        default=False, description=_REALLY_RAW_CAPTURE_DESC
    )
    name: str = Field(min_length=1)
    mime_type: str = "application/octet-stream"
    data: str = Field(min_length=1, description="base64-encoded payload")
    description: str = Field(
        min_length=1,
        description="What this capture shows and why it matters as evidence — e.g. "
        "'screenshot of the admin panel reached without authentication'. Shown "
        "next to the file in the UI.",
    )


class BulkStepUpdateRequest(BaseModel):
    status: StepStatus | None = None
    observations: str | None = None
    base_observations: str | None = Field(default=None, description=BASE_OBSERVATIONS_DESC)
    endpoint_id: str | None = None
    raw_captures: AddRawCaptureRequest | None = None


class AdvancePhaseRequest(NotesEditFields):
    skip_optional: bool = False
    phase_id: str | None = Field(
        default=None,
        description=(
            "Optional. When set to a phase id that is in modify mode and not the "
            "current phase, this call closes modify mode for that phase (requires "
            "every non-disabled step in that phase to be finished). When None or "
            "equal to the current phase, this is a normal advance."
        ),
    )


class FinishStepRequest(NotesEditFields):
    """Body for POST /workflow/.../finish. Empty when `notes_required=False`."""


class UnfocusEndpointRequest(NotesEditFields):
    """Body for POST /endpoint/{id}/unfocus. Empty when `notes_required=False`."""


class AddCredentialRequest(BaseModel):
    username: str = ""
    password: str = ""
    observations: str = ""
    source: str = "ai"


class EditNotesRequest(BaseModel):
    """Edit-tool style notes update: replace one occurrence of `old_string`
    with `new_string`. `old_string` must match the current notes verbatim
    and uniquely — re-read GET /api/v1/notes first if unsure. Edits that
    would alter the <immutable>…</immutable> header are rejected."""

    old_string: str
    new_string: str


class SetInfoRequest(BaseModel):
    info: str


class CreateFindingRequest(BaseModel):
    title: str = Field(min_length=1)
    severity: str = "info"
    description: str = ""
    recommendation: str = ""


class UpdateFindingRequest(BaseModel):
    title: str | None = None
    severity: str | None = None
    description: str | None = None
    recommendation: str | None = None


# -------- misc responses --------

class AdvancePhaseResponse(BaseModel):
    current_phase: str
    advanced: bool
    update_notes: NextAction


class DeleteResponse(BaseModel):
    removed: bool


class ErrorResponse(BaseModel):
    detail: str

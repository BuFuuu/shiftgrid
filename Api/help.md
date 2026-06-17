# ShiftGrid API

ShiftGrid is a workflow/checklist-driven project runner. A human
operator owns scope via the Web UI; a lead tester AI followes the workflow
through this API. The currently loaded project is server-side state — the API
itself is stateless. Scope is fixed at project creation and cannot be changed
via the API.

**Identify yourself first.** ShiftGrid is worked concurrently, so every request
(except this one and `GET /api/v1/agent-id`) must carry `X-Agent-Id` and
`X-Agent-Alias` headers — calls without them are rejected. `GET /api/v1/agent-id`
mints an id and explains how to pick an alias and persist both. Do that before anything else.

Start here: `GET /api/v1/workflow/now` — the current step plus the next action.

Notes (short-term memory for the test): `GET /api/v1/notes` — read first,
refresh after each step/advance/check/endpoint transition. Use Markdown for them.
The notes should provide an overview of the target, vulnerabilities, and workflow observations. However, they should be more than just a log of the test. They should also support important decisions and help resolve issues an agent might encounter during testing. So they have this holistic view of the test which cares about the question what is the risk model the app cares about. They are the central source of information that transcends steps, phases, checks, and tests.

Full schema: `/api/v1/openapi.json` · interactive docs: `/api/v1/docs`.

The work is organised as three loops:

All three loops share one shape — focus → work → record → **finish** — and every
loop-driving response carries a `next` hint with the concrete call to make.
Follow `next` after each action instead of memorising paths.

1. **Workflow loop** — walk the project's phases and steps in order
   (`/api/v1/workflow/...`): see what's now, focus a stetp, do the work, record observations, upload evidence,
   finish each step (`POST .../steps/{step_id}/finish`), advance when the
   phase is complete. For the observations (capped at 120 words): Use newlines to separate thoughts, `**bold**` for key findings, `*italic*` for emphasis, and `# heading` for section labels. Follow the steps closely.
2. **Checklist loop** — when working on the checklist step, focus → work → record →
   status → finish each global check (`POST /api/v1/check/{check_id}/finish`).
   Try to finish one check first before starting another. But first follow the steps of this phase.
3. **Endpoint loop** — when working on the endpoints testing step, walk each endpoint through
   focus → adjust checks (mark any checks that don't apply as 'not applicable',
   then confirm via `.../checks-adjusted`) → run each assigned check → finish
   (`POST /api/v1/endpoint/{endpoint_id}/finish` marks it tested). But first follow the steps of this phase.
   Reopen (`.../reopen`) is the one way back out of `tested` (tested → todo). 

**Focus is mandatory, and you focus by setting status to `focused`.** You MUST
claim a workflow step or global checklist check before working on it — recording
observations, setting a result status, or attaching raw captures on an unfocused
step/check is rejected with `409 {error: step_not_focused | check_not_focused}`
and a `fix` hint. Focus is a *status*, set the same way everywhere:

- workflow step → `PUT /api/v1/workflow/phases/{phase_id}/steps/{step_id}` with `{"status": "focused"}`
- global check → `PUT /api/v1/check/{check_id}/status` with `{"status": "focused"}`

So the shape is always `pending → focused → result → finish`. (Endpoints are
claimed the same way, `PUT /api/v1/endpoint/{id}` with `{"status": "focused"}`;
their per-endpoint checks are governed by that endpoint focus and cannot be set to
`focused` directly.) Claiming work this way lets concurrent agents see what's in
progress, prioritize unassigned work, and avoid duplication. The `next` hint always
points you at the focus call when one is due.

Follow the workflow. Do not do free pentesting if step/phase does not explicitly tells you to.


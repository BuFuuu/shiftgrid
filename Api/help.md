# ShiftGrid API

ShiftGrid is a workflow/checklist-driven pentest project runner. A human
operator owns scope via the Web UI; a pentester AIs drive the test
through the API.

**Identify yourself first.** ShiftGrid is worked concurrently, so every request
(except this one and `GET /api/v1/agent-id`) must carry `X-Agent-Id` and
`X-Agent-Alias` headers — calls without them are rejected. `GET /api/v1/agent-id`
mints an id and explains how to pick an alias and persist both (it also tells
restarted agents and subagents what to do). Do that before anything else.

Start here: `GET /api/v1/workflow/now` — the current step's full detail and the
next action to take. There are phases like recon, checklist-work, free-testing
etc.; each phase has different steps.

Notes (short-term memory for the test): `GET /api/v1/notes` — read first,
refresh after each step/advance/check/endpoint transition. Use Markdown for them.
They should provide an overview of the target, vulnerabilities, and workflow observations. However, they should be more than just a log of the test. They should also support important decisions and help resolve issues an agent might encounter during testing. So they have this holistic view of the test which cares about the question what is the risk model the app cares about. They are the central source of information that transcends steps, phases, checks, and tests.

Focus is mandatory, and you focus by setting status to `focused`. You MUST
claim a workflow step or global checklist check before working on it — recording
observations, setting a result status, or attaching raw captures on an unfocused
step/check is rejected. This lets concurrent agents see
what's already in progress, prioritize unassigned work, and avoid duplication. The
`next` hint always points you at the focus call when one is due.

Add raw_captures to workflow steps, check and findings when ever a tool produced meaning full output or you want to add evidence like a raw file or a screenshot to your observations. Do not skip this step. It is part of the loops! Never upload information that basically just repeat the observation.

**Runs (repeat count).** A phase, global check, or endpoint can be set to run more
than once for a deeper dive (`runs`: an int, default 1 = once, or `"indefinite"`).
When you finish a check / mark an endpoint tested / advance a phase that still has
runs left, it does not settle: it resets to its starting state (status back to
pending/todo, observations kept) and you get a 409 `{"error": "run_again"}` telling
you to work through it again, harder, using what you learned last time. On the final
run it settles normally. The operator's "Try harder" switches are shortcuts: the
one on the checklist page adds one run (+1) to every global check, the one on the
endpoints page adds one run (+1) to every endpoint.

Full OpenAPI spec: `/api/v1/openapi.json` · interactive docs: `/api/v1/docs`.

**Raw captures are file uploads** (`multipart/form-data`) — send the file bytes as
the `file` field, never base64 or JSON. E.g. `curl -F file=@nmap.txt -F source_type=tool_output
-F description=... -F this_really_is_raw_capture_and_not_an_ai_script=true -F agent_composed=false .../raw-captures`.

# Agent Workflow Loop
     
1. GET /api/v1/notes  — read the pentest notes for ongoing context
2. GET /api/v1/workflow/now  — see the current step + next action
3. PUT /api/v1/workflow/phases/{phase_id}/steps/{step_id} — choose a step to work on and set status to focused. Prioritize pending steps first
3. action: do the work in your environment
4. PUT /api/v1/workflow/phases/{phase_id}/steps/{step_id}  — set observations and status (done|skipped) (capped at 200 words) (Use newlines to separate thoughts, `**bold**` for key findings, `*italic*` for emphasis, and `# heading` for section labels)
5. POST /api/v1/workflow/phases/{phase_id}/steps/{step_id}/raw-captures (not mandatory but very important)  — attach raw tool output, screenshots, captured HTTP responses, etc. NEVER for anything you wrote, narrated, summarized, or wrapped
6. POST /api/v1/workflow/phases/{phase_id}/steps/{step_id}/finish  — moves /workflow/now to the next step (when notes_required=true, carry notes_old_string + notes_new_string in the body)
[7. optional: PATCH /api/v1/notes  — include insights from this step (optional, only when notes_required=false)]
8. only when phase_complete=true: POST /api/v1/workflow/advance (advancing never requires a notes diff, even when notes_required=true — PATCH /api/v1/notes separately if you have context to record; a multi-run phase resets its steps to pending and loops instead of advancing — the `next` hint shows `(run N/M)`; see Runs)

# Work-on-Checklist phase loop

During the "Work on Checklist" phase, advance only after every global-scope check has been focused, worked on, and finished. Loop per check:

1. GET /api/v1/checklist/filter?status=pending&scope=global  — pick one global-check that is still pending
2. GET /api/v1/check/{check_id}  — read its full detail
3. PUT /api/v1/check/{check_id}/status `{"status": "focused"}`  — claim it (mandatory before any result write). Multiple agents may focus the same item, but unfocused items should be prioritized first
4. do the work in your environment
5. PUT /api/v1/check/{check_id}/observations 
6. POST /api/v1/check/{check_id}/raw-captures
7. PUT /api/v1/check/{check_id}/status  — passed, vulnerable, warning, failed, not applicable (be precise about this! `passed` = ran and target is fine; `vulnerable` = confirmed vuln; `warning` = suspicious-unconfirmed; `failed` = could not run; `not applicable` = check doesn't apply, observations required). Requires the check to be focused first (step 3).
8. POST /api/v1/check/{check_id}/finish  — requires a recorded result (not pending/focused) and non-empty observations. carries the notes diff inline. (a multi-run check resets to pending and returns 409 `run_again` instead of settling; see Runs)
9. PATCH /api/v1/notes  — optional, only when notes_required=false

# Work-on-Endpoints phase loop

During the "Work on Endpoints" (`endpoint_testing`) phase, advance only after every endpoint is either `tested` or `out-of-scope`. Each endpoint walks this guarded state machine:  out-of-scope <-> todo  <->  focused  ->  (adjust checks  ->  !actually execute testing here!)  ->  tested  - -reopen- ->  todo

"adjust checks" does NOT mean adding/removing checks (the operator curates the check set in the Web UI). It means: review the checks assigned to this endpoint and mark any that don't apply as `not applicable` (with an observation), then confirm with `.../checks-adjusted`.

`reopen` is the ONLY edge out of `tested`, and it only does `tested -> todo`. It is not a generic "set to todo": unfocus a `focused` endpoint with `.../unfocus`, and rescope an `out-of-scope` endpoint with `PUT /api/v1/endpoint/{id}` `{"status":"todo"}`. From `todo` you can then go to `focused` or `out-of-scope`.

Guards: `focused` requires a feature group; `tested` requires checks adjusted + every per-endpoint check non-pending;

[0: PUT /api/v1/endpoint/{endpoint_id}/feature-group]
[0: GET /api/v1/endpoint/{endpoint_id}]
1. POST /api/v1/endpoint/{endpoint_id}/focus — Agents can set an endpoint to focus when they want to work on it. Multiple agents may focus the same item, but unfocused items should be prioritized first.
2. Any checks not needed or too much? -> Set status to "not applicable" with a observation (observations are mandatory)
3. POST /api/v1/endpoint/{endpoint_id}/checks-adjusted (confirm step 2. is finished) 
4. For each endpoint check: do the work now! It's testing time!
5. PUT /api/v1/check/{check_id}/status and observations with `endpoint_id` set — the response's `next` names the next pending check for this endpoint
6. POST /api/v1/endpoint/{endpoint_id}/finish  — marks it tested (same guards as before); its `next` points at the next candidate (a multi-run endpoint resets to todo and returns 409 `run_again` instead of settling; see Runs)

# API Endpoints

{{ENDPOINTS}}

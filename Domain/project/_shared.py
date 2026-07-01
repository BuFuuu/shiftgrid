from __future__ import annotations

import base64
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..catalog import Workflow


@dataclass
class NextStep:
    """The single structural 'what to do next' for the workflow. Phase-kind
    aware: it points at a step, the checklist, or the endpoint sweep, or tells
    you to advance. Drives the UI 'next action' indicator; the API decorates it
    with an agent hint."""

    kind: str                       # do_step | work_checklist | test_endpoints | advance_phase | close_modify | done
    phase_id: str
    label: str
    target: str | None = None       # step_id for do_step
    waiting_on: str | None = None    # "checklist" | "endpoints"
    remaining: int = 0


class ProjectNotFoundError(Exception):
    pass


class ScopeLockedError(Exception):
    pass


class NoProjectLoadedError(Exception):
    pass


class PhaseIncompleteError(Exception):
    """Raised when trying to advance a phase that still has unfinished steps."""
    def __init__(self, message: str, unfinished: list[dict] | None = None):
        super().__init__(message)
        self.unfinished = unfinished or []


class WorkflowOrderError(Exception):
    """Raised when a workflow step update would violate phase or step order."""


class StepDisabledError(Exception):
    """Raised when a write targets a disabled workflow step. Disabled is a
    project-wide flag that takes a step out of the active workflow without
    changing its underlying status."""


class FocusRequiredError(Exception):
    """Raised when a write (observations, a result status, raw captures) targets a
    workflow step or global check that has not been focused first. Focus is the
    mandatory 'claim' that precedes any work, for both agents and the operator.
    Carries a structured payload so the agent API can hand back a `fix` hint."""

    def __init__(self, message: str, *, kind: str, fix: dict | None = None):
        super().__init__(message)
        self.kind = kind  # "step_not_focused" | "check_not_focused"
        self.fix = fix or {}


class RunAgainError(Exception):
    """Raised by a finish action (finish a global check, mark an endpoint tested)
    when the item is configured to run more times than it has been run. The
    finish is NOT applied as terminal; instead the item is reset to its starting
    state — observations kept — and the agent is told to run through it again,
    harder. Surfaced as a 409. Phase runs do not use this exception (advancing is
    a valid action that loops rather than fails); they carry the message on the
    advance response instead."""

    def __init__(self, message: str, *, runs_completed: int = 0, target: "int | str" = 1):
        super().__init__(message)
        self.runs_completed = runs_completed
        self.target = target


def _normalize_runs(value) -> "int | str":
    """Clean a configured run count into a positive int or the string
    'indefinite'. The count is the TOTAL number of times an item runs: 1 (the
    default) = run once, no looping. Anything unset, non-numeric, or < 1 falls
    back to 1. Accepts a few spellings of 'indefinite' from hand-edited JSON /
    operator input, plus the ∞ glyph the UI renders."""
    if isinstance(value, bool):  # bool is an int subclass — never a count
        return 1
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("indefinite", "indefinitely", "infinite", "infinity", "inf", "∞"):
            return "indefinite"
        try:
            value = int(v)
        except ValueError:
            return 1
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def _runs_should_loop(target, runs_completed: int) -> bool:
    """True when, after completing this run, more runs remain — i.e. the finish
    should reset the item and loop instead of moving on. `runs_completed` counts
    runs already looped, NOT counting the one being completed now. target=1 (the
    default) never loops; 'indefinite' always loops until the operator lowers the
    number."""
    target = _normalize_runs(target)
    if target == "indefinite":
        return True
    return (runs_completed + 1) < target


def _bump_runs(current, delta: int) -> "int | str":
    """Add `delta` to a run count, flooring at 1 and leaving 'indefinite' alone.
    Used by the 'Try harder' switches to bump a whole category of run counts."""
    current = _normalize_runs(current)
    if current == "indefinite":
        return current
    return max(1, int(current) + delta)


def _runs_message(kind: str, subject: str, run_number: int, target) -> str:
    """The nudge surfaced when an item resets to run again. `run_number` is the
    run about to start (1-based); `kind` is 'phase' / 'check' / 'endpoint';
    `subject` names the thing (phase name, check title, endpoint address)."""
    of = "∞" if _normalize_runs(target) == "indefinite" else str(target)
    return (
        f"{subject} reset for run {run_number}/{of} — work through it again, "
        f"harder, using what you learned last time."
    )


class NotesEditError(Exception):
    """Base class for notes-edit failures."""


class NotesOldStringNotFound(NotesEditError):
    pass


class NotesOldStringNotUnique(NotesEditError):
    def __init__(self, message: str, count: int):
        super().__init__(message)
        self.count = count


class NotesImmutableRegionViolation(NotesEditError):
    pass


OBSERVATIONS_MAX_WORDS = 200
_OBSERVATION_WORD_RE = re.compile(r"\w+", re.UNICODE)


class ObservationsTooLongError(Exception):
    """Raised when an observations field exceeds OBSERVATIONS_MAX_WORDS words.
    A "word" is a run of alphanumeric/underscore characters — punctuation,
    symbols and whitespace don't count toward the limit.

    Intentionally NOT a ValueError so route handlers' generic `except ValueError`
    blocks don't mask it — the dedicated FastAPI exception handler returns a
    structured 400 with `word_count` and `limit`. Web routes catch it explicitly
    to flash a friendly message."""

    def __init__(self, field: str, word_count: int, limit: int = OBSERVATIONS_MAX_WORDS):
        self.field = field
        self.word_count = word_count
        self.limit = limit
        super().__init__(
            f"{field} is {word_count} words; the limit is {limit} "
            f"(alphanumeric tokens are counted, punctuation and symbols ignored). "
            f"Trim it: observations are a summary, not a transcript. Keep only "
            f"the load-bearing facts — what you tested, what you saw, what it "
            f"means. Move raw tool output, payloads and long captures to raw-captures "
            f"(POST /api/v1/.../raw-captures), and longer running context to "
            f"/api/v1/notes (PATCH)."
        )


def _count_observation_words(text: str) -> int:
    if not text:
        return 0
    return len(_OBSERVATION_WORD_RE.findall(text))


def _validate_observations(text: str | None, field: str = "observations") -> None:
    if not text:
        return
    n = _count_observation_words(text)
    if n > OBSERVATIONS_MAX_WORDS:
        raise ObservationsTooLongError(field, n)


def _validate_endpoint_name(name: str) -> None:
    """Roughly sanity-check an endpoint's address (IP / host / URL).

    Not a full URL/IP grammar — just enough to reject obviously malformed
    input. An address is a single token, so it cannot contain whitespace.
    """
    if not name or not name.strip():
        raise ValueError("endpoint name (IP / Host / URL) is required")
    if any(ch.isspace() for ch in name):
        raise ValueError(
            f"endpoint name (IP / Host / URL) {name!r} cannot contain spaces"
        )


_IMMUTABLE_RE = re.compile(r"<immutable>.*?</immutable>", re.DOTALL)


def _immutable_blocks(text: str) -> list[str]:
    return _IMMUTABLE_RE.findall(text)


# "focused" is the mandatory claim a global-scope check passes through before any
# result is recorded — the same shape as a workflow step (pending → focused →
# result). Per-endpoint checks are claimed via their endpoint's focus instead and
# must not take this status directly.
CHECK_STATUSES = ("pending", "focused", "failed", "passed", "warning", "vulnerable", "not applicable")
# The result statuses — everything except the two working states (pending,
# focused). Setting any of these on a global-check requires it to be focused first.
CHECK_RESULT_STATUSES = ("failed", "passed", "warning", "vulnerable", "not applicable")
WORKFLOW_STEP_STATUSES = ("pending", "focused", "done", "skipped")
ENDPOINT_STATUSES = ("todo", "focused", "tested", "out-of-scope")

# Assumed endpoint count for the project-wide progress estimate. The real number
# isn't known up front — a tester adds endpoints as they're discovered — so the
# overall-progress denominator (see WorkflowMixin.overall_progress) reserves room
# for this many endpoints' worth of per-endpoint checks until the actual count
# overtakes it. Keeps the browser-tab "% done" from reading near-complete before
# any endpoint testing has begun.
ESTIMATED_ENDPOINTS = 20

# Single source of truth for which status edges are legal. Same->same is always
# a no-op. Forward moves (focused, tested) carry extra guards enforced in
# set_endpoint_status; this table decides which edges exist at all. Notably,
# out-of-scope is reachable only from/to todo -- you cannot drop a focused or
# tested endpoint straight to out-of-scope. tested can only be reopened to todo.
ENDPOINT_TRANSITIONS = {
    "todo": {"focused", "out-of-scope"},
    "focused": {"todo", "tested"},
    "tested": {"todo"},          # reopen only
    "out-of-scope": {"todo"},    # rescope
}

# Evidence is filed under one of four top-level folders by what it documents.
# Per-endpoint check evidence goes under ENDPOINTS_DIR; global-check evidence under
# CHECKLIST_DIR.
WORKFLOW_DIR = "workflow"
CHECKLIST_DIR = "checklist"
ENDPOINTS_DIR = "endpoints"
FINDINGS_DIR = "findings"
EVIDENCE_DIRS = (WORKFLOW_DIR, CHECKLIST_DIR, ENDPOINTS_DIR, FINDINGS_DIR)


def _now() -> int:
    return int(time.time())


def _safe_name(name: str) -> str:
    keep = "-_.() "
    out = "".join(c for c in name if c.isalnum() or c in keep).strip().replace(" ", "_")
    return out or "file"


__all__ = [
    'base64',
    're',
    'time',
    'uuid',
    'dataclass',
    'Path',
    'Workflow',
    'NextStep',
    'ProjectNotFoundError',
    'ScopeLockedError',
    'NoProjectLoadedError',
    'PhaseIncompleteError',
    'WorkflowOrderError',
    'StepDisabledError',
    'FocusRequiredError',
    'RunAgainError',
    '_normalize_runs',
    '_runs_should_loop',
    '_bump_runs',
    '_runs_message',
    'NotesEditError',
    'NotesOldStringNotFound',
    'NotesOldStringNotUnique',
    'NotesImmutableRegionViolation',
    'OBSERVATIONS_MAX_WORDS',
    '_OBSERVATION_WORD_RE',
    'ObservationsTooLongError',
    '_count_observation_words',
    '_validate_observations',
    '_validate_endpoint_name',
    '_IMMUTABLE_RE',
    '_immutable_blocks',
    'CHECK_STATUSES',
    'CHECK_RESULT_STATUSES',
    'WORKFLOW_STEP_STATUSES',
    'ENDPOINT_STATUSES',
    'ESTIMATED_ENDPOINTS',
    'ENDPOINT_TRANSITIONS',
    'WORKFLOW_DIR',
    'CHECKLIST_DIR',
    'ENDPOINTS_DIR',
    'FINDINGS_DIR',
    'EVIDENCE_DIRS',
    '_now',
    '_safe_name',
]

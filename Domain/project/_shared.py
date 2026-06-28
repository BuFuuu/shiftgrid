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


class TryHarderError(Exception):
    """Raised by a finish action when try-harder mode is on and the item is being
    finished for the first time. The finish is held back and the agent is nudged
    to do a deeper pass and then call finish again — the second call goes through.
    Carries TRY_HARDER_MESSAGE as its message."""


# The nudge an agent gets back on the first finish while try-harder mode is on.
TRY_HARDER_MESSAGE = (
    "Good job! But now try harder! Review what you have done and think harder "
    "about task. Do a deepdive and spend a lot of time figuring out if there is "
    "more to do or better to do. Create a plan on that and try to verify "
    "yourself. Be critical and do not give up! Do not make stuff up though - "
    "keep it real! After you have done that and maybe change the observations or "
    "evidence call finish again to actually finish this task."
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


OBSERVATIONS_MAX_WORDS = 120
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
    'TryHarderError',
    'TRY_HARDER_MESSAGE',
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

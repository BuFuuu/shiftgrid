from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class WorkflowMixin:
    @property
    def workflow_steps(self) -> dict[str, dict]:
        return self.data.setdefault("workflow_steps", {})

    @property
    def workflow_phases(self) -> dict[str, dict]:
        return self.data.setdefault("workflow_phases", {})

    def next_workflow_step(self, workflow: Workflow) -> dict | None:
        phase = workflow.phase(self.current_phase)
        if phase is None:
            return None
        for step in phase.get("steps", []):
            step_id = step.get("id") or step.get("check")
            if not step_id:
                continue
            state = self.get_step_state(phase["id"], step_id)
            if state.get("disabled"):
                continue
            if state.get("finished"):
                continue
            return {
                "step_id": step_id,
                "phase_id": phase["id"],
                "check_id": None,
                "title": step.get("title") or step_id.replace("_", " ").title(),
                "scope": step.get("scope", "global"),
                "status": state.get("status", "pending"),
                "observations": state.get("observations", ""),
                "ts": state.get("ts"),
            }
        return None

    def resolve_next_step(self, workflow: Workflow) -> NextStep:
        """The one resolver, and the gate advance_phase consults. While the
        current phase has an active (non-disabled, non-finished) step it points
        at the work: the checklist or the endpoint sweep in those phase kinds,
        otherwise the step itself. Once the phase's steps are clear it mirrors the
        cross-phase advance gates (the global-checks must be finished before the
        endpoint-testing phase; every endpoint must be settled before a free
        phase) and otherwise points at advance — or done."""
        phase = workflow.phase(self.current_phase)
        if phase is None:
            return self._reopened_phase_next_step(workflow) or NextStep("done", self.current_phase, "Workflow complete")
        pid = phase["id"]
        kind = phase.get("kind", "step")

        def work_checklist(n):
            return NextStep("work_checklist", pid, f"Work the checklist - {n} check(s) left",
                            waiting_on="checklist", remaining=n)

        def test_endpoints(n):
            return NextStep("test_endpoints", pid, f"Test endpoints - {n} remaining",
                            waiting_on="endpoints", remaining=n)

        nxt = self.next_workflow_step(workflow)
        if nxt is not None:
            if kind == "checklist":
                pending = self._unfinished_global_checks()
                if pending:
                    return work_checklist(len(pending))
            elif kind == "endpoint":
                counts = self.endpoint_status_counts()
                blocking = counts["todo"] + counts["focused"]
                if blocking:
                    return test_endpoints(blocking)
            return NextStep("do_step", pid, f"Finish step: {nxt['title']}", target=nxt["step_id"])

        nxt_phase = self._next_active_phase(workflow, pid)
        if nxt_phase is None:
            return self._reopened_phase_next_step(workflow) or NextStep("done", pid, "Workflow complete")
        if nxt_phase["id"] == "endpoint_testing" and self._has_active_checklist_phase(workflow):
            pending = self._unfinished_global_checks()
            if pending:
                return work_checklist(len(pending))
        if nxt_phase.get("free") and pid in ("endpoint_testing", "specific_checks"):
            counts = self.endpoint_status_counts()
            blocking = counts["todo"] + counts["focused"]
            if blocking:
                return test_endpoints(blocking)
        return NextStep("advance_phase", pid, f"Advance to {nxt_phase.get('name', nxt_phase['id'])}")

    def _reopened_phase_next_step(self, workflow: Workflow) -> "NextStep | None":
        """Top-to-bottom, the first phase the operator reopened in modify mode and
        the work left there: its first unfinished step, or — when every step is
        still finished — closing modify mode. Consulted only once the workflow has
        otherwise reached its end, so reopening a done phase pulls 'what's next'
        (and the finished banner) back onto it instead of reporting done. Multiple
        reopened phases resolve top-to-bottom."""
        for phase in workflow.phases:
            pid = phase["id"]
            if not self.is_phase_in_modify_mode(pid):
                continue
            unfinished = self._unfinished_steps_in_phase(workflow, pid)
            if unfinished:
                first = unfinished[0]
                return NextStep("do_step", pid,
                                f"Finish reopened step: {first['title']}", target=first["step_id"])
            # Steps are clear, but a checklist/endpoint phase still can't close
            # while its global-checks / endpoints are unsettled — point at that work
            # instead of offering to close (which the close gate would refuse).
            kind = (phase or {}).get("kind", "step")
            if kind == "checklist" and self._phase_kind_blockers(workflow, pid):
                n = len(self._unfinished_global_checks())
                return NextStep("work_checklist", pid, f"Work the checklist - {n} check(s) left",
                                waiting_on="checklist", remaining=n)
            if kind == "endpoint" and self._phase_kind_blockers(workflow, pid):
                counts = self.endpoint_status_counts()
                n = counts["todo"] + counts["focused"]
                return NextStep("test_endpoints", pid, f"Test endpoints - {n} remaining",
                                waiting_on="endpoints", remaining=n)
            return NextStep("close_modify", pid,
                            f"Close modify mode on {phase.get('name', pid)}", target=pid)
        return None

    def _endpoint_testing_step(
        self,
        kind: str,
        endpoint: dict | None = None,
        check_id: str | None = None,
        check_title: str | None = None,
    ) -> dict:
        return {
            "kind": kind,
            "endpoint_id": endpoint["id"] if endpoint else None,
            "endpoint_name": endpoint.get("name", "") if endpoint else None,
            "check_id": check_id,
            "check_title": check_title,
        }

    def current_endpoint_testing_step(self, endpoint_id: str | None = None) -> dict | None:
        """The action to take for one endpoint's testing cycle right now.
        If endpoint_id is given, walks that endpoint's state machine.
        If endpoint_id is None, suggests starting testing on the first candidate (or None)."""
        if self.current_phase != "endpoint_testing":
            return None
        if endpoint_id is None:
            candidates = self.endpoint_testing_candidates()
            if not candidates:
                return None
            return self._endpoint_testing_step("focus_endpoint")
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint is None or endpoint.get("status") != "focused":
            return None
        if not endpoint.get("checks_adjusted", False):
            return self._endpoint_testing_step("adjust_checks", endpoint)
        nxt_check = self.next_endpoint_check(endpoint)
        if nxt_check is not None:
            return self._endpoint_testing_step(
                "run_check", endpoint,
                check_id=nxt_check["check_id"],
                check_title=nxt_check["title"],
            )
        return self._endpoint_testing_step("mark_tested", endpoint)

    def next_endpoint_testing_step(self, endpoint_id: str | None = None) -> dict | None:
        """The endpoint-testing step that would come after current_endpoint_testing_step.
        Walks the state machine forward by one without mutating."""
        current = self.current_endpoint_testing_step(endpoint_id)
        if current is None:
            return None
        kind = current["kind"]
        if kind == "focus_endpoint":
            candidates = self.endpoint_testing_candidates()
            if not candidates:
                return None
            return self._endpoint_testing_step("adjust_checks", candidates[0])
        endpoint = self.get_endpoint(endpoint_id) if endpoint_id else None
        if endpoint is None:
            return None
        if kind == "adjust_checks":
            progress = self._endpoint_check_progress(endpoint)
            if not progress:
                return self._endpoint_testing_step("mark_tested", endpoint)
            first = progress[0]
            return self._endpoint_testing_step(
                "run_check", endpoint,
                check_id=first["check_id"],
                check_title=first["title"],
            )
        if kind == "run_check":
            progress = self._endpoint_check_progress(endpoint)
            passed = False
            for link in progress:
                if not passed:
                    if link["check_id"] == current["check_id"]:
                        passed = True
                    continue
                if link.get("status", "pending") == "pending":
                    return self._endpoint_testing_step(
                        "run_check", endpoint,
                        check_id=link["check_id"],
                        check_title=link["title"],
                    )
            return self._endpoint_testing_step("mark_tested", endpoint)
        if kind == "mark_tested":
            return None
        return None

    @staticmethod
    def _workflow_step_id(step: dict) -> str | None:
        return step.get("id") or step.get("check")

    @staticmethod
    def _workflow_step_title(step: dict, step_id: str) -> str:
        return step.get("title") or step_id.replace("_", " ").title()

    def unfinished_steps_in_current_phase(self, workflow: Workflow) -> list[dict]:
        phase = workflow.phase(self.current_phase)
        if phase is None:
            return []
        unfinished = []
        for step in phase.get("steps", []):
            step_id = self._workflow_step_id(step)
            if not step_id:
                continue
            state = self.get_step_state(phase["id"], step_id)
            if state.get("disabled"):
                continue
            if state.get("finished"):
                continue
            unfinished.append({
                "step_id": step_id,
                "title": self._workflow_step_title(step, step_id),
                "status": state.get("status", "pending"),
            })
        return unfinished

    def is_phase_in_modify_mode(self, phase_id: str) -> bool:
        entry = self.workflow_phases.get(phase_id) or {}
        return bool(entry.get("modify_mode"))

    def _phase_index(self, workflow: Workflow, phase_id: str) -> int:
        ids = [p["id"] for p in workflow.phases]
        if phase_id not in ids:
            raise ValueError(f"unknown workflow phase {phase_id}")
        return ids.index(phase_id)

    def set_phase_modify_mode(self, workflow: Workflow, phase_id: str, enabled: bool) -> dict:
        """Open or close modify mode for a done phase. Modify mode lifts the
        past_phase access lock so step status, observations, and finish can be
        re-edited. Allowed on phases strictly before the current phase, and on the
        current phase too once the workflow is terminal (its final phase is
        complete) — there the phase reads as 'done', so it must be re-openable like
        any other. Forbidden on a current phase that is still in progress (it is
        already editable) and on future phases (they must be reached normally)."""
        idx = self._phase_index(workflow, phase_id)
        current_idx = self._phase_index(workflow, self.current_phase)
        if idx > current_idx:
            raise WorkflowOrderError(
                f"cannot toggle modify mode on phase {phase_id}: it is a future "
                f"phase (current is {self.current_phase})"
            )
        if idx == current_idx and not self.is_workflow_terminal(workflow):
            raise WorkflowOrderError(
                f"cannot toggle modify mode on the current phase {phase_id} while "
                f"the workflow is still in progress; it is already editable"
            )
        entry = self.workflow_phases.setdefault(phase_id, {})
        entry["modify_mode"] = bool(enabled)
        entry["ts"] = _now()
        return dict(entry)

    def phase_has_enabled_steps(self, workflow: Workflow, phase_id: str) -> bool:
        """A phase counts as 'active' only while it still has at least one step
        the human operator has not disabled. A phase with no steps at all — or
        one whose every step has been disabled — is treated as disabled: the
        workflow skips straight over it instead of stopping there. 'You can only
        do stuff in a phase that has (enabled) steps.'"""
        phase = workflow.phase(phase_id)
        if phase is None:
            return False
        for step in phase.get("steps", []):
            step_id = self._workflow_step_id(step)
            if step_id and not self.is_step_disabled(phase_id, step_id):
                return True
        return False

    def _next_active_phase(self, workflow: Workflow, phase_id: str, skip_optional: bool = False):
        """Like Workflow.next_phase, but also skips any phase that has no enabled
        steps (see phase_has_enabled_steps). A fully-disabled phase is considered
        disabled as well, so advancing passes over it straight to the next phase
        that actually has something to do — or to the end of the workflow."""
        nxt = workflow.next_phase(phase_id, skip_optional=skip_optional)
        while nxt is not None and not self.phase_has_enabled_steps(workflow, nxt["id"]):
            nxt = workflow.next_phase(nxt["id"], skip_optional=skip_optional)
        return nxt

    def _has_active_checklist_phase(self, workflow: Workflow) -> bool:
        """True when a `kind == "checklist"` phase still has an enabled step.
        The 'finish the global-checks before endpoint testing' gate only applies
        while that phase is live; if the operator disabled its step the phase is
        skipped and the gate must not block the way forward."""
        for p in workflow.phases:
            if p.get("kind") == "checklist" and self.phase_has_enabled_steps(workflow, p["id"]):
                return True
        return False

    def _unfinished_steps_in_phase(self, workflow: Workflow, phase_id: str) -> list[dict]:
        phase = workflow.phase(phase_id)
        if phase is None:
            return []
        unfinished = []
        for step in phase.get("steps", []):
            step_id = self._workflow_step_id(step)
            if not step_id:
                continue
            state = self.get_step_state(phase_id, step_id)
            if state.get("disabled"):
                continue
            if state.get("finished"):
                continue
            unfinished.append({
                "step_id": step_id,
                "title": self._workflow_step_title(step, step_id),
                "status": state.get("status", "pending"),
            })
        return unfinished

    def advance_phase(self, workflow: Workflow, skip_optional: bool = False, phase_id: str | None = None) -> str | None:
        # A phase_id that names a different phase, or the current phase while it
        # is re-opened, means "close that phase's modify mode" — never advance.
        if phase_id is not None and (phase_id != self.current_phase or self.is_phase_in_modify_mode(phase_id)):
            return self._close_modify_mode_via_advance(workflow, phase_id)
        # Single gate: the same resolver that drives the UI/agent hint decides
        # whether the phase is clear to advance, so the hint and the gate can
        # never disagree.
        ns = self.resolve_next_step(workflow)
        if ns.kind not in ("advance_phase", "done"):
            raise PhaseIncompleteError(
                f"cannot advance: {ns.label}",
                unfinished=self.unfinished_steps_in_current_phase(workflow),
            )
        nxt = self._next_active_phase(workflow, self.current_phase, skip_optional=skip_optional)
        if nxt is None:
            return None
        self.data["current_phase"] = nxt["id"]
        entry = self.workflow_phases.setdefault(nxt["id"], {})
        entry.setdefault("advanced_at", _now())
        return nxt["id"]

    def _phase_kind_blockers(self, workflow: Workflow, phase_id: str) -> list[str]:
        """Cross-cutting completion gates for a phase beyond its own steps: a
        checklist-kind phase isn't done while global-scope checks are still
        unfinished; an endpoint-kind phase isn't done while endpoints are still
        todo or focused. These are the same conditions resolve_next_step uses to
        gate a normal advance — shared so closing a re-opened phase can't slip
        past them. Empty list means the phase kind is satisfied."""
        kind = (workflow.phase(phase_id) or {}).get("kind", "step")
        if kind == "checklist":
            pending = self._unfinished_global_checks()
            if pending:
                return [
                    f"{len(pending)} global-scope checklist check(s) still unfinished; "
                    "finish them on the checklist first"
                ]
        elif kind == "endpoint":
            counts = self.endpoint_status_counts()
            blocking = counts["todo"] + counts["focused"]
            if blocking:
                return [
                    f"{counts['todo']} endpoint(s) still todo and {counts['focused']} still focused; "
                    "mark each one tested or out-of-scope first"
                ]
        return []

    def is_phase_complete(self, workflow: Workflow, phase_id: str) -> bool:
        """True when a phase has no unfinished (enabled) steps and its kind-gate
        (global-checks for a checklist phase, endpoint states for an endpoint phase)
        is satisfied — i.e. it is genuinely done on its own, independent of whether
        any other phase is reopened in modify mode."""
        if self._unfinished_steps_in_phase(workflow, phase_id):
            return False
        return not self._phase_kind_blockers(workflow, phase_id)

    def is_workflow_terminal(self, workflow: Workflow) -> bool:
        """True once the workflow has reached its end: the current phase is
        complete and there is no further active phase. Unlike resolve_next_step —
        which points back at a reopened (modify-mode) phase and so stops reporting
        'done' — this stays True while a done phase is reopened, so the UI can keep
        the final phase marked done instead of flipping it back to 'active'."""
        phase = workflow.phase(self.current_phase)
        if phase is None:
            return True
        if not self.is_phase_complete(workflow, self.current_phase):
            return False
        return self._next_active_phase(workflow, self.current_phase) is None

    def workflow_finished_at(self, workflow: Workflow) -> int | None:
        """When the workflow most recently became finished, as a UTC epoch int,
        or None if it is not finished. Derived rather than stored: the finished
        state is itself derived (resolve_next_step == done) and can toggle as
        phases are reopened and reclosed, so the finish time is the timestamp of
        the last completing action — the most recent ts across the workflow's
        step and phase records. This naturally moves when a done phase is reopened
        and reclosed, and disappears when the workflow is no longer finished."""
        if self.resolve_next_step(workflow).kind != "done":
            return None
        stamps = [
            s["ts"]
            for s in list(self.workflow_steps.values()) + list(self.workflow_phases.values())
            if isinstance(s, dict) and s.get("ts")
        ]
        return max(stamps) if stamps else None

    def _workflow_step_counts(self, workflow: Workflow) -> tuple[int, int]:
        """(finished, total) across every live (non-disabled) workflow step in
        all phases. Disabled steps are excluded — they can never be finished."""
        total = 0
        done = 0
        for phase in workflow.phases:
            for step in phase.get("steps", []):
                step_id = self._workflow_step_id(step)
                if not step_id:
                    continue
                state = self.get_step_state(phase["id"], step_id, workflow=workflow)
                if state.get("disabled"):
                    continue
                total += 1
                if state.get("finished"):
                    done += 1
        return done, total

    def workflow_progress(self, workflow: Workflow) -> int:
        """Percentage (0-100) of workflow steps finished across all phases.
        Disabled steps are excluded — they can never be finished — so a workflow
        with every live step done reads 100%. 0 when there are no live steps."""
        done, total = self._workflow_step_counts(workflow)
        return round(done / total * 100) if total else 0

    def overall_progress(self, workflow: Workflow) -> int:
        """Percentage (0-100) across every unit of work the project tracks:
        workflow steps, global-scope checklist checks, and per-endpoint checks.
        This is the figure shown in the browser-tab title, so it reflects the
        whole engagement rather than the workflow alone.

        The per-endpoint contribution is an estimate. The endpoint count isn't
        known up front, so an empty (or barely-populated) endpoints list would
        otherwise let the project read near-100% before any endpoint testing has
        begun. Until the real count overtakes it, the denominator reserves
        ESTIMATED_ENDPOINTS endpoints' worth of per-endpoint checks; once more
        endpoints exist than the estimate the real count is used, so completed
        work can never overshoot the total."""
        wf_done, wf_total = self._workflow_step_counts(workflow)
        global_done, global_total = self.global_check_counts()
        per_endpoint_checks = len(self._per_endpoint_check_ids())
        estimated_endpoints = max(ESTIMATED_ENDPOINTS, len(self.endpoints))
        ep_total = per_endpoint_checks * estimated_endpoints
        ep_done = self.completed_endpoint_check_count()
        done = wf_done + global_done + ep_done
        total = wf_total + global_total + ep_total
        return round(done / total * 100) if total else 0

    def _close_modify_mode_via_advance(self, workflow: Workflow, phase_id: str) -> str | None:
        """Close modify mode for a past phase after the operator/agent has
        re-finished every (non-disabled) step. Does NOT touch current_phase —
        the workflow continues from wherever it was."""
        self._phase_index(workflow, phase_id)
        if not self.is_phase_in_modify_mode(phase_id):
            raise WorkflowOrderError(
                f"phase {phase_id} is not in modify mode; nothing to close"
            )
        unfinished = self._unfinished_steps_in_phase(workflow, phase_id)
        if unfinished:
            titles = ", ".join(u["title"] for u in unfinished)
            raise PhaseIncompleteError(
                f"cannot close modify mode on {phase_id}: {len(unfinished)} step(s) "
                f"still pending or unfinished ({titles})",
                unfinished=unfinished,
            )
        blockers = self._phase_kind_blockers(workflow, phase_id)
        if blockers:
            raise PhaseIncompleteError(
                f"cannot close modify mode on {phase_id}: {'; '.join(blockers)}"
            )
        entry = self.workflow_phases.setdefault(phase_id, {})
        entry["modify_mode"] = False
        entry["ts"] = _now()
        return self.current_phase

    @staticmethod
    def _step_key(phase_id: str, step_id: str) -> str:
        return f"{phase_id}/{step_id}"

    def is_step_disabled(self, phase_id: str, step_id: str) -> bool:
        key = self._step_key(phase_id, step_id)
        s = self.workflow_steps.get(key) or {}
        # back-compat: an earlier draft of this feature used "muted"
        return bool(s.get("disabled") or s.get("muted"))

    def set_step_disabled(self, phase_id: str, step_id: str, disabled: bool) -> dict:
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.setdefault(
            key, {"status": "pending", "observations": "", "ts": None}
        )
        state["disabled"] = bool(disabled)
        state.pop("muted", None)
        state["ts"] = _now()
        return dict(state)

    def step_finish_blockers(self, phase_id: str, step_id: str) -> list[str]:
        """Step-specific blockers beyond the generic status+observations gate.
        Returns human-readable strings describing why this step can't be finished
        right now; empty list means the step is fine to finish.

        Currently only the two endpoint-testing steps that wrap up the endpoint
        sweep — they require every endpoint to land on tested or out-of-scope
        so the operator can't close the phase with todo / focused endpoints
        lying around."""
        if phase_id == "endpoint_testing" and step_id in ("test_endpoints", "review_notes_reopen_endpoints"):
            counts = self.endpoint_status_counts()
            blocking = counts["todo"] + counts["focused"]
            if blocking:
                return [
                    f"{counts['todo']} endpoint(s) still todo and {counts['focused']} still focused; "
                    "mark each one tested or out-of-scope before finishing this step"
                ]
        return []

    def mark_step_finished(self, phase_id: str, step_id: str, agent: dict | None = None) -> dict:
        """Flip the `finished` flag on a workflow step. POST /workflow/.../finish
        is the only call (besides the Web UI Finish button) that does this — PUT
        only changes status/observations and does NOT advance past the step."""
        blockers = self.step_finish_blockers(phase_id, step_id)
        if blockers:
            raise ValueError("; ".join(blockers))
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.setdefault(
            key, {"status": "pending", "observations": "", "ts": None}
        )
        state["finished"] = True
        state.pop("focused_by", None)
        if agent is not None:
            state["done_by"] = agent
        state["ts"] = _now()
        return dict(state)

    def get_step_state(self, phase_id: str, step_id: str, workflow: Workflow | None = None) -> dict:
        """Project state for one workflow step. If `workflow` is given, the source
        JSON's `description`/`examples` are used as defaults when the project state
        leaves them empty — letting authors set defaults in the source files and
        per-project overrides in `workflow_phases` / `workflow_steps`."""
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.get(key)
        out = dict(state) if state else {"status": "pending", "observations": "", "ts": None}
        out.setdefault("description", "")
        out.setdefault("examples", "")
        out["disabled"] = bool(out.get("disabled") or out.get("muted"))
        out.pop("muted", None)
        # back-compat: the step status was once "in_progress"; it is now "focused"
        if out.get("status") == "in_progress":
            out["status"] = "focused"
        if workflow is not None and (not out["description"] or not out["examples"]):
            src = self._source_step(workflow, phase_id, step_id) or {}
            if not out["description"]:
                out["description"] = src.get("description", "")
            if not out["examples"]:
                out["examples"] = src.get("examples", "")
        return out

    @staticmethod
    def _source_step(workflow: Workflow, phase_id: str, step_id: str) -> dict | None:
        phase = workflow.phase(phase_id)
        if not phase:
            return None
        for s in phase.get("steps", []):
            if (s.get("id") or s.get("check")) == step_id:
                return s
        return None

    def get_phase_view(self, workflow: Workflow, phase_id: str) -> dict:
        """Phase metadata with project description override applied on top of
        the source JSON. Returns {id, name, description, optional, free,
        advanced_at, modify_mode, is_done, disabled}. `disabled` is True when
        every step in the phase has been disabled (or it has no steps) — such a
        phase is skipped on advance. `advanced_at` is the unix-time
        stamp of the first time this phase was reached (set on advance_phase,
        and on the initial phase at project creation); 0 if the phase has not
        been entered yet. `is_done` is True for phases strictly before the
        current phase. `modify_mode` is True when the operator has reopened
        a done phase for editing."""
        phase = workflow.phase(phase_id) or {}
        override = self.workflow_phases.get(phase_id, {})
        description = override.get("description") or phase.get("description", "")
        ids = [p["id"] for p in workflow.phases]
        is_done = False
        if phase_id in ids and self.current_phase in ids:
            is_done = ids.index(phase_id) < ids.index(self.current_phase)
        return {
            "id": phase_id,
            "name": phase.get("name", phase_id),
            "description": description,
            "optional": bool(phase.get("optional", False)),
            "free": bool(phase.get("free", False)),
            "advanced_at": int(override.get("advanced_at") or 0),
            "modify_mode": bool(override.get("modify_mode")),
            "is_done": is_done,
            "disabled": not self.phase_has_enabled_steps(workflow, phase_id),
        }

    def update_phase_context(self, phase_id: str, description: str | None = None) -> dict:
        if description is None:
            return dict(self.workflow_phases.get(phase_id, {}))
        entry = self.workflow_phases.setdefault(phase_id, {})
        entry["description"] = description
        entry["ts"] = _now()
        return dict(entry)

    def add_workflow_step_evidence(
        self,
        phase_id: str,
        step_id: str,
        name: str,
        data_b64: str,
        mime_type: str = "application/octet-stream",
        source_type: str = "other",
        description: str = "",
    ) -> dict:
        if self.is_step_disabled(phase_id, step_id):
            raise StepDisabledError(
                f"step {step_id} is disabled in this project by the human operator — "
                "do not work on it. Move on to the next step in the workflow."
            )
        # Focus gate: raw captures are a result write — the step must be focused.
        if self.get_step_state(phase_id, step_id).get("status") != "focused":
            raise self._step_focus_required(phase_id, step_id, "attach raw captures to it")
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.setdefault(
            key, {"status": "pending", "observations": "", "ts": None}
        )
        rel_dir = Path(WORKFLOW_DIR) / key.replace("/", "__")
        entry = self._write_evidence(rel_dir, name, data_b64, mime_type, source_type, description)
        state.setdefault("evidence", []).append(entry)
        state["ts"] = _now()
        return entry

    def get_workflow_step_evidence(
        self,
        phase_id: str,
        step_id: str,
        evidence_id: str,
    ) -> tuple[dict, Path]:
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.get(key) or {}
        for e in state.get("evidence", []):
            if e["id"] == evidence_id:
                return e, (self.folder / e["path"]).resolve()
        raise ValueError(f"unknown evidence {evidence_id} for step {phase_id}/{step_id}")

    def update_step(
        self,
        phase_id: str,
        step_id: str,
        status: str | None = None,
        observations: str | None = None,
        agent: dict | None = None,
    ) -> dict:
        if observations is not None:
            _validate_observations(observations, field=f"step {phase_id}/{step_id} observations")
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.setdefault(
            key, {"status": "pending", "observations": "", "ts": None}
        )
        if status is not None:
            if status not in WORKFLOW_STEP_STATUSES:
                raise ValueError(f"bad step status {status}")
            state["status"] = status
            if status == "focused" and agent is not None:
                # One focused step per agent: claim a new one only after finishing
                # or releasing the current one.
                held = next(
                    (okey for okey, ostate in self.workflow_steps.items()
                     if okey != key and isinstance(ostate, dict)
                     and ostate.get("status") in ("focused", "in_progress")
                     and (ostate.get("focused_by") or {}).get("id") == agent.get("id")),
                    None,
                )
                if held is not None:
                    raise WorkflowOrderError(
                        f"you already have step {held} focused; finish it "
                        f"(POST .../finish) or release it (PUT the step {{\"status\": \"pending\"}}) "
                        f"before focusing another — one focused step per agent."
                    )
                state["focused_by"] = agent
            # If the agent backs out of a terminal status, the step is no
            # longer finished — they must call POST /finish again to advance —
            # and the stale attribution tags are cleared.
            if status not in ("done", "skipped") and state.get("finished"):
                state["finished"] = False
            if status == "pending":
                state.pop("focused_by", None)
                state.pop("done_by", None)
        if observations is not None:
            state["observations"] = observations
        state["ts"] = _now()
        return dict(state)

    def _step_access(self, workflow: Workflow, phase_id: str, step_id: str) -> dict:
        phase_ids = [p["id"] for p in workflow.phases]
        if phase_id not in phase_ids:
            raise ValueError(f"unknown workflow phase {phase_id}")
        current_idx = phase_ids.index(self.current_phase)
        phase_idx = phase_ids.index(phase_id)
        if phase_idx < current_idx and not self.is_phase_in_modify_mode(phase_id):
            return {"allowed": False, "reason": "past_phase"}
        if phase_idx > current_idx:
            return {"allowed": False, "reason": "future_phase"}

        phase = workflow.phase(phase_id)
        steps = phase.get("steps", []) if phase else []
        step_ids = [self._workflow_step_id(s) for s in steps]
        if step_id not in step_ids:
            raise ValueError(f"step {step_id} not found in phase {phase_id}")

        idx = step_ids.index(step_id)
        blockers = []
        for previous in steps[:idx]:
            prev_id = self._workflow_step_id(previous)
            if not prev_id:
                continue
            prev_state = self.get_step_state(phase_id, prev_id)
            if prev_state.get("disabled"):
                continue
            prev_status = prev_state.get("status", "pending")
            prev_observations = (prev_state.get("observations") or "").strip()
            if prev_status not in ("done", "skipped") or not prev_observations:
                blockers.append({
                    "step_id": prev_id,
                    "title": self._workflow_step_title(previous, prev_id),
                    "status": prev_status,
                    "missing_observations": not prev_observations,
                })
        if blockers:
            reason = "previous_steps_need_observations" if all(b["status"] in ("done", "skipped") for b in blockers) else "previous_steps_pending"
            return {"allowed": False, "reason": reason, "blockers": blockers}

        started_after = []
        for following in steps[idx + 1:]:
            next_id = self._workflow_step_id(following)
            if not next_id:
                continue
            next_state = self.get_step_state(phase_id, next_id)
            if next_state.get("status", "pending") != "pending":
                started_after.append({
                    "step_id": next_id,
                    "title": self._workflow_step_title(following, next_id),
                    "status": next_state.get("status", "pending"),
                })
        return {"allowed": True, "reason": "", "started_after": started_after}

    def workflow_step_access(self, workflow: Workflow, phase_id: str, step_id: str) -> dict:
        return self._step_access(workflow, phase_id, step_id)

    @staticmethod
    def _step_focus_required(phase_id: str, step_id: str, action: str) -> FocusRequiredError:
        return FocusRequiredError(
            f"focus step {step_id} before you {action}: this project requires a step to be "
            "claimed (status='focused') before any result is recorded on it, so concurrent "
            "agents and the operator can see it is in progress.",
            kind="step_not_focused",
            fix={
                "method": "PUT",
                "path": f"/api/v1/workflow/phases/{phase_id}/steps/{step_id}",
                "example_body": {"status": "focused"},
            },
        )

    def update_workflow_step(
        self,
        workflow: Workflow,
        phase_id: str,
        step_id: str,
        status: str | None = None,
        observations: str | None = None,
        agent: dict | None = None,
    ) -> dict:
        if self.is_step_disabled(phase_id, step_id):
            raise StepDisabledError(
                f"step {step_id} is disabled in this project by the human operator — "
                "do not work on it. Move on to the next step in the workflow."
            )
        # Mandatory focus gate: a step must be claimed (status='focused') before
        # any result write. Setting 'focused' (the claim) or 'pending' (release)
        # is always allowed.
        current = self.get_step_state(phase_id, step_id, workflow=workflow)
        currently_focused = current.get("status") == "focused"
        will_focus = status == "focused"
        if status in ("done", "skipped") and not currently_focused:
            raise self._step_focus_required(phase_id, step_id, "mark it done/skipped")
        if observations is not None and not (currently_focused or will_focus):
            raise self._step_focus_required(phase_id, step_id, "record observations")
        access = self._step_access(workflow, phase_id, step_id)
        if status is not None:
            if (
                phase_id == "endpoint_testing"
                and self.all_endpoints_unassigned()
                and (
                    (step_id == "sort_and_group_endpoints" and status in ("done", "skipped"))
                    or (step_id == "review_each_endpoint_group" and status != "pending")
                )
            ):
                raise WorkflowOrderError(
                    "cannot move to endpoint group review: all endpoints are still unassigned; "
                    "assign at least one endpoint to a feature group first"
                )
            if not access["allowed"]:
                reason = access["reason"]
                if reason == "past_phase":
                    raise WorkflowOrderError(
                        f"cannot update step {step_id}: phase {phase_id} is before the current phase {self.current_phase}"
                    )
                if reason == "future_phase":
                    raise WorkflowOrderError(
                        f"cannot update step {step_id}: phase {phase_id} is after the current phase {self.current_phase}"
                    )
                blockers = access.get("blockers", [])
                titles = ", ".join(b["title"] for b in blockers)
                if reason == "previous_steps_need_observations":
                    raise WorkflowOrderError(
                        f"cannot update step {step_id}: previous step(s) need observations before moving on ({titles})"
                    )
                raise WorkflowOrderError(
                    f"cannot update step {step_id}: previous step(s) must be done or skipped first ({titles})"
                )
            if (
                status == "pending"
                and access.get("started_after")
                and not self.is_phase_in_modify_mode(phase_id)
            ):
                titles = ", ".join(s["title"] for s in access["started_after"])
                raise WorkflowOrderError(
                    f"cannot reset step {step_id} to pending: later step(s) already started ({titles})"
                )
        return self.update_step(phase_id, step_id, status=status, observations=observations, agent=agent)

    def update_step_context(
        self,
        phase_id: str,
        step_id: str,
        description: str | None = None,
        examples: str | None = None,
    ) -> dict:
        key = self._step_key(phase_id, step_id)
        state = self.workflow_steps.setdefault(
            key, {"status": "pending", "observations": "", "ts": None}
        )
        if description is not None:
            state["description"] = description
        if examples is not None:
            state["examples"] = examples
        state["ts"] = _now()
        return dict(state)

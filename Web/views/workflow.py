from flask import render_template, request, redirect, url_for, flash, send_file

from Domain import PhaseIncompleteError, WorkflowOrderError, StepDisabledError, ObservationsTooLongError, FocusRequiredError

from ..routes import web_bp, service, require_loaded, _common_ctx, OPERATOR_AGENT


@web_bp.get("/project/workflow")
@require_loaded
def workflow():
    s = service()
    project = s.current
    wf = s.workflow_for(project)
    step_states = {}
    step_access = {}
    phase_views = {}
    for phase in wf.phases:
        phase_views[phase["id"]] = project.get_phase_view(wf, phase["id"])
        for step in phase.get("steps", []):
            check_id = step.get("check") or step.get("id")
            step_id = step.get("id") or check_id
            step_states[f"{phase['id']}/{step_id}"] = project.get_step_state(phase["id"], step_id, workflow=wf)
            step_access[f"{phase['id']}/{step_id}"] = project.workflow_step_access(wf, phase["id"], step_id)
    focused_global = project.focused_global_checks()
    # The "recently worked on" panel is meaningful only while the agent is in
    # the Work-on-Checklist phase. Once we've advanced past it, clear the list.
    if project.current_phase == "general_checks":
        recent_global = project.recently_worked_global_checks(limit=3)
    else:
        recent_global = []
    recent_tested_endpoints = project.recently_tested_endpoints(limit=3)
    ctx = _common_ctx()
    return render_template(
        "workflow.html",
        section="workflow",
        step_states=step_states,
        page_progress=ctx["workflow_progress"],
        step_access=step_access,
        phase_views=phase_views,
        focused_global_checks=focused_global,
        recent_global_checks=recent_global,
        recent_tested_endpoints=recent_tested_endpoints,
        next_action=project.resolve_next_step(wf),
        **ctx,
    )


@web_bp.post("/project/workflow/phase/context")
@require_loaded
def update_workflow_phase_context():
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    if not phase_id:
        flash("phase_id required")
        return redirect(url_for("web.workflow"))
    description = request.form.get("description")
    project.update_phase_context(phase_id, description=description)
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/step/context")
@require_loaded
def update_workflow_step_context():
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    step_id = request.form.get("step_id", "").strip()
    if not phase_id or not step_id:
        flash("phase_id and step_id required")
        return redirect(url_for("web.workflow"))
    description = request.form.get("description")
    examples = request.form.get("examples")
    try:
        project.update_step_context(phase_id, step_id, description=description, examples=examples)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.workflow"))
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/step/finish")
@require_loaded
def finish_workflow_step():
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    step_id = request.form.get("step_id", "").strip()
    if not phase_id or not step_id:
        flash("phase_id and step_id required")
        return redirect(url_for("web.workflow"))
    state = project.get_step_state(phase_id, step_id)
    missing = []
    if state.get("status", "pending") not in ("done", "skipped"):
        missing.append("status (set to done or skipped)")
    if not (state.get("observations") or "").strip():
        missing.append("observations")
    missing.extend(project.step_finish_blockers(phase_id, step_id))
    if missing:
        flash(f"Cannot finish step: {'; '.join(missing)}")
        return redirect(url_for("web.workflow"))
    try:
        project.mark_step_finished(phase_id, step_id, agent=OPERATOR_AGENT)
    except ValueError as e:
        flash(f"Cannot finish step: {e}")
        return redirect(url_for("web.workflow"))
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/step")
@require_loaded
def update_workflow_step():
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    step_id = request.form.get("step_id", "").strip()
    status = request.form.get("status") or None
    observations = request.form.get("observations")
    if not phase_id or not step_id:
        flash("phase_id and step_id required")
        return redirect(url_for("web.workflow"))
    try:
        project.update_workflow_step(s.workflow_for(project), phase_id, step_id, status=status, observations=observations, agent=OPERATOR_AGENT)
    except (ValueError, WorkflowOrderError, StepDisabledError, FocusRequiredError, ObservationsTooLongError) as e:
        flash(str(e))
        return redirect(url_for("web.workflow"))
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/phase/modify-mode")
@require_loaded
def toggle_workflow_phase_modify_mode():
    """Operator-only: re-open a done phase for editing, or close modify mode
    on one. Closing only succeeds when every non-disabled step in the phase
    is finished — same gating as advance."""
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    if not phase_id:
        flash("phase_id required")
        return redirect(url_for("web.workflow"))
    wf = s.workflow_for(project)
    raw = request.form.get("enabled", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        enabled = True
    elif raw in ("0", "false", "off", "no"):
        enabled = False
    else:
        enabled = not project.is_phase_in_modify_mode(phase_id)
    if not enabled and project.is_phase_in_modify_mode(phase_id):
        try:
            project.advance_phase(wf, phase_id=phase_id)
        except (PhaseIncompleteError, WorkflowOrderError, ValueError) as e:
            flash(str(e))
            return redirect(url_for("web.workflow"))
    else:
        try:
            project.set_phase_modify_mode(wf, phase_id, enabled)
        except (WorkflowOrderError, ValueError) as e:
            flash(str(e))
            return redirect(url_for("web.workflow"))
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/step/disabled")
@require_loaded
def toggle_workflow_step_disabled():
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    step_id = request.form.get("step_id", "").strip()
    if not phase_id or not step_id:
        flash("phase_id and step_id required")
        return redirect(url_for("web.workflow"))
    raw = request.form.get("disabled", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        disabled = True
    elif raw in ("0", "false", "off", "no"):
        disabled = False
    else:
        disabled = not project.is_step_disabled(phase_id, step_id)
    project.set_step_disabled(phase_id, step_id, disabled)
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/agent-advance")
@require_loaded
def toggle_agent_advance():
    """Operator-only: toggle whether agents may advance phases too, or only the
    human operator. When restricted to the operator, the agent's
    POST /workflow/advance is rejected — advancing happens only from the UI."""
    s = service()
    project = s.current
    raw = request.form.get("allowed", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        allowed = True
    elif raw in ("0", "false", "off", "no"):
        allowed = False
    else:
        allowed = not project.agent_advance_allowed
    project.set_agent_advance_allowed(allowed)
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/try-harder")
@require_loaded
def toggle_try_harder():
    """Operator-only: the 'Try harder' switch, scoped by page. `scope=checks`
    (checklist page) adds/removes one run on every global check; `scope=endpoints`
    (endpoints page) adds/removes one run on every endpoint. Turning it on is +1,
    off is -1 (floored at 1). `return_to` brings the operator back."""
    s = service()
    project = s.current
    scope = request.form.get("scope", "").strip()
    return_to = request.form.get("return_to") or url_for("web.workflow")
    if scope == "checks":
        current = project.try_harder_checks
    elif scope == "endpoints":
        current = project.try_harder_endpoints
    else:
        flash(f"unknown try-harder scope {scope!r}")
        return redirect(return_to)
    raw = request.form.get("enabled", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        enabled = True
    elif raw in ("0", "false", "off", "no"):
        enabled = False
    else:
        enabled = not current
    if scope == "checks":
        project.set_try_harder_checks(enabled)
    else:
        project.set_try_harder_endpoints(enabled)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/advance")
@require_loaded
def advance():
    s = service()
    project = s.current
    skip = request.form.get("skip_optional") == "1"
    wf = s.workflow_for(project)
    try:
        project.advance_phase(wf, skip_optional=skip)
    except PhaseIncompleteError as e:
        flash(str(e))
        return redirect(request.form.get("return_to") or url_for("web.workflow"))
    s.save(project)
    # Phase runs: instead of advancing, the phase reset for another run — surface
    # the nudge so the operator sees why the phase didn't move on.
    if project.run_notice:
        flash(project.run_notice)
    return redirect(request.form.get("return_to") or url_for("web.workflow"))


@web_bp.post("/project/workflow/phase/runs")
@require_loaded
def set_workflow_phase_runs():
    """Operator-only: set how many times a phase runs for this run. Accepts a
    number, or 'indefinite' / '∞' for an unbounded loop (stop it by lowering the
    number later)."""
    s = service()
    project = s.current
    phase_id = request.form.get("phase_id", "").strip()
    if not phase_id:
        flash("phase_id required")
        return redirect(url_for("web.workflow"))
    try:
        project.set_phase_runs(s.workflow_for(project), phase_id, request.form.get("runs"))
    except (ValueError, WorkflowOrderError) as e:
        flash(str(e))
        return redirect(url_for("web.workflow"))
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.post("/project/workflow/<phase_id>/<step_id>/evidence")
@require_loaded
def add_step_evidence(phase_id, step_id):
    s = service()
    project = s.current
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        flash("no file selected")
        return redirect(url_for("web.workflow"))
    try:
        project.add_workflow_step_evidence(
            phase_id,
            step_id,
            uploaded.filename,
            uploaded.read(),
            mime_type=uploaded.mimetype or "application/octet-stream",
            source_type=request.form.get("source_type") or "other",
            description=(request.form.get("description") or "").strip(),
        )
    except (ValueError, StepDisabledError, FocusRequiredError) as e:
        flash(str(e))
        return redirect(url_for("web.workflow"))
    s.save(project)
    return redirect(url_for("web.workflow"))


@web_bp.get("/project/workflow/<phase_id>/<step_id>/evidence/<evidence_id>")
@require_loaded
def download_step_evidence(phase_id, step_id, evidence_id):
    project = service().current
    try:
        entry, abs_path = project.get_workflow_step_evidence(phase_id, step_id, evidence_id)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.workflow"))
    if not abs_path.is_file():
        flash("evidence file missing on disk")
        return redirect(url_for("web.workflow"))
    return send_file(
        abs_path,
        mimetype=entry.get("mime_type", "application/octet-stream"),
        as_attachment=True,
        download_name=entry["name"],
    )

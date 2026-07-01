from flask import render_template, request, redirect, url_for, flash, send_file

from Domain import ObservationsTooLongError, FocusRequiredError, RunAgainError

from ..routes import web_bp, service, require_loaded, _common_ctx, OPERATOR_AGENT


def _checklist_groups(project):
    groups = {}
    ordered = []
    for item in project.checklist:
        category = item.get("category", "uncategorized")
        group = groups.get(category)
        if group is None:
            group = {
                "id": category,
                "name": item.get("category_name", "Uncategorized"),
                "checks": [],
            }
            groups[category] = group
            ordered.append(group)
        group["checks"].append(item)
    return ordered


@web_bp.get("/project/checklist")
@require_loaded
def checklist():
    ctx = _common_ctx()
    global_done, global_total = ctx["project"].global_check_counts()
    page_progress = round(global_done / global_total * 100) if global_total else 0
    return render_template(
        "checklist.html",
        section="checklist",
        checklist_groups=_checklist_groups(ctx["project"]),
        page_progress=page_progress,
        **ctx,
    )


@web_bp.post("/project/checks/<check_id>")
@require_loaded
def update_check(check_id):
    s = service()
    project = s.current
    status = request.form.get("status") or None
    observations = request.form.get("observations")
    endpoint_id = request.form.get("endpoint_id") or None
    return_to = request.form.get("return_to", "checklist")
    try:
        project.update_check(check_id, status=status, observations=observations, endpoint_id=endpoint_id, agent=OPERATOR_AGENT)
    except (ValueError, FocusRequiredError, ObservationsTooLongError) as e:
        flash(str(e))
        return redirect(url_for(f"web.{return_to}"))
    s.save(project)
    return redirect(url_for(f"web.{return_to}"))


@web_bp.post("/project/checks/<check_id>/context")
@require_loaded
def update_check_context(check_id):
    s = service()
    project = s.current
    description = request.form.get("description")
    examples = request.form.get("examples")
    try:
        project.update_check_context(check_id, description=description, examples=examples)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.checklist"))
    s.save(project)
    return redirect(url_for("web.checklist"))


@web_bp.post("/project/checks/<check_id>/finish")
@require_loaded
def finish_check(check_id):
    s = service()
    project = s.current
    return_to = request.form.get("return_to", "checklist")
    try:
        project.finish_global_check(check_id, agent=OPERATOR_AGENT)
    except RunAgainError as e:
        # Runs gate: this finish reset the check for another run. Persist the reset
        # and surface the nudge.
        s.save(project)
        flash(str(e))
        return redirect(url_for(f"web.{return_to}"))
    except ValueError as e:
        flash(str(e))
        return redirect(url_for(f"web.{return_to}"))
    s.save(project)
    return redirect(url_for(f"web.{return_to}"))


@web_bp.post("/project/checks/<check_id>/runs")
@require_loaded
def set_check_runs(check_id):
    """Operator-only: set how many times this check runs before it settles.
    Accepts a number, or 'indefinite' / '∞' for an unbounded loop."""
    s = service()
    project = s.current
    return_to = request.form.get("return_to", "checklist")
    try:
        project.set_check_runs(check_id, request.form.get("runs"))
    except ValueError as e:
        flash(str(e))
        return redirect(url_for(f"web.{return_to}"))
    s.save(project)
    return redirect(url_for(f"web.{return_to}"))


@web_bp.post("/project/checks/<check_id>/evidence")
@require_loaded
def add_check_evidence(check_id):
    s = service()
    project = s.current
    endpoint_id = request.form.get("endpoint_id") or None
    return_to = request.form.get("return_to", "checklist")
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        flash("no file selected")
        return redirect(url_for(f"web.{return_to}"))
    try:
        project.add_check_evidence(
            check_id,
            uploaded.filename,
            uploaded.read(),
            mime_type=uploaded.mimetype or "application/octet-stream",
            endpoint_id=endpoint_id,
            source_type=request.form.get("source_type") or "other",
            description=(request.form.get("description") or "").strip(),
        )
    except (ValueError, FocusRequiredError) as e:
        flash(str(e))
        return redirect(url_for(f"web.{return_to}"))
    s.save(project)
    return redirect(url_for(f"web.{return_to}"))


@web_bp.get("/project/checks/<check_id>/evidence/<evidence_id>")
@require_loaded
def download_check_evidence(check_id, evidence_id):
    project = service().current
    endpoint_id = request.args.get("endpoint_id") or None
    try:
        entry, abs_path = project.get_check_evidence(check_id, evidence_id, endpoint_id=endpoint_id)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.checklist"))
    if not abs_path.is_file():
        flash("evidence file missing on disk")
        return redirect(url_for("web.checklist"))
    return send_file(
        abs_path,
        mimetype=entry.get("mime_type", "application/octet-stream"),
        as_attachment=True,
        download_name=entry["name"],
    )

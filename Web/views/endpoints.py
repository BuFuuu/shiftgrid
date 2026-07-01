import re

from flask import render_template, request, redirect, url_for, flash

from Domain import ObservationsTooLongError, RunAgainError

from ..routes import web_bp, service, require_loaded, _common_ctx, OPERATOR_AGENT


@web_bp.get("/project/endpoints")
@require_loaded
def endpoints():
    ctx = _common_ctx()
    counts = ctx["project"].endpoint_status_counts()
    in_scope = counts["total"] - counts["out-of-scope"]
    page_progress = round(counts["tested"] / in_scope * 100) if in_scope else 0
    return render_template("endpoints.html", section="endpoints", page_progress=page_progress, **ctx)


@web_bp.post("/project/endpoints")
@require_loaded
def add_endpoint():
    s = service()
    project = s.current
    name = request.form.get("name", "").strip()
    atype = request.form.get("type", "host").strip() or "host"
    observations = request.form.get("observations", "").strip()
    if name:
        try:
            project.add_endpoint(name, atype, observations=observations)
        except (ValueError, ObservationsTooLongError) as e:
            flash(str(e))
            return redirect(url_for("web.endpoints"))
        s.save(project)
    return redirect(url_for("web.endpoints"))


# Bulk-import classifiers: a line is imported if it looks like a URL, an IP, or
# a dotted hostname. Anything else is dropped.
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*$")


@web_bp.post("/project/endpoints/bulk")
@require_loaded
def bulk_import_endpoints():
    s = service()
    project = s.current
    f = request.files.get("file")
    if not f:
        return redirect(url_for("web.endpoints"))
    atype = "host" if s.workflow_for(project).id == "network_infrastructure" else "web"
    imported = 0
    skipped = []
    for raw in f.read().decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _URL_RE.match(line) or _IP_RE.match(line) or _HOST_RE.match(line):
            project.add_endpoint(line, atype)
            imported += 1
        else:
            skipped.append(line)
    s.save(project)
    msg = f"Imported {imported} endpoint(s)."
    if skipped:
        msg += f" Skipped {len(skipped)} line(s): " + ", ".join(skipped)
    flash(msg)
    return redirect(url_for("web.endpoints"))


@web_bp.post("/project/endpoints/<endpoint_id>/edit")
@require_loaded
def edit_endpoint(endpoint_id):
    s = service()
    project = s.current
    fields = {}
    for k in ("type", "observations"):
        if k in request.form:
            fields[k] = request.form.get(k, "").strip()
    return_to = request.form.get("return_to") or url_for("web.endpoints")
    try:
        project.update_endpoint(endpoint_id, agent=OPERATOR_AGENT, **fields)
    except (ValueError, ObservationsTooLongError) as e:
        flash(str(e))
        return redirect(return_to)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/endpoints/<endpoint_id>/status")
@require_loaded
def set_endpoint_status(endpoint_id):
    s = service()
    project = s.current
    status = request.form.get("status", "").strip()
    return_to = request.form.get("return_to") or url_for("web.endpoints")
    try:
        project.set_endpoint_status(endpoint_id, status, agent=OPERATOR_AGENT)
    except RunAgainError as e:
        # Runs gate: marking tested reset the endpoint for another run. Persist the
        # reset and surface the nudge.
        s.save(project)
        flash(str(e))
        return redirect(return_to)
    except ValueError as e:
        flash(str(e))
        return redirect(return_to)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/endpoints/<endpoint_id>/runs")
@require_loaded
def set_endpoint_runs(endpoint_id):
    """Operator-only: set how many times this endpoint runs before it settles.
    Accepts a number, or 'indefinite' / '∞' for an unbounded loop."""
    s = service()
    project = s.current
    return_to = request.form.get("return_to") or url_for("web.endpoints")
    try:
        project.set_endpoint_runs(endpoint_id, request.form.get("runs"))
    except ValueError as e:
        flash(str(e))
        return redirect(return_to)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/endpoints/<endpoint_id>/focus")
@require_loaded
def focus_endpoint(endpoint_id):
    s = service()
    project = s.current
    return_to = request.form.get("return_to") or url_for("web.workflow")
    try:
        project.focus_endpoint(endpoint_id, agent=OPERATOR_AGENT)
    except ValueError as e:
        flash(str(e))
        return redirect(return_to)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/endpoints/<endpoint_id>/unfocus")
@require_loaded
def unfocus_endpoint(endpoint_id):
    s = service()
    project = s.current
    return_to = request.form.get("return_to") or url_for("web.workflow")
    try:
        project.unfocus_endpoint(endpoint_id)
    except ValueError as e:
        flash(str(e))
        return redirect(return_to)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/endpoints/<endpoint_id>/checks-adjusted")
@require_loaded
def confirm_endpoint_checks_adjusted(endpoint_id):
    s = service()
    project = s.current
    return_to = request.form.get("return_to") or url_for("web.workflow")
    try:
        project.confirm_endpoint_checks_adjusted(endpoint_id)
    except ValueError as e:
        flash(str(e))
        return redirect(return_to)
    s.save(project)
    return redirect(return_to)


@web_bp.post("/project/endpoints/<endpoint_id>/checks")
@require_loaded
def update_endpoint_checks(endpoint_id):
    s = service()
    project = s.current
    check_ids = request.form.getlist("checks")
    try:
        project.update_endpoint_checks(endpoint_id, check_ids)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.endpoints"))
    s.save(project)
    return redirect(url_for("web.endpoints"))


@web_bp.post("/project/endpoints/<endpoint_id>/checks/assign")
@require_loaded
def assign_endpoint_check(endpoint_id):
    s = service()
    project = s.current
    check_id = request.form.get("check_id", "").strip()
    try:
        project.assign_endpoint_check(endpoint_id, check_id)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.endpoints"))
    s.save(project)
    return redirect(url_for("web.endpoints"))


@web_bp.post("/project/endpoints/<endpoint_id>/delete")
@require_loaded
def delete_endpoint(endpoint_id):
    s = service()
    project = s.current
    project.remove_endpoint(endpoint_id)
    s.save(project)
    return redirect(url_for("web.endpoints"))


@web_bp.post("/project/endpoints/<endpoint_id>/feature-group")
@require_loaded
def set_endpoint_feature_group(endpoint_id):
    s = service()
    project = s.current
    group_id = request.form.get("group_id", "").strip()
    if group_id == "_unassigned":
        group_id = ""
    try:
        project.set_endpoint_feature_group(endpoint_id, group_id)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.endpoints"))
    s.save(project)
    return redirect(url_for("web.endpoints"))


@web_bp.post("/project/feature-groups")
@require_loaded
def add_feature_group():
    s = service()
    project = s.current
    name = request.form.get("name", "").strip()
    if not name:
        flash("group name is required")
        return redirect(url_for("web.endpoints"))
    try:
        project.add_feature_group(name)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.endpoints"))
    s.save(project)
    return redirect(url_for("web.endpoints"))


@web_bp.post("/project/feature-groups/<group_id>/delete")
@require_loaded
def delete_feature_group(group_id):
    s = service()
    project = s.current
    project.remove_feature_group(group_id)
    s.save(project)
    return redirect(url_for("web.endpoints"))

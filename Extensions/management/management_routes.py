"""Management extension: the Projects management page (list / switch / backup / delete).

Registered as an extra blueprint via the factory's `extra_blueprints` knob.
Endpoints live under the `management.` namespace; core endpoints (scope_bp.scope,
web.landing, web.create) are reused as-is.
"""
import io
import json
import zipfile

from flask import Blueprint, render_template, request, redirect, url_for, current_app, flash, send_file

from Domain import ProjectNotFoundError
from Web.routes import service, _require_loaded, _common_ctx, _workflow_options, _checklist_options

management_bp = Blueprint("management", __name__)


@management_bp.post("/project/create")
def create_project():
    """Create a project and switch to it — on a built-in workflow, or on a pasted
    custom workflow + checklist stored inside the project folder."""
    s = service()
    # The create form lives on both the (no-project) landing page and the Projects
    # page; send errors back to whichever the operator came from.
    error_view = "management.projects" if s.is_loaded else "web.landing"
    name = request.form.get("name", "").strip()
    scope_raw = request.form.get("scope", "")
    endpoints_raw = request.form.get("endpoints", "")
    base_path = request.form.get("path", "").strip() or None
    workflow_def_raw = request.form.get("workflow_def", "").strip()
    checklist_def_raw = request.form.get("checklist_def", "").strip()
    if not name:
        flash("name is required")
        return redirect(url_for(error_view))
    scope = [ln.strip() for ln in scope_raw.splitlines() if ln.strip()]
    endpoints = [{"name": ln.strip()} for ln in endpoints_raw.splitlines() if ln.strip()]

    kwargs = dict(name=name, scope=scope, initial_endpoints=endpoints, base_path=base_path)
    if workflow_def_raw or checklist_def_raw:
        if not (workflow_def_raw and checklist_def_raw):
            flash("paste BOTH a custom workflow and a custom checklist, or neither")
            return redirect(url_for(error_view))
        try:
            kwargs["workflow_def"] = json.loads(workflow_def_raw)
        except json.JSONDecodeError as e:
            flash(f"custom workflow is not valid JSON: {e}")
            return redirect(url_for(error_view))
        try:
            kwargs["checklist_def"] = json.loads(checklist_def_raw)
        except json.JSONDecodeError as e:
            flash(f"custom checklist is not valid JSON: {e}")
            return redirect(url_for(error_view))
    else:
        wf = request.form.get("workflow", "").strip()
        if not wf:
            flash("workflow is required")
            return redirect(url_for(error_view))
        kwargs["workflow_id"] = wf
        kwargs["checklist_id"] = request.form.get("checklist", "").strip() or None

    try:
        s.create(**kwargs)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for(error_view))
    except OSError as e:
        flash(f"could not create project folder: {e}")
        return redirect(url_for(error_view))
    return redirect(url_for(current_app.config.get("PROJECT_HOME", "web.workflow")))


@management_bp.get("/project/projects")
def projects():
    r = _require_loaded()
    if r:
        return r
    s = service()
    return render_template(
        "projects.html",
        section="projects",
        projects=s.list_projects(),
        workflows=_workflow_options(s),
        checklists=_checklist_options(s),
        default_path=current_app.config.get("DEFAULT_PROJECTS_DIR", ""),
        **_common_ctx(),
    )


@management_bp.post("/project/projects/switch")
def switch_project():
    r = _require_loaded()
    if r:
        return r
    s = service()
    path = request.form.get("path", "").strip()
    if not path:
        flash("project path is required")
        return redirect(url_for("management.projects"))
    try:
        s.switch(path)
    except (ProjectNotFoundError, ValueError, OSError) as e:
        flash(str(e))
        return redirect(url_for("management.projects"))
    return redirect(url_for("scope_bp.scope"))


@management_bp.get("/project/projects/backup")
def backup_project():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project_id = request.args.get("id", "").strip()
    if not project_id:
        flash("project id is required")
        return redirect(url_for("management.projects"))
    match = next((p for p in s.list_projects() if p["id"] == project_id), None)
    if match is None:
        flash("project not found")
        return redirect(url_for("management.projects"))
    project_path = s._translate(match["path"]).expanduser().resolve()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in project_path.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(project_path.parent))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{project_path.name}.zip",
    )


@management_bp.post("/project/projects/delete")
def delete_project():
    r = _require_loaded()
    if r:
        return r
    s = service()
    path = request.form.get("path", "").strip()
    if not path:
        flash("project path is required")
        return redirect(url_for("management.projects"))
    try:
        deleted_current = s.delete(path)
    except (ProjectNotFoundError, ValueError, OSError) as e:
        flash(str(e))
        return redirect(url_for("management.projects"))
    if deleted_current:
        flash("project deleted")
        return redirect(url_for("web.landing"))
    return redirect(url_for("management.projects"))


# Phase/category accent colours for the preview, in the same purple -> red order
# the Workflow page walks through its phases.
_PREVIEW_PALETTE = (
    "var(--sp-purple)", "var(--sp-indigo)", "var(--sp-blue)", "var(--sp-cyan)",
    "var(--sp-teal)", "var(--sp-green)", "var(--sp-amber)", "var(--sp-orange)",
    "var(--sp-red)", "var(--sp-magenta)",
)


def _spread_color(i: int, n: int) -> str:
    """Colour for item i of n, spread evenly across the whole palette."""
    if n <= 1:
        return _PREVIEW_PALETTE[0]
    return _PREVIEW_PALETTE[round(i * (len(_PREVIEW_PALETTE) - 1) / (n - 1))]


def _workflow_preview(wf) -> dict:
    phases = []
    for i, phase in enumerate(wf.phases):
        steps = [
            {
                "title": step.get("title") or step.get("check", "").replace("_", " ").title(),
                "description": step.get("description", ""),
                "examples": step.get("examples", ""),
                "repeatable": bool(step.get("repeatable")),
            }
            for step in phase.get("steps", [])
        ]
        phases.append({
            "name": phase.get("name") or phase.get("id", ""),
            "icon": phase.get("icon"),
            "description": phase.get("description", ""),
            "optional": bool(phase.get("optional")),
            "operator_gate": bool(phase.get("human_advance")),
            "steps": steps,
            "color": _spread_color(i, len(wf.phases)),
        })
    return {
        "id": wf.id,
        "name": wf.name,
        "phases": phases,
        "step_count": sum(len(p["steps"]) for p in phases),
        "gate_count": sum(1 for p in phases if p["operator_gate"]),
    }


def _checklist_preview(cl) -> dict:
    categories: dict[str, dict] = {}
    for item in cl.to_dict().get("items", []):
        key = item.get("category_name") or item.get("category") or "Other"
        cat = categories.setdefault(key, {"name": key, "items": []})
        cat["items"].append({
            "title": item.get("title", item.get("id", "")),
            "per_endpoint": item.get("scope") == "per_endpoint",
            "description": item.get("description", ""),
            "examples": item.get("examples", ""),
        })
    cats = list(categories.values())
    for i, cat in enumerate(cats):
        cat["color"] = _spread_color(i, len(cats))
    items = [it for c in cats for it in c["items"]]
    return {
        "id": cl.id,
        "name": cl.name,
        "categories": cats,
        "item_count": len(items),
        "per_endpoint_count": sum(1 for it in items if it["per_endpoint"]),
    }


@management_bp.get("/definitions/preview")
def definition_preview():
    """Read-only HTML fragment previewing a workflow and a checklist, for the
    new-project form. Works without a loaded project; like the form's pickers it
    only knows the built-in (file-based) workflows and checklists."""
    s = service()
    workflow_id = request.args.get("workflow", "")
    wf = s.workflows.get(workflow_id) if workflow_id in s.builtin_workflow_ids else None
    if wf is None:
        return "unknown workflow", 404
    checklist_id = request.args.get("checklist", "").strip() or wf.checklist_id
    cl = s.checklists.get(checklist_id) if checklist_id in s.builtin_checklist_ids else None
    if cl is None:
        return "unknown checklist", 404
    return render_template(
        "_definition_preview.html",
        workflow=_workflow_preview(wf),
        checklist=_checklist_preview(cl),
    )

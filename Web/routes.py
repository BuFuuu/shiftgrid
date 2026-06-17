from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, current_app, flash, jsonify

from Domain import ProjectNotFoundError
from Infrastructure import WORKFLOW_ALIASES

web_bp = Blueprint("web", __name__)

# Identity stamped on items the human operator focuses/finishes from the Web UI,
# mirroring the agents' X-Agent-Id / X-Agent-Alias tags. Agents have no auth here;
# the operator is simply a fixed, well-known identity.
OPERATOR_AGENT = {"id": "13337", "alias": "op"}


def service():
    return current_app.config["PROJECT_SERVICE"]


def _project_home():
    """Endpoint a loaded project lands on. Configurable via the PROJECT_HOME
    app config; defaults to the workflow page."""
    return current_app.config.get("PROJECT_HOME", "web.workflow")


def _projects_view():
    """Endpoint to return to after creating while a project is already loaded.
    Configurable via the PROJECTS_VIEW app config; defaults to the landing page."""
    return current_app.config.get("PROJECTS_VIEW", "web.landing")


def _require_loaded():
    if not service().is_loaded:
        return redirect(url_for("web.landing"))
    return None


def require_loaded(view):
    """View decorator: redirect to the landing page when no project is loaded,
    otherwise run the view. Replaces the `r = _require_loaded(); if r: return r`
    guard at the top of project-scoped handlers."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        r = _require_loaded()
        if r:
            return r
        return view(*args, **kwargs)
    return wrapper


_ALIAS_SOURCE = {a["alias_id"]: a["source_id"] for a in WORKFLOW_ALIASES}


def _workflow_options(s):
    """Workflow choices enriched with the checklist they pull from, for the
    new-project picker. For alias project types, surface the underlying
    workflow id so the UI doesn't claim a workflow file that doesn't exist."""
    options = []
    for wf in s.workflows.values():
        cl = s.checklists.get(wf.checklist_id)
        options.append({
            "id": wf.id,
            "name": wf.name,
            "color": wf.color,
            "workflow_id": _ALIAS_SOURCE.get(wf.id, wf.id),
            "checklist_id": wf.checklist_id,
            "checklist_name": cl.name if cl else wf.checklist_id,
        })
    return options


@web_bp.get("/project/heartbeat")
def heartbeat():
    """Cheap JSON endpoint polled by live.js to detect project changes.
    Returns the project's updated_at timestamp; the client reloads when it
    changes. Always responds (no redirect) so the poll loop is safe to run
    even on pages where no project is loaded."""
    s = service()
    if not s.is_loaded:
        return jsonify({"loaded": False, "updated_at": 0})
    return jsonify({"loaded": True, "updated_at": s.current.data.get("updated_at", 0)})


@web_bp.get("/")
def landing():
    s = service()
    if s.is_loaded:
        return redirect(url_for(_project_home()))
    return render_template(
        "landing.html",
        workflows=_workflow_options(s),
        default_path=current_app.config.get("DEFAULT_PROJECTS_DIR", ""),
        recent_projects=s.recent_projects(),
    )


@web_bp.post("/create")
def create():
    s = service()
    return_to_projects = s.is_loaded
    name = request.form.get("name", "").strip()
    wf = request.form.get("workflow", "").strip()
    scope_raw = request.form.get("scope", "")
    endpoints_raw = request.form.get("endpoints", "")
    base_path = request.form.get("path", "").strip() or None
    if not name or not wf:
        flash("name and workflow are required")
        return redirect(url_for(_projects_view() if return_to_projects else "web.landing"))
    scope = [s.strip() for s in scope_raw.splitlines() if s.strip()]
    endpoints = [{"name": s.strip()} for s in endpoints_raw.splitlines() if s.strip()]
    try:
        s.create(name=name, workflow_id=wf, scope=scope, initial_endpoints=endpoints, base_path=base_path)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for(_projects_view() if return_to_projects else "web.landing"))
    except OSError as e:
        flash(f"could not create project folder: {e}")
        return redirect(url_for(_projects_view() if return_to_projects else "web.landing"))
    return redirect(url_for(_project_home()))


@web_bp.post("/open")
def open_project():
    s = service()
    if s.is_loaded:
        return redirect(url_for(_project_home()))
    path = request.form.get("path", "").strip()
    if not path:
        flash("path is required")
        return redirect(url_for("web.landing"))
    try:
        s.open(path)
    except ProjectNotFoundError as e:
        flash(str(e))
        return redirect(url_for("web.landing"))
    except (ValueError, OSError) as e:
        flash(str(e))
        return redirect(url_for("web.landing"))
    return redirect(url_for(_project_home()))


@web_bp.get("/project")
def project_root():
    if not service().is_loaded:
        return redirect(url_for("web.landing"))
    return redirect(url_for(_project_home()))


def _common_ctx():
    s = service()
    project = s.current
    wf = s.workflow_for(project)
    return {
        "project": project,
        "project_folder": s._display_path(project.folder),
        "workflow": wf,
        "endpoint_testing_status": project.endpoint_testing_ready(),
        "focused_endpoints_workflow": project.focused_endpoints_workflow(),
        "workflow_finished": project.resolve_next_step(wf).kind == "done",
        "workflow_finished_at": project.workflow_finished_at(wf),
        "workflow_terminal": project.is_workflow_terminal(wf),
        "workflow_progress": project.workflow_progress(wf),
        "overall_progress": project.overall_progress(wf),
    }


# ---------- operator docs ----------

# Flask blueprint prefix -> display group for the operator action list.
# Endpoints whose blueprint isn't listed here are omitted from the page.
_OPERATOR_DOC_GROUPS = {
    "web": "Project",
    "scope_bp": "Scope & settings",
    "management": "Project management",
}

# Operator endpoints that are app/landing plumbing, not project actions.
_OPERATOR_DOC_SKIP = {
    "web.heartbeat", "web.landing", "web.create", "web.open_project",
    "web.project_root", "web.operator_docs",
}


@web_bp.get("/project/operator-docs")
@require_loaded
def operator_docs():
    """List every operator action, derived live from the Flask URL map so it
    stays in sync as routes change. The agent API (FastAPI) is not in this map,
    so this is exactly the operator surface and nothing else."""
    groups = {label: {"name": label, "rules": []} for label in _OPERATOR_DOC_GROUPS.values()}
    for rule in current_app.url_map.iter_rules():
        if rule.endpoint in _OPERATOR_DOC_SKIP:
            continue
        label = _OPERATOR_DOC_GROUPS.get(rule.endpoint.split(".", 1)[0])
        if label is None:
            continue
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        view = current_app.view_functions.get(rule.endpoint)
        doc = ((view.__doc__ or "").strip().splitlines() or [""])[0] if view else ""
        if not doc:
            doc = rule.endpoint.split(".", 1)[-1].replace("_", " ").capitalize()
        groups[label]["rules"].append({
            "methods": methods,
            "is_action": methods != ["GET"],
            "path": rule.rule,
            "description": doc,
        })
    for g in groups.values():
        # Actions (mutations) first, then plain GET views; then by path.
        g["rules"].sort(key=lambda x: (not x["is_action"], x["path"]))
    doc_groups = [g for g in groups.values() if g["rules"]]
    return render_template("operator_docs.html", section="operator_docs", doc_groups=doc_groups, **_common_ctx())


# Resource view handlers attach to web_bp on import. Imported last so web_bp and
# the shared helpers above already exist when each module binds to them.
from .views import notes, checklist, endpoints, workflow, findings  # noqa: E402

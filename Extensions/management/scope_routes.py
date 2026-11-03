"""Management extension: the scope / settings hub page.

Scope editing, project context/details, timezone, the notes-required toggle and
credentials — everything the core deliberately omits. Registered as an extra
blueprint via the factory; endpoints live under the `scope_bp.` namespace.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from Domain import ScopeLockedError
from Web.routes import service, _require_loaded, _common_ctx

scope_bp = Blueprint("scope_bp", __name__)


# IANA zones offered on the scope page. Kept short; Berlin is the default.
TIMEZONE_CHOICES = (
    "Europe/Berlin",
    "UTC",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
    "Australia/Sydney",
)


@scope_bp.get("/project/scope")
def scope():
    r = _require_loaded()
    if r:
        return r
    return render_template(
        "scope.html",
        section="scope",
        timezone_choices=TIMEZONE_CHOICES,
        **_common_ctx(),
    )


@scope_bp.post("/project/scope")
def scope_save():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    if "scope" in request.form:
        scope_items = [s.strip() for s in request.form.get("scope", "").splitlines() if s.strip()]
        try:
            project.set_scope(scope_items)
        except ScopeLockedError as e:
            flash(str(e))
    for field in ("context", "details"):
        if field in request.form:
            project.set_scope_field(field, request.form.get(field, ""))
    s.save(project)
    return redirect(url_for("scope_bp.scope"))


@scope_bp.post("/project/notes-required")
def set_notes_required():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    raw = request.form.get("notes_required", "").strip().lower()
    project.set_notes_required(raw in ("1", "true", "on", "yes"))
    s.save(project)
    return redirect(url_for("scope_bp.scope"))


@scope_bp.post("/project/observations-required")
def set_observations_required():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    raw = request.form.get("observations_required", "").strip().lower()
    project.set_observations_required(raw in ("1", "true", "on", "yes"))
    s.save(project)
    return redirect(url_for("scope_bp.scope"))


@scope_bp.post("/project/timezone")
def set_timezone():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    tz = request.form.get("timezone", "").strip()
    if tz not in TIMEZONE_CHOICES:
        flash("Unknown timezone.")
        return redirect(url_for("scope_bp.scope"))
    project.set_timezone(tz)
    s.save(project)
    return redirect(url_for("scope_bp.scope"))


@scope_bp.post("/project/scope/lock")
def scope_lock():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    project.lock_scope()
    s.save(project)
    return redirect(url_for("scope_bp.scope"))


@scope_bp.post("/project/credentials/add")
def add_credential():
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    project.add_credential(
        username=request.form.get("username", "").strip(),
        password=request.form.get("password", ""),
        observations=request.form.get("observations", "").strip(),
        source=request.form.get("source", "manual").strip() or "manual",
    )
    s.save(project)
    return redirect(url_for("scope_bp.scope"))


@scope_bp.post("/project/credentials/<cred_id>/delete")
def delete_credential(cred_id):
    r = _require_loaded()
    if r:
        return r
    s = service()
    project = s.current
    project.remove_credential(cred_id)
    s.save(project)
    return redirect(url_for("scope_bp.scope"))

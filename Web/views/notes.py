from flask import render_template, request, redirect, url_for

from ..routes import web_bp, service, require_loaded, _common_ctx


@web_bp.get("/project/notes")
@require_loaded
def notes():
    return render_template("notes.html", section="notes", **_common_ctx())


@web_bp.post("/project/notes")
@require_loaded
def notes_save():
    s = service()
    project = s.current
    project.set_notes(request.form.get("notes", ""))
    s.save(project)
    return redirect(url_for("web.notes"))

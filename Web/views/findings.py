import re

import base64

from flask import render_template, request, redirect, url_for, flash, send_file

from ..routes import web_bp, service, require_loaded, _common_ctx


_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _sorted_findings(project):
    """Findings ordered highest-risk first, newest first within a severity tier;
    unknown severities sink to the bottom."""
    return sorted(
        project.findings,
        key=lambda f: (
            _SEV_ORDER.get((f.get("severity") or "info").lower(), 99),
            -int(f.get("created_at") or 0),
        ),
    )


@web_bp.get("/project/findings")
@require_loaded
def findings():
    return render_template(
        "findings.html",
        section="findings",
        sorted_findings=_sorted_findings(service().current),
        **_common_ctx(),
    )


@web_bp.get("/project/findings/export")
@require_loaded
def export_findings():
    project = service().current
    fmt = (request.args.get("format") or "json").lower()
    findings = _sorted_findings(project)
    slug = re.sub(r"[^a-z0-9]+", "-", (project.name or "project").lower()).strip("-") or "project"

    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "severity", "title", "description", "recommendation", "evidence"])
        for f in findings:
            evidence = "; ".join(e.get("name", "") for e in f.get("evidence", []))
            writer.writerow([
                f.get("id", ""),
                f.get("severity", ""),
                f.get("title", ""),
                f.get("description", ""),
                f.get("recommendation", ""),
                evidence,
            ])
        body, mime, ext = buf.getvalue(), "text/csv", "csv"

    elif fmt in ("md", "markdown"):
        body, mime, ext = _findings_markdown(project, findings), "text/markdown", "md"

    else:  # json (default)
        import json
        export = [
            {
                "id": f.get("id"),
                "severity": f.get("severity"),
                "title": f.get("title"),
                "description": f.get("description"),
                "recommendation": f.get("recommendation"),
                "evidence": [e.get("name") for e in f.get("evidence", [])],
            }
            for f in findings
        ]
        body, mime, ext = json.dumps(export, indent=2), "application/json", "json"

    from flask import Response
    return Response(
        body,
        mimetype=mime,
        headers={"Content-Disposition": f'attachment; filename="{slug}-findings.{ext}"'},
    )


def _findings_markdown(project, findings) -> str:
    """One self-contained markdown file: a header with per-severity counts, then
    every finding as a section grouped most-severe first."""
    counts = {}
    for f in findings:
        sev = (f.get("severity") or "info").lower()
        counts[sev] = counts.get(sev, 0) + 1
    summary = ", ".join(
        f"{sev.capitalize()}: {counts.get(sev, 0)}"
        for sev in ("critical", "high", "medium", "low", "info")
    )

    lines = [f"# Findings — {project.name}", "", f"{len(findings)} findings — {summary}", ""]
    for f in findings:
        sev = (f.get("severity") or "info").upper()
        lines.append(f"## [{sev}] {f.get('title', '')}")
        lines.append("")
        desc = (f.get("description") or "").strip()
        if desc:
            lines.append(desc)
            lines.append("")
        rec = (f.get("recommendation") or "").strip()
        if rec:
            lines.append("**Recommendation:**")
            lines.append("")
            lines.append(rec)
            lines.append("")
        evidence = f.get("evidence", [])
        if evidence:
            lines.append("**Evidence:**")
            for e in evidence:
                lines.append(f"- {e.get('name', '')}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@web_bp.post("/project/findings")
@require_loaded
def add_finding():
    s = service()
    project = s.current
    title = request.form.get("title", "").strip()
    if not title:
        flash("title is required")
        return redirect(url_for("web.findings"))
    project.add_finding(
        title=title,
        severity=request.form.get("severity", "info").strip() or "info",
        description=request.form.get("description", ""),
        recommendation=request.form.get("recommendation", ""),
    )
    s.save(project)
    return redirect(url_for("web.findings"))


@web_bp.post("/project/findings/<finding_id>/edit")
@require_loaded
def edit_finding(finding_id):
    s = service()
    project = s.current
    fields = {}
    for k in ("title", "severity", "description", "recommendation"):
        if k in request.form:
            fields[k] = request.form.get(k, "")
    try:
        project.update_finding(finding_id, **fields)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.findings"))
    s.save(project)
    return redirect(url_for("web.findings"))


@web_bp.post("/project/findings/<finding_id>/delete")
@require_loaded
def delete_finding(finding_id):
    s = service()
    project = s.current
    project.remove_finding(finding_id)
    s.save(project)
    return redirect(url_for("web.findings"))


@web_bp.post("/project/findings/<finding_id>/evidence")
@require_loaded
def add_finding_evidence(finding_id):
    s = service()
    project = s.current
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        flash("no file selected")
        return redirect(url_for("web.findings"))
    data_b64 = base64.b64encode(uploaded.read()).decode("ascii")
    try:
        project.add_finding_evidence(
            finding_id,
            uploaded.filename,
            data_b64,
            mime_type=uploaded.mimetype or "application/octet-stream",
            source_type=request.form.get("source_type") or "other",
            description=(request.form.get("description") or "").strip(),
        )
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.findings"))
    s.save(project)
    return redirect(url_for("web.findings"))


@web_bp.get("/project/findings/<finding_id>/evidence/<evidence_id>")
@require_loaded
def download_finding_evidence(finding_id, evidence_id):
    project = service().current
    try:
        entry, abs_path = project.get_finding_evidence(finding_id, evidence_id)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("web.findings"))
    if not abs_path.is_file():
        flash("evidence file missing on disk")
        return redirect(url_for("web.findings"))
    return send_file(
        abs_path,
        mimetype=entry.get("mime_type", "application/octet-stream"),
        as_attachment=True,
        download_name=entry["name"],
    )


@web_bp.get("/project/findings/<finding_id>/evidence/<evidence_id>/view")
@require_loaded
def view_finding_evidence(finding_id, evidence_id):
    """Serve evidence inline for the viewer popup. Images keep their mime type
    so <img> can render them; everything else is forced to text/plain so an
    uploaded HTML/SVG payload can never execute in the app's origin."""
    project = service().current
    try:
        entry, abs_path = project.get_finding_evidence(finding_id, evidence_id)
    except ValueError as e:
        return (str(e), 404)
    if not abs_path.is_file():
        return ("evidence file missing on disk", 404)
    mime = entry.get("mime_type", "application/octet-stream")
    is_image = mime.startswith("image/") and mime != "image/svg+xml"
    return send_file(
        abs_path,
        mimetype=mime if is_image else "text/plain",
        as_attachment=False,
        download_name=entry["name"],
    )

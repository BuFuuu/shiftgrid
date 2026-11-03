"""Management extension: multi-project management on top of the core service.

The core `ProjectService` only knows about the single loaded project. This
subclass adds listing / switching / deleting across many project folders —
the behaviour behind the Projects page.

It also adds *custom per-project definitions*: a project can ship its own
workflow + checklist instead of picking a built-in one. Those definitions live
as `workflow-def.json` / `checklist-def.json` inside the project folder (delete
the folder -> they're gone, like everything else about a project). Because the
rest of the app resolves a workflow by id on every request, a custom workflow is
registered into the in-memory registry whenever its project is created or loaded.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from Application import ProjectService
from Domain import ProjectNotFoundError, Workflow, Checklist


# Custom definitions are stored under these names in the project folder. They are
# deliberately NOT `workflow.json` / `checklist.json` — those two are already used
# by the repository for a project's workflow/checklist *state*.
WORKFLOW_DEF_FILE = "workflow-def.json"
CHECKLIST_DEF_FILE = "checklist-def.json"


def _validate_definition(workflow_def: dict, checklist_def: dict) -> tuple[dict, dict]:
    """Validate a custom workflow+checklist pair supplied at project creation and
    return them normalised so the workflow points at the checklist. Raises
    ValueError with a human-readable message on any problem — the message is what
    the operator sees (flash) and the agent gets back (400 detail), so it names
    the exact field at fault."""
    if not isinstance(workflow_def, dict):
        raise ValueError("workflow definition must be a JSON object")
    if not isinstance(checklist_def, dict):
        raise ValueError("checklist definition must be a JSON object")

    def _nonempty_str(d, key, what):
        v = d.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{what} needs a non-empty string '{key}'")

    _nonempty_str(workflow_def, "id", "workflow definition")
    _nonempty_str(workflow_def, "name", "workflow definition")
    phases = workflow_def.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("workflow definition needs a non-empty 'phases' list")
    for i, ph in enumerate(phases):
        if not isinstance(ph, dict):
            raise ValueError(f"workflow phase #{i + 1} must be a JSON object")
        _nonempty_str(ph, "id", f"workflow phase #{i + 1}")

    _nonempty_str(checklist_def, "id", "checklist definition")
    items = checklist_def.get("items")
    if not isinstance(items, list):
        raise ValueError("checklist definition needs an 'items' list")
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"checklist item #{i + 1} must be a JSON object")
        _nonempty_str(it, "id", f"checklist item #{i + 1}")
        _nonempty_str(it, "title", f"checklist item #{i + 1} ({it.get('id', '?')})")

    # Point the workflow at this checklist so core create() resolves it, whatever
    # the workflow's own 'checklist' field happened to say.
    workflow_def = dict(workflow_def)
    workflow_def["checklist"] = checklist_def["id"]
    return workflow_def, checklist_def


class ManagedProjectService(ProjectService):
    # A custom (per-project) definition may never reuse a built-in id
    # (`builtin_workflow_ids` / `builtin_checklist_ids`, set by the core) — that
    # keeps registering a custom definition from ever clobbering a built-in in the
    # shared in-memory registry.

    # ---- custom per-project definitions ----

    def _register_defs(self, workflow_def: dict, checklist_def: dict) -> str:
        """Put a custom workflow+checklist into the in-memory registry so the rest
        of the app (which resolves workflows by id on every request) can find them.
        Returns the workflow id."""
        wf = Workflow(workflow_def)
        cl = Checklist(checklist_def)
        self.workflows[wf.id] = wf
        self.checklists[cl.id] = cl
        return wf.id

    def _register_project_defs(self, folder: Path) -> None:
        """If the project folder carries custom definition files, load and register
        them. Called before loading a project so its custom workflow resolves. On a
        corrupt/partial pair it stays silent and lets the normal 'unknown workflow'
        error surface from the loader."""
        folder = Path(folder)
        wpath = folder / WORKFLOW_DEF_FILE
        cpath = folder / CHECKLIST_DEF_FILE
        if not wpath.exists() or not cpath.exists():
            return
        try:
            workflow_def = json.loads(wpath.read_text(encoding="utf-8"))
            checklist_def = json.loads(cpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._register_defs(workflow_def, checklist_def)

    def create(
        self,
        name: str,
        workflow_id: str | None = None,
        scope: list[str] | None = None,
        initial_endpoints: list[dict] | None = None,
        base_path: str | Path | None = None,
        workflow_def: dict | None = None,
        checklist_def: dict | None = None,
        checklist_id: str | None = None,
    ) -> "Project":
        """Create a project. Either pick a built-in workflow by `workflow_id`
        (optionally with a built-in `checklist_id` other than its default), or
        supply a custom `workflow_def` + `checklist_def` pair (both required
        together) that is validated, registered and stored inside the project
        folder so it travels with the project."""
        if workflow_def is not None or checklist_def is not None:
            if workflow_def is None or checklist_def is None:
                raise ValueError("a custom project needs BOTH workflow_def and checklist_def")
            workflow_def, checklist_def = _validate_definition(workflow_def, checklist_def)
            if workflow_def["id"] in self.builtin_workflow_ids:
                raise ValueError(
                    f"custom workflow id {workflow_def['id']!r} clashes with a built-in "
                    "workflow; give the custom workflow a unique id"
                )
            if checklist_def["id"] in self.builtin_checklist_ids:
                raise ValueError(
                    f"custom checklist id {checklist_def['id']!r} clashes with a built-in "
                    "checklist; give the custom checklist a unique id"
                )
            workflow_id = self._register_defs(workflow_def, checklist_def)
            checklist_id = None  # the custom workflow brings its own checklist
        else:
            # Another project's custom workflow may still sit in the in-memory
            # registry; reusing it by id would create a project without the
            # definition files, which breaks after a restart. Built-ins only.
            if workflow_id and workflow_id not in self.builtin_workflow_ids:
                raise ValueError(f"unknown workflow {workflow_id}")
            if checklist_id and checklist_id not in self.builtin_checklist_ids:
                raise ValueError(f"unknown checklist {checklist_id}")

        if not workflow_id:
            raise ValueError("workflow is required (a built-in id, or a custom workflow_def)")

        project = super().create(
            name=name,
            workflow_id=workflow_id,
            scope=scope or [],
            initial_endpoints=initial_endpoints,
            base_path=base_path,
            checklist_id=checklist_id,
        )

        if workflow_def is not None:
            # Persist the definitions inside the project folder so they survive a
            # restart and travel with the project. Not part of the repository's
            # split files, so a project save never rewrites them.
            (project.folder / WORKFLOW_DEF_FILE).write_text(
                json.dumps(workflow_def, indent=2), encoding="utf-8"
            )
            (project.folder / CHECKLIST_DEF_FILE).write_text(
                json.dumps(checklist_def, indent=2), encoding="utf-8"
            )
        return project

    def open(self, folder_path: str | Path) -> "Project":
        folder = self._translate(folder_path).expanduser().resolve()
        self._register_project_defs(folder)
        return super().open(folder_path)

    def list_projects(self) -> list[dict]:
        projects = {}

        def add(folder: Path, opened_at: int = 0) -> None:
            try:
                folder = Path(folder).expanduser().resolve()
                if not self._repo.exists(folder):
                    return
                project = self._repo.load(folder)
            except (OSError, ValueError, json.JSONDecodeError):
                return
            key = str(folder)
            current = projects.get(key, {})
            workflow = self.workflows.get(project.workflow_id)
            projects[key] = {
                "id": project.id,
                "name": project.name,
                "workflow": project.workflow_id,
                "workflow_name": workflow.name if workflow else project.workflow_id.replace("_", " ").title(),
                "path": self._display_path(folder),
                "scope_count": len(project.scope),
                "loaded": self._project is not None and str(self._project.folder.resolve()) == key,
                "opened_at": max(opened_at, current.get("opened_at", 0)),
                "created_at": int(project.data.get("created_at") or 0),
            }

        add(self.default_base)
        if self.default_base.exists():
            for child in self.default_base.iterdir():
                if child.is_dir():
                    add(child)
        for entry in self.recent_projects():
            path = entry.get("path")
            if path:
                add(self._translate(path), int(entry.get("opened_at") or 0))
        if self._project is not None:
            add(self._project.folder, int(time.time()))

        return sorted(projects.values(), key=lambda p: (not p["loaded"], -p["opened_at"], p["name"].lower()))

    def switch(self, folder_path: str | Path) -> "Project":
        folder = self._translate(folder_path).expanduser().resolve()
        self._register_project_defs(folder)
        project = self._repo.load(folder)
        if project.workflow_id not in self.workflows:
            raise ValueError(f"project references unknown workflow: {project.workflow_id}")
        self._project = project
        self._record_recent(project)
        return project

    def delete(self, folder_path: str | Path) -> bool:
        folder = self._translate(folder_path).expanduser().resolve()
        if not self._repo.exists(folder):
            raise ProjectNotFoundError(f"{folder} does not contain a project.json")
        deleting_current = self._project is not None and str(self._project.folder.resolve()) == str(folder)
        shutil.rmtree(folder)
        self._forget_recent(folder)
        if deleting_current:
            self._project = None
        return deleting_current

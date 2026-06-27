from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from Domain import (
    Project,
    Workflow,
    Checklist,
    NoProjectLoadedError,
    EVIDENCE_DIRS,
    _now,
)

RECENT_FILE = ".recent.json"
RECENT_LIMIT = 10


SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slug(name: str) -> str:
    return SLUG_RE.sub("-", name.lower()).strip("-") or "project"


class ProjectService:
    """Holds the currently loaded project for the session."""

    def __init__(
        self,
        default_base: Path,
        workflows: dict[str, Workflow],
        checklists: dict[str, Checklist],
        repository,
        host_prefix: str | None = None,
        lock_scope_on_create: bool = False,
        notes_template: str = "",
    ):
        self._lock_scope_on_create = lock_scope_on_create
        self._notes_template = notes_template
        self.default_base = Path(default_base).expanduser().resolve()
        self.default_base.mkdir(parents=True, exist_ok=True)
        self.workflows = workflows
        self.checklists = checklists
        self._repo = repository
        self._project: Project | None = None
        self._host_prefix = host_prefix.rstrip("/\\") if host_prefix else None
        self._container_prefix = str(self.default_base)

    def _translate(self, path: str | Path) -> Path:
        s = str(path)
        if self._host_prefix and (s == self._host_prefix or s.startswith(self._host_prefix + "/") or s.startswith(self._host_prefix + "\\")):
            remainder = s[len(self._host_prefix):].lstrip("/\\")
            return (Path(self._container_prefix) / remainder) if remainder else Path(self._container_prefix)
        return Path(s)

    @property
    def current(self) -> Project:
        if self._project is None:
            raise NoProjectLoadedError("no project loaded")
        return self._project

    @property
    def is_loaded(self) -> bool:
        return self._project is not None

    def workflow_for(self, project: Project) -> Workflow:
        wf = self.workflows.get(project.workflow_id)
        if wf is None:
            raise ValueError(f"workflow {project.workflow_id} not loaded")
        return wf

    def checklist_for(self, workflow: Workflow) -> Checklist:
        cl = self.checklists.get(workflow.checklist_id)
        if cl is None:
            raise ValueError(f"checklist {workflow.checklist_id} not loaded")
        return cl

    def save(self, project: Project) -> None:
        self._repo.save(project)

    # ---- recent projects ----

    def _recent_path(self) -> Path:
        return self.default_base / RECENT_FILE

    def _display_path(self, folder: Path) -> str:
        """Return folder path translated back to the host-side prefix for display."""
        s = str(folder)
        if self._host_prefix and s.startswith(self._container_prefix):
            return self._host_prefix + s[len(self._container_prefix):]
        return s

    def recent_projects(self) -> list[dict]:
        path = self._recent_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        # Drop entries whose project folder no longer exists, so the recent
        # list never offers a project that can't be opened.
        existing = [e for e in data[:RECENT_LIMIT] if self._project_exists(e.get("path"))]
        return existing

    def _project_exists(self, display_path: str | None) -> bool:
        if not display_path:
            return False
        try:
            folder = self._translate(display_path).expanduser().resolve()
        except OSError:
            return False
        return self._repo.exists(folder)

    def _forget_recent(self, folder: Path) -> None:
        target = str(folder.resolve())
        recent = []
        for entry in self.recent_projects():
            try:
                entry_path = str(self._translate(entry.get("path", "")).expanduser().resolve())
            except OSError:
                entry_path = ""
            if entry_path != target:
                recent.append(entry)
        try:
            with open(self._recent_path(), "w", encoding="utf-8") as f:
                json.dump(recent[:RECENT_LIMIT], f, indent=2)
        except OSError:
            pass

    def _record_recent(self, project: Project) -> None:
        entry = {
            "id": project.id,
            "name": project.name,
            "workflow": project.workflow_id,
            "path": self._display_path(project.folder),
            "opened_at": int(time.time()),
        }
        recent = [e for e in self.recent_projects() if e.get("path") != entry["path"]]
        recent.insert(0, entry)
        recent = recent[:RECENT_LIMIT]
        try:
            self.default_base.mkdir(parents=True, exist_ok=True)
            with open(self._recent_path(), "w", encoding="utf-8") as f:
                json.dump(recent, f, indent=2)
        except OSError:
            pass

    def find_existing_project(self) -> Path | None:
        """Single-project bootstrap: the most recently modified project folder
        under the projects dir (or the dir itself), or None if there is none."""
        candidates = []
        if self._repo.exists(self.default_base):
            candidates.append(self.default_base)
        if self.default_base.exists():
            for child in self.default_base.iterdir():
                if child.is_dir() and self._repo.exists(child):
                    candidates.append(child)
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def open(self, folder_path: str | Path) -> Project:
        if self._project is not None:
            raise RuntimeError("a project is already loaded; restart to switch")
        folder = self._translate(folder_path).expanduser().resolve()
        project = self._repo.load(folder)
        if project.workflow_id not in self.workflows:
            raise ValueError(f"project references unknown workflow: {project.workflow_id}")
        self._project = project
        self._record_recent(project)
        return project

    def create(
        self,
        name: str,
        workflow_id: str,
        scope: list[str],
        initial_endpoints: list[dict] | None = None,
        base_path: str | Path | None = None,
    ) -> Project:
        wf = self.workflows.get(workflow_id)
        if wf is None:
            raise ValueError(f"unknown workflow {workflow_id}")
        cl = self.checklist_for(wf)
        base = self._translate(base_path).expanduser().resolve() if base_path else self.default_base
        base.mkdir(parents=True, exist_ok=True)
        slug = _slug(name)
        pid = f"{slug}-{uuid.uuid4().hex[:6]}"
        folder = base / pid
        if folder.exists():
            raise ValueError("project folder already exists")
        folder.mkdir(parents=True)
        # Evidence folders, one per kind (workflow / checklist / endpoints /
        # findings). Pre-created so the project layout is visible from the start.
        for evidence_dir in EVIDENCE_DIRS:
            (folder / evidence_dir).mkdir()

        checklist = []
        for item in cl.to_dict().get("items", []):
            checklist.append({
                "id": item["id"],
                "phase": item.get("phase", "checklist"),
                "title": item["title"],
                "category": item.get("category", "uncategorized"),
                "category_name": item.get("category_name", "Uncategorized"),
                "scope": item.get("scope", "global"),
                "repeatable": item.get("repeatable", False),
                "produces_endpoints": item.get("produces_endpoints", False),
                "description": item.get("description", ""),
                "examples": item.get("examples", ""),
                "results": {},
            })

        data = {
            "id": pid,
            "name": name,
            "workflow": workflow_id,
            "scope": list(scope),
            "scope_locked": self._lock_scope_on_create,
            "credentials": [],
            "context": "",
            "current_phase": wf.phases[0]["id"],
            "endpoints": [],
            "checklist": checklist,
            "workflow_steps": {},
            "workflow_phases": {wf.phases[0]["id"]: {"advanced_at": _now()}},
            "findings": [],
            "details": "",
            "notes": self._notes_template,
            "notes_required": True,
            "created_at": _now(),
            "updated_at": _now(),
        }
        project = Project(folder, data)
        for a in initial_endpoints or []:
            project.add_endpoint(
                a["name"],
                a.get("type", "host"),
                source="initial",
                observations=a.get("observations", ""),
            )
        self._repo.save(project)
        self._project = project
        self._record_recent(project)
        return project

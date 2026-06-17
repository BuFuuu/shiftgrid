from __future__ import annotations

import json
import os
from pathlib import Path

from Domain import Project, ProjectNotFoundError, _now


class JsonProjectRepository:
    """Persists a Project across several JSON files in its folder.

    The domain keeps the whole project as one in-memory dict (``Project.data``).
    On disk that dict is split so the big, independently-edited sections each
    live in their own file:

        workflow.json   -> workflow_steps, workflow_phases, current_phase
        checklist.json  -> checklist
        endpoints.json  -> endpoints, feature_groups
        notes.json      -> notes
        findings.json   -> findings
        project.json    -> everything else (scope, credentials, config, ...)

    project.json is the anchor that marks a folder as a project. Older projects
    stored everything in project.json; on load those keys are read straight back
    (the split files simply don't exist yet) and moved into their own files on
    the next save.
    """

    FILE = "project.json"

    SPLIT_FILES = {
        "workflow.json": ("workflow_steps", "workflow_phases", "current_phase"),
        "checklist.json": ("checklist",),
        "endpoints.json": ("endpoints", "feature_groups"),
        "notes.json": ("notes",),
        "findings.json": ("findings",),
    }

    def exists(self, folder: Path) -> bool:
        return (Path(folder) / self.FILE).exists()

    def load(self, folder: Path) -> Project:
        folder = Path(folder)
        path = folder / self.FILE
        if not path.exists():
            raise ProjectNotFoundError(f"{path} not found")
        data = self._read(path)
        # Merge in the split files. A missing split file means either a freshly
        # created project not yet saved in pieces, or an old single-file project
        # — either way those keys (if present) already sit in project.json.
        for fname in self.SPLIT_FILES:
            fpath = folder / fname
            if fpath.exists():
                data.update(self._read(fpath))
        self._migrate(data)
        return Project(folder, data)

    @staticmethod
    def _migrate(data: dict) -> None:
        """In-place upgrades for projects saved by older versions. The
        once-scope checks were renamed to global: rewrite the stored ``scope``
        value and the per-check results key (``_once`` -> ``_global``) so old
        projects keep working. Idempotent — re-running on already-migrated data
        is a no-op. The migrated shape is written back on the next save."""
        for item in data.get("checklist") or []:
            if not isinstance(item, dict):
                continue
            if item.get("scope") == "once":
                item["scope"] = "global"
            results = item.get("results")
            if isinstance(results, dict) and "_once" in results and "_global" not in results:
                results["_global"] = results.pop("_once")

    def save(self, project: Project) -> None:
        project.data["updated_at"] = _now()
        folder = project.folder

        for fname, keys in self.SPLIT_FILES.items():
            section = {k: project.data[k] for k in keys if k in project.data}
            self._write(folder / fname, section)

        split_keys = {k for keys in self.SPLIT_FILES.values() for k in keys}
        core = {k: v for k, v in project.data.items() if k not in split_keys}
        self._write(folder / self.FILE, core)

    @staticmethod
    def _read(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

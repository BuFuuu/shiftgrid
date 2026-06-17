from __future__ import annotations

# Read-models for the two operator-authored definition files: the workflow
# (phases/steps) and the checklist (security checks). Both are thin, read-only
# wrappers over the parsed JSON, loaded by Infrastructure.loaders.


class Workflow:
    def __init__(self, data: dict):
        self.id = data["id"]
        self.name = data["name"]
        self.checklist_id = data.get("checklist", data["id"])
        self.color = data.get("color")
        self.phases = data["phases"]
        self._raw = data

    def phase(self, phase_id: str):
        for p in self.phases:
            if p["id"] == phase_id:
                return p
        return None

    def step(self, phase_id: str, step_id: str):
        phase = self.phase(phase_id)
        if not phase:
            return None
        for s in phase.get("steps", []):
            if s["id"] == step_id:
                return s
        return None

    def next_phase(self, phase_id: str, skip_optional: bool = False):
        ids = [p["id"] for p in self.phases]
        if phase_id not in ids:
            return None
        idx = ids.index(phase_id) + 1
        while idx < len(self.phases):
            p = self.phases[idx]
            if skip_optional and p.get("optional"):
                idx += 1
                continue
            return p
        return None

    def to_dict(self):
        return self._raw


class Checklist:
    def __init__(self, data: dict):
        self.id = data["id"]
        self.name = data.get("name", self.id)
        self.items = {item["id"]: item for item in data.get("items", [])}
        self._raw = data

    def title(self, item_id: str) -> str:
        item = self.items.get(item_id)
        if item is None:
            raise KeyError(f"checklist item {item_id} not in {self.id}")
        return item["title"]

    def to_dict(self):
        return self._raw

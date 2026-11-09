from __future__ import annotations

import copy
import json
from pathlib import Path

from Domain import Workflow, Checklist


# A workflow alias reuses another workflow's phases with a different checklist:
# {"alias_id": ..., "source_id": ..., "name": ..., "checklist": ...}
WORKFLOW_ALIASES: list[dict] = []


def load_workflows(folder: Path) -> dict[str, Workflow]:
    result = {}
    preferred_order = {"web_application": 0, "web_application_swift": 1, "web_service": 2, "external_network_infrastructure": 3, "internal_network_infrastructure": 4}
    paths = sorted(
        Path(folder).glob("*.json"),
        key=lambda p: (preferred_order.get(p.stem, 99), p.stem),
    )
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        wf = Workflow(data)
        result[wf.id] = wf

    for alias in WORKFLOW_ALIASES:
        source = result.get(alias["source_id"])
        if source is None:
            continue
        data = copy.deepcopy(source.to_dict())
        data["id"] = alias["alias_id"]
        data["name"] = alias["name"]
        data["checklist"] = alias["checklist"]
        # "common" is a per-file flag; an alias has no file of its own.
        data["common"] = alias.get("common", False)
        result[alias["alias_id"]] = Workflow(data)

    ordered = {}
    for key in sorted(result, key=lambda k: (preferred_order.get(k, 99), k)):
        ordered[key] = result[key]
    return ordered


def load_checklists(folder: Path) -> dict[str, Checklist]:
    result = {}
    for path in Path(folder).glob("*.json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cl = Checklist(data)
        result[cl.id] = cl
    return result

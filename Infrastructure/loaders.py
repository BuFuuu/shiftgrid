from __future__ import annotations

import copy
import json
from pathlib import Path

from Domain import Workflow, Checklist


WORKFLOW_ALIASES = [
    {
        "alias_id": "web_application_red_team",
        "source_id": "web_application",
        "name": "Web Application (RT)",
        "checklist": "web_application_red_team",
    },
]


def load_workflows(folder: Path) -> dict[str, Workflow]:
    result = {}
    preferred_order = {"web_application": 0, "web_application_red_team": 1, "web_service": 2, "network_infrastructure": 3}
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

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class ProjectBase:
    def __init__(self, folder: Path, data: dict):
        self.folder = Path(folder)
        self.data = data

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def workflow_id(self) -> str:
        return self.data["workflow"]

    @property
    def scope(self) -> list[str]:
        return list(self.data.get("scope", []))

    @property
    def scope_locked(self) -> bool:
        return bool(self.data.get("scope_locked", False))

    @property
    def notes_required(self) -> bool:
        """When True, agent finish/done/advance actions must carry a notes edit
        (notes_old_string/notes_new_string) — Edit-tool semantics. Enforces
        consistent short-term-memory updates without adding extra API round-trips."""
        return bool(self.data.get("notes_required", True))

    def set_notes_required(self, value: bool) -> None:
        self.data["notes_required"] = bool(value)

    @property
    def observations_required(self) -> bool:
        """When True, a global or per-endpoint check must already carry (or be
        given in the same call) a non-empty observation before its status can be
        changed to anything other than 'pending'. Off by default so existing
        projects keep their looser behaviour."""
        return bool(self.data.get("observations_required", False))

    def set_observations_required(self, value: bool) -> None:
        self.data["observations_required"] = bool(value)

    @property
    def agent_advance_allowed(self) -> bool:
        """When True (default), both the agent (API) and the human operator can
        advance phases. When False, advancing the live workflow is restricted to
        the operator via the UI and the agent's POST /workflow/advance is
        rejected. Closing modify mode on a reopened phase is unaffected, and the
        per-phase `human_advance` gate still applies on top of this."""
        return bool(self.data.get("agent_advance_allowed", True))

    def set_agent_advance_allowed(self, value: bool) -> None:
        self.data["agent_advance_allowed"] = bool(value)

    @property
    def try_harder(self) -> bool:
        """When True, the first finish of any workflow step, global check, or
        endpoint is intercepted with a 'try harder' nudge instead of completing
        it; the item only finishes on the second finish call. Off by default. A
        project-wide switch the operator toggles from the Web UI."""
        return bool(self.data.get("try_harder", False))

    def set_try_harder(self, value: bool) -> None:
        self.data["try_harder"] = bool(value)

    def try_harder_nudge(self, holder: dict) -> bool:
        """Try-harder gate for a finish action, operating on the item's state
        dict (a step state, a check's `_global` result, or an endpoint). With
        try-harder mode on, the first finish is intercepted: set the item's nudge
        flag and return True — the caller must then NOT finish, but surface
        TRY_HARDER_MESSAGE so the agent does a deeper pass and calls finish again.
        Returns False when the mode is off or the item was already nudged, so the
        second finish proceeds. The finish methods clear the flag, so a reopened
        item nudges again."""
        if not self.try_harder:
            return False
        if holder.get("try_harder_nudged"):
            return False
        holder["try_harder_nudged"] = True
        return True

    @property
    def timezone(self) -> str:
        """IANA timezone name used when rendering timestamps for this project."""
        tz = self.data.get("timezone")
        return tz if isinstance(tz, str) and tz else "Europe/Berlin"

    def set_timezone(self, value: str) -> None:
        self.data["timezone"] = str(value)

    @property
    def current_phase(self) -> str:
        return self.data["current_phase"]

    def set_scope(self, items: list[str]):
        if self.scope_locked:
            raise ScopeLockedError("scope is locked and cannot be changed")
        self.data["scope"] = list(items)

    def lock_scope(self):
        self.data["scope_locked"] = True

    def set_scope_field(self, field: str, value: str):
        if field not in ("context", "details"):
            raise ValueError(f"unknown scope field {field}")
        self.data[field] = value

    def set_details(self, text: str):
        self.data["details"] = text

    @staticmethod
    def _empty_result() -> dict:
        return {"status": "pending", "observations": "", "evidence": [], "ts": None}

    def _write_evidence(
        self,
        rel_dir: Path,
        name: str,
        data_b64: str,
        mime_type: str,
        source_type: str = "other",
        description: str = "",
    ) -> dict:
        eid = uuid.uuid4().hex[:8]
        safe = _safe_name(name)
        rel_path = rel_dir / f"{eid}_{safe}"
        (self.folder / rel_dir).mkdir(parents=True, exist_ok=True)
        try:
            payload = base64.b64decode(data_b64, validate=True)
        except Exception as e:
            raise ValueError(f"invalid base64 evidence: {e}")
        (self.folder / rel_path).write_bytes(payload)
        return {
            "id": eid,
            "name": name,
            "mime_type": mime_type,
            "source_type": source_type,
            "description": description,
            "path": str(rel_path).replace("\\", "/"),
            "size": len(payload),
            "added_at": _now(),
        }

    def to_public(self, workflow: Workflow | None = None) -> dict:
        out = dict(self.data)
        out["folder"] = str(self.folder)
        if workflow is not None:
            out["workflow_definition"] = workflow.to_dict()
        return out

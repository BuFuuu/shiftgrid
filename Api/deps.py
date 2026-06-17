from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request

from Application import ProjectService


def get_service(request: Request) -> ProjectService:
    return request.app.state.service


def require_loaded(service: ProjectService = Depends(get_service)) -> ProjectService:
    if not service.is_loaded:
        raise HTTPException(status_code=409, detail="no project loaded")
    return service


@dataclass
class Agent:
    """The acting agent's identity, carried on every request via the
    X-Agent-Id / X-Agent-Alias headers (see require_agent). `tag()` is the
    shape stored on focused/finished items for attribution."""

    id: str
    alias: str

    def tag(self) -> dict:
        return {"id": self.id, "alias": self.alias}


def require_agent(
    x_agent_id: str | None = Header(default=None),
    x_agent_alias: str | None = Header(default=None),
) -> Agent:
    """Enforce that the caller identifies itself. Applied to the whole API
    surface except the two bootstrap endpoints (GET /help, GET /agent-id).
    The values are not validated for shape — the agent persists and replays
    them — only required to be present and non-blank."""
    if not (x_agent_id or "").strip() or not (x_agent_alias or "").strip():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "agent_identity_required",
                "message": (
                    "Every ShiftGrid API request (except GET /api/v1/help and "
                    "GET /api/v1/agent-id) must carry the X-Agent-Id and X-Agent-Alias "
                    "headers so concurrent agents can be told apart and their work "
                    "attributed. If you don't have an id yet, GET /api/v1/agent-id to "
                    "obtain one and learn how to persist it."
                ),
            },
        )
    return Agent(id=x_agent_id.strip(), alias=x_agent_alias.strip())

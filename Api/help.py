"""Agent-facing API front door (`GET /api/v1/help`).

Returns a single `{"help": "<everything>"}` payload. The body is a template
string (markdown): a short default ships built in (see `help.md`), or a caller
can inject its own via the factory's `help_template` knob.

A template may contain the token `{{ENDPOINTS}}`, which is replaced at request
time with a flat, token-efficient per-endpoint listing rendered from the live
OpenAPI spec. Templates without the token (e.g. the default) get no
listing.
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

ENDPOINTS_TOKEN = "{{ENDPOINTS}}"


# Served by GET /api/v1/agent-id alongside a freshly minted id. Explains why an
# identity is needed, how to persist it, and the subagent variant. Kept as a
# constant (not a template file) because it is short and self-contained.
_AGENT_ID_HELP = """\
# Your ShiftGrid agent identity

ShiftGrid is worked **concurrently** — a lead AI, its subagents, and the human
operator all act on one shared project. So every request must say *who* is
acting, so work can be claimed (avoiding two agents on the same thing) and
attributed once done.

**Every API request except `GET /api/v1/help` and `GET /api/v1/agent-id` must
carry two headers:**

    X-Agent-Id: <your id>
    X-Agent-Alias: <your alias>

Calls without them are rejected (HTTP 400).

## First, check for an existing identity
Before using the `agent_id` served with this response, look for a
`.shiftgrid-agent-id` file in your current working directory.

- **If it exists**, use it as-is: line 1 is your id, line 2 is your alias. Do
  NOT mint a new one — you were probably just restarted and should keep the
  same identity.
- **If it does not exist**, adopt the `agent_id` from this response and:
  1. Think up a funny alias, **4–7 characters** long (e.g. `zorp`, `mr-byte`).
  2. Write the file with exactly two lines —

         <agent_id>
         <alias>

Then send both on every request via the headers above.

## Subagents
If you know you are a **subagent** (shorter-lived, more temporal), do not touch
`.shiftgrid-agent-id`. Instead create your own file at
`/tmp/.shiftgrid-agent-id-<uuid>` with the same two lines (id on line 1, alias
on line 2), and use those values in your headers.
"""

_DEFAULT_TEMPLATE = (Path(__file__).resolve().parent / "help.md").read_text(encoding="utf-8")


class HelpResponse(BaseModel):
    """The whole front door, as one string. `help` carries the configured
    template with the endpoint listing (if requested) already spliced in."""

    help: str


class AgentIdResponse(BaseModel):
    """A freshly minted agent id plus the instructions for using and persisting
    it. `agent_id` is a 5-char hex token; `help` is markdown."""

    agent_id: str
    help: str


# Per-endpoint listing for {{ENDPOINTS}}: rendered from the OpenAPI spec on
# first request, then cached for the process lifetime.

_api_description_cache: str | None = None


def _ad_resolve_ref(schema: dict, root: dict) -> tuple[dict, str | None]:
    ref = schema.get("$ref")
    if not ref or not ref.startswith("#/components/schemas/"):
        return schema, None
    name = ref.rsplit("/", 1)[-1]
    return root.get("components", {}).get("schemas", {}).get(name, {}), name


def _ad_type_str(schema: dict, root: dict) -> str:
    if not schema:
        return "any"
    if schema.get("format") == "binary":
        return "file"
    if "$ref" in schema:
        _, name = _ad_resolve_ref(schema, root)
        return name or "object"
    if "anyOf" in schema or "oneOf" in schema:
        parts = [_ad_type_str(s, root) for s in schema.get("anyOf") or schema.get("oneOf") or []]
        non_null = [p for p in parts if p != "null"]
        if len(non_null) == 1 and "null" in parts:
            return non_null[0] + "?"
        return "|".join(parts)
    if "allOf" in schema:
        return "&".join(_ad_type_str(s, root) for s in schema["allOf"])
    t = schema.get("type")
    if t == "array":
        return f"list[{_ad_type_str(schema.get('items', {}), root)}]"
    if t == "object":
        return "object"
    if isinstance(t, list):
        return "|".join(t)
    return t or "any"


def _ad_fields(schema: dict, root: dict) -> str:
    resolved, _ = _ad_resolve_ref(schema, root)
    props = resolved.get("properties") or {}
    if not props:
        return _ad_type_str(resolved, root)
    required = set(resolved.get("required") or [])
    parts = []
    for name, sub in props.items():
        sep = ":" if name in required else "?:"
        parts.append(f"{name}{sep}{_ad_type_str(sub, root)}")
    return ", ".join(parts)


def _ad_params(operation: dict, root: dict) -> str:
    qs = []
    for p in operation.get("parameters") or []:
        if p.get("in") != "query":
            continue
        sep = "=" if p.get("required") else "?="
        qs.append(f"{p['name']}{sep}{_ad_type_str(p.get('schema') or {}, root)}")
    return "&".join(qs)


def _ad_response_type(operation: dict, root: dict) -> str | None:
    responses = operation.get("responses") or {}
    for code in ("200", "201", "default"):
        resp = responses.get(code)
        if not resp:
            continue
        content = (resp.get("content") or {}).get("application/json")
        if not content:
            continue
        return _ad_type_str(content.get("schema") or {}, root)
    return None


def _ad_body(operation: dict, root: dict) -> str | None:
    body = operation.get("requestBody")
    if not body:
        return None
    content = body.get("content") or {}
    json_c = content.get("application/json")
    if json_c:
        return _ad_fields(json_c.get("schema") or {}, root)
    # File-upload routes (raw captures) carry a multipart/form-data body, not JSON.
    # Render its form fields too, prefixed so the agent knows to send a real upload.
    mp = content.get("multipart/form-data")
    if mp:
        return "multipart/form-data " + _ad_fields(mp.get("schema") or {}, root)
    return None


def _top_segment(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else ""


def _render_api_description(spec: dict) -> str:
    """Endpoint lines grouped by top-level path segment. Each line:

        METHOD /path?query  body: ...  -> ResponseType  - summary
    """
    lines: list[str] = []
    paths = spec.get("paths") or {}
    prev_group: str | None = None
    for path in sorted(paths):
        methods = paths[path]
        group = _top_segment(path)
        first_in_group = group != prev_group
        prev_group = group
        emitted_for_path = False
        for method in ("get", "post", "put", "patch", "delete"):
            op = methods.get(method)
            if not op:
                continue
            if first_in_group and not emitted_for_path and lines:
                lines.append("")
            emitted_for_path = True
            line = f"{method.upper()} {path}"
            qs = _ad_params(op, spec)
            if qs:
                line += f"?{qs}"
            body = _ad_body(op, spec)
            if body:
                line += f"  body: {body}"
            resp = _ad_response_type(op, spec)
            if resp:
                line += f"  → {resp}"
            note = (op.get("summary") or (op.get("description") or "")).strip()
            if note:
                line += f"  - { ' '.join(note.split()) }"
            lines.append(line)
    return "\n".join(lines)


def _endpoint_listing(spec_provider) -> str:
    global _api_description_cache
    if _api_description_cache is None:
        _api_description_cache = _render_api_description(spec_provider())
    return _api_description_cache


def build_help_router(help_template: str = _DEFAULT_TEMPLATE) -> APIRouter:
    """Router exposing `GET /help` for the given template. The endpoint listing
    is spliced in lazily only when the template uses the `{{ENDPOINTS}}` token."""
    router = APIRouter()

    @router.get("/help", response_model=HelpResponse)
    def api_help(request: Request) -> HelpResponse:
        """Front door for agents. Always start here."""
        body = help_template
        if ENDPOINTS_TOKEN in body:
            body = body.replace(ENDPOINTS_TOKEN, _endpoint_listing(request.app.openapi))
        return HelpResponse(help=body)

    @router.get("/agent-id", response_model=AgentIdResponse)
    def api_agent_id() -> AgentIdResponse:
        """Mint a new 5-char agent id and explain how to persist and send it.
        One of the two endpoints (with /help) that need no identity headers."""
        return AgentIdResponse(agent_id=uuid.uuid4().hex[:5], help=_AGENT_ID_HELP)

    return router

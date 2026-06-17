import copy
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from a2wsgi import WSGIMiddleware
from fastapi import Depends, FastAPI
from flask import Flask, g
from jinja2 import ChoiceLoader, FileSystemLoader

from Api import api_router, register_exception_handlers
from Api.deps import require_agent
from Api.help import build_help_router
from Application import ProjectService
from Infrastructure import JsonProjectRepository, load_workflows, load_checklists
from Web import web_bp


API_DESCRIPTION = """
ShiftGrid is a workflow/checklist-driven pentest project runner.
"""


def _api_port() -> int:
    return int(os.environ.get("API_PORT", 8001))


# live.js polls these constantly; suppress their access-log lines so they don't
# drown the log in near-identical 200s.
_QUIET_ACCESS_PATHS = frozenset({"/project/heartbeat"})


class _QuietAccessFilter(logging.Filter):
    """Drop access-log records for _QUIET_ACCESS_PATHS.

    uvicorn's record.args is (client_addr, method, full_path, http_version,
    status_code); the path is index 2."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2]).split("?", 1)[0]
            if path in _QUIET_ACCESS_PATHS:
                return False
        return True


def _quiet_log_config() -> dict:
    """uvicorn's default log config plus _QuietAccessFilter on the access handler."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["quiet_access"] = {
        "()": "factory._QuietAccessFilter",
    }
    access_handler = config.get("handlers", {}).get("access")
    if access_handler is not None:
        access_handler.setdefault("filters", []).append("quiet_access")
    return config


def _build_api(service: ProjectService, extra_api_routers=(), help_template=None) -> FastAPI:
    """The agent API (/api/v1) as a FastAPI app. Built twice — operator base and
    the standalone agent server — sharing one `service`.

    help_template: body for GET /api/v1/help; None -> built-in default (Api/help.md)."""
    api = FastAPI(
        title="ShiftGrid API",
        description=API_DESCRIPTION,
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    api.state.service = service
    register_exception_handlers(api)
    api.include_router(api_router, prefix="/api/v1", dependencies=[Depends(require_agent)])
    # help router has no agent guard so agents can bootstrap an id first
    # (GET /help, GET /agent-id); every other route requires require_agent.
    help_router = build_help_router(help_template) if help_template is not None else build_help_router()
    api.include_router(help_router, prefix="/api/v1")
    for r in extra_api_routers:
        api.include_router(r, prefix="/api/v1", dependencies=[Depends(require_agent)])
    return api


def _build_flask(
    service: ProjectService,
    root: Path,
    default_projects_dir: str,
    extra_blueprints=(),
    extra_template_dirs=(),
    extra_nav=(),
    single_project=False,
    project_home="web.workflow",
    projects_view="web.landing",
) -> Flask:
    flask_app = Flask(
        __name__,
        template_folder=str(root / "Web" / "templates"),
        static_folder=str(root / "Web" / "static"),
    )
    if extra_template_dirs:
        # Searched first so a caller can override a built-in template by name.
        flask_app.jinja_loader = ChoiceLoader(
            [FileSystemLoader([str(d) for d in extra_template_dirs]), flask_app.jinja_loader]
        )
    flask_app.jinja_env.globals["extra_nav"] = extra_nav
    flask_app.jinja_env.globals["single_project"] = single_project
    # Templates build the cross-port "Agent API docs" link from this.
    flask_app.jinja_env.globals["api_port"] = _api_port()
    flask_app.secret_key = os.environ.get("SECRET_KEY", "dev-shiftgrid")
    flask_app.config["PROJECT_SERVICE"] = service
    flask_app.config["DEFAULT_PROJECTS_DIR"] = default_projects_dir
    flask_app.config["PROJECT_HOME"] = project_home
    flask_app.config["PROJECTS_VIEW"] = projects_view
    # Without this, each nav does a 304 per /static file and <head> scripts block
    # on them (~500ms/nav). 1-day cache; hard-refresh (Ctrl+Shift+R) after editing
    # style.css/live.js in dev.
    flask_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

    def _fmt_dt(ts):
        try:
            ts_int = int(ts or 0)
        except (TypeError, ValueError):
            return ""
        if ts_int <= 0:
            return ""
        # Stored as UTC epoch ints; render in the project's timezone, falling
        # back to Berlin when none is loaded or the name is unknown.
        tz_name = "Europe/Berlin"
        project = service.current if service.is_loaded else None
        if project is not None:
            tz_name = project.timezone or tz_name
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("Europe/Berlin")
        return datetime.fromtimestamp(ts_int, tz=timezone.utc).astimezone(tz).strftime("%H:%M - %d-%m-%Y")

    flask_app.jinja_env.filters["fmt_dt"] = _fmt_dt

    flask_app.register_blueprint(web_bp)
    for bp in extra_blueprints:
        flask_app.register_blueprint(bp)

    @flask_app.before_request
    def _perf_start():
        g._perf_start = time.perf_counter()

    @flask_app.after_request
    def _perf_server_timing(resp):
        start = getattr(g, "_perf_start", None)
        if start is not None:
            dur_ms = (time.perf_counter() - start) * 1000
            resp.headers["Server-Timing"] = f'total;dur={dur_ms:.1f}'
        return resp

    return flask_app


def create_app(
    project_service_cls=ProjectService,
    extra_blueprints=(),
    extra_template_dirs=(),
    extra_workflow_dirs=(),
    extra_checklist_dirs=(),
    extra_nav=(),
    single_project=False,
    projects_dirname="shiftgrid-projects",
    extra_api_routers=(),
    help_template=None,
    lock_scope_on_create=False,
    notes_template="",
    project_home="web.workflow",
    projects_view="web.landing",
) -> FastAPI:
    root = Path(__file__).parent

    workflows = load_workflows(root / "Workflows")
    for d in extra_workflow_dirs:
        workflows.update(load_workflows(Path(d)))
    checklists = load_checklists(root / "Checklists")
    for d in extra_checklist_dirs:
        checklists.update(load_checklists(Path(d)))
    missing = [wf.checklist_id for wf in workflows.values() if wf.checklist_id not in checklists]
    if missing:
        raise RuntimeError(f"workflow references missing checklist(s): {missing}")

    default_base = Path(os.environ.get("PROJECTS_DIR") or (Path.home() / projects_dirname))
    host_prefix = os.environ.get("HOST_PROJECTS_DIR")
    repository = JsonProjectRepository()
    service = project_service_cls(
        default_base,
        workflows,
        checklists,
        repository=repository,
        host_prefix=host_prefix,
        lock_scope_on_create=lock_scope_on_create,
        notes_template=notes_template,
    )
    # Display path: host-side folder under Docker (HOST_PROJECTS_DIR), else the
    # resolved local path.
    default_projects_dir = host_prefix or str(default_base.expanduser().resolve())

    # Single-project mode: open an existing project at startup so the app comes
    # up already loaded (no project -> landing page handles creation).
    if single_project:
        existing = service.find_existing_project()
        if existing is not None:
            try:
                service.open(existing)
            except (ValueError, OSError):
                pass

    flask_app = _build_flask(
        service,
        root,
        default_projects_dir,
        extra_blueprints=extra_blueprints,
        extra_template_dirs=extra_template_dirs,
        extra_nav=extra_nav,
        single_project=single_project,
        project_home=project_home,
        projects_view=projects_view,
    )

    # Agent-facing API: exposes only /api/v1, no operator UI.
    api_app = _build_api(service, extra_api_routers, help_template=help_template)

    # Operator UI only — the API is deliberately NOT mounted here; operator and
    # agent both reach it on the API port. Carries `service` + api_app so run()
    # can serve both from one process.
    operator_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    operator_app.state.service = service
    operator_app.state.api_app = api_app
    operator_app.mount("/", WSGIMiddleware(flask_app))
    return operator_app


def run(app: FastAPI) -> None:
    """Serve UI and agent API on two ports from one process, sharing one
    in-memory ProjectService. UI -> PORT (8000); API -> API_PORT (8001).

    Isolation is the port split, so launch with `python app.py`, not
    `uvicorn app:app` (which binds only the UI port and exposes no API)."""
    import asyncio
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    web_port = int(os.environ.get("PORT", 8000))
    api_port = _api_port()
    log_config = _quiet_log_config()
    servers = [
        uvicorn.Server(uvicorn.Config(app, host=host, port=web_port, log_config=log_config)),
        uvicorn.Server(uvicorn.Config(app.state.api_app, host=host, port=api_port, log_config=log_config)),
    ]

    async def _serve():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(_serve())

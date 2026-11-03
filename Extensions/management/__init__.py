"""Management extension: multi-project management, the Scope hub and credentials.

Adds the Projects page (list / switch / backup / delete, custom per-project
workflow + checklist), the Scope page (scope, project details, timezone,
notes-required toggle, credentials), the credentials API, and the admin-agent
projects API (gated by ALLOW_ADMIN_AGENTS, default off).

Wired in by `app.py` via `create_app(**factory_kwargs())`.
"""
from pathlib import Path

from .credentials_api import router as credentials_router
from .managed_project_service import ManagedProjectService
from .management_routes import management_bp
from .nav import EXTRA_NAV
from .projects_api import router as projects_router, admin_agents_enabled
from .scope_routes import scope_bp

_HERE = Path(__file__).resolve().parent


def factory_kwargs() -> dict:
    """The `create_app` knobs this extension sets. Lists are fresh copies, so a
    caller stacking another extension on top can extend them in place."""
    return dict(
        project_service_cls=ManagedProjectService,
        extra_blueprints=[management_bp, scope_bp],
        extra_api_routers=[credentials_router, projects_router],
        extra_template_dirs=[str(_HERE / "templates")],
        extra_workflow_dirs=[],
        extra_checklist_dirs=[],
        extra_nav=list(EXTRA_NAV),
        notes_template=(_HERE / "notes-template.md").read_text(encoding="utf-8"),
        project_home="scope_bp.scope",
        projects_view="management.projects",
    )


def startup_banner(edition: str = "") -> None:
    """Print which edition/mode is starting, so it's obvious in `docker compose up`.
    Admin mode gets a red warning — agents can then create/switch projects and set
    scope over the API."""
    bar = "=" * 62
    print(f"\n\n{bar}")
    print(f"  ShiftGrid{f'  --  {edition}' if edition else ''}")
    if admin_agents_enabled():
        red, reset = "\033[1;31m", "\033[0m"
        print(f"{red}  !!!  ADMIN AGENTS ENABLED  !!!{reset}")
        print(f"{red}  Agents can CREATE / SWITCH projects and SET SCOPE via the API.{reset}")
    else:
        print("  Admin agents: disabled  (agents cannot create/switch projects)")
    print(f"{bar}\n\n", flush=True)

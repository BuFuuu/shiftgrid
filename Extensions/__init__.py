"""Extensions: optional feature packages layered onto the core via `create_app`.

The core (`factory.create_app`) knows nothing about any extension. An extension
is a package that hands the factory's injection knobs what it contributes —
typically through a `factory_kwargs()` function returning a dict:

    project_service_cls    ProjectService subclass (extra service behaviour)
    extra_blueprints       Flask blueprints (operator UI pages)
    extra_api_routers      FastAPI routers, mounted under /api/v1 behind require_agent
    extra_template_dirs    template folders, searched before the core's (override by name)
    extra_workflow_dirs    additional Workflows/*.json folders
    extra_checklist_dirs   additional Checklists/*.json folders
    extra_nav              sidebar items: {endpoint, label, section, icon}
    help_template          body for GET /api/v1/help
    notes_template, project_home, projects_view, single_project, lock_scope_on_create

`app.py` composes them. To stack another extension on top, merge its dict over
(or extend the lists of) the one it builds on — see `management` for a full
example.
"""

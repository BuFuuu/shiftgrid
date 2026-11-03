"""Extra sidebar nav items contributed by the management extension (Projects, Scope).

Passed to the factory's `extra_nav` knob; the shared nav template renders each
item generically (endpoint + label + icon), so it carries no reference to these
endpoints itself.
"""

_PROJECTS_ICON = (
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
    '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" '
    'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>'
    '<path d="M3 10h18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
)

_SCOPE_ICON = (
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/>'
    '<circle cx="12" cy="12" r="5" fill="none" stroke="currentColor" stroke-width="1.8"/>'
    '<circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>'
)

EXTRA_NAV = [
    {"endpoint": "management.projects", "label": "Projects", "section": "projects", "icon": _PROJECTS_ICON},
    {"endpoint": "scope_bp.scope", "label": "Scope", "section": "scope", "icon": _SCOPE_ICON},
]

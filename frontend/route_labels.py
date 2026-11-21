"""Capability id to workflow-tab mapping and assurance labels (pure, no streamlit)."""

WORKFLOW_BY_CAPABILITY = {
    'co2_dragster': 'CO2 dragster',
    'regen_chamber': 'Deterministic rocket nozzle',
    'glider_cem': 'Physics-based glider CEM',
    'rectangular_cantilever': 'Checked design',
    'rectangular_plate': 'Checked design',
}
V1_WORKFLOW = 'Checked design'
_V1_NAMES = {**WORKFLOW_BY_CAPABILITY, 'rectangular_cantilever': 'Checked beam', 'rectangular_plate': 'Checked plate'}

_ASSURANCE = {'checked_design': 'Checked design', 'unverified_geometry': 'Unverified geometry',
              'geometry_checks_only': 'Geometry checks only', 'screening_only': 'Screening only'}


def workflow_for(event: dict):
    """Workflow radio label for a ``route`` event, or None when it names no known capability."""
    key = event.get('capability_id') or event.get('workflow')
    return WORKFLOW_BY_CAPABILITY.get(key) if isinstance(key, str) else None


def assurance_label(value):
    return _ASSURANCE.get(value) if isinstance(value, str) else None


def route_target(event: dict, prompt=None):
    """Where a route event goes: the v1 view for a named capability, else the legacy tab.

    prompt is returned only when the backend attached a rules draft, so the v1 view asks
    for a rules draft from the same prompt; otherwise the form starts empty.
    """
    cid = event.get("capability_id") if isinstance(event, dict) else None
    if isinstance(cid, str) and cid in _V1_NAMES:
        hinted = cid != "rectangular_cantilever" and isinstance(event.get("draft"), dict)
        wanted = prompt if hinted and isinstance(prompt, str) and prompt.strip() else None
        return dict(workflow=V1_WORKFLOW, capability_id=cid, prompt=wanted,
                    notice=f"Checked design: {_V1_NAMES[cid]}")
    legacy = workflow_for(event) if isinstance(event, dict) and not cid else None
    if legacy:
        return dict(workflow=legacy, capability_id=None, prompt=None, notice=f"Checked design: {legacy}")
    return None

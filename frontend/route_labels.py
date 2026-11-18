"""Capability id to workflow-tab mapping and assurance labels (pure, no streamlit)."""

WORKFLOW_BY_CAPABILITY = {
    'co2_dragster': 'CO2 dragster',
    'regen_chamber': 'Deterministic rocket nozzle',
    'glider_cem': 'Physics-based glider CEM',
    'rectangular_cantilever': 'Checked beam',
}

_ASSURANCE = {'checked_design': 'Checked design', 'unverified_geometry': 'Unverified geometry'}


def workflow_for(event: dict):
    """Workflow radio label for a ``route`` event, or None when it names no known capability."""
    key = event.get('capability_id') or event.get('workflow')
    return WORKFLOW_BY_CAPABILITY.get(key) if isinstance(key, str) else None


def assurance_label(value):
    return _ASSURANCE.get(value) if isinstance(value, str) else None

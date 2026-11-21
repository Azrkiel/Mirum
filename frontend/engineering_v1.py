"""Pure helpers for the checked-design v1 view (no streamlit, no network).

initial_draft_body drafts generators through form or rules mode only and never builds an
'ai' draft. AI drafts are built only by ai_draft_body, and only when the server reports
ai_enabled. Review fields of a generator are the leaves of its
normalized specification, computed here by the same rule the backend uses.
"""
from copy import deepcopy
import json
import math

from engineering_state import checked_bytes

BEAM_ID = 'rectangular_cantilever'
PLATE_ID = 'rectangular_plate'
MAX_INPUT_BYTES = 16 * 1024
SPEC_PREFIX = 'specification.'
CHECKED_LABEL = 'Checked design'
DOWNLOADS = {
    'zip': ('package.zip', 'application/zip'), 'brief': ('brief.json', 'application/json'),
    'spec': ('selected_spec.json', 'application/json'), 'source': ('model.py', 'text/plain'),
    'step': ('model.step', 'application/step'), 'glb': ('preview.glb', 'model/gltf-binary'),
    'attempts': ('attempts.json', 'application/json'), 'report': ('report.json', 'application/json'),
    'markdown': ('report.md', 'text/markdown'), 'manifest': ('manifest.json', 'application/json'),
    'generator-spec': ('generator-spec.json', 'application/json'),
    'generator-report': ('generator-report.json', 'application/json'),
    'generator-body-step': ('generator-body.step', 'application/step'),
    'generator-preview-glb': ('generator-preview.glb', 'model/gltf-binary'),
    'generator-cleaned-stl': ('generator-cleaned.stl', 'model/stl'),
    'generator-spec-canonical': ('generator-spec.canonical.json', 'application/json'),
    'generator-bundle-zip': ('generator-bundle.zip', 'application/zip'),
    'generator-result': ('generator-result.json', 'application/json'),
}


def capability_options(metadata):
    entries = metadata.get('capabilities') if isinstance(metadata, dict) else None
    options = []
    for e in entries or []:
        if not isinstance(e, dict) or not isinstance(e.get('id'), str) or not isinstance(e.get('version'), str):
            continue
        options.append(dict(id=e['id'], label=str(e.get('label') or e['id']),
                            kind='beam' if e['id'] == BEAM_ID else 'generator', available=bool(e.get('available'))))
    return options


def find_capability(metadata, capability_id):
    for e in (metadata.get('capabilities') if isinstance(metadata, dict) else None) or []:
        if isinstance(e, dict) and e.get('id') == capability_id:
            return e
    return None


def is_generator(entry):
    return entry.get('id') != BEAM_ID


def capability_reference(entry):
    return dict(id=entry['id'], version=entry['version'],
                numerical_policy_version=entry['numerical']['version'],
                resource_policy_version=entry['resources']['version'])


def initial_draft_body(entry, *, prompt=None, specification=None):
    """POST /drafts body: rules when a prompt is given, otherwise form. Never 'ai'."""
    if not is_generator(entry):
        raise ValueError('The beam uses its own form')
    cid = entry['id']
    if isinstance(prompt, str) and prompt.strip() and specification is None:
        return dict(mode='rules', capability=cid, input={}, prompt=prompt)
    payload = dict(capability=capability_reference(entry))
    if specification is not None:
        payload['specification'] = specification
    return dict(mode='form', capability=cid, input=payload)


def _kind(value):
    if isinstance(value, bool): return 'bool'
    if isinstance(value, int): return 'int'
    if isinstance(value, float): return 'float'
    if isinstance(value, str): return 'str'
    return 'json'


def leaf_fields(specification, prefix=''):
    """Leaves in the backend's order: sorted keys; non-empty dicts recurse; lists, nulls, {} are leaves."""
    fields = []
    for key in sorted(specification):
        value, path = specification[key], f'{prefix}{key}'
        if isinstance(value, dict) and value:
            fields += leaf_fields(value, path + '.')
        else:
            fields.append(dict(path=path, kind=_kind(value), value=value))
    return fields


def review_field_names(specification):
    return tuple(SPEC_PREFIX + f['path'] for f in leaf_fields(specification))


def text_for(field):
    """Text shown in the editor for a non-boolean leaf."""
    if field['kind'] == 'str': return field['value']
    return json.dumps(field['value'], allow_nan=False)


def _reject_constant(name):
    raise ValueError('Non-finite number')


def _parse(kind, raw):
    if kind == 'bool':
        if not isinstance(raw, bool): raise ValueError('Expected true or false')
        return raw
    if not isinstance(raw, str): raise ValueError('Expected text')
    text = raw.strip()
    if kind == 'str': return raw
    if kind == 'int':
        try: return int(text)
        except ValueError: raise ValueError('Expected a whole number') from None
    if kind == 'float':
        try: value = float(text)
        except ValueError: raise ValueError('Expected a number') from None
        if not math.isfinite(value): raise ValueError('Expected a finite number')
        return value
    try: return json.loads(text, parse_constant=_reject_constant)
    except ValueError: raise ValueError('Expected valid JSON') from None


def edited_specification(specification, edits):
    """Copy of ``specification`` with each edited leaf parsed per its original kind."""
    result = deepcopy(specification)
    kinds = {f['path']: f['kind'] for f in leaf_fields(specification)}
    for path, raw in edits.items():
        if path not in kinds: raise ValueError('Unknown field')
        node = result
        parts = path.split('.')
        for part in parts[:-1]: node = node[part]
        node[parts[-1]] = _parse(kinds[path], raw)
    return result


def parse_specification_json(text):
    if len(text.encode()) > MAX_INPUT_BYTES: raise ValueError('Specification exceeds 16 KiB')
    try: value = json.loads(text, parse_constant=_reject_constant)
    except ValueError: raise ValueError('Specification is not valid JSON') from None
    if not isinstance(value, dict): raise ValueError('Specification must be a JSON object')
    return value


def draft_specification(prepared):
    """The normalized specification when the draft has content, otherwise the typed input's."""
    content = prepared['draft'].get('content')
    if content: return content['specification']
    spec = (prepared.get('input') or {}).get('specification')
    return spec if isinstance(spec, dict) else None


def patch_body(prepared, specification):
    return dict(expected_revision=prepared['draft']['revision'],
                input=dict(capability=deepcopy(prepared['input']['capability']), specification=specification))


def review_body(prepared, *, reviewed_at, reviewed_by='local browser reviewer'):
    draft, content = prepared['draft'], prepared['draft'].get('content')
    if not content or prepared.get('issues'):
        raise ValueError('Resolve the listed issues before review')
    return dict(draft_id=draft['draft_id'], draft_revision=draft['revision'], brief_sha256=prepared['brief_sha256'],
                acknowledged_fields=list(review_field_names(content['specification'])),
                reviewed_at=reviewed_at, reviewed_by=reviewed_by)


def job_body(brief):
    return dict(brief_id=brief['brief_id'], idempotency_key='checked-ui:' + brief['brief_id'])


def create_generator_draft(client, entry, *, prompt=None):
    """(prepared, 'rules' | 'empty'). A refused rules prompt (422) falls back to an empty form draft."""
    if isinstance(prompt, str) and prompt.strip():
        try:
            return client.request('POST', '/drafts', initial_draft_body(entry, prompt=prompt)), 'rules'
        except ValueError as error:
            if getattr(error, 'status', None) != 422: raise
    return client.request('POST', '/drafts', initial_draft_body(entry)), 'empty'


def finalize(client, prepared, *, reviewed_at):
    return client.request('POST', '/briefs', review_body(prepared, reviewed_at=reviewed_at))


def submit(client, brief):
    return client.request('POST', '/jobs', job_body(brief))


def claim_of(entry, job=None):
    """None = checked design (the beam has no claim). The job's own claim wins (the capability list may not carry
    one); a generator without any claim gets the weakest one."""
    if not isinstance(entry, dict) or entry.get('id') == BEAM_ID: return None
    for source in (job, entry):
        claim = source.get('claim') if isinstance(source, dict) else None
        if claim in ('geometry_checks_only', 'screening_only'): return claim
        if claim == 'checked_design' and entry.get('id') == PLATE_ID: return None
    return 'screening_only'


def accepted_label(entry, job=None):
    return {None: CHECKED_LABEL, 'geometry_checks_only': 'Geometry checks passed', 'screening_only': 'Screened'}[claim_of(entry, job)]


def result_heading(entry, job=None):
    return {None: 'Checked result', 'geometry_checks_only': 'Geometry check result', 'screening_only': 'Screening result'}[claim_of(entry, job)]


_NOT_EVALUATED = 'Standards conformity, manufactured behavior and material qualification: not evaluated.'


def outcome_message(entry, job):
    """(streamlit kind, text) for a terminal job; wording follows the capability claim."""
    claim = claim_of(entry, job)
    if job.get('outcome') == 'accepted':
        if claim is None: text = 'Accepted under the declared digital model. ' + _NOT_EVALUATED
        elif claim == 'geometry_checks_only':
            text = 'Geometry checks passed under the declared digital model; no performance or competition claim. ' + _NOT_EVALUATED
        else:
            text = 'Screened under the declared digital model: not accepted for manufacture; not engineering certified. ' + _NOT_EVALUATED
        return 'success', text
    noun = 'screened' if claim == 'screening_only' else 'accepted'
    if job.get('outcome') == 'rejected':
        return 'warning', f'A required check did not pass. The report gives the measured reasons; no {noun} geometry is offered.'
    if job.get('lifecycle') == 'cancelled':
        return 'warning', f'Cancelled before terminal commit. No {noun} package is offered.'
    return 'error', f'A required execution or publication stage is unavailable. No {noun} package is offered.'


def attempt_outcome(entry, job, attempt):
    """An attempt row's outcome, relabelled from the claim like the state line (never a bare 'accepted')."""
    outcome = attempt.get('outcome')
    if outcome == 'accepted':
        outcome = {None: 'accepted', 'geometry_checks_only': 'geometry checks passed', 'screening_only': 'screened'}[claim_of(entry, job)]
    return str(outcome)


def state_line(entry, job):
    """The 'State | engineering outcome' line; the raw job outcome is relabelled from the claim."""
    outcome = job.get('outcome')
    if outcome == 'accepted':
        outcome = {None: 'accepted', 'geometry_checks_only': 'geometry checks passed', 'screening_only': 'screened'}[claim_of(entry, job)]
    return 'State: ' + str(job.get('lifecycle')) + ' | engineering outcome: ' + str(outcome)


def result_view(job, entry=None):
    accepted = job.get('outcome') == 'accepted' and job.get('delivery') == 'available'
    downloads = []
    if job.get('delivery') == 'available':
        offered = job.get('artifacts') or {}
        downloads = [dict(format=f, name=n, mime=m) for f, (n, m) in DOWNLOADS.items() if offered.get(f) is True]
    return dict(label=accepted_label(entry, job) if accepted else None, outcome=job.get('outcome'),
                lifecycle=job.get('lifecycle'), downloads=downloads)


def verified_download(job, format, value):
    if format not in DOWNLOADS: raise ValueError('Unknown download format')
    return checked_bytes(job, DOWNLOADS[format][0], value)


MAX_AI_PROMPT = 2000
_AI_UNAVAILABLE = 'AI preparation unavailable; typed form remains available.'
_AI_NOT_AVAILABLE = 'AI draft preparation is not available; the typed form remains available.'


def ai_enabled(metadata):
    return isinstance(metadata, dict) and metadata.get('ai_enabled') is True


def ai_available(metadata):
    return isinstance(metadata, dict) and metadata.get('ai_available') is True


def ai_draft_body(entry, prompt):
    if not is_generator(entry): raise ValueError('The beam uses its own form')
    if not isinstance(prompt, str) or not prompt.strip(): raise ValueError('Describe the request')
    if len(prompt) > MAX_AI_PROMPT: raise ValueError('Request exceeds 2000 characters')
    return dict(mode='ai', capability=entry['id'], input={}, prompt=prompt)


def _provenance(prepared):
    draft = prepared.get('draft') if isinstance(prepared, dict) else None
    prov = draft.get('provenance') if isinstance(draft, dict) else None
    return prov if isinstance(prov, dict) else {}


def ai_origins(prepared):
    entries = _provenance(prepared).get('field_origins')
    if not isinstance(entries, list): return {}
    return {e['field']: e['origin'] for e in entries
            if isinstance(e, dict) and isinstance(e.get('field'), str) and isinstance(e.get('origin'), str)}


def is_ai_draft(prepared):
    return _provenance(prepared).get('input_mode') == 'ai'


def unverified_fields(prepared):
    return tuple(f for f, o in ai_origins(prepared).items() if o == 'ai_proposed')


def review_ack_required(prepared):
    return bool(unverified_fields(prepared))


def field_label(path, origins):
    origin = (origins or {}).get(SPEC_PREFIX + path)
    if origin == 'ai_proposed': return path + ' (AI-proposed, unverified)'
    if origin == 'policy': return path + ' (policy default)'
    return path


def ai_status(prepared):
    draft = prepared['draft']
    states = [i.get('state') for i in prepared.get('issues') or [] if isinstance(i, dict)]
    if 'unsupported' in states: return 'unsupported'
    if draft.get('content') is None or 'clarification_required' in states: return 'clarification_required'
    return 'proposed'


def ai_failure_message(error):
    body = getattr(error, 'body', None)
    if isinstance(body, dict) and body.get('state') == 'unavailable': return _AI_UNAVAILABLE
    return _AI_NOT_AVAILABLE


def propose_ai_draft(client, entry, prompt):
    body = ai_draft_body(entry, prompt)
    try:
        prepared = client.request('POST', '/drafts', body)
    except Exception as error:
        if getattr(error, 'status', None) != 503: raise
        err_body = getattr(error, 'body', None)
        kept = err_body.get('prepared') if isinstance(err_body, dict) else None
        return dict(status='unavailable', prepared=kept if isinstance(kept, dict) else None,
                    message=ai_failure_message(error))
    return dict(status=ai_status(prepared), prepared=prepared, message=None)


# ---------------------------------------------------------------- rectangular_plate (typed form, result view)

PLATE_ASSUMPTIONS = 'kirchhoff_uniform_pressure_uniform_edges_linear_elastic'
PLATE_NUMBERS = (  # (field, label) in form order; edge condition sits between width and pressure
    ('length_a_mm', 'Length a (mm)'), ('width_b_mm', 'Width b (mm)'), ('pressure_mpa', 'Pressure q (MPa)'),
    ('youngs_modulus_mpa', "Young's modulus E (MPa)"), ('poissons_ratio', "Poisson's ratio nu"),
    ('allowable_stress_mpa', 'Allowable stress (MPa)'), ('max_deflection_mm', 'Max deflection (mm)'),
    ('dimensional_tolerance_mm', 'Dimensional tolerance (mm)'))
PLATE_LABELS = dict(PLATE_NUMBERS, candidate_thicknesses_mm='Candidate thickness', material_reference='Material reference')
PLATE_EDGES = {'simply_supported': 'Simply supported', 'clamped': 'Clamped'}
PLATE_SOURCES = {'illustrative': 'Illustrative', 'user_supplied': 'User supplied', 'documented': 'Documented'}
PLATE_MODES = {'fixed_design': 'Fixed thickness', 'bounded_thickness': 'Bounded thickness (1-4 candidates)'}
PLATE_SHEAR_NOTE = 'The checked deflection includes the shear allowance w_check = w_K (1 + 20 (h/b)^2).'
PLATE_CORNER_NOTE = ('corners held down: the simply supported edges and corners are held against lift; '
                     'corner hold-down forces are reported')
PLATE_ALLOWABLE_NOTE = 'The allowable stress is your design allowable; no hidden safety factor is applied'


def _plate_number(raw, field, label):
    text = raw.get(field)
    try: value = float(str(text).strip())
    except ValueError: raise ValueError(f'{label}: expected a number') from None
    if not math.isfinite(value): raise ValueError(f'{label}: expected a finite number')
    return value


def plate_brief(raw):
    """PlateBrief payload from the form's raw strings. Values are parsed, never clipped, swapped or defaulted."""
    numbers = {field: _plate_number(raw, field, label) for field, label in PLATE_NUMBERS}
    thicknesses = []
    for part in str(raw.get('candidate_thicknesses_mm', '')).split(','):
        thicknesses.append(_plate_number(dict(t=part), 't', 'Candidate thickness'))
    requirements = dict(
        family='rectangular_plate', units='mm_N_MPa', length_a_mm=numbers['length_a_mm'], width_b_mm=numbers['width_b_mm'],
        edge_condition=raw.get('edge_condition'), pressure_mpa=numbers['pressure_mpa'],
        youngs_modulus_mpa=numbers['youngs_modulus_mpa'], poissons_ratio=numbers['poissons_ratio'],
        allowable_stress_mpa=numbers['allowable_stress_mpa'], max_deflection_mm=numbers['max_deflection_mm'],
        dimensional_tolerance_mm=numbers['dimensional_tolerance_mm'],
        material_reference=dict(source_kind=raw.get('source_kind'), reference=str(raw.get('material_reference', ''))),
        assumptions=PLATE_ASSUMPTIONS)
    return dict(mode=raw.get('mode'), requirements=requirements, candidate_thicknesses_mm=thicknesses)


def _plate_text(value):
    return json.dumps(value, allow_nan=False) if isinstance(value, (int, float)) and not isinstance(value, bool) else ''


def plate_form_values(brief):
    """Raw form strings from a PlateBrief dict (a draft's specification); blanks for a new form."""
    brief = brief if isinstance(brief, dict) else {}
    req = brief.get('requirements') if isinstance(brief.get('requirements'), dict) else {}
    material = req.get('material_reference') if isinstance(req.get('material_reference'), dict) else {}
    values = {field: _plate_text(req.get(field)) for field, _ in PLATE_NUMBERS}
    thicknesses = brief.get('candidate_thicknesses_mm')
    values.update(
        edge_condition=req.get('edge_condition') if req.get('edge_condition') in PLATE_EDGES else 'simply_supported',
        material_reference=material.get('reference') if isinstance(material.get('reference'), str) else '',
        source_kind=material.get('source_kind') if material.get('source_kind') in PLATE_SOURCES else 'illustrative',
        mode=brief.get('mode') if brief.get('mode') in PLATE_MODES else 'fixed_design',
        candidate_thicknesses_mm=', '.join(_plate_text(t) for t in thicknesses) if isinstance(thicknesses, list) else '')
    return values


def _dict(value):
    return value if isinstance(value, dict) else {}


def plate_result_view(report):
    """Facts the plate result shows, from generator-report.json. Missing parts stay None/empty, never invented."""
    report = _dict(report)
    attempts = [a for a in report.get('attempts') or [] if isinstance(a, dict)]
    verify = _dict(attempts[-1].get('verify')) if attempts else {}
    checks, history = _dict(verify.get('checks')), [h for h in verify.get('mesh_history') or [] if isinstance(h, dict)]
    deflection, stress = _dict(checks.get('deflection_limit')), _dict(checks.get('stress_limit'))
    corner = report.get('corner_forces')
    notes = [PLATE_SHEAR_NOTE]
    if isinstance(corner, dict): notes.append(PLATE_CORNER_NOTE)
    notes.append(PLATE_ALLOWABLE_NOTE)
    return dict(
        selected_thickness_mm=report.get('selected_thickness_mm'),
        attempts=[dict(thickness_mm=a.get('thickness_mm'), passed=a.get('passed') is True) for a in attempts],
        deflection=dict(checked_mm=deflection.get('quantity'), limit_mm=deflection.get('limit'),
                        kirchhoff_mm=history[-1].get('w_max') if history else None),
        stress=dict(von_mises_mpa=stress.get('quantity'), allowable_mpa=stress.get('limit'),
                    location=verify.get('vm_max_location')),
        mesh_history=history, references=_dict(verify.get('references')) or None,
        guards=_dict(verify.get('guards')), corner_forces=corner if isinstance(corner, dict) else None,
        notes=notes,
        lines=['engineering_certified: ' + ('true' if report.get('engineering_certified') is True else 'false'),
               'standards: not evaluated'])

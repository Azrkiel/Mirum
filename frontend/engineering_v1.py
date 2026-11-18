"""Pure helpers for the checked-design v1 view (no streamlit, no network).

Generators (dragster, regen, glider) are drafted through form or rules mode only; this
module never builds an 'ai' draft. Review fields of a generator are the leaves of its
normalized specification, computed here by the same rule the backend uses.
"""
from copy import deepcopy
import json
import math

from engineering_state import checked_bytes

BEAM_ID = 'rectangular_cantilever'
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


def result_view(job):
    accepted = job.get('outcome') == 'accepted' and job.get('delivery') == 'available'
    downloads = []
    if job.get('delivery') == 'available':
        offered = job.get('artifacts') or {}
        downloads = [dict(format=f, name=n, mime=m) for f, (n, m) in DOWNLOADS.items() if offered.get(f) is True]
    return dict(label=CHECKED_LABEL if accepted else None, outcome=job.get('outcome'),
                lifecycle=job.get('lifecycle'), downloads=downloads)


def verified_download(job, format, value):
    if format not in DOWNLOADS: raise ValueError('Unknown download format')
    return checked_bytes(job, DOWNLOADS[format][0], value)

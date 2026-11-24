"""Opt-in MEL editor over the existing checked-program and typed proposal routes."""

from hashlib import sha256
import json
import re

import requests
import streamlit as st

from engineering_ui import APIError

_ID = re.compile(r'[a-f0-9]{32}\Z')
_BASE = '/engineering/v1'
_BEAM = 'rectangular_cantilever'
_EXAMPLE = '''
language mirum "0.1";
design reference_beam {
  capability = "rectangular_cantilever";
  capability_version = "1.0.0";
  backend = "occt";
  numerical_policy = "beam_numerical_v1";
  resource_policy = "engineering_resources_v1";
  length = 200 mm;
  width = 10 mm;
  dimensional_tolerance = 0.01 mm;
  tip_force = 50 N;
  max_tip_deflection = 1 mm;
  youngs_modulus = 70000 MPa;
  allowable_stress = 120 MPa;
  material_reference = "Illustrative material";
  material_source_kind = "illustrative";
  assumptions = "ideal_clamp_end_force_linear_elastic";
  mode = "bounded_thickness";
  candidate_heights = [5 mm, 10 mm, 15 mm];
  exports = ["step", "glb"];
}
'''.lstrip()
_BAY = '''
language mirum "0.1";
design equipment_bay {
  capability = "equipment_bay";
  capability_version = "1.0.0";
  backend = "occt";
  numerical_policy = "equipment_bay_numerical_v1";
  resource_policy = "engineering_resources_v1";
  length = 560 mm;
  width = 260 mm;
  module_count = 10;
  preferred_angle = 0 deg;
  supplier = "nominal";
  exports = ["step"];
}
'''.lstrip()


class WorkspaceError(ValueError):
    def __init__(self, status, details):
        self.status = status
        self.details = details
        super().__init__(details.get('message') or f'Program request failed ({status})')


class WorkspaceClient:
    def __init__(self, backend, headers):
        self.base = backend.rstrip('/') + _BASE
        self.headers = headers

    def request(self, method, path, *, source=None, body=None, parent=None, guard=None, artifact=False):
        headers = self.headers()
        if source is not None:
            headers = {**headers, 'Content-Type': 'application/vnd.mirum.mel'}
        if parent is not None:
            headers = {**headers, 'X-Mirum-Expected-Parent': parent}
        if guard is not None:
            headers = {**headers, 'X-Mirum-Require-Requirements': guard}
        try:
            response = requests.request(method, self.base + path, headers=headers, data=source, json=body,
                                        allow_redirects=False, timeout=(5, 90), stream=True)
            with response:
                limit = 64 * 1024**2 if artifact and response.ok else 1024**2
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > limit:
                        raise WorkspaceError(502, {'message': 'Response exceeded the workspace size limit.'})
                    chunks.append(chunk)
                data = b''.join(chunks)
                if response.ok and artifact:
                    if sha256(data).hexdigest() != response.headers.get('X-Content-SHA256'):
                        raise WorkspaceError(502, {'message': 'Downloaded bytes failed the fingerprint check.'})
                    return data
                value = json.loads(data)
                if not response.ok:
                    raise WorkspaceError(response.status_code, value)
                return value
        except (requests.RequestException, ValueError) as error:
            if isinstance(error, WorkspaceError):
                raise
            raise WorkspaceError(503, {'message': 'The program service could not be reached.'}) from None

    def artifact(self, revision, fmt):
        return self.request('GET', f'/revisions/{revision}/artifacts/{fmt}', artifact=True)


def _source_from_beam(content):
    """Serialize an existing typed beam draft into input for the standard MEL compiler."""
    req = content['requirements']
    mat = req['material']
    fields = {
        'length': (req['length_mm'], 'mm'), 'width': (req['width_mm'], 'mm'),
        'dimensional_tolerance': (req['dimensional_tolerance_mm'], 'mm'),
        'tip_force': (req['tip_force_n'], 'N'), 'max_tip_deflection': (req['max_tip_deflection_mm'], 'mm'),
        'youngs_modulus': (mat['youngs_modulus_mpa'], 'MPa'),
        'allowable_stress': (mat['allowable_stress_mpa'], 'MPa'),
        'material_reference': (mat['reference'], ''), 'material_source_kind': (mat['source_kind'], ''),
        'assumptions': (req['assumptions'], ''), 'mode': (content['mode'], ''),
    }
    statements = [line for line in _EXAMPLE.splitlines() if not any(
        line.strip().startswith(name + ' =') for name in (*fields, 'candidate_heights')) and line != '}']
    for name, (value, unit) in fields.items():
        statements.append(f'  {name} = {json.dumps(value, allow_nan=False)}{(" " + unit) if unit else ""};')
    heights = ', '.join(f'{json.dumps(height, allow_nan=False)} mm' for height in content['candidate_heights_mm'])
    return '\n'.join((*statements, f'  candidate_heights = [{heights}];', '}', ''))


def _show_error(error):
    st.error('Program request failed.')
    st.text(str(error))
    if isinstance(error, WorkspaceError):
        diag = error.details.get('span') or {}
        if diag:
            st.text(f"Line {diag.get('line', '?')}, column {diag.get('column', '?')}")
        if error.details.get('code'):
            st.text(error.details['code'])


def _replace_source(source, *, guard=None):
    st.session_state['mel_next_source'] = source
    st.session_state['mel_proposal_guard'] = guard
    st.session_state.pop('mel_plan', None)
    st.rerun()


def _proposal(client, typed, head):
    prompt = st.text_area('Describe a beam revision', max_chars=2000, key='mel_ai_prompt')
    if st.button('Request AI draft', disabled=not head or not prompt.strip()):
        try:
            prepared = typed.request('POST', '/drafts', {'mode': 'ai', 'capability': _BEAM,
                                                        'input': {}, 'prompt': prompt})
            draft = prepared['draft']['content']
            if prepared['issues'] or draft is None:
                st.info('The proposal needs clarification before it can become MEL.')
                st.json(prepared['issues'])
                return
            source = _source_from_beam(draft)
            client.request('POST', '/mel/plan', source=source.encode('utf-8'))
            _replace_source(source, guard=head['fixed_requirements_sha256'])
        except (WorkspaceError, APIError) as error:
            _show_error(error)


def _review(client, revision, current_source):
    rid = revision['revision_id']
    prefix = f'/revisions/{rid}'
    if revision['state'] != 'staged':
        return
    if sha256(current_source).hexdigest() != revision['source_sha256']:
        st.info('Load this revision’s MEL source into the editor before reviewing or running it.')
        return
    fields = st.session_state.get('mel_review_fields', {}).get(rid)
    if fields is None:
        ir = json.loads(client.artifact(rid, 'program-ir'))
        if ir['capability']['id'] == 'equipment_bay':
            fields = ['specification.' + name for name in
                      ('length_mm', 'module_count', 'preferred_angle_rad', 'supplier', 'width_mm')]
        else:
            fields = ['length_mm', 'width_mm', 'dimensional_tolerance_mm', 'tip_force_n', 'max_tip_deflection_mm',
                      'material.youngs_modulus_mpa', 'material.allowable_stress_mpa', 'material.reference',
                      'material.source_kind', 'assumptions', 'mode', 'candidate_heights_mm']
    st.subheader('Review staged requirements')
    checked = st.checkbox('I reviewed the listed fields, assumptions and candidate choices.', key=f'mel_review_{rid}')
    st.text('\n'.join(fields))
    reviewer = st.text_input('Reviewer name', max_chars=120, key=f'mel_reviewer_{rid}')
    if st.button('Record review', disabled=not checked or not reviewer.strip()):
        client.request('POST', prefix + '/review', body={'brief_sha256': revision['brief_sha256'],
                       'acknowledged_fields': fields, 'reviewed_by': reviewer.strip()})
        st.session_state['mel_reviewed'] = rid
        st.rerun()
    st.caption('Review creates the execution authorization for these exact requirements.')
    reviewed = st.session_state.get('mel_reviewed') == rid
    if reviewed:
        st.success('Review recorded')
    if st.button('Run reviewed revision', disabled=not reviewed):
        client.request('POST', prefix + '/run')
        st.rerun()


def _results(client, revision, preview):
    rid = revision['revision_id']
    st.write(f"Revision state: {revision['state']}")
    if revision['reason']:
        st.text(revision['reason'])
    if revision['state'] in ('staged', 'running') and st.button('Cancel revision'):
        client.request('POST', f'/revisions/{rid}/cancel')
        st.rerun()
    with st.expander('Structured program'):
        if revision['state'] != 'staged' or st.button('Show structured program', key=f'mel_structure_{rid}'):
            st.json(json.loads(client.artifact(rid, 'program-ir')), expanded=False)
    inspected = client.request('GET', f'/revisions/{rid}/evidence')
    if inspected['evidence']['status'] == 'present' or inspected['evidence'].get('run'):
        st.subheader('Revision evidence')
        st.json(inspected['evidence'], expanded=False)
    fmt_by_file = {'model.step': 'step', 'preview.glb': 'glb', 'report.json': 'report',
                   'generator-body.step': 'generator-body-step', 'generator-bom.json': 'generator-bom',
                   'generator-report.json': 'generator-report'}
    if revision['artifacts'] == 'available':
        identities = {row['path']: row['sha256'] for row in revision['bundle']['payloads']}
        files = [name for name in fmt_by_file if name in identities]
        files += ['package.zip']
        fmt_by_file['package.zip'] = 'zip'
        identities['package.zip'] = revision['bundle']['zip_sha256']
        selected = st.selectbox('Revision artifact', files, key=f'mel_artifact_{rid}')
        if selected:
            value = client.artifact(rid, fmt_by_file[selected])
            if sha256(value).hexdigest() != identities[selected]:
                raise WorkspaceError(502, {'message': 'Artifact is not associated with this revision.'})
            st.download_button(f'Download {selected}', value, file_name=selected, key=f'mel_download_{rid}')
            if selected == 'preview.glb' and preview:
                preview(value)
            elif selected.endswith('.json'):
                st.json(json.loads(value), expanded=False)
    elif revision['state'] == 'rejected' and st.button('Read rejection report'):
        st.json(json.loads(client.artifact(rid, 'report')), expanded=False)


def _measured(client, revision):
    if revision['artifacts'] != 'available':
        return {}
    names = {row['path'] for row in revision['bundle']['payloads']}
    rid = revision['revision_id']
    if 'generator-report.json' in names:
        report = json.loads(client.artifact(rid, 'generator-report'))
        length, width = report['envelope']['measured_mm']
        offsets = [row['offset_mm'] for row in report['mounting']['correspondences']]
        return {'length_mm': length, 'width_mm': width, 'max_offset_mm': max(offsets)}
    if 'report.json' in names:
        report = json.loads(client.artifact(rid, 'report'))
        return report['evaluations'][-1]['native_report']['measurements']
    return {}



def render_program(backend, headers, typed, preview=None):
    client = WorkspaceClient(backend, headers)
    try:
        client.request('GET', '/mel/meta')
    except WorkspaceError as error:
        _show_error(error)
        return
    st.subheader('MEL program')
    if 'mel_next_source' in st.session_state:
        st.session_state['mel_source'] = st.session_state.pop('mel_next_source')
    beam, bay = st.columns(2)
    if beam.button('Load beam example'):
        _replace_source(_EXAMPLE)
    if bay.button('Load equipment-bay example'):
        _replace_source(_BAY)
    source = st.text_area('MEL source', value=_EXAMPLE, height=370, max_chars=65536, key='mel_source').encode('utf-8')
    try:
        if st.button('Validate and preview plan'):
            st.session_state['mel_plan'] = (sha256(source).hexdigest(),
                                           client.request('POST', '/mel/plan', source=source))
        plan = st.session_state.get('mel_plan')
        if plan and plan[0] == sha256(source).hexdigest():
            st.json(plan[1], expanded=False)
        if st.button('Create program'):
            st.session_state['mel_program_id'] = client.request('POST', '/programs')['program_id']
            st.session_state['mel_base_parent'] = None
            st.session_state.pop('mel_selected_revision', None)
            st.rerun()
        resume = st.text_input('Resume program ID', max_chars=32)
        if st.button('Open program', disabled=not _ID.fullmatch(resume)):
            opened = client.request('GET', f'/programs/{resume}')
            st.session_state['mel_program_id'] = resume
            st.session_state['mel_base_parent'] = opened['head_revision_id']
            st.session_state.pop('mel_selected_revision', None)
            st.rerun()
        program = st.session_state.get('mel_program_id')
        if not program:
            return
        program_path = f'/programs/{program}'
        st.text(f'Program ID {program}')
        st.button('Refresh revision status')
        history = client.request('GET', program_path + '/history')
        parent = st.session_state.get('mel_base_parent')
        stale = parent != history['head_revision_id']
        if stale:
            st.warning('The accepted head changed. Load its source or explicitly use the latest head before staging.')
            if st.button('Use latest accepted head'):
                st.session_state['mel_base_parent'] = history['head_revision_id']
                st.rerun()
        head = next((rev for rev in history['revisions'] if rev['is_current']), None)
        if head:
            with st.expander('AI-assisted beam proposal'):
                _proposal(client, typed, head)
        guard = st.session_state.get('mel_proposal_guard')
        if st.button('Stage source', disabled=stale):
            staged = client.request('POST', program_path + '/revisions', source=source, parent=parent, guard=guard)
            rid = staged['revision']['revision_id']
            st.session_state.setdefault('mel_review_fields', {})[rid] = staged['review_fields']
            st.session_state['mel_selected_revision'] = rid
            st.rerun()
        revisions = {row['revision_id']: row for row in history['revisions']}
        if not revisions:
            return
        rid = st.selectbox('Revision', list(revisions),
                           format_func=lambda key: f"{key[:12]} · {revisions[key]['state']}",
                           key='mel_selected_revision')
        revision = revisions[rid]
        if st.button('Load selected revision source'):
            if revision['is_current']:
                st.session_state['mel_base_parent'] = rid
            _replace_source(client.artifact(rid, 'program-source').decode('utf-8'))
        _review(client, revision, source)
        _results(client, revision, preview)
        others = [other for other in revisions if other != rid]
        if others:
            other = st.selectbox('Compare with revision', others, format_func=lambda key: key[:12])
            if st.button('Compare saved revisions'):
                comparison = client.request('POST', f'/revisions/{rid}/compare', body={'other_revision_id': other})
                st.json(comparison['diff'], expanded=False)
                before, after = _measured(client, revision), _measured(client, revisions[other])
                deltas = [{'measurement': name, 'first': before[name], 'second': after[name],
                           'second minus first': after[name] - before[name]}
                          for name in sorted(before.keys() & after.keys())
                          if type(before[name]) in (int, float) and type(after[name]) in (int, float)]
                if deltas:
                    st.table(deltas)
        if revision['state'] == 'published' and not revision['is_current']:
            confirmed = st.checkbox('Restore this saved revision as the accepted head.')
            if st.button('Restore selected revision', disabled=not confirmed):
                client.request('POST', f'/revisions/{rid}/restore', parent=history['head_revision_id'])
                st.rerun()
    except (WorkspaceError, APIError) as error:
        _show_error(error)

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
_PICO = 'regen_chamber'
_EXPORT_BYTES = 64 * 1024**2
_PICO_STL_BYTES = 128 * 1024**2
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
_PICO_EXAMPLE = '''
language mirum "0.1";
design picogk_reference {
  capability = "regen_chamber";
  capability_version = "1.0.0";
  backend = "picogk";
  numerical_policy = "regen_chamber_numerical_v1";
  resource_policy = "engineering_resources_v1";
  preset = "reference";
  exports = ["stl"];
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

    def request(self, method, path, *, source=None, source_id=None, body=None, parent=None, guard=None, artifact_bytes=0, expected_sha=None):
        headers = self.headers()
        if source is not None:
            headers = {**headers, 'Content-Type': 'application/vnd.mirum.mel'}
        if source_id is not None:
            headers = {**headers, 'X-Mirum-Source-Id': source_id}
        if parent is not None:
            headers = {**headers, 'X-Mirum-Expected-Parent': parent}
        if guard is not None:
            headers = {**headers, 'X-Mirum-Require-Requirements': guard}
        try:
            response = requests.request(method, self.base + path, headers=headers, data=source, json=body,
                                        allow_redirects=False, timeout=(5, 90), stream=True)
            with response:
                limit = artifact_bytes if artifact_bytes and response.ok else 1024**2
                # One growing buffer and a running digest: no chunk list, no join copy.
                data, digest = bytearray(), sha256()
                for chunk in response.iter_content(65536):
                    if len(data) + len(chunk) > limit:
                        raise WorkspaceError(502, {'message': 'Response exceeded the workspace size limit.'})
                    data += chunk
                    digest.update(chunk)
                if response.ok and artifact_bytes:
                    claimed = response.headers.get('X-Content-SHA256')
                    if digest.hexdigest() != claimed:
                        raise WorkspaceError(502, {'message': 'Downloaded bytes failed the fingerprint check.'})
                    # Fail closed: the verified header digest must be the revision record's digest.
                    if expected_sha is not None and claimed != expected_sha:
                        raise WorkspaceError(502, {'message': 'Artifact is not associated with this revision.'})
                    return bytes(data)
                value = json.loads(data)
                if not response.ok:
                    raise WorkspaceError(response.status_code, value)
                return value
        except (requests.RequestException, ValueError) as error:
            if isinstance(error, WorkspaceError):
                raise
            raise WorkspaceError(503, {'message': 'The program service could not be reached.'}) from None

    def artifact(self, revision, fmt, expected_sha=None):
        limit = _PICO_STL_BYTES if fmt == 'generator-cleaned-stl' else _EXPORT_BYTES
        return self.request('GET', f'/revisions/{revision}/artifacts/{fmt}', artifact_bytes=limit,
                            expected_sha=expected_sha)


def _artifact_value(client, rid, fmt, name, expected_sha, state):
    """Fetch one artifact for this render only. The session keeps just its verified identity, never the
    bytes (a 128 MiB artifact must not sit in every viewer's session); a rerun fetches it again."""
    # The client has verified the bytes against X-Content-SHA256 and that header against expected_sha.
    value = client.artifact(rid, fmt, expected_sha=expected_sha)
    state['mel_artifact_cache'] = ((rid, name, expected_sha), expected_sha)
    return value


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


_CLICK_TO_FETCH = ('.glb', '.step', '.stl', '.zip')


def _needs_click(name):
    """Large binary artifacts are fetched only when the user asks, never on every Streamlit rerun."""
    return name.endswith(_CLICK_TO_FETCH)


def _should_fetch(state, identity, clicked):
    """Whether this render fetches the selected artifact. Small files always; a large binary only after a
    click, and then on the reruns a download click triggers (Streamlit 1.39 has no on_click="ignore"), until
    another artifact is chosen. Only the identity is remembered, never bytes."""
    if not _needs_click(identity[1]):
        state.pop('mel_prepared', None)
        return True
    if clicked:
        state['mel_prepared'] = identity
    elif state.get('mel_prepared') != identity:
        state.pop('mel_prepared', None)
    return state.get('mel_prepared') == identity


def _busy_message(error):
    if isinstance(error, WorkspaceError) and error.status == 429 and error.details.get('code') == 'admission_busy':
        return 'Exports are busy right now. Retry in a moment.'
    return None


def _show_error(error):
    busy = _busy_message(error)
    if busy:
        st.info(busy)
        return
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
    with st.form('mel_ai_proposal'):
        prompt = st.text_area('Describe a beam revision', max_chars=2000)
        submitted = st.form_submit_button('Request AI draft')
    if not submitted:
        return
    if not prompt.strip():
        st.info('Describe the beam revision before requesting a draft.')
        return
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
        elif ir['capability']['id'] == _PICO:
            fields = ['specification.' + name for name in (
                'assumptions', 'chamber_length_mm', 'chamber_radius_mm', 'channel_count', 'channel_radius_mm',
                'channel_twist_rad_per_mm', 'convergent_length_mm', 'divergent_length_mm', 'end_margin_mm',
                'exit_radius_mm', 'family', 'manifold_radius_mm', 'throat_radius_mm', 'units', 'voxel_size_mm',
                'wall_thickness_mm')]
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
    if revision['is_current']:
        st.caption('Accepted head')
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
    fmt_by_file = {'preview.glb': 'glb', 'generator-body.step': 'generator-body-step', 'model.step': 'step',
                   'generator-bom.json': 'generator-bom', 'generator-report.json': 'generator-report',
                   'generator-cleaned.stl': 'generator-cleaned-stl',
                   'report.json': 'report'}
    if revision['artifacts'] == 'available':
        identities = {row['path']: row['sha256'] for row in revision['bundle']['payloads']}
        files = [name for name in fmt_by_file if name in identities]
        files += ['package.zip']
        fmt_by_file['package.zip'] = 'zip'
        identities['package.zip'] = revision['bundle']['zip_sha256']
        selected = st.selectbox('Revision artifact', files, key=f'mel_artifact_{rid}')
        if selected:
            clicked = _needs_click(selected) and st.button(f'Prepare {selected} for download',
                                                           key=f'mel_prepare_{rid}_{selected}')
            if not _should_fetch(st.session_state, (rid, selected, identities[selected]), clicked):
                st.caption('Large files are fetched only when requested.')
                return
            value = _artifact_value(client, rid, fmt_by_file[selected], selected, identities[selected],
                                    st.session_state)
            st.download_button(f'Download {selected}', value, file_name=selected, key=f'mel_download_{rid}')
            if selected == 'preview.glb' and preview:
                preview(value)
            elif selected.endswith('.json'):
                st.json(json.loads(value), expanded=False)
    else:
        st.session_state.pop('mel_artifact_cache', None)
        if revision['state'] == 'rejected' and st.button('Read rejection report'):
            capability = json.loads(client.artifact(rid, 'program-ir'))['capability']['id']
            report_format = 'generator-report' if capability == 'equipment_bay' else 'report'
            report = json.loads(client.artifact(rid, report_format))
            st.subheader('Saved rejection report')
            failures = [row for row in report.get('mounting', {}).get('correspondences', [])
                        if row['outcome'] != 'pass']
            if failures:
                st.table([{field: row[field] for field in ('occurrence_id', 'module_hole_id',
                                                          'offset_mm', 'tolerance_mm', 'outcome')}
                          for row in failures])
            st.json(report, expanded=False)
        elif revision['artifacts'] == 'expired':
            st.info('This revision’s package expired. Its evidence history is still available.')


def _measured(client, revision):
    if revision['artifacts'] != 'available':
        return {}
    names = {row['path'] for row in revision['bundle']['payloads']}
    rid = revision['revision_id']
    if 'generator-report.json' in names:
        report = json.loads(client.artifact(rid, 'generator-report'))
        if 'envelope' in report:
            length, width = report['envelope']['measured_mm']
            offsets = [row['offset_mm'] for row in report['mounting']['correspondences']]
            return {'length_mm': length, 'width_mm': width, 'max_offset_mm': max(offsets)}
        return {key: value for key, value in report.get('checks', {}).get('volume', {}).items()
                if type(value) in (int, float)}
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
    beam, bay, pico = st.columns(3)
    if beam.button('Load beam example'):
        _replace_source(_EXAMPLE)
    if bay.button('Load equipment-bay example'):
        _replace_source(_BAY)
    if pico.button('Load PicoGK example'):
        _replace_source(_PICO_EXAMPLE)
    source = st.text_area('MEL source', value=_EXAMPLE, height=370, max_chars=65536, key='mel_source').encode('utf-8')
    try:
        if st.button('Validate and preview plan'):
            try:
                st.session_state['mel_plan'] = (sha256(source).hexdigest(),
                                               client.request('POST', '/mel/plan', source=source))
            except WorkspaceError as error:
                st.session_state.pop('mel_plan', None)
                _show_error(error)
        plan = st.session_state.get('mel_plan')
        if plan and plan[0] == sha256(source).hexdigest():
            st.json(plan[1], expanded=False)
        if st.button('Create program'):
            st.session_state['mel_program_id'] = client.request('POST', '/programs')['program_id']
            st.session_state['mel_base_parent'] = None
            st.session_state.pop('mel_selected_revision', None)
            st.session_state['mel_revision_epoch'] = st.session_state.get('mel_revision_epoch', 0) + 1
            st.rerun()
        resume = st.text_input('Resume program ID', max_chars=32)
        if st.button('Open program', disabled=not _ID.fullmatch(resume)):
            opened = client.request('GET', f'/programs/{resume}')
            st.session_state['mel_program_id'] = resume
            st.session_state['mel_base_parent'] = opened['head_revision_id']
            st.session_state.pop('mel_selected_revision', None)
            st.session_state['mel_revision_epoch'] = st.session_state.get('mel_revision_epoch', 0) + 1
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
        if head and head['bundle'] and any(row['path'] == 'model.step' for row in head['bundle']['payloads']):
            with st.expander('AI-assisted beam proposal'):
                _proposal(client, typed, head)
        guard = st.session_state.get('mel_proposal_guard')
        if st.button('Stage source', disabled=stale):
            source_id = 'typed-ai-proposal.mir' if guard else None
            try:
                staged = client.request('POST', program_path + '/revisions', source=source, source_id=source_id,
                                        parent=parent, guard=guard)
            except WorkspaceError as error:
                _show_error(error)
            else:
                rid = staged['revision']['revision_id']
                st.session_state.setdefault('mel_review_fields', {})[rid] = staged['review_fields']
                st.session_state['mel_selected_revision'] = rid
                st.session_state['mel_revision_epoch'] = st.session_state.get('mel_revision_epoch', 0) + 1
                st.rerun()
        revisions = {row['revision_id']: row for row in history['revisions']}
        if not revisions:
            return
        options = sorted(revisions)
        selected = st.session_state.get('mel_selected_revision', history['head_revision_id'])

        widget_id = f"mel_revision_widget_{st.session_state.get('mel_revision_epoch', 0)}"

        def select_revision():
            st.session_state['mel_selected_revision'] = st.session_state[widget_id]

        st.selectbox('Revision', options, index=options.index(selected) if selected in options else 0,
                     format_func=lambda key: key[:12], key=widget_id, on_change=select_revision)
        rid = selected if selected in options else options[0]
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
                st.subheader('Saved revision comparison')
                st.json(comparison['diff'], expanded=False)
                before, after = _measured(client, revision), _measured(client, revisions[other])
                deltas = [{'measurement': name, 'first': before[name], 'second': after[name],
                           'second minus first': after[name] - before[name]}
                          for name in sorted(before.keys() & after.keys())
                          if type(before[name]) in (int, float) and type(after[name]) in (int, float)]
                if deltas:
                    st.table(deltas)
        if revision['state'] == 'published' and revision['artifacts'] == 'available' and not revision['is_current']:
            confirmed = st.checkbox('Restore this saved revision as the accepted head.')
            if st.button('Restore selected revision', disabled=not confirmed):
                restored = client.request('POST', f'/revisions/{rid}/restore', parent=history['head_revision_id'])
                st.session_state['mel_selected_revision'] = restored['revision']['revision_id']
                st.session_state['mel_base_parent'] = restored['revision']['revision_id']
                st.session_state['mel_revision_epoch'] = st.session_state.get('mel_revision_epoch', 0) + 1
                st.rerun()
    except (WorkspaceError, APIError) as error:
        _show_error(error)

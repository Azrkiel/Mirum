"""Review and run the dragster, regen and glider generators through /engineering/v1.

Form and rules drafts; an AI draft is requested only when the server reports ai_enabled and the
user picks the AI entry method. AI values are unverified until the reviewer acknowledges them. Every server
string is rendered through ``engineering_ui._plain`` or as JSON/plain text.
"""
from datetime import datetime, timezone

import streamlit as st

from engineering_state import CheckedState
from engineering_ui import APIError, _plain, _refs
from engineering_v1 import (ai_enabled, ai_available, ai_origins, field_label, is_ai_draft, propose_ai_draft,
                            review_ack_required, unverified_fields, create_generator_draft, draft_specification, edited_specification, finalize,
                            leaf_fields, parse_specification_json, outcome_message, patch_body, result_heading, result_view, state_line, submit, text_for,
                            verified_download)


def _states():
    return st.session_state.setdefault('v1gen_states', {})


def _ref(cid, name):
    return f'gen:{cid}:{name}'


def _remember(cid, state):
    refs = _refs()
    if state.prepared: refs[_ref(cid, 'draft')] = state.prepared['draft']['draft_id']
    if state.brief: refs[_ref(cid, 'brief')] = state.brief['brief_id']
    else: refs.pop(_ref(cid, 'brief'), None)
    if state.job_id: refs[_ref(cid, 'job')] = state.job_id


def _forget_draft(cid, state):
    state.prepared = None; state.brief = None; state.stale = False
    for name in ('draft', 'brief'): _refs().pop(_ref(cid, name), None)


def adopt_routed_draft(client, entry, state, prompt):
    """One-shot: a rules draft from the routed prompt. Any refusal leaves the form empty."""
    try:
        prepared, note = create_generator_draft(client, entry, prompt=prompt)
    except APIError as error:
        st.error(str(error)); return
    state.adopt_draft(prepared); _remember(entry['id'], state)
    if note == 'rules': st.info('The routed request was drafted by the rules proposer. Review and edit every value.')
    else: st.info('No rules draft was available for that request. Enter the specification yourself.')


def _ai_entry(client, entry, state, metadata):
    """AI-assisted entry: propose a draft from a request. Returns True when the typed form should be shown."""
    cid = entry['id']
    mode = st.radio('Entry method', ['Typed form', 'AI-assisted draft'], horizontal=True, key='v1gen_entry_' + cid)
    if not ai_available(metadata): st.caption('AI is unavailable. The typed form works without provider credentials.')
    if mode != 'AI-assisted draft': return True
    text = st.text_area('Engineering request', max_chars=2000, key='v1gen_ai_prompt_' + cid)
    st.caption('AI only proposes data. It cannot finalize a brief or execute geometry.')
    if st.button('Propose draft', key='v1gen_ai_' + cid, disabled=not text.strip()):
        try:
            out = propose_ai_draft(client, entry, text)
        except (APIError, ValueError) as error:
            st.error(str(error)); return False
        if out['status'] == 'unavailable': st.error(_plain(out['message']))
        else:
            state.adopt_draft(out['prepared']); _remember(cid, state); st.rerun()
    return False


def _ai_banner(prepared):
    draft = prepared['draft']
    st.warning('AI-proposed values are unverified. Review and edit every marked field before finalizing.')
    from engineering_v1 import ai_status
    st.caption({'proposed': 'AI proposed a draft.',
                'clarification_required': 'AI needs clarification; no values were proposed. Answer in the typed fields below or start a new request.',
                'unsupported': 'This request is not supported; no values were proposed.'}[ai_status(prepared)])
    for item in draft.get('clarifications') or []: st.caption(_plain(str(item)))
    fields = unverified_fields(prepared)
    if fields:
        st.caption('Unverified AI-proposed fields:')
        for name in fields: st.text(_plain(name))


def _result(client, state, entry):
    cid = entry['id']
    try: job = client.request('GET', '/jobs/' + state.job_id)
    except APIError as error:
        st.error(str(error)); return
    st.subheader(result_heading(entry, job))
    st.caption(_plain('Job ' + str(job.get('job_id')) + ' | brief SHA-256 ' + str(job.get('brief_sha256'))))
    if job.get('expired') or job.get('delivery') == 'expired':
        st.warning('Expired. The download window ended. Review a new request to generate again.'); return
    view = result_view(job, entry)
    active = job.get('lifecycle') not in ('completed', 'unavailable', 'cancelled')
    if view['label']: st.markdown('**' + view['label'] + '**')
    st.text(state_line(entry, job))
    if active:
        st.info('Native checks are running for this immutable reviewed brief.')
        if st.button('Cancel current job', key='v1gen_cancel_' + cid):
            try: client.request('POST', '/jobs/' + state.job_id + '/cancel'); st.rerun()
            except APIError as error: st.error(str(error))
        return
    kind, text = outcome_message(entry, job)
    getattr(st, kind)(text)
    for reason in job.get('failures', []) or []: st.text(str(reason))
    attempts = job.get('attempts') or []
    if attempts:
        st.dataframe([dict(outcome=str(a.get('outcome')), reasons='; '.join(map(str, a.get('failures') or [])) or 'All mandatory digital checks passed')
                      for a in attempts], hide_index=True, use_container_width=True)
    if job.get('delivery') != 'available':
        if job.get('outcome') == 'accepted': st.error('Required package integrity or storage is unavailable. Downloads are withheld.')
        return
    cache = st.session_state.setdefault('checked_files', {})
    seal = (job.get('bundle') or {}).get('manifest_sha256', 'diagnostic')
    files = {}
    try:
        for item in view['downloads']:
            key = (state.job_id, seal, item['format'])
            if key not in cache:
                value = client.request('GET', f"/jobs/{state.job_id}/artifacts/{item['format']}", binary=True)
                if job.get('outcome') == 'accepted': verified_download(job, item['format'], value)
                cache[key] = value
            files[item['format']] = cache[key]
    except (APIError, ValueError) as error:
        for key in [k for k in cache if k[0] == state.job_id]: del cache[key]
        st.error(str(error)); return
    if job.get('artifact_expires_at'):
        st.caption('Available until ' + datetime.fromtimestamp(job['artifact_expires_at'], timezone.utc).isoformat() + ' (UTC).')
    if 'markdown' in files:
        with st.expander('Read report', expanded=False): st.text(files['markdown'].decode('utf-8', 'replace'))
    st.subheader('Revision-bound downloads')
    columns = st.columns(3)
    for n, item in enumerate(view['downloads']):
        columns[n % 3].download_button('Download ' + item['name'], files[item['format']], file_name=item['name'],
                                       mime=item['mime'], key=f"v1gen_dl_{state.job_id}_{item['format']}")


def render_generator(client, entry, metadata):
    cid = entry['id']
    state = _states().setdefault(cid, CheckedState())
    st.caption(_plain(entry.get('label', cid)) + ' | ' + _plain(entry.get('selection_rule', '')))
    st.info('Digital model only. No physical testing, manufacturing release or standards certification.')
    for text in entry.get('limitations', []) or []: st.caption(_plain(text))
    if not entry.get('available'):
        st.warning('This capability is not installed on the server. Typed preparation stays available but a run cannot start.')
    refs = _refs()
    try:
        if state.prepared is None and refs.get(_ref(cid, 'draft')):
            state.adopt_draft(client.request('GET', '/drafts/' + refs[_ref(cid, 'draft')]))
        if state.prepared: state.observe(client.request('GET', '/drafts/' + state.prepared['draft']['draft_id']))
        if state.brief is None and not state.stale and refs.get(_ref(cid, 'brief')):
            try: state.reviewed(client.request('GET', '/briefs/' + refs[_ref(cid, 'brief')]))
            except ValueError: refs.pop(_ref(cid, 'brief'), None)
        if state.job_id is None: state.job_id = refs.get(_ref(cid, 'job'))
    except APIError as error:
        st.error(str(error))
        if error.status == 410: _forget_draft(cid, state)
        else: return
    if state.stale:
        st.warning('This draft changed in another tab. Load the latest revision before editing, confirming or running it.')
        if st.button('Load latest revision', key='v1gen_reload_' + cid):
            state.adopt_draft(client.request('GET', '/drafts/' + state.prepared['draft']['draft_id'])); _remember(cid, state); st.rerun()
    prepared = state.prepared
    st.subheader('1. Prepare the specification')
    typed = prepared is None and (_ai_entry(client, entry, state, metadata) if ai_enabled(metadata) else True)
    if typed:
        prompt = st.text_input('Describe the request (rules proposer, no AI)', max_chars=2000, key='gen_prompt_' + cid)
        left, right = st.columns(2)
        if left.button('Draft from description', key='v1gen_rules_' + cid, disabled=not prompt.strip()):
            adopt_routed_draft(client, entry, state, prompt); st.rerun()
        if right.button('Start an empty draft', key='v1gen_empty_' + cid):
            try:
                prepared, _ = create_generator_draft(client, entry)
                state.adopt_draft(prepared); _remember(cid, state); st.rerun()
            except APIError as error: st.error(str(error))
    elif prepared is not None:
        if st.button('Start a new request', key='v1gen_new_' + cid,
                     help='Creates a separate draft. The earlier draft stays in its own revision.'):
            _forget_draft(cid, state); st.rerun()
    prepared = state.prepared
    if prepared is None: return
    ai_draft = is_ai_draft(prepared)
    if ai_draft: _ai_banner(prepared)
    origins = ai_origins(prepared) if ai_draft else {}
    issues = prepared.get('issues', [])
    for issue in issues:
        (st.error if issue['state'] == 'unsupported' else st.warning)(_plain(str(issue['field']) + ': ' + str(issue['reason'])))
    draft = prepared['draft']
    epoch = f"{cid}_{draft['draft_id']}_{draft['revision']}"
    specification = draft_specification(prepared)
    if specification:
        with st.form('v1gen_form_' + epoch):
            st.caption('Every field below is a review field. Edit values, then save a new revision.')
            raw = {}
            for field in leaf_fields(specification):
                key, label = 'v1gen_' + epoch + '_' + field['path'], _plain(field_label(field['path'], origins))
                if field['kind'] == 'bool': raw[field['path']] = st.checkbox(label, value=field['value'], key=key)
                else: raw[field['path']] = st.text_input(label, value=text_for(field), key=key)
            save = st.form_submit_button('Save draft', disabled=state.stale)
        if save:
            try:
                new = edited_specification(specification, raw)
                state.adopt_draft(client.request('PATCH', '/drafts/' + draft['draft_id'], patch_body(prepared, new)))
                _remember(cid, state); st.rerun()
            except (APIError, ValueError) as error:
                if isinstance(error, APIError) and error.status == 409: state.stale = True; state.brief = None
                st.error(str(error))
    with st.expander('Replace the whole specification (JSON object)', expanded=not specification):
        text = st.text_area('Specification JSON', max_chars=16384, key='gen_json_' + epoch)
        if st.button('Save specification', key='v1gen_json_save_' + epoch, disabled=state.stale or not text.strip()):
            try:
                state.adopt_draft(client.request('PATCH', '/drafts/' + draft['draft_id'],
                                                 patch_body(prepared, parse_specification_json(text))))
                _remember(cid, state); st.rerun()
            except (APIError, ValueError) as error:
                if isinstance(error, APIError) and error.status == 409: state.stale = True; state.brief = None
                st.error(str(error))
    prepared = state.prepared
    draft = prepared['draft']
    st.subheader('2. Review the exact draft')
    st.caption(_plain(f"Draft {draft['draft_id']} | revision {draft['revision']} | brief SHA-256 {prepared.get('brief_sha256')}"))
    if draft.get('content'):
        with st.expander('Complete normalized specification and required checks'): st.json(draft['content'])
    with st.expander('Input provenance and unresolved requirements'):
        st.json(dict(provenance=draft['provenance'], issues=prepared['issues']))
    complete = bool(draft.get('content') and not prepared['issues'] and not state.stale)
    ack = st.checkbox('I reviewed every value of the normalized specification and the required checks.',
                      key='gen_ack_' + epoch, disabled=not complete)
    ai_ack = True
    if review_ack_required(prepared):
        ai_ack = st.checkbox('I reviewed every AI-proposed (unverified) field listed above.',
                             key='gen_aiack_' + epoch, disabled=not complete)
    if st.button('Finalize reviewed brief', key='v1gen_final_' + cid,
                 disabled=not complete or not ack or not ai_ack or state.can_start):
        try:
            brief = finalize(client, prepared, reviewed_at=datetime.now(timezone.utc).isoformat())
            state.reviewed(brief); _remember(cid, state); st.rerun()
        except (APIError, ValueError) as error: st.error(str(error))
    st.subheader('3. Run the reviewed design')
    if state.brief: st.caption(_plain('Finalized brief ' + state.brief['brief_id'] + ' | ' + state.brief['brief_sha256']))
    if st.button('Run reviewed checks', key='v1gen_run_' + cid, disabled=not state.can_start or not entry.get('available')):
        try:
            job = submit(client, state.brief)
            state.job_id = job['job_id']; _remember(cid, state); st.rerun()
        except APIError as error: st.error(str(error))
    if state.job_id:
        @st.fragment(run_every='1s')
        def result_fragment(): _result(client, state, entry)
        result_fragment()

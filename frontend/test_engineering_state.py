"""Revision-bound UI state, independent from Streamlit or a provider."""
import hashlib
import pytest


def test_edits_invalidate_review_but_keep_owned_job_for_cancel():
    from frontend.engineering_state import CheckedState
    state=CheckedState()
    state.adopt_draft(dict(draft=dict(draft_id='draft',revision=1),brief_sha256='a'*64))
    state.reviewed(dict(brief_id='brief',draft_id='draft',draft_revision=1,brief_sha256='a'*64))
    assert state.can_start
    state.job_id='job'
    state.adopt_draft(dict(draft=dict(draft_id='draft',revision=2),brief_sha256='a'*64))
    assert not state.can_start and state.brief is None and state.job_id=='job'


def test_other_tab_cannot_replace_rendered_revision_or_confirm_it():
    from frontend.engineering_state import CheckedState
    state=CheckedState()
    first=dict(draft=dict(draft_id='draft',revision=1),brief_sha256='a'*64)
    state.adopt_draft(first)
    assert not state.observe(dict(draft=dict(draft_id='draft',revision=2),brief_sha256='a'*64))
    assert state.stale and state.prepared==first and not state.can_start
    with pytest.raises(ValueError):
        state.reviewed(dict(brief_id='brief',draft_id='draft',draft_revision=2,brief_sha256='a'*64))


def test_refresh_reconstructs_review_and_job_exactly_and_preview_is_bound():
    from frontend.engineering_state import CheckedState,checked_bytes
    state=CheckedState()
    state.adopt_draft(dict(draft=dict(draft_id='draft',revision=2),brief_sha256='a'*64))
    state.reviewed(dict(brief_id='brief',draft_id='draft',draft_revision=2,brief_sha256='a'*64))
    state.job_id='job'
    assert state.can_start
    value=b'captured preview'
    job=dict(job_id='job',outcome='accepted',delivery='available',bundle=dict(payloads=[dict(path='preview.glb',sha256=hashlib.sha256(value).hexdigest())]))
    assert checked_bytes(job,'preview.glb',value)==value
    with pytest.raises(ValueError): checked_bytes(job,'preview.glb',b'other revision')
    with pytest.raises(ValueError): checked_bytes(dict(job,delivery='expired'),'preview.glb',value)


def test_form_preserves_unsupported_input_and_requires_explicit_new_request():
    from frontend.engineering_state import merge_form
    payload=merge_form({'features':['holes'],'requirements':{'other_load':'torsion'}},
        {'requirements':{'length_mm':200.},'mode':'fixed_design'})
    assert payload['features']==['holes']
    assert payload['requirements']['other_load']=='torsion'

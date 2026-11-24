"""F09 task 2: program_list, ai_edit interface surface, facade seams, staged-event origin provenance.

Pure and facade tests run on the host (HostStore + stub adapter from the F08 facade tests). The HTTP tests need
fastapi, httpx and the posix SQLiteStore, so they skip on the Windows laptop and run in the CI shards; they reuse
the F08 app harness (TestClient over main.app, temp data dir, MIRUM_ENGINEERING_PROGRAMS=1 via monkeypatch).
No provider, key or network is touched: the edit service is a tiny stub that drives the facade seams.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import typing

import pytest

from engineering.program import interface as api
from engineering.program import mel_edit, schema_export
from engineering.program.facade import ProgramQuota
from engineering.program.interface import InterfaceFailure
from engineering.program.revisions import JournalEntry
from tests.test_program_interface_api import (  # noqa: F401  (fixtures are imported by name)
    ALICE, BEAM, BEAM_5_10, BOB, V1, _http_app, count, db_counts, hdr, http, light, make_program,
    new_program, run, stage,
)

BEAM_TEXT = BEAM.decode('utf-8')
PROMPT = 'mel_edit_v1'
SECRET_TEXT = 'make-it-secret-request-text-123'


# ------------------------------------------------------------------------------------------ helpers

def failure_of(fn, *args, **kwargs) -> api.InterfaceError:
    """The envelope of the InterfaceFailure that fn raises (sync callable, or one returning a coroutine)."""
    with pytest.raises(InterfaceFailure) as info:
        out = fn(*args, **kwargs)
        if asyncio.iscoroutine(out):
            run(out)
    return info.value.error


def journal(w, pid, client=ALICE):
    return run(w.face.journal(api.HistoryRequest(program_id=pid), client=client))


def staged_details(w, pid, client=ALICE):
    return [dict(line.detail) for line in journal(w, pid, client) if line.event == 'staged']


class StubEdits:
    """The shape task 3 plugs in: async edit(face, request, client=...) over the three facade seams."""

    def __init__(self, rewrite=None):
        self.rewrite = rewrite or (lambda text: text.replace('[5 mm, 10 mm, 15 mm]', '[5 mm, 10 mm]'))
        self.calls = 0

    async def edit(self, face, request, *, client):
        self.calls += 1
        _head, text = face.head_for_edit(request.program_id, request.expected_parent, client=client)
        face.precheck_stage(client, len(text))
        stage_response = await face.stage_edited(self.rewrite(text), request.program_id,
                                                 request.expected_parent, client=client)
        return api.AiEditResponse(state='staged', stage=stage_response, provider_calls=0)


@pytest.fixture
def headed(light, monkeypatch):
    """`light` whose service.current reads the programs.head column without the published check, so a staged
    revision can stand in as a head (the host has no job controller to publish one)."""
    service = light.service

    def current(program_id, *, client):
        with light.store.transaction() as db:
            program = service._owned_program(db, program_id, client)
            head = program['head']
        return None if head is None else service.view(head, client=client)

    monkeypatch.setattr(service, 'current', current)
    return light


def make_head(w, pid, client=ALICE, source=BEAM):
    rev = stage(w, pid, client=client, source=source).revision
    with w.store.transaction() as db:
        db.execute('UPDATE programs SET head=? WHERE id=?', (rev.revision_id, pid))
    return rev


# ---------------------------------------------------------------------------- registry and schema (pure)

def test_new_operations_registry_entries():
    by = api.OPERATIONS_BY_NAME
    listing, edit = by['program_list'], by['ai_edit']
    assert listing.transports == ('python', 'http') and edit.transports == ('python', 'http')
    assert (listing.http_method, listing.http_path, listing.mutating) == ('GET', '/programs', False)
    assert (edit.http_method, edit.http_path, edit.mutating) == ('POST', '/programs/{pid}/ai-edits', True)
    assert edit.human_only is True and edit.body == 'json' and listing.body == 'none'
    assert listing.response_model is api.ProgramList and edit.response_model is api.AiEditResponse
    assert listing.input_model is api.ProgramListRequest and edit.input_model is api.AiEditRequest
    assert 'program_list' not in api.MCP_TOOL_NAMES and 'ai_edit' not in api.MCP_TOOL_NAMES
    assert not listing.mcp and not edit.mcp


def test_new_models_are_exported_as_schemas():
    assert schema_export.check() == []
    models = set(api.API_SCHEMA_MODELS.values())
    for model in (api.ProgramListRequest, api.ProgramList, api.ProgramListEntry, api.AiEditRequest,
                  api.AiEditResponse):
        assert model in models
    for model in (api.ProgramList, api.ProgramListEntry, api.AiEditResponse):
        assert model in api.RESPONSE_MODELS
    assert api.MelMeta(language_version='0.1', compiler_version='c').ai_edits is False
    assert api.MelMeta(language_version='0.1', compiler_version='c', ai_edits=True).ai_edits is True


def test_journal_schema_is_unchanged_by_origin_detail():
    entry = JournalEntry(seq=1, program_id='a' * 32, revision_id='b' * 32, event='staged',
                         detail={'origin': 'ai_edit', 'prompt': PROMPT},
                         created_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))
    assert entry.detail == {'origin': 'ai_edit', 'prompt': PROMPT}


def test_reason_codes_equal_mel_edit_reason_codes():
    assert api.AI_EDIT_REASON_CODES == mel_edit.REASON_CODES
    annotation = api.AiEditResponse.model_fields['reason_code'].annotation
    literals = [arg for arg in typing.get_args(annotation) if typing.get_origin(arg) is typing.Literal]
    assert len(literals) == 1
    assert typing.get_args(literals[0]) == mel_edit.REASON_CODES


@pytest.mark.parametrize('code,kind,status,retryable,action', [
    ('ai_edits_disabled', 'unavailable', 503, False, 'none'),
    ('ai_unavailable', 'unavailable', 503, True, 'retry_later'),
    ('requirements_change_not_allowed', 'refused', 409, False, 'fix_request'),
])
def test_new_error_codes(code, kind, status, retryable, action):
    err = api.error(code)
    assert (err.kind, err.code, err.retryable, err.next_action) == (kind, code, retryable, action)
    assert api.http_status(err) == status and err.changed == 'nothing_written'
    assert err.message.isascii() and err.hint is not None and err.hint.isascii()


def test_ai_edit_request_bounds():
    ok = api.AiEditRequest(program_id='a' * 32, request='use two heights', expected_parent='b' * 32)
    assert ok.request == 'use two heights'
    api.AiEditRequest(program_id='a' * 32, request='x' * 2000, expected_parent='b' * 32)
    for bad in ('', ' ', '\n\t  ', 'x' * 2001):
        with pytest.raises(Exception):
            api.AiEditRequest(program_id='a' * 32, request=bad, expected_parent='b' * 32)
    with pytest.raises(Exception):
        api.AiEditRequest(program_id='a' * 32, request='ok')  # expected_parent is required
    with pytest.raises(Exception):
        api.AiEditRequest(program_id='A' * 32, request='ok', expected_parent='b' * 32)
    with pytest.raises(Exception):
        api.AiEditRequest(program_id='a' * 32, request='ok', expected_parent='b' * 32, extra=1)


def test_ai_edit_response_shape_rules():
    refused = api.AiEditResponse(state='refused', reason_code='ambiguous', provider_calls=1)
    assert refused.stage is None and refused.provider_calls == 1
    for kwargs in (dict(state='refused'), dict(state='staged'),
                   dict(state='refused', reason_code='not_a_code'),
                   dict(state='refused', reason_code='ambiguous', provider_calls=3)):
        with pytest.raises(Exception):
            api.AiEditResponse(**kwargs)


def test_program_list_is_bounded_at_64():
    assert api.MAX_PROGRAM_LIST == 64
    entry = api.ProgramListEntry(program_id='a' * 32, created='2026-01-01T00:00:00.000000Z')
    assert api.ProgramList(programs=(entry,) * 64)
    with pytest.raises(Exception):
        api.ProgramList(programs=(entry,) * 65)


# ----------------------------------------------------------------------------- origin provenance (facade)

def test_human_stage_records_origin_human_and_rebuild_records_none(headed):
    pid = new_program(headed)
    head = make_head(headed, pid)
    assert staged_details(headed, pid) == [{'origin': 'human'}]
    run(headed.face.rebuild(api.RebuildRequest(revision_id=head.revision_id, expected_parent=head.revision_id),
                            client=ALICE))
    assert staged_details(headed, pid) == [{'origin': 'human'}, {}]


def test_service_stage_origin_allowlist(light):
    pid = new_program(light)
    for kwargs in (dict(origin='robot'), dict(origin='human', origin_prompt='other'),
                   dict(origin='ai_edit', origin_prompt='mel_edit_v2')):
        with pytest.raises(ValueError):
            run(light.service.stage(pid, BEAM, source_id='beam.mir', expected_parent=None, client=ALICE,
                                    **kwargs))
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == 0
    rev = run(light.service.stage(pid, BEAM, source_id='beam.mir', expected_parent=None, client=ALICE,
                                  origin='ai_edit', origin_prompt=PROMPT))
    assert rev.state == 'staged'
    assert staged_details(light, pid) == [{'origin': 'ai_edit', 'prompt': PROMPT}]


def test_ai_edit_origin_is_journaled_and_meaning_does_not_depend_on_it(headed):
    human_pid, ai_pid = new_program(headed), new_program(headed)
    # stage_edited labels its source with the default source id, so the human side does too.
    human = run(headed.face.stage(BEAM, api.StageRequest(program_id=human_pid), client=ALICE)).revision
    head = make_head(headed, ai_pid)
    edited = run(headed.face.stage_edited(BEAM_TEXT, ai_pid, head.revision_id, client=ALICE))
    assert staged_details(headed, ai_pid) == [{'origin': 'human'}, {'origin': 'ai_edit', 'prompt': PROMPT}]
    ai_rev = edited.revision
    assert ai_rev.parent == head.revision_id and ai_rev.state == 'staged'
    # Same source -> same semantic hash, brief hash, source hash, IR and requirements, whoever staged it.
    for field in ('semantic_sha256', 'brief_sha256', 'source_sha256', 'fixed_requirements_sha256'):
        assert getattr(ai_rev, field) == getattr(human, field), field
    human_payload = headed.service.source(human.revision_id, client=ALICE)
    ai_payload = headed.service.source(ai_rev.revision_id, client=ALICE)
    assert ai_payload.source == human_payload.source and ai_payload.ir_json == human_payload.ir_json


def test_typed_edit_equals_hand_written_edit(headed):
    ai_pid, human_pid = new_program(headed), new_program(headed)
    head = make_head(headed, ai_pid)
    ai_rev = run(headed.face.stage_edited(BEAM_5_10.decode('utf-8'), ai_pid, head.revision_id,
                                          client=ALICE)).revision
    human_rev = stage(headed, human_pid, source=BEAM_5_10).revision
    assert ai_rev.semantic_sha256 == human_rev.semantic_sha256 and ai_rev.brief_sha256 == human_rev.brief_sha256
    assert ai_rev.semantic_sha256 != head.semantic_sha256


# --------------------------------------------------------------------------------------- facade seams

def test_ai_edit_is_disabled_without_an_edit_service(light):
    assert light.face.ai_edits is None
    pid = new_program(light)
    request = api.AiEditRequest(program_id=pid, request='two heights', expected_parent='0' * 32)
    err = failure_of(light.face.ai_edit, request, client=ALICE)
    assert (err.kind, err.code) == ('unavailable', 'ai_edits_disabled')
    # It fails before anything is read: an unknown program gives the same envelope.
    unknown = api.AiEditRequest(program_id='f' * 32, request='two heights', expected_parent='0' * 32)
    assert failure_of(light.face.ai_edit, unknown, client=BOB) == err
    assert count(light, 'SELECT COUNT(*) FROM program_revisions') == 0


def test_ai_edit_delegates_to_the_attached_service(headed):
    stub = StubEdits()
    headed.face.ai_edits = stub
    pid = new_program(headed)
    head = make_head(headed, pid)
    out = run(headed.face.ai_edit(api.AiEditRequest(program_id=pid, request='drop 15 mm',
                                                    expected_parent=head.revision_id), client=ALICE))
    assert isinstance(out, api.AiEditResponse) and out.state == 'staged' and stub.calls == 1
    assert out.stage.revision.parent == head.revision_id
    assert out.stage.revision.semantic_sha256 != head.semantic_sha256
    assert list(out.stage.review_fields)


def test_head_for_edit_seam(headed):
    pid = new_program(headed)
    assert failure_of(headed.face.head_for_edit, pid, '0' * 32, client=ALICE).code == 'stale_parent'  # no head
    head = make_head(headed, pid)
    got_head, text = headed.face.head_for_edit(pid, head.revision_id, client=ALICE)
    assert got_head.revision.revision_id == head.revision_id and text == BEAM_TEXT
    assert failure_of(headed.face.head_for_edit, pid, '0' * 32, client=ALICE).code == 'stale_parent'
    unknown = failure_of(headed.face.head_for_edit, 'f' * 32, head.revision_id, client=ALICE)
    foreign = failure_of(headed.face.head_for_edit, pid, head.revision_id, client=BOB)
    assert unknown.code == foreign.code == 'not_found' and unknown == foreign


def test_head_for_edit_compacted_source_is_expired(headed):
    pid = new_program(headed)
    head = make_head(headed, pid)
    with headed.store.transaction() as db:
        db.execute('UPDATE program_revisions SET payload=NULL WHERE id=?', (head.revision_id,))
    err = failure_of(headed.face.head_for_edit, pid, head.revision_id, client=ALICE)
    assert (err.kind, err.code) == ('expired', 'history_expired')


def test_stage_edited_refuses_changed_requirements_and_stages_nothing(headed):
    pid = new_program(headed)
    head = make_head(headed, pid)
    before = db_counts_light(headed)
    changed = BEAM_TEXT.replace('tip_force = 50 N', 'tip_force = 60 N')
    err = failure_of(headed.face.stage_edited, changed, pid, head.revision_id, client=ALICE)
    assert (err.kind, err.code) == ('refused', 'requirements_changed')
    assert db_counts_light(headed) == before


def test_stage_edited_requires_a_head(headed):
    pid = new_program(headed)
    err = failure_of(headed.face.stage_edited, BEAM_TEXT, pid, None, client=ALICE)
    assert err.code == 'requirements_changed'
    assert count(headed, 'SELECT COUNT(*) FROM program_revisions') == 0


def test_stage_edited_stale_parent_is_a_conflict_and_stages_nothing(headed):
    pid = new_program(headed)
    make_head(headed, pid)
    before = db_counts_light(headed)
    err = failure_of(headed.face.stage_edited, BEAM_TEXT, pid, '0' * 32, client=ALICE)
    assert (err.kind, err.code) == ('conflict', 'stale_parent')
    assert db_counts_light(headed) == before


def test_stage_edited_validates_its_arguments(headed):
    pid = new_program(headed)
    head = make_head(headed, pid)
    assert failure_of(headed.face.stage_edited, BEAM, pid, head.revision_id, client=ALICE).code == 'invalid_field'
    err = failure_of(headed.face.stage_edited, BEAM_TEXT, pid, head.revision_id, client=ALICE, origin='robot')
    assert err.code == 'invalid_field'
    foreign = failure_of(headed.face.stage_edited, BEAM_TEXT, pid, head.revision_id, client=BOB)
    assert foreign.code == 'not_found'


def test_stage_edited_goes_through_the_stage_slot_and_quotas(headed):
    pid = new_program(headed)
    head = make_head(headed, pid)
    run(headed.face._slot.acquire())
    try:
        assert failure_of(headed.face.stage_edited, BEAM_TEXT, pid, head.revision_id,
                          client=ALICE).code == 'slot_busy'
    finally:
        headed.face._slot.release()
    headed.face.program_quota = ProgramQuota(headed.store, staged=1, clock=headed.clock)
    assert failure_of(headed.face.stage_edited, BEAM_TEXT, pid, head.revision_id,
                      client=ALICE).code == 'quota_staged'


def db_counts_light(w):
    return tuple(count(w, f'SELECT COUNT(*) FROM {t}') for t in
                 ('programs', 'program_revisions', 'program_journal', 'drafts', 'briefs'))


def test_precheck_stage_writes_nothing_and_reports_quota_busy(light):
    pid = new_program(light)
    stage(light, pid)
    before = db_counts_light(light)
    assert light.face.precheck_stage(ALICE, len(BEAM)) is None
    assert db_counts_light(light) == before
    light.face.program_quota = ProgramQuota(light.store, staged=1, clock=light.clock)
    err = failure_of(light.face.precheck_stage, ALICE, len(BEAM))
    assert (err.kind, err.code) == ('busy', 'quota_staged') and err.retry_after is not None
    light.face.program_quota = ProgramQuota(light.store, payload_bytes=10, clock=light.clock)
    assert failure_of(light.face.precheck_stage, ALICE, len(BEAM)).code == 'quota_bytes'
    assert db_counts_light(light) == before


def test_precheck_stage_reports_the_draft_quota(light):
    from tests.test_program_interface_api import new_quota
    pid = new_program(light)
    stage(light, pid)
    light.face.preparation_quota = new_quota(light.store, per_client_drafts=1)
    before = db_counts_light(light)
    err = failure_of(light.face.precheck_stage, ALICE, len(BEAM))
    assert (err.kind, err.code) == ('busy', 'quota_drafts')
    assert db_counts_light(light) == before
    assert light.face.precheck_stage(BOB, len(BEAM)) is None  # per client


# ------------------------------------------------------------------------------------------ program_list

def test_program_list_is_the_callers_programs_only(headed):
    empty = new_program(headed)
    headed_pid = new_program(headed)
    head = make_head(headed, headed_pid)
    foreign = new_program(headed, BOB)
    out = run(headed.face.program_list(api.ProgramListRequest(), client=ALICE))
    assert isinstance(out, api.ProgramList)
    by_id = {entry.program_id: entry for entry in out.programs}
    assert set(by_id) == {empty, headed_pid} and foreign not in by_id
    assert by_id[empty].head is None and by_id[empty].fixed_requirements_sha256 is None
    entry = by_id[headed_pid]
    assert entry.head.revision_id == head.revision_id and entry.head.result_kind == 'pending'
    assert entry.fixed_requirements_sha256 == head.fixed_requirements_sha256
    assert entry.created.endswith('Z')
    mine = run(headed.face.program_list(client=BOB))
    assert [e.program_id for e in mine.programs] == [foreign]
    assert run(headed.face.program_list(client='nobody')).programs == ()


def test_program_list_is_read_only(headed):
    new_program(headed)
    make_head(headed, new_program(headed))
    before = db_counts_light(headed)
    run(headed.face.program_list(api.ProgramListRequest(), client=ALICE))
    assert db_counts_light(headed) == before


def test_program_list_is_capped_at_64(light):
    now = light.clock()
    with light.store.transaction() as db:
        for i in range(70):
            db.execute('INSERT INTO programs(id, client, head, head_seq, created, updated) VALUES (?,?,NULL,0,?,?)',
                       (f'{i:032x}', ALICE, now + i, now + i))
    out = run(light.face.program_list(client=ALICE))
    assert len(out.programs) == 64
    assert [e.program_id for e in out.programs] == [f'{i:032x}' for i in range(64)]  # oldest first


# ----------------------------------------------------------------------------------------- HTTP transport
# Need fastapi, httpx and the posix SQLiteStore (the F08 harness skips otherwise).

def ai_post(w, pid, body, who=ALICE, **kwargs):
    return w.c.post(f'{V1}/programs/{pid}/ai-edits', json=body, headers=hdr(who), **kwargs)


def good_body(parent='0' * 32, text='use two heights'):
    return {'request': text, 'expected_parent': parent}


def test_http_program_list_lists_only_the_callers_programs(http):
    mine = {make_program(http), make_program(http)}
    theirs = make_program(http, 'bob')
    response = http.c.get(V1 + '/programs', headers=hdr())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['api_version'] == 'mirum_program_api_v0_1'
    assert {entry['program_id'] for entry in body['programs']} == mine
    assert all(entry['head'] is None and entry['fixed_requirements_sha256'] is None for entry in body['programs'])
    assert response.headers['cache-control'] == 'no-store'
    assert theirs not in response.text
    seen = http.c.get(V1 + '/programs', headers=hdr('bob')).json()
    assert [entry['program_id'] for entry in seen['programs']] == [theirs]
    for text in (response.text, json_text(seen)):
        for needle in ('draft_id', 'brief_id', 'job_id'):
            assert needle not in text


def json_text(value):
    import json
    return json.dumps(value)


def test_http_new_routes_need_the_access_token(http, monkeypatch):
    pid = make_program(http)
    monkeypatch.setenv('MIRUM_ACCESS_TOKEN', 'fixture-token')
    for method, url, kwargs in (('GET', V1 + '/programs', {}),
                                ('POST', f'{V1}/programs/{pid}/ai-edits', {'json': good_body()})):
        denied = http.c.request(method, url, **kwargs)
        assert denied.status_code == 401, (method, url)
        assert denied.json() == {'detail': 'Access token required.'}
        wrong = http.c.request(method, url, headers={'X-Mirum-Access-Token': 'nope'}, **kwargs)
        assert wrong.status_code == 401


def test_http_meta_reports_ai_edits_off_by_default(http):
    body = http.c.get(V1 + '/mel/meta', headers=hdr()).json()
    assert body['ai_edits'] is False


def test_http_ai_edit_flag_unset_is_503_and_imports_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv('MIRUM_ENGINEERING_PROGRAM_AI', raising=False)
    monkeypatch.delitem(sys.modules, 'engineering.program.ai_edit', raising=False)
    with _http_app(tmp_path, monkeypatch) as w:
        assert w.face is not None and w.face.ai_edits is None
        pid = make_program(w)
        before = db_counts(w)
        response = ai_post(w, pid, good_body(text=SECRET_TEXT))
        assert response.status_code == 503, response.text
        body = response.json()
        assert (body['kind'], body['code'], body['retryable'], body['next_action']) == (
            'unavailable', 'ai_edits_disabled', False, 'none')
        assert SECRET_TEXT not in response.text and pid not in response.text
        assert db_counts(w) == before  # nothing staged, nothing created
        assert 'engineering.program.ai_edit' not in sys.modules
        assert w.c.get(V1 + '/mel/meta', headers=hdr()).json()['ai_edits'] is False


def test_http_flag_on_without_a_usable_service_leaves_it_off(tmp_path, monkeypatch, caplog):
    """MIRUM_ENGINEERING_PROGRAM_AI alone never turns typed edits on: the other flags and the module decide."""
    monkeypatch.setenv('MIRUM_ENGINEERING_PROGRAM_AI', '1')
    monkeypatch.delenv('MIRUM_ENGINEERING_AI_DRAFTS', raising=False)
    with caplog.at_level(logging.WARNING):
        with _http_app(tmp_path, monkeypatch) as w:
            assert w.face is not None and w.face.ai_edits is None
            assert w.c.get(V1 + '/mel/meta', headers=hdr()).json()['ai_edits'] is False
            assert ai_post(w, make_program(w), good_body()).status_code == 503


@pytest.mark.parametrize('body', [
    {'request': '', 'expected_parent': '0' * 32},
    {'request': '   \n ', 'expected_parent': '0' * 32},
    {'request': 'x' * 2001, 'expected_parent': '0' * 32},
    {'request': 'ok'},
    {'expected_parent': '0' * 32},
    {'request': 'ok', 'expected_parent': '0' * 32, 'extra': 1},
    {'request': 5, 'expected_parent': '0' * 32},
])
def test_http_ai_edit_bad_bodies_are_422(http, body):
    pid = make_program(http)
    response = ai_post(http, pid, body)
    assert response.status_code == 422, response.text
    assert response.json()['kind'] == 'invalid_request'


def test_http_ai_edit_body_program_id_is_refused(http):
    pid = make_program(http)
    response = ai_post(http, pid, dict(good_body(), program_id=pid))
    assert response.status_code == 422
    assert response.json()['kind'] == 'invalid_request' and response.json()['field'] == 'program_id'
    other = ai_post(http, pid, dict(good_body(), program_id='f' * 32))
    assert other.status_code == 422


def test_http_ai_edit_body_shape_and_media_are_checked(http):
    pid = make_program(http)
    url = f'{V1}/programs/{pid}/ai-edits'
    assert http.c.post(url, content=b'not json', headers=hdr(**{'Content-Type': 'application/json'})
                       ).status_code == 422
    assert http.c.post(url, content=b'[1]', headers=hdr(**{'Content-Type': 'application/json'})
                       ).status_code == 422
    assert http.c.post(url, content=b'{}', headers=hdr(**{'Content-Type': 'text/plain'})).status_code == 422
    assert http.c.post(url, content=b'{"request":"' + b'x' * 70000 + b'"}',
                       headers=hdr(**{'Content-Type': 'application/json'})).status_code == 413


def test_http_ai_edit_with_a_service_unknown_and_foreign_are_404(http):
    http.face.ai_edits = StubEdits()
    pid = make_program(http)
    foreign = ai_post(http, pid, good_body(), who='bob')
    unknown = ai_post(http, 'f' * 32, good_body(), who='bob')
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json() and foreign.json()['kind'] == 'not_found'
    assert pid not in foreign.text
    malformed = http.c.post(f'{V1}/programs/not-an-id/ai-edits', json=good_body(), headers=hdr())
    assert malformed.status_code == 404
    assert http.face.ai_edits.calls == 2  # the malformed id never reaches the service
    assert db_counts(http)[1] == 0


def test_http_ai_edit_stale_parent_is_a_conflict_and_stages_nothing(http):
    http.face.ai_edits = StubEdits()
    pid = make_program(http)
    before = db_counts(http)
    response = ai_post(http, pid, good_body(parent='1' * 32))
    assert response.status_code == 409, response.text
    assert (response.json()['kind'], response.json()['code']) == ('conflict', 'stale_parent')
    assert db_counts(http) == before


def test_http_ai_edit_stages_through_a_service_with_origin_journaled(http):
    http.face.ai_edits = StubEdits()
    pid = make_program(http)
    head = stage_and_head(http, pid)
    response = ai_post(http, pid, good_body(parent=head))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['state'] == 'staged' and body['reason_code'] is None and body['provider_calls'] == 0
    assert body['stage']['revision']['parent'] == head and body['stage']['review_fields']
    lines = http.c.get(f'{V1}/programs/{pid}/journal', headers=hdr()).json()['journal']
    staged = [line['detail'] for line in lines if line['event'] == 'staged']
    assert staged == [{'origin': 'human'}, {'origin': 'ai_edit', 'prompt': PROMPT}]
    assert http.c.get(V1 + '/mel/meta', headers=hdr()).json()['ai_edits'] is True


def stage_and_head(w, pid):
    """A head the HTTP harness can use without a job controller: stage, then point programs.head at it and let
    the service read the head column without the published check (test scaffolding only)."""
    from tests.test_program_interface_api import post_mel
    response = post_mel(w, f'/programs/{pid}/revisions', BEAM, ALICE, **{'X-Mirum-Source-Id': 'beam.mir'})
    assert response.status_code == 201, response.text
    rid = response.json()['revision']['revision_id']
    service = w.face.service
    with service.store.transaction() as db:
        db.execute('UPDATE programs SET head=? WHERE id=?', (rid, pid))

    def current(program_id, *, client):
        with service.store.transaction() as db:
            head = service._owned_program(db, program_id, client)['head']
        return None if head is None else service.view(head, client=client)
    service.current = current  # the harness app is discarded after the test
    return rid


def test_http_request_log_masks_the_ai_edit_path_and_never_logs_the_text(http, caplog):
    pid = make_program(http)
    with caplog.at_level(logging.INFO, logger='mirum.api'):
        assert ai_post(http, pid, good_body(text=SECRET_TEXT)).status_code == 503
        assert http.c.get(V1 + '/programs', headers=hdr()).status_code == 200
    assert pid not in caplog.text and SECRET_TEXT not in caplog.text
    assert '/engineering/v1/programs/{pid}/ai-edits' in caplog.text
    assert '/engineering/v1/programs' in caplog.text


def test_http_new_routes_carry_per_client_rate_limits(http, monkeypatch):
    import main
    names = {}
    for route in main.app.routes:
        path = getattr(route, 'path', '')
        if path in (V1 + '/programs', V1 + '/programs/{pid}/ai-edits'):
            for method in route.methods - {'HEAD', 'OPTIONS'}:
                endpoint = route.endpoint
                names[(method, path[len(V1):])] = '%s.%s' % (endpoint.__module__, endpoint.__name__)
    assert ('GET', '/programs') in names and ('POST', '/programs/{pid}/ai-edits') in names
    for key in (('GET', '/programs'), ('POST', '/programs/{pid}/ai-edits')):
        assert names[key] in main.limiter._route_limits, key
    # POST /programs/{pid}/ai-edits is 10/minute: the 11th call in a minute is a busy envelope. slowapi binds
    # key_func at decoration, so the test's x-test-client identity does not reach the limiter (see F08).
    pid = make_program(http)
    monkeypatch.setattr(main.limiter, 'enabled', True)
    main.app.state.limiter.reset()
    try:
        codes = [ai_post(http, pid, good_body()).status_code for _ in range(11)]
        assert 429 not in codes[:10] and codes[10] == 429
        limited = ai_post(http, pid, good_body())
        assert limited.json()['kind'] == 'busy'
    finally:
        main.app.state.limiter.reset()
